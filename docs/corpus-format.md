# Corpus format

genericCorpusMcpBuilder builds its database from a **corpus directory** in the format below. Each source (Sefaria, Perseus, your
own texts) has an adapter that downloads or reads the source and writes this format; `corpus-mcp build` then turns any
corpus directory into `corpus.db`. One corpus per server: run one server (container) per corpus.

```
<corpus>/
  corpus.json          manifest
  works.jsonl          one work per line
  texts/*.jsonl        one file per version (edition or translation) of a work
  citations.jsonl      optional: other citation systems (Stephanus pages, Torah portions, ...)
  links.jsonl          optional: cross-references between passages
  topics.jsonl         optional: curated topics with their sources
  profile.json         optional: how the server searches, groups and presents this corpus (see Profile)
  instructions.md      optional: what the server tells the AI about the corpus and how to answer from it
  answer_prompt.md     optional: the answer_question prompt; {question} is replaced by the question
```

All files are UTF-8. `.jsonl` files hold one JSON object per line. Fields marked *optional* may be left out.

## Citation model

A work has one or more **parts**; a part has a name and a list of **levels** (book, chapter, line ...). Every
citable position in a part (a **segment**) has one **label per level**, stored as text: `["1", "22", "4"]`,
`["73a", "4"]`, `["pr."]`. Citations are the part name followed by the labels joined with the part's separator:

| part name | separator | labels | citation |
|---|---|---|---|
| `Genesis` | `:` | `["1", "1"]` | `Genesis 1:1` |
| `Shabbat` | `:` | `["73a", "4"]` | `Shabbat 73a:4` |
| `Iliad` | `.` | `["1", "1"]` | `Iliad 1.1` |
| `Shulchan Arukh, Orach Chayim` | `:` | `["308", "1"]` | `Shulchan Arukh, Orach Chayim 308:1` |

Labels are matched, never computed: a citation is resolved by finding the segments whose labels start with the
labels given (case-insensitive). `Genesis 1` is every segment under `["1"]`; `Genesis 1:3-2:5` runs from the first
segment under `["1", "3"]` to the last under `["2", "5"]`; `Genesis 1:3-5` takes the missing leading labels of the
end from the start. Reading order follows the level kinds (see `works.jsonl`): `number` and `folio` levels sort
numerically (`2` before `10`, `73a` before `73b` before `74a`); `label` levels keep the order in which their labels
first appear in the text files.

Other citation systems (Stephanus, Bekker, Vilna pages, Torah portions) are listed in `citations.jsonl`: each entry
is a label mapped to a range of the main system.

Quotes inside a segment are cited by word number, `Genesis 1:1@2-3`, counting the whitespace-separated words of
the segment's text in the work's original language.

## corpus.json

```json
{
  "id": "sefaria",
  "name": "Sefaria",
  "source": "Sefaria export (gs://sefaria-export)",
  "source_exported_at": "2026-11-01",
  "source_version": "books.json generated 2026-11-01T03:12:00",
  "license": "per version; see each version's license",
  "languages": ["he", "en"],
  "original_language": "he"
}
```

| field | |
|---|---|
| `id`, `name` | identify the corpus |
| `source`, `source_exported_at`, `source_version` | provenance, recorded in the build manifest and part of the build id |
| `license` | *optional*, overall license note |
| `languages` | language codes used by versions |
| `original_language` | the language works are written in unless `works.jsonl` says otherwise |

## works.jsonl

```json
{"title": "Shabbat", "titles": {"he": "שבת"}, "aliases": ["Masechet Shabbat", "Tractate Shabbat"],
 "categories": ["Talmud", "Bavli", "Seder Moed"], "authors": [], "date": [450, 550], "date_text": "c. 450-550 CE",
 "era": "A", "comments_on": [], "dependence": null, "collective_title": null, "description": "...",
 "parts": [{"name": "Shabbat", "titles": {"he": "שבת"},
            "levels": [{"name": "Daf", "kind": "folio"}, {"name": "Line"}], "separator": ":"}]}
```

| field | |
|---|---|
| `title` | unique name of the work; versions and citations refer to it |
| `language` | *optional*, the language the work was written in (default: corpus.json `original_language`). Versions in this language are the original, all others translations; word citations (`@n-m`) count the original's words. A corpus can mix them: a Greek gospel with a Latin and an English translation next to a Latin original with an English one |
| `titles` | *optional*, the title in other languages, by language code |
| `aliases` | *optional*, other names a citation may use (abbreviations, transliterations) |
| `categories` | *optional*, broadest first; the first is the work's main category |
| `authors` | *optional*, names |
| `date`, `date_text` | *optional*, composition `[earliest, latest]` year (negative = BCE) and a readable form |
| `era` | *optional*, corpus-defined period code |
| `comments_on` | *optional*, titles of the works this one comments on |
| `dependence` | *optional*, how it depends on them (`Commentary`, `Targum`, ...); null for base texts |
| `collective_title`, `description` | *optional* |
| `parts` | the work's citable parts, in reading order. Most works have one part named like the work |

