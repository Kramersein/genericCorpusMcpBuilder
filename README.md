# genericCorpusMcpBuilder

Turns a corpus of texts into an MCP server that an AI can research with: exact text in the original and its
translations, cited the way readers cite it, every quote checked against the source, plus keyword and semantic
search, cross-references and commentaries where the corpus has them.

One server serves one corpus. Built in:

| source | what | adapter |
|---|---|---|
| [Sefaria](https://www.sefaria.org) | Tanakh, Mishnah, Talmud, Midrash, codes with their commentaries, responsa; Sefaria's link graph and topics | `--source sefaria` |
| [Perseus](https://github.com/PerseusDL) | Greek and Latin literature with English translations | `--source perseus` |
| your TEI editions | a folder of TEI XML files | `--source tei --raw DIR` |
| your tables | CSV/TSV, one row per segment | `--source table --raw DIR` |
| anything else | write an adapter: `corpus-mcp new-adapter NAME` | [docs/adapters.md](docs/adapters.md) |

The server retrieves and verifies sources; it does not decide questions for anyone.

## Tools

Built for an agent answering questions from people with no background in the texts. The server's instructions and
the `answer_question` prompt (both per corpus) give the agent this workflow: research → read in context →
commentary → verify every citation → answer, quoting the sources and separating quote, translation and
explanation. The table shows the Sefaria server; tools without data in a corpus (topics, links) are left out, and
`trace_halakha` is Sefaria's own.

| tool | use |
|---|---|
| `research_question(question, key_terms)` | **start here**: sources for a plain-language question, found by meaning, curated topics and Hebrew key terms, followed to the Shulchan Arukh, arranged Torah → Mishnah → Talmud → commentaries → codes → responsa |
| `explore_topic(name)` | a Sefaria topic: description, related topics, curated sources by layer |
| `get_text(ref, context)` | exact Hebrew + English for a ref, range, named passage or word span, optionally with surrounding segments |
| `get_commentary(ref)` | what Rashi, Tosafot, Mishnah Berurah etc. say on a passage, earliest first |
| `trace_halakha(ref)` | a law's earlier sources and later codification via the link graph |
| `verify_citations([{ref, quote}])` | **before answering**: every ref exists and every quote is there; returns word-exact `cite_as` |
| `find_quote(quote)` | the true source of a phrase, earliest first |
| `search`, `semantic_search`, `get_links` | targeted follow-ups |
| `get_work_info`, `list_works`, `about_corpus` | metadata, translations, citation systems, build provenance |

Refs: `Shabbat 73a:4`, `Shulchan Arukh, Orach Chayim 308:1`, `Mishnah Berurah 253:25`, or Hebrew
`שבת עג.`, `שו"ע או"ח סימן שח סעיף א`.

## Citation model

- **Locations**: every citable position (verse, line of Gemara, seif) has a stable integer id in reading order, so any
  ref or range is an id range. Links between texts are resolved to id ranges at build time.
- **Several citation systems per work**: Sefaria's alternative structures are imported, so `Parashat Noach 2`,
  `Shabbat, Bameh Madlikin`, `Jerusalem Talmud Shabbat, Vilna 7a` and Shulchan Arukh topics resolve to exact ranges, and
  every passage reports its other names (`also_cited_as`).
- **Word-level citations**: `ref@n-m` cites words n..m of one segment's original text (`Moed Katan 8b:9@29-32`);
  `verify_citation` and `find_quote` return such citations for every match, across segment boundaries if needed.
  Words are counted deterministically from the stored text (rule recorded in the build manifest), so no word table
  is stored.
- **Provenance**: each segment's text is hashed (blake2b-128); answers carry the build id and a fingerprint of the
  exact text returned. `about_corpus` returns the manifest (source export date, code fingerprint, counts).

## Data choices

- Hebrew: Sefaria's merged edition per work (source versions recorded in `versions.sources`).
- English: every English-language version is stored separately, labelled; the default is Sefaria's own top
  priority version. Versions tagged `[fr]`, `[de]`, `[pt]` etc. are skipped.
- In the William Davidson Talmud, **bold** marks the literal translation; plain text is elucidation.

## Data

The server does not ship any texts. Each installation builds its own index from Sefaria's free public export
(`gs://sefaria-export`) and API, so there is no copy of Sefaria to maintain. The texts keep the licenses Sefaria
gives them; check those on sefaria.org before redistributing a built data directory.

A built **data directory** holds:

| path | size | needed |
|---|---|---|
| `corpus.db` | ~7 GB | yes |
| `precomputed.db` | ~60 MB | optional: faster `explore_topic` |
| `vectors/` | ~400 MB | optional: semantic search (`semantic_search`, meaning-based part of `research_question`) |
| `raw/` | ~5 GB | only during the build: the download |
| `corpus/` | ~5 GB | only during the build: the download converted to the corpus format |

`raw/` and `corpus/` can be deleted afterwards (`--delete-raw`); keep `corpus/` if you want to rebuild without
converting again (`corpus-mcp build --corpus data/corpus`).

It can live anywhere: pass `--data DIR` or set `CORPUS_MCP_DATA` (default `./data`). The embedding model
(`BAAI/bge-m3`, ~2 GB) is downloaded on first use into the Hugging Face cache (`HF_HOME`, default `~/.cache/huggingface`).

Plan for ~25 GB free disk while building. Serving Sefaria with semantic search takes about 0.8 GB of memory of its
own (the embedding model) plus whatever of the model, vector and database files the system keeps cached; that cache
is reclaimable, so `systemctl status` showing several GB is not a problem. Without semantic search, ~0.1 GB.

### Sources and the corpus format

A build has three stages: a **source adapter** downloads its source and converts it into a **corpus directory**
(plain JSON-lines files with a general citation model, described in [docs/corpus-format.md](docs/corpus-format.md)),
and the builder turns any corpus directory into `corpus.db`. Sefaria is the only adapter so far
(`genericCorpusMcpBuilder/sources/sefaria/`); other sources (e.g. Perseus) need only a new adapter, or you can write a corpus
directory yourself and build it with `corpus-mcp build --corpus DIR`. One server serves one corpus.

How the server treats a corpus comes from the corpus itself: an optional `profile.json` (languages and how to search
them, eras and historical strata, how research groups its results, which links it follows, corpus-specific tools
such as Sefaria's `trace_halakha`, which works get embeddings) plus `instructions.md` (what the AI is told) and
`answer_prompt.md`. Without them the server uses neutral defaults: results grouped by date, earliest first. Sefaria's
are in `genericCorpusMcpBuilder/sources/sefaria/`; the keys are described in the format document.

### Perseus (Greek and Latin)

```sh
uv run corpus-mcp build --source perseus --data /srv/perseus-data            # a starting set, ~1 minute
uv run corpus-mcp build --source perseus --data /srv/perseus-data \
    --option works=tlg0012.tlg001,tlg0059.tlg030,phi0690.phi003             # chosen works (CTS ids)
uv run corpus-mcp build --source perseus --data /srv/perseus-data --option works=all   # both repositories
uv run corpus-mcp serve --data /srv/perseus-data --port 8766
```

Texts come from Perseus's GitHub repositories (canonical-greekLit, canonical-latinLit; CC BY-SA 4.0), in the
original with English translations (`--option translations=eng,ger` or `all` for others). Works are cited as
Perseus cites them: `Iliad 1.1`, `Thucydides 1.22.4`, `Aeneid 1.1`, and Plato by Stephanus section, both
`Republic 1.327a` and `Republic 327a`. Greek and Latin search ignore accents, breathings and u/v, i/j spelling.
Dates are approximate (by author) and only order the results. Problems in Perseus's own files are reported during
the build and worked around where possible (a translation whose citation pattern matches nothing is read from its
milestones; a citation repeated in one file keeps its first text).

## Install and run (with uv)

Needs [uv](https://docs.astral.sh/uv/) and Linux x86_64 (the lockfile targets that platform; elsewhere use Docker).

```sh
git clone <this repo> genericCorpusMcpBuilder && cd genericCorpusMcpBuilder
uv sync --extra semantic            # leave out --extra semantic for keyword search only (no torch)

# build the data directory: downloads ~10 GB; the embedding step takes many hours on CPU (downloads and
# embeddings resume where they stopped if interrupted)
uv run corpus-mcp build --data /srv/sefaria-data --delete-raw
uv run corpus-mcp build --data /srv/sefaria-data --no-vectors     # or: skip semantic search, much faster

uv run corpus-mcp serve --data /srv/sefaria-data                  # http://127.0.0.1:8765/mcp
```

`corpus-mcp build --skip-fetch` rebuilds from an existing `raw/` download; `--raw DIR` keeps the download elsewhere.
`python -m genericCorpusMcpBuilder ...` is the same as `corpus-mcp ...`.

### Connect a client

Local, over stdio (the client starts the server itself; nothing to keep running):

```sh
claude mcp add sefaria -e CORPUS_MCP_DATA=/srv/sefaria-data -- \
    uv run --directory /path/to/genericCorpusMcpBuilder corpus-mcp serve --transport stdio
```

Over HTTP (a running server, shared by several clients):

```sh
claude mcp add --transport http sefaria http://127.0.0.1:8765/mcp
```

Other MCP clients take the same command or URL in their config, e.g.

```json
{"mcpServers": {"torah": {"command": "uv",
  "args": ["run", "--directory", "/path/to/genericCorpusMcpBuilder", "corpus-mcp", "serve", "--transport", "stdio"],
  "env": {"CORPUS_MCP_DATA": "/srv/sefaria-data"}}}}
```

## Run in Docker

The image holds only the code; the data directory is mounted at `/data` and the model cache is a named volume.

```sh
mkdir -p data                                   # or set CORPUS_MCP_DATA_DIR=/srv/sefaria-data (in .env or the shell)
docker compose run --rm builder                 # build the data directory (hours; resumable, just re-run it)
docker compose up -d                            # serve http://127.0.0.1:8765/mcp
```

The container runs as uid 1000; the data directory must be writable by it for the build
(`sudo chown 1000 data`), and only readable for serving (it is mounted read-only). The builder needs ~25 GB free
in that directory while it runs.

Without compose:

```sh
docker build -t corpus-mcp .                               # --build-arg EXTRAS= for an image without semantic search
docker run -d -p 127.0.0.1:8765:8765 -v /srv/sefaria-data:/data:ro -v torah-models:/cache corpus-mcp
docker run -i --rm -v /srv/sefaria-data:/data:ro -v torah-models:/cache corpus-mcp serve --transport stdio
```

The last form is a stdio server for a client config (`"command": "docker", "args": ["run", "-i", "--rm", ...]`).

## Configuration

Command-line flags win over environment variables.

| variable | flag | default | |
|---|---|---|---|
| `CORPUS_MCP_DATA` | `--data` | `./data` (`/data` in Docker) | data directory |
| `CORPUS_MCP_TRANSPORT` | `--transport` | `http` | `http` (streamable HTTP at `/mcp`) or `stdio` |
| `CORPUS_MCP_HOST` | `--host` | `127.0.0.1` (`0.0.0.0` in Docker) | HTTP bind address |
| `CORPUS_MCP_PORT` | `--port` | `8765` | HTTP port |
| `CORPUS_MCP_ALLOWED_HOSTS` | | `127.0.0.1:*,localhost:*` | accepted `Host` headers (DNS-rebinding protection); add your server's name when clients reach it by name |
| `CORPUS_MCP_ALLOWED_ORIGINS` | | none | accepted `Origin` headers, for browser-based clients (e.g. `https://mcp.example.org`) |
| `HF_HOME` | | `~/.cache/huggingface` (`/cache/hf` in Docker) | where the embedding model is cached; set `HF_HUB_OFFLINE=1` once it is there |

The server has no authentication. Keep it on localhost, or put it behind a reverse proxy that restricts access
(see [Exposing it over HTTPS](#exposing-it-over-https)).

## Exposing it over HTTPS

The server itself speaks plain HTTP and has no authentication. To reach it from other machines, put a reverse proxy in
front: the proxy holds the certificate, serves HTTPS and forwards `/mcp` to the server (this is called TLS
termination). Pick one:

| situation | use |
|---|---|
| only this machine uses it | nothing: stdio, or `http://127.0.0.1:8765/mcp` |
| Docker, any network | the bundled Caddy (below): certificates are created and renewed automatically |
| your devices on a Tailscale network | `tailscale serve` (below): real certificates, no domain, no open ports |
| a server that already runs nginx with a certificate | `deploy/install.sh` (next section) |

### Caddy (Docker)

```sh
# .env next to compose.yaml
CORPUS_MCP_DOMAIN=myserver.local          # the name clients use
CORPUS_MCP_CERT_ISSUER=internal           # internal | acme

docker compose --profile https up -d     # https://myserver.local/mcp
```

- **`internal`** (default): Caddy creates its own small CA and signs the certificate. Works for LAN names,
  `localhost` and IP addresses with no internet access. Clients must trust Caddy's root certificate once:

  ```sh
  docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt
  NODE_EXTRA_CA_CERTS=$PWD/caddy-root.crt claude mcp add --transport http sefaria https://myserver.local/mcp
  ```

  (Or install `caddy-root.crt` into the OS trust store of each client.) The CA lives in the `caddy-data` volume;
  keep it, or clients will have to trust a new one.
- **`acme`**: a free Let's Encrypt certificate that every client already trusts. Needs a real DNS name
  (`mcp.example.org`) pointing at this machine and port 80 or 443 reachable from the internet.

By default only private networks (LAN, localhost) get in; anyone else gets 403. To change it, set
`CORPUS_MCP_ALLOW_FROM` to space-separated CIDRs, e.g. `CORPUS_MCP_ALLOW_FROM="192.168.1.0/24 10.8.0.0/24"`.
Opening it to the whole internet (`0.0.0.0/0 ::/0`) makes your server free for anyone to use; don't, unless you
add authentication in Caddy. The check relies on Caddy seeing real client addresses, which it does on Linux
Docker; Docker Desktop (Mac/Windows) shows every client as an internal address, so there it lets everyone through.

Caddy needs ports 80 and 443; if another web server already uses them, add the `/mcp` route to that one instead.
To use the same Caddyfile with a Caddy installed on the host (no Docker), point it at the local server:
`CORPUS_MCP_UPSTREAM=127.0.0.1:8765 caddy run --config deploy/Caddyfile`. To check the file after editing it, run
`caddy validate --config deploy/Caddyfile`, or `docker compose --profile https up caddy` and read the log.

### Tailscale

On a machine in your tailnet, with the server running on port 8765:

```sh
tailscale serve --bg 8765                 # https://<machine>.<tailnet>.ts.net → 127.0.0.1:8765
```

If clients get `Invalid Host header`, allow that name in the server:
`CORPUS_MCP_ALLOWED_HOSTS=127.0.0.1:*,localhost:*,<machine>.<tailnet>.ts.net`.
Only devices in your tailnet can reach it.

## Run as a systemd service behind nginx

`deploy/install.sh` installs the app and a built data directory under one root, runs it as the `corpusmcp` user via
systemd on `127.0.0.1:$CORPUS_MCP_PORT`, and, if nginx is installed, adds `location /mcp` (restricted to the LAN) to
your HTTPS server block.

```sh
sudo CORPUS_MCP_HOME=/opt/corpus-mcp CORPUS_MCP_PUBLIC_HOST=myserver.local sh deploy/install.sh
```

Its settings (install root, data source, port, public host name, nginx on/off, which certificate's server block
to use, LAN range) are listed at the top of the script. Clients then connect to `https://<public host>/mcp`; if the
certificate comes from a private CA, the client must trust it, e.g.
`NODE_EXTRA_CA_CERTS=/path/to/ca.crt claude mcp add --transport http sefaria https://myserver.local/mcp`.

To build on a stronger machine and install on a smaller one (e.g. a NAS), build the data directory on the first,
copy the checkout, the data directory and the model cache across (`sh deploy/stage.sh <destination> [data dir]` does this), and run
`install.sh` there.

## Refresh

Sefaria re-exports monthly. Run `corpus-mcp build` into a **new, empty** data directory, then point the server at it
(or redeploy). Re-using an old directory would skip files already downloaded and resume the old embeddings, so the
result would mix two exports.

## Development

```sh
uv sync --extra semantic
uv run pytest                                   # integration tests use ./data/corpus.db or $CORPUS_MCP_TEST_DB
```

See `playground/README.md` for a terminal chat with a local model that calls these tools.
