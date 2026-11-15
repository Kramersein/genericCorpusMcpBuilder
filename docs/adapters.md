# Writing an adapter

An adapter turns a source into a corpus directory ([corpus-format.md](corpus-format.md)); `build` does the rest
(database, search indexes, embeddings, server). Before writing one, check whether a built-in adapter already fits:

| source | your files are | use |
|---|---|---|
| a selection of Gutenberg books, Wikisource editions and your own files | a CSV/TSV list, one row per text | `--source collection --option list=FILE` |
| spreadsheets, exports, anything tabular | CSV/TSV, one row per verse/line/section | `--source table --raw DIR` |
| TEI XML editions that declare their citations (refsDecl/cRefPattern) | a folder of `.xml` files | `--source tei --raw DIR` |
| Perseus (Greek and Latin) | nothing: downloaded | `--source perseus` |
| Sefaria | nothing: downloaded | `--source sefaria` |
| Project Gutenberg books | nothing: downloaded (ebook numbers) | `--source gutenberg --option books=1342` |
| Wikisource editions, any language | nothing: downloaded (page titles) | `--source wikisource --option works=...` |
| anything else | | your own adapter (below) |

```sh
corpus-mcp build --source table --raw my-texts/ --data data-mine --option name="My texts" --option original_language=la
```

`table` columns: `work`, `cite` (`1.2.3`), `text`, optional `lang`, `version`, `notes`, and for a work (first row):
`language`, `author`, `date`, `levels` (`book.chapter.section`). `tei`: a subfolder per work holding its versions
(original and translations), or one file per work.

## The words used here, with one example

Take the Gospel of John in Greek, with a Latin and an English translation.

| word | means | in the example |
|---|---|---|
| **work** | one text, whatever its versions | the Gospel of John |
| **version** | one edition or translation of a work, in one language | the Greek SBLGNT, the Latin Vulgate, the Douay-Rheims |
| **original** | the version in the language the work was written in (`language=` of the work); all others are translations | the Greek |
| **segment** | the smallest citable piece of a version, with its text | John 1:14 in the Vulgate: "Et Verbum caro factum est..." |
| **levels** | the names of the parts of a citation, outermost first | chapter, verse |
| **labels** (`cite`) | a segment's position, one label per level | `["1", "14"]`, written `John 1:14` |
| **separator** | what joins the labels in a citation | `:` (Perseus and most classics use `.`) |
| **language code** | short code for a language; common ones are understood (`en`, `la`/`lat`, `grc`, `he`, `zh`, `lzh`, `ja`, `ru`, `ar`, ...) | `grc`, `lat`, `en` |
| **corpus** | all the works one server serves | New Testament and church fathers |

Every version of a work uses the same levels and labels, so `John 1:14` finds the Greek, the Latin and the English
at once. A translation may be divided differently (fewer, longer segments); it is still placed by its labels.

## Your own adapter in five minutes

```sh
corpus-mcp new-adapter my_texts                          # writes my_texts/ with a working example
corpus-mcp build --source ./my_texts --data data-my_texts --no-vectors
corpus-mcp serve --data data-my_texts
```

The template runs as it is. An adapter is a Python file or folder with up to two functions:

```python
from genericCorpusMcpBuilder import SimpleCorpus

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

`--option KEY=VALUE` values arrive as keyword arguments of both functions. Helpers, all importable from
`genericCorpusMcpBuilder`: `download(url, path)` (resumable), `pause(seconds)`, `cached_json(path, ask)` (API answers
saved once), `html_blocks(html)` and `chapters_from_blocks(blocks)` (a web page as headings and paragraphs, then
chapters), `section_labels(headings)` ("Chapter IV" -> "4"), `html_to_text(html)`, `read_tei(path)` (a TEI edition
into segments), `read_usfx(path)` (a USFX Bible), `language_code(code)`.

`corpus.text()` takes segments as `(cite, text)` tuples, dicts or a pandas DataFrame (`cite=`, `text=`, `notes=` name
the columns); a cite can be a list of labels, a string like `"1.14"` or a number.

Look at what you built without setting up an AI client:

```sh
corpus-mcp show "Genesis 1:1" --data data-my_texts          # the passage in every language, with its versions
corpus-mcp search "beginning" --data data-my_texts
corpus-mcp check data-my_texts/corpus                        # problems in what the adapter wrote
```

### Recipes

**A spreadsheet or DataFrame** (one row per segment):

```python
import pandas as pd
from genericCorpusMcpBuilder import SimpleCorpus