A part has `name` (the citation prefix; unique across the corpus), *optional* `titles`, `levels`,
`separator` (default `:`) and `joiner` (what goes between the name and the address, default a space; SuttaCentral's
`MN 1:3.4` uses `":"`. Either is accepted in input). A level has `name` and *optional* `kind`:

| kind | labels | input accepted |
|---|---|---|
| `number` (default) | digits: `1`, `2`, ... | digits only, so `7a` is left to other citation systems; also Hebrew numerals (`כג`) |
| `folio` | a number and one side/column letter: `73a`, `12c` | a bare number means side `a`; `73a-b` means `73a-73b`; Hebrew `עג.` / `ע"ב` |
| `label` | free text (`pr.`, `Prologue`, `a`) | case-insensitive; may contain the separator (`Odes pr.2`) |

## texts/*.jsonl

One file per version. The first line describes the version, every following line is a segment in reading order:

```json
{"version": {"work": "Shabbat", "lang": "en", "title": "William Davidson Edition - English",
             "source": "https://www.sefaria.org", "rank": 0, "merged_from": []}}
{"part": "Shabbat", "cite": ["2a", "1"], "text": "MISHNA: The acts of carrying out ...", "notes": ""}
{"part": "Shabbat", "cite": ["2a", "2"], "text": "..."}
```

| version field | |
|---|---|
| `work`, `lang`, `title` | which work, which language, the version's name |
| `source` | *optional*, URL or bibliographic source |
| `rank` | *optional*, preference among versions of the same work and language (0 = preferred). Unranked versions follow, the one with most segments first |
| `merged_from` | *optional*, the sources a merged edition was made from |
| `license` | *optional* |

| segment field | |
|---|---|
| `part` | the part's name; a part not declared in `works.jsonl` is created with numbered levels |
| `cite` | the labels; unique within the version (later repeats are dropped, with a warning) |
| `text` | the text to show and search, plain (`**bold**` allowed) |
| `notes` | *optional*, footnotes or apparatus kept apart from the text |

A work's citable positions are the union of the segments of all its versions (a translation may have positions the
original lacks). Files are read in file-name order; that order also breaks ties between equally ranked versions and
decides the first-seen order of `label` levels, so number the files (`000001 ...jsonl`) when it matters.

## citations.jsonl

```json
{"work": "Genesis", "scheme": "Parasha", "labels": ["Noach", "Parashat Noach"], "titles": {"he": "נח"},
 "ref": "Genesis 6:9-11:32", "global_labels": ["parashat noach", "noach"]}
```

| field | |
|---|---|
| `work`, `scheme` | the work and the name of the citation system |
| `labels` | names this entry is cited by within the work, the preferred one first (`Republic 327a` is `327a`) |
| `titles` | *optional*, the label in other languages |
| `ref` | the range it covers, as a citation in the main system |
| `global_labels` | *optional*, names that identify the entry without the work's title (`Parashat Noach`) |

## links.jsonl

```json
{"from": "Shabbat 73a:4", "to": "Rashi on Shabbat 73a:4:1", "type": "commentary"}
```

`from` and `to` are citations; `type` is free text (`commentary`, `quotation`, ...). Links whose ends do not
resolve are skipped and counted in the build log.

## topics.jsonl

```json
{"slug": "shabbat", "name": {"en": "Shabbat", "he": "שבת"}, "names": ["Shabbat", "Sabbath", "שבת"],
 "description": "...", "sources": [{"ref": "Exodus 20:8", "relevance": 0.93}],
 "links": [{"topic": "melakhah", "relation": "related-to"}]}
```

`sources` are in order of relevance; `links` relate topics to each other.

## Profile

`profile.json` tells the server how to treat the corpus. Every key is optional; without a profile the server
searches each language plainly, groups research results into one list, earliest first, and offers no
corpus-specific tools. `genericCorpusMcpBuilder/sources/sefaria/profile.json` is a complete example.

