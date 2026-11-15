"""Command line: `corpus-mcp serve` runs the MCP server, `corpus-mcp build` builds a data directory from a source."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_DATA = "data"


def data_dir(value: str | None) -> Path:
    return Path(value or os.environ.get("CORPUS_MCP_DATA") or DEFAULT_DATA).expanduser().resolve()


def serve(args: argparse.Namespace) -> None:
    directory = data_dir(args.data)
    if not (directory / "corpus.db").exists():
        sys.exit(f"no corpus.db in {directory}; build it with `corpus-mcp build --data {directory}` "
                 "or point --data / CORPUS_MCP_DATA at an existing data directory")
    os.environ["CORPUS_MCP_DATA"] = str(directory)
    for name, value in (("CORPUS_MCP_HOST", args.host), ("CORPUS_MCP_PORT", args.port),
                        ("CORPUS_MCP_TRANSPORT", args.transport)):
        if value is not None:
            os.environ[name] = str(value)
    from genericCorpusMcpBuilder import server  # the corpus opens on import, so the environment must be set first
    server.main()


def step(description: str, *module_args: str) -> None:
    print(f"\n== {description}", flush=True)
    subprocess.run([sys.executable, "-m", *module_args], check=True)


def build(args: argparse.Namespace) -> None:
    directory = data_dir(args.data)
    directory.mkdir(parents=True, exist_ok=True)
    raw = Path(args.raw).expanduser().resolve() if args.raw else directory / "raw"
    converted = Path(args.corpus).expanduser().resolve() if args.corpus else directory / "corpus"
    db, vectors = str(directory / "corpus.db"), str(directory / "vectors")
    print(f"data directory: {directory}", flush=True)
    downloaded = False
    if not args.corpus:
        from genericCorpusMcpBuilder.sources import adapter
        source = adapter(args.source)
        options = dict(option.split("=", 1) for option in args.option)
        downloaded = hasattr(source, "fetch")          # adapters for local files (tei, table) only convert
        if downloaded and not args.skip_fetch:
            print(f"\n== downloading {args.source} into {raw} (resumable)", flush=True)
            source.fetch(raw, **options)
        print(f"\n== converting {raw} into the corpus format at {converted}", flush=True)
        if converted.exists():
            import shutil
            shutil.rmtree(converted)
        source.convert(raw, converted, **options)
    problems = check_corpus(converted)
    if problems:
        raise SystemExit(f"{converted} has {problems} problem(s) (listed above); fix the source or the adapter")
    step("building corpus.db", "genericCorpusMcpBuilder.build_corpus", str(converted), db)
    if not args.no_vectors:
        step("embedding passages (many hours on CPU, resumable)", "genericCorpusMcpBuilder.vectors", db, vectors)
        step("embedding topics", "genericCorpusMcpBuilder.vectors", "--topics", db, vectors)
        step("locating passages in this build", "genericCorpusMcpBuilder.vectors", "--locate", db, vectors)
        if not (Path(vectors) / "onnx" / "model.onnx").exists():
            step("exporting the embedding model to ONNX (serving then needs no torch)",
                 "genericCorpusMcpBuilder.vectors", "--export-onnx", vectors)
    step("precomputing topic dossiers", "genericCorpusMcpBuilder.precompute", db, str(directory / "precomputed.db"))
    if args.delete_raw and not args.corpus:
        import shutil
        if downloaded:                                 # never delete input the user supplied (tei, table)
            shutil.rmtree(raw, ignore_errors=True)
        shutil.rmtree(converted, ignore_errors=True)
    print(f"\ndone: serve it with `corpus-mcp serve --data {directory}`", flush=True)


def check_corpus(corpus: Path) -> int:
    from genericCorpusMcpBuilder.check import check
    print(f"\n== checking {corpus}", flush=True)
    return check(corpus)


def check_command(args: argparse.Namespace) -> None:
    raise SystemExit(1 if check_corpus(Path(args.corpus).expanduser().resolve()) else 0)


def set_profile(args: argparse.Namespace) -> None:
    from genericCorpusMcpBuilder.build_corpus import update_profile
    from genericCorpusMcpBuilder.sources import ADAPTERS
    directory = data_dir(args.data)
    source = Path(args.source).expanduser()
    if args.source in ADAPTERS:                       # an adapter's own profile files, e.g. sefaria
        import importlib.util
        source = Path(importlib.util.find_spec(ADAPTERS[args.source]).origin).parent
    if not source.is_dir():
        raise SystemExit(f"{args.source}: give an adapter name ({', '.join(ADAPTERS)}) or a folder with profile.json, "
                         "instructions.md and/or answer_prompt.md")
    update_profile(directory / "corpus.db", source)
    print(f"done. Restart the server to use it (stop it before running this on a live corpus: it opens the database "
          f"read-only and does not expect changes). If the research settings (strata, grouping) changed, also run "
          f"python -m genericCorpusMcpBuilder.precompute {directory}/corpus.db {directory}/precomputed.db", flush=True)


def open_corpus(data: str | None):
    from genericCorpusMcpBuilder.corpus import Corpus
    path = data_dir(data) / "corpus.db"
    if not path.exists():
        raise SystemExit(f"no corpus.db in {path.parent}; build one first (corpus-mcp build ...)")
    return Corpus(str(path))


def show(args: argparse.Namespace) -> None:
    """A passage as the server would return it, in plain text: to try citations without an MCP client."""
    from genericCorpusMcpBuilder.corpus import RefNotFound
    corpus = open_corpus(args.data)
    try:
        result = corpus.get_text(args.ref, (args.language,) if args.language else None, None, args.context)
    except RefNotFound as error:
        raise SystemExit(str(error))
    work = result["work"]
    print(f"{result['ref']}  ({work['title']}, written in {work.get('language')})"
          + (f"  also cited as: {result['cited_as']}" if result.get("cited_as") else ""))
    if not result["found"]:
        print("  (the citation is valid, but there is no text there)")
    for segment in result["segments"]:
        print(f"\n{segment['ref']}")
        for lang in corpus.profile.languages:
            if lang in segment:
                version = segment.get(f"{lang}_version")
                print(f"  [{lang}{' ' + version if version else ''}] {segment[lang]}")
                if segment.get(f"{lang}_notes"):
                    print(f"      notes: {segment[f'{lang}_notes']}")
    others = result.get("also_cited_as") or []
    if others:
        print(f"\nother citations: {'; '.join(others)}")


def search_command(args: argparse.Namespace) -> None:
    corpus = open_corpus(args.data)
    try:
        hits = corpus.search(args.query, lang=args.language, limit=args.limit)
    except ValueError as error:
        raise SystemExit(str(error))
    for hit in hits:
        print(f"{hit['ref']}  [{hit['lang']}]  {hit['text'][:160]}")
    print(f"({len(hits)} found)")


def new_adapter(args: argparse.Namespace) -> None:
    from genericCorpusMcpBuilder.scaffold import write_adapter
    write_adapter(args.name, Path(args.dir).expanduser().resolve())


def load_settings_file(path: Path = Path(".env")) -> None:
    """KEY=VALUE lines from .env (kept out of git) for settings like CORPUS_MCP_CONTACT; the environment wins."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.removeprefix("export ").strip(), value.strip().strip("'\""))