def convert(raw, out, **options):
    corpus = SimpleCorpus(out, id="sayings", name="Sayings", original_language="la")
    df = pd.read_csv(raw / "sayings.csv")                       # columns: book, number, latin, english
    df["ref"] = df["book"].astype(str) + "." + df["number"].astype(str)
    work = corpus.work("Sayings", levels=["book", "number"], language="la")
    corpus.text(work, "la", "Latin text", df, cite="ref", text="latin")
    corpus.text(work, "en", "My translation", df, cite="ref", text="english")
    corpus.finish()
```

**A folder of plain-text files**, one per chapter, one verse per line:

```python
for path in sorted(raw.glob("*.txt")):                          # 01.txt, 02.txt, ...
    chapter = str(int(path.stem))
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    corpus.text(work, "en", "My edition", [([chapter, str(n)], line) for n, line in enumerate(lines, 1)])
```

(Call `text()` once per version with all its segments: collect them in a list first if they come from several files.)

**A JSON file**:

```python
import json
data = json.loads((raw / "book.json").read_text(encoding="utf-8"))    # {"chapters": [{"verses": ["...", ...]}, ...]}
segments = [([str(c), str(v)], text) for c, chapter in enumerate(data["chapters"], 1)
            for v, text in enumerate(chapter["verses"], 1)]
corpus.text(work, "en", "JSON edition", segments)
```

**Web pages**: download them in `fetch()` (politely: one at a time, with a pause) and read them in `convert()`:

```python
from genericCorpusMcpBuilder import SimpleCorpus, chapters_from_blocks, download, html_blocks, pause, section_labels

def fetch(raw, **options):
    if download("https://example.org/my-book.html", raw / "book.html"):    # True only if it was downloaded now
        pause(2)

def convert(raw, out, **options):
    corpus = SimpleCorpus(out, id="book", name="My book", original_language="en")
    blocks = html_blocks((raw / "book.html").read_text(encoding="utf-8"),
                         skip_ids=["site-footer"], skip_classes=["pagenum", "navigation"])  # what is not the text
    # (skip_attributes={"epub:type": ["noteref"]} also drops elements by any other attribute, as in EPUB files)
    chapters = chapters_from_blocks(blocks)          # [(heading, [paragraphs]), ...] at the main heading level
    labels = section_labels([heading for heading, paragraphs in chapters])    # "Chapter IV" -> "4"
    work = corpus.work("My Book", levels=["chapter", "paragraph"], language="en")
    segments = []
    for label, (heading, paragraphs) in zip(labels, chapters):
        for number, paragraph in enumerate(paragraphs, 1):
            segments.append(([label, str(number)], paragraph))
    corpus.text(work, "en", "Web edition", segments)
    corpus.finish()
```

`html_to_text(html)` gives the plain text of a page when there is no structure to keep.

**An API** (a service you ask for one passage at a time): save every answer so nothing is asked twice and an
interrupted download continues where it stopped, and pause between requests:

```python
from genericCorpusMcpBuilder import cached_json, pause

def ask(urn):
    answer = some_api.get_text(urn)        # whatever the source's API or Python module offers
    pause(3)                               # only after a real request: cached answers return at once
    return answer

def fetch(raw, **options):
    for urn in ["book/1", "book/2"]:
        cached_json(raw / "answers" / (urn.replace("/", "_") + ".json"), lambda: ask(urn))
```

Read the source's terms first (rate limits, keys, whether automatic downloading is allowed at all), stop at its
"too many requests" error instead of retrying, and send a User-Agent with contact details where it asks for one
(`download(url, path, headers={"User-Agent": "..."})`). To test without the network, give your adapter a stand-in for the
API module that returns saved answers: in a pytest test, `monkeypatch.setitem(sys.modules, "some_api", fake)` before
importing the adapter, where `fake = types.ModuleType("some_api")` has the functions your adapter calls.

Check a site's terms before downloading it, and keep the license in `SimpleCorpus(license=...)` and, where it
differs per version, `corpus.text(..., license="...")`.

**Chinese, Japanese and other scripts**: nothing special to do; give the right language code (`zh`, `lzh` for
classical Chinese, `ja`, `ko`, `ru`, `ar`, `sa`, ...). Chinese characters and Japanese kana count as one unit each
for search, quotes and word citations (`論語 1.1@3-7`), since those languages do not separate words with spaces.

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