```jsonc
{
  "languages": {                      // keys are the codes in corpus.json "languages"
    "grc": {"name": "Ancient Greek", "script": "greek", "search": "plain"},
    "en":  {"name": "English", "script": "latin", "search": "english"}
  },
  "eras": [{"code": "A", "name": "Archaic"}, {"code": "C", "name": "Classical"}],   // works' "era", in order
  "periods": [{"label": "Archaic", "until": -480}, {"label": "Classical", "until": -323},
              {"label": "Hellenistic and later"}],                                 // groups by works' "date"
  "strata": [{"id": "epic", "label": "Epic"}, {"id": "drama", "label": "Drama"}, {"id": "other", "label": "Other"}],
  "stratum_rules": [{"stratum": "epic", "category": ["Epic"]}, {"stratum": "drama", "category": ["Tragedy", "Comedy"]}],
  "research": { ... },
  "trace": {"tool": "trace_halakha", "categories": ["..."], "description": "..."},
  "embedding": { ... },
  "source_url": "https://example.org/read/{ref}",
  "citation_scheme_preference": ["Stephanus", "Bekker"],
  "tool_descriptions": {"research_question": "...", "get_text": "..."}
}
```

| key | |
|---|---|
| `languages` | per language: display `name`; `script` it is written in (`latin`, `greek`, `coptic`, `hebrew`, `arabic`, `syriac`, `cyrillic`, `armenian`, `georgian`, `ethiopic`, `devanagari`, `tibetan`, `thai`, `han`, `japanese`, `hangul`), used to tell which language a query or quote is in; common language codes get the right script and search mode by themselves; `search`: `plain` (default; accents ignored), `english` (also stems English words), `latin` (also folds u/v, i/j), `hebrew` (also ignores prefix letters ו/ה/ב/כ/ל/מ/ש/ד and -ים/-ין), `cjk` (Chinese and Japanese: each character or kana is a unit; queries match character sequences) |
| `eras` | the era codes works use, earliest first, with readable names; used to order works without dates |
| `periods` | date ranges (`until` = last year, inclusive) to group results by when `research.group_by` is `date` |
| `strata` | named layers of the tradition, in order; the last one also takes works no rule matches |
| `stratum_rules` | first match wins. Conditions (all must hold): `category` (work's main category in list), `title_prefix`, `comments_on_prefix`, `dependent` (true/false: is it a commentary), `era` |
| `source_url` | link to the source online; `{ref}` is replaced by the citation with spaces as `_` |
| `citation_scheme_preference` | when a bare label exists in several citation systems (Vilna and Venice page 7a), the order to prefer |
| `tool_descriptions` | replaces a tool's description, to speak in the corpus's own terms |

`research`:

| key | default | |
|---|---|---|
| `group_by` | `strata` if strata are defined, else `date` | how results are grouped: `strata`, `date` (by `periods`, or one list), `work`, `none` (one list, most relevant first) |
| `keyword_categories` | all | categories the key-term search looks in |
| `keyword_base_texts_only` | false | leave commentaries out of key-term search ... |
| `keyword_dependent_categories` | none | ... except those whose main category is listed (e.g. codes that comment on codes) |
| `keyword_strata` | all | strata key-term hits must belong to |
| `follow_links` | none | rules for following links from good results to related works: `from_strata`, `to_title_prefix`, `link_types`, `candidates` (how many top results to follow, 10), `score_factor` (0.8) |
| `reserve` | none | `{stratum: n}`: always include up to n results from that stratum |
| `major_commentaries` | none | for results whose title starts with `on_title_prefix`, list commentaries whose titles start with `commentary_prefixes` (up to `limit`) |
| `relevance_margin` | 0.12 | per language: how much less similar than the best result a result may be before it is dropped |
| `next_steps` | generic advice | the advice research_question returns |

`trace`: if present, the server offers a tool (named by `tool`, default `trace_sources`) that follows links from a
passage to earlier sources and later works, limited to works in `categories`.

`embedding`: which works semantic search covers. `title_prefixes` are always included; otherwise base texts (not
commentaries) whose main category is in `base_text_categories` or whose categories start with one of
`category_paths` (all base texts if neither is set). Each work is embedded in its primary translation when that
covers at least `translation_coverage` (0.8) of the original, else in the original. With several translations,
`prefer_languages` (e.g. `["en"]`) says which to use first; the model handles English best.

## Writing an adapter

An adapter is a package in `genericCorpusMcpBuilder/sources/` with two functions, registered by name in
`genericCorpusMcpBuilder/sources/__init__.py` (`ADAPTERS`):

- `fetch(raw)` downloads the source into the directory `raw` (resumably, if it is large);
- `convert(raw, out)` writes the corpus directory `out`, using `genericCorpusMcpBuilder.corpus_format.CorpusWriter` and `TextWriter`.

`corpus-mcp build --source <name>` then runs fetch, convert and the build. The adapter does all source-specific work
(markup, numbering conventions, choosing versions) so that `text` is ready to display and `cite` holds the labels
people cite by. See `genericCorpusMcpBuilder/sources/sefaria/` for a complete example.
