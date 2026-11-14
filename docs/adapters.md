# Writing an adapter

An adapter turns a source into a corpus directory ([corpus-format.md](corpus-format.md)); `build` does the rest
(database, search indexes, embeddings, server). Before writing one, check whether a built-in adapter already fits:

| source | your files are | use |
|---|---|---|
| spreadsheets, exports, anything tabular | CSV/TSV, one row per verse/line/section | `--source table --raw DIR` |
| TEI XML editions that declare their citations (refsDecl/cRefPattern) | a folder of `.xml` files | `--source tei --raw DIR` |
| Perseus (Greek and Latin) | nothing: downloaded | `--source perseus` |
| Sefaria | nothing: downloaded | `--source sefaria` |
| anything else | | your own adapter (below) |

```sh
corpus-mcp build --source table --raw my-texts/ --data data-mine --option name="My texts" --option original_language=la
```

`table` columns: `work`, `cite` (`1.2.3`), `text`, optional `lang`, `version`, `notes`, and for a work (first row):
`language`, `author`, `date`, `levels` (`book.chapter.section`). `tei`: a subfolder per work holding its versions
(original and translations), or one file per work.

## Your own adapter in five minutes

```sh
corpus-mcp new-adapter my_texts                          # writes my_texts/ with a working example
corpus-mcp build --source ./my_texts --data data-my_texts --no-vectors
corpus-mcp serve --data data-my_texts
```

The template runs as it is. An adapter is a Python file or folder with up to two functions:

```python
from genericCorpusMcpBuilder.sources.kit import SimpleCorpus

def fetch(raw, **options):           # optional: download into raw/ (leave out for local files, given with --raw)
    ...

def convert(raw, out, **options):    # required: read raw/, write the corpus directory out/
    corpus = SimpleCorpus(out, id="augustine", name="Augustine", original_language="lat",
                          source="...", license="...")
    work = corpus.work("Confessions", author="Augustine", levels=["book", "chapter", "section"], date=398,
                       aliases=["Conf."])
    corpus.text(work, "lat", "Skutella 1934", [(["1", "1", "1"], "Magnus es, domine..."), ...])
    corpus.text(work, "en", "Pusey 1838", [(["1", "1", "1"], "Great art thou, O Lord..."), ...])
    corpus.finish(profile_dir=Path(__file__).parent)  # profile.json, instructions.md, answer_prompt.md if present
```

`--option KEY=VALUE` values arrive as keyword arguments of both functions. `genericCorpusMcpBuilder.sources.http.download(url,
path)` downloads resumably; `genericCorpusMcpBuilder.sources.tei.read_tei(path)` reads a TEI edition into segments.

### What SimpleCorpus does for you

- **Level kinds** are worked out from the labels you write: all digits is a number level (`2` sorts before `10`),
  digits plus one letter a folio level (`73a`, `327b`; a bare `73` means `73a`), anything else a free label
  (`pr.`, `121A`). Override with `kinds={"section": "label"}`.
- **Languages** are collected from your texts and normalized (`eng` → `en`, `ger` → `de`, `latin` → `lat`).
  Each work has its own original `language`; versions in another language are its translations.
- **Names**: a title that is already taken gets the author (`Xenophon, Apology`).
- **Reading order** is the order you give the segments in. Translations may be divided differently from the
  original: positions are the union of all versions' labels (an English passage labelled 1.33 sits at line 1.33).
- **Second citation systems** (`corpus.citation(work, scheme, labels, ref)`), and for folio-like inner levels
  `corpus.page_citations(work, "Stephanus")` adds `Republic 327a` and `Republic 327` for `Republic 1.327a`.
- **Links** between passages (`corpus.link(from_ref, to_ref, "commentary")`) and **topics** (`corpus.topic(...)`)
  are optional; the server offers the commentary, links and topic tools only when there are some.

## What real sources taught us

Lessons from Sefaria and Perseus that are likely to hold for your source too:

1. **Cite what readers cite.** Use the labels people type (`73a`, `327a`, `1.22.4`), not internal numbers, and add
   the abbreviations they use as aliases (`Thuc.`, `Il.`, `Gen.`); sources rarely contain them.
2. **Declared structure and actual structure differ.** A file may say "book.section" and contain something else.
   Read what is there, report what does not fit (print a line; it shows in the build log), and keep going.
3. **The finest citable unit may be a marker inside the text** (Stephanus sections, Bekker lines, page breaks).
   Split segments there (`genericCorpusMcpBuilder.sources.tei.segments_of` does it for TEI milestones).
4. **Translations are cut differently from originals.** Do not force them onto the original's divisions; give
   them their own labels in the same system and let the union align them.
5. **Metadata is thin.** Dates, categories and language codes are often missing or inconsistent. Approximate dates
   are fine for ordering (say so in `date_text`); categories can be the author; normalize language codes.
6. **Labels are messy** (`121A`, `5_6`, `pr`). Keep them as they are; SimpleCorpus makes such a level a label level
   so it still resolves. Repeated citations within one version are a data error: the first text is kept and the
   build reports the rest.
7. **Check before you build.** `corpus-mcp check data-x/corpus` lists errors (which stop the build) and warnings,
   with examples. `build` runs it after converting.

Then give the corpus a `profile.json` (languages, periods or strata, research grouping, tool descriptions) and an
`instructions.md` that tells the AI what the texts are and how to answer from them; see the Profile section of
[corpus-format.md](corpus-format.md) and the examples in `genericCorpusMcpBuilder/sources/sefaria/` and `genericCorpusMcpBuilder/sources/perseus/`.