def main() -> None:
    load_settings_file()
    parser = argparse.ArgumentParser(prog="corpus-mcp", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve_parser = commands.add_parser("serve", help="run the MCP server")
    serve_parser.add_argument("--data", help=f"data directory (default: $CORPUS_MCP_DATA or ./{DEFAULT_DATA})")
    serve_parser.add_argument("--transport", choices=["stdio", "http"],
                              help="stdio for a local client, http for a network service (default: http)")
    serve_parser.add_argument("--host", help="HTTP bind address (default: $CORPUS_MCP_HOST or 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, help="HTTP port (default: $CORPUS_MCP_PORT or 8765)")
    serve_parser.set_defaults(handler=serve)

    build_parser = commands.add_parser("build", help="download a source, convert it and build the data directory")
    build_parser.add_argument("--data", help=f"data directory to create (default: $CORPUS_MCP_DATA or ./{DEFAULT_DATA})")
    build_parser.add_argument("--source", default="sefaria",
                              help="source adapter: sefaria, perseus, tei, table, or the path of your own adapter "
                                   "(default: sefaria)")
    build_parser.add_argument("--option", action="append", default=[], metavar="KEY=VALUE",
                              help="adapter option, repeatable (perseus: works=..., repositories=..., translations=...)")
    build_parser.add_argument("--corpus", help="build from an existing corpus directory (docs/corpus-format.md) "
                                               "instead of downloading and converting a source")
    build_parser.add_argument("--raw", help="the source's files: where downloads are kept, or your own files for the "
                                            "tei and table sources (default: <data>/raw)")
    build_parser.add_argument("--skip-fetch", action="store_true", help="reuse an already downloaded source")
    build_parser.add_argument("--no-vectors", action="store_true",
                              help="skip semantic search (saves many hours; keyword search still works)")
    build_parser.add_argument("--delete-raw", action="store_true", help="delete the downloaded and converted source files when done")
    build_parser.set_defaults(handler=build)

    check_parser = commands.add_parser("check", help="check a corpus directory for problems before building it")
    check_parser.add_argument("corpus", help="the corpus directory (docs/corpus-format.md)")
    check_parser.set_defaults(handler=check_command)

    profile_parser = commands.add_parser("set-profile", help="replace a built corpus's profile, instructions and "
                                                             "answer prompt without rebuilding")
    profile_parser.add_argument("source", help="an adapter name (sefaria, perseus) or a folder with profile.json, "
                                               "instructions.md, answer_prompt.md")
    profile_parser.add_argument("--data", help=f"the built data directory (default: $CORPUS_MCP_DATA or ./{DEFAULT_DATA})")
    profile_parser.set_defaults(handler=set_profile)

    show_parser = commands.add_parser("show", help="print a passage of a built corpus (try citations without a client)")
    show_parser.add_argument("ref", help='a citation, e.g. "John 1:1-3" or "Iliad 1.1"')
    show_parser.add_argument("--data", help=f"the built data directory (default: $CORPUS_MCP_DATA or ./{DEFAULT_DATA})")
    show_parser.add_argument("--context", type=int, default=0, help="segments before and after")
    show_parser.add_argument("--language", help="only this language")
    show_parser.set_defaults(handler=show)

    search_parser = commands.add_parser("search", help="keyword search in a built corpus")
    search_parser.add_argument("query")
    search_parser.add_argument("--data", help=f"the built data directory (default: $CORPUS_MCP_DATA or ./{DEFAULT_DATA})")
    search_parser.add_argument("--language", help="search this language (default: by the query's script)")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.set_defaults(handler=search_command)

    adapter_parser = commands.add_parser("new-adapter", help="write a working adapter template to start from")
    adapter_parser.add_argument("name", help="the adapter's name, e.g. my_texts")
    adapter_parser.add_argument("--dir", default=".", help="where to create it (default: here)")
    adapter_parser.set_defaults(handler=new_adapter)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
