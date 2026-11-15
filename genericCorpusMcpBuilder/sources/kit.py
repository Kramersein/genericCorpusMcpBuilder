"""Toolkit for writing source adapters: SimpleCorpus takes works and texts as plain Python values and writes a
corpus directory (docs/corpus-format.md), working out the format's details itself:

- each level's kind (number, folio, label) from the labels actually used,
- the corpus languages, and language codes normalized (eng -> en, ger -> de, ...),
- unique part names when two authors' works share a title,
- an optional second citation system without the outer levels (Plato's `Republic 327a` for `Republic 1.327a`).

A minimal adapter (see docs/adapters.md):

    def convert(raw, out, **options):
        corpus = SimpleCorpus(out, id="my-texts", name="My texts", original_language="la")
        work = corpus.work("Confessions", author="Augustine", levels=["book", "chapter", "section"], date=398)
        corpus.text(work, "la", "Skutella 1934", [(["1", "1", "1"], "Magnus es, domine..."), ...])
        corpus.finish()
"""
import difflib
import html
import json
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

from genericCorpusMcpBuilder.corpus_format import CorpusWriter, TextWriter, text_file_name

# ISO 639-2/3 and common names -> the codes profiles and defaults use (profile.KNOWN_LANGUAGES)
LANGUAGE_CODES = {"eng": "en", "english": "en", "ger": "de", "deu": "de", "german": "de", "fre": "fr", "fra": "fr",
                  "french": "fr", "ita": "it", "italian": "it", "spa": "es", "spanish": "es", "heb": "he",
                  "hebrew": "he", "ara": "ar", "arabic": "ar", "gre": "el", "ell": "el", "la": "lat", "latin": "lat",
                  "greek": "grc", "ancient greek": "grc", "syr": "syc", "syriac": "syc", "aramaic": "arc"}
DIGITS, FOLIO = re.compile(r"^\d+$"), re.compile(r"^\d+[a-z]$")
PROFILE_FILES = ("profile.json", "instructions.md", "answer_prompt.md")


def language_code(code: str | None, default: str = "und") -> str:
    code = (code or "").strip()
    return LANGUAGE_CODES.get(code.lower(), code.lower()) if code else default


def level_kind(labels) -> str | None:
    """number (returned as None, the default) if every label is digits, folio if every label is digits plus one
    letter (73a, 327b), else label."""
    labels = list(labels)
    if all(DIGITS.match(label) for label in labels):
        return None
    if all(FOLIO.match(label) for label in labels):
        return "folio"
    return "label"


def rows(segments, cite: str = "cite", text: str = "text", notes: str = "notes"):
    """(cite, text, notes) from tuples, dicts or a pandas DataFrame (anything with to_dict("records"))."""
    if hasattr(segments, "to_dict"):
        segments = segments.to_dict("records")
    for item in segments:
        if isinstance(item, dict):
            missing = [name for name in (cite, text) if name not in item]
            if missing:
                raise ValueError(f"a segment has no {' or '.join(map(repr, missing))} (it has {sorted(item)}); "
                                 f"name the columns with cite=..., text=...")
            yield item[cite], item[text], item.get(notes) or ""
        else:
            item = list(item)
            if len(item) < 2:
                raise ValueError(f"a segment must be (cite, text) or (cite, text, notes), not {item!r}")
            yield item[0], item[1], item[2] if len(item) > 2 else ""


def cite_labels(value, separator: str, where: str) -> list[str]:
    """["1", "3"] from ["1", "3"], (1, 3), "1.3" (at the separator) or 3; 3.0 from a spreadsheet becomes "3"."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        labels = [str(value)]
    elif isinstance(value, str):
        labels = value.strip().split(separator)
    elif isinstance(value, (list, tuple)):
        labels = [str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip() for v in value]
    else:
        raise ValueError(f"{where}: a cite must be labels (a list), a string or a number, not {value!r}")
    if not labels or not all(labels):
        raise ValueError(f"{where}: empty label in cite {value!r}")
    return labels


class _TextOnly(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skipping = [], 0

    def handle_starttag(self, tag, attrs):
        self.skipping += tag in ("script", "style")
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        self.skipping -= tag in ("script", "style") and self.skipping > 0
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


class _Blocks(HTMLParser):
    """Headings and paragraphs of an HTML page in order, leaving out elements with skipped ids or classes."""
    HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCKS = HEADINGS | {"p", "li", "dd", "dt", "td", "th"}
    VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col", "area", "base", "source"}

    def __init__(self, skip_ids, skip_classes, skip_attributes=None):
        super().__init__(convert_charrefs=True)
        self.skip_ids, self.skip_classes = set(skip_ids), set(skip_classes)
        self.skip_attributes = {name: set(values) for name, values in (skip_attributes or {}).items()}
        self.stack, self.skipping, self.blocks, self.current = [], 0, [], None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in self.VOID:
            if self.current is not None and not self.skipping:
                if tag == "br":
                    self.current[1].append(" ")
                elif tag == "img" and attributes.get("alt"):     # a drop cap: the letter is the image's alt text
                    self.current[1].append(attributes["alt"])
            return
        skip = (tag in ("script", "style", "sup") and "noteref" in (attributes.get("class") or "")) or \
            tag in ("script", "style") or attributes.get("id") in self.skip_ids or \
            bool(self.skip_classes & set((attributes.get("class") or "").split())) or \
            any(values & set((attributes.get(name) or "").split()) for name, values in self.skip_attributes.items())
        self.stack.append((tag, skip))
        self.skipping += skip
        if tag in self.BLOCKS and not self.skipping and self.current is None:
            self.current = (tag, [])

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        while self.stack:                          # close up to the matching tag (tolerates unclosed children)
            open_tag, skip = self.stack.pop()
            self.skipping -= skip
            if self.current is not None and open_tag == self.current[0]:
                text = re.sub(r"\s+", " ", "".join(self.current[1])).strip()
                if text:
                    self.blocks.append((open_tag, text))
                self.current = None
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self.current is not None and not self.skipping:
            self.current[1].append(data)


def html_blocks(markup: str, skip_ids=(), skip_classes=(), skip_attributes=None) -> list[tuple[str, str]]:
    """An HTML page as (tag, text) blocks in reading order: headings ("h1".."h6") and paragraphs ("p", list items,
    table cells), each as plain text. Elements whose id is in skip_ids or which have a class in skip_classes are
    left out with everything inside them (headers, footers, page numbers, navigation, footnote markers), and so are
    elements with one of the values of skip_attributes ({"epub:type": ["noteref"]})."""
    parser = _Blocks(skip_ids, skip_classes, skip_attributes)
    parser.feed(markup)
    parser.close()
    return parser.blocks


def chapters_from_blocks(blocks: list[tuple[str, str]], heading: str | None = None) -> list[tuple[str, list[str]]]:
    """Group blocks into chapters at one heading level: [(heading text, [paragraphs]), ...]. Without heading=, the
    most frequent heading level is taken (chapters outnumber parts). Text before the first such heading (title
    pages) is left out; headings of other levels inside a chapter become ordinary paragraphs."""
    counts = {}
    for tag, _ in blocks:
        if tag.startswith("h"):
            counts[tag] = counts.get(tag, 0) + 1
    if heading is None:
        if not counts:
            return [("", [text for _, text in blocks])] if blocks else []
        heading = max(counts, key=lambda tag: (counts[tag], -int(tag[1])))
    chapters = []
    for tag, text in blocks:
        if tag == heading:
            chapters.append((text, []))
        elif chapters:
            chapters[-1][1].append(text)
    return [(title, paragraphs) for title, paragraphs in chapters if paragraphs]


ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_NUMERAL = r"(\d+|(?=[mdclxvi])m{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3}))"
_KEYWORD = (r"(?:chapter|chap\.?|book|part|letter|canto|section|stave|act|scene|psalm|volume|vol\.?"
            r"|liber|caput|cap\.?|pars|epistula|sermo"                       # Latin
            r"|buch|kapitel|teil|band"                                         # German
            r"|livre|chapitre|partie|tome"                                     # French
            r"|libro|cap[ií]tulo|capitolo|parte)")                             # Spanish, Italian
NUMBER_WORDS = {word: n for n, words in enumerate([
    ("one", "first"), ("two", "second"), ("three", "third"), ("four", "fourth"), ("five", "fifth"), ("six", "sixth"),
    ("seven", "seventh"), ("eight", "eighth"), ("nine", "ninth"), ("ten", "tenth"), ("eleven", "eleventh"),
    ("twelve", "twelfth"), ("thirteen", "thirteenth"), ("fourteen", "fourteenth"), ("fifteen", "fifteenth"),
    ("sixteen", "sixteenth"), ("seventeen", "seventeenth"), ("eighteen", "eighteenth"), ("nineteen", "nineteenth"),
    ("twenty", "twentieth")], 1) for word in words}
_WORD = "(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + ")"
WORD_HEADINGS = [re.compile(rf"^\s*{_KEYWORD}\s+(?:the\s+)?{_WORD}\b", re.IGNORECASE),   # BOOK ONE, Chapter the First
                 re.compile(rf"^\s*(?:the\s+)?{_WORD}\s+{_KEYWORD}\b", re.IGNORECASE)]  # THE FIRST BOOK, Second Part
NUMBERED_HEADINGS = [re.compile(rf"^\s*{_KEYWORD}\s+{_NUMERAL}\b", re.IGNORECASE),      # Chapter IV, BOOK 2
                     re.compile(rf"^\s*{_NUMERAL}\s*[.:]?\s*$", re.IGNORECASE),          # IV.  /  12
                     re.compile(rf"^\s*{_NUMERAL}[.:]\s+\S", re.IGNORECASE)]             # IV. The Storm


def roman_to_int(numeral: str) -> int:
    values = [ROMAN[c] for c in numeral.lower()]
    return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))


def heading_number(heading: str) -> int | None:
    """4 for "Chapter IV.", "CHAPTER 4", "IV.", "IV. The Storm", "BOOK FOUR" or "THE FOURTH BOOK"; None for
    "Preface", "Mild Days" or "His First Book" (a number word only counts at the start, before or after a keyword)."""
    for pattern in WORD_HEADINGS:
        match = pattern.match(heading or "")
        if match:
            return NUMBER_WORDS[match.group(1).lower()]
    for pattern in NUMBERED_HEADINGS:
        match = pattern.match(heading or "")
        if match and match.group(1):
            token = match.group(1)
            return int(token) if token.isdigit() else roman_to_int(token)
    return None


def section_labels(headings: list[str]) -> list[str]:
    """Citation labels for chapters from their headings: "Chapter IV." -> "4", "BOOK 2" -> "2", "Preface" ->
    "Preface". If any label would repeat (chapter numbers starting again in each book), the chapters are numbered
    1, 2, 3 ... in order instead, so no two chapters share a citation."""
    labels = []
    for heading in headings:
        number = heading_number(heading)
        if number is not None:
            labels.append(str(number))
        else:
            text = re.sub(r"\s+", " ", (heading or "").replace(".", " ")).strip(" :;,")
            labels.append(text or str(len(labels) + 1))
    if len({label.lower() for label in labels}) < len(labels):
        return [str(number) for number in range(1, len(labels) + 1)]
    return labels


def cached_json(path, ask):
    """The answer saved at path, or ask() (a function that calls an API) saved there first. So an API is never asked
    twice for the same thing, and an interrupted download continues where it stopped."""
    path = Path(path)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    answer = ask()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return answer


def pause(seconds) -> None:
    """Wait between requests to a server (be polite: most sources ask for one request at a time, with a pause)."""
    import time
    time.sleep(float(seconds))


def html_to_text(markup: str) -> str:
    """The text of an HTML page or fragment: tags, scripts and styles dropped, one line per paragraph or line break."""
    parser = _TextOnly()
    parser.feed(markup)
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in html.unescape("".join(parser.parts)).split("\n"))
    return "\n".join(line for line in lines if line)


class LevelKinds:
    """Running classification of one level's labels (no need to keep the labels)."""

    def __init__(self):
        self.digits = self.folio = True

    def add(self, label: str) -> None:
        self.digits = self.digits and bool(DIGITS.match(label))
        self.folio = self.folio and bool(FOLIO.match(label))

    @property
    def kind(self) -> str | None:
        return None if self.digits else "folio" if self.folio else "label"


class SimpleCorpus:
    def __init__(self, out: Path, *, id: str, name: str, original_language: str, source: str = "",
                 license: str = "", exported_at: str = "unknown", version: str = "unknown"):
        self.writer = CorpusWriter(Path(out))
        self.manifest = {"id": id, "name": name, "source": source, "license": license,
                         "original_language": language_code(original_language), "source_exported_at": exported_at,
                         "source_version": version}
        self.works: dict[str, dict] = {}
        self.kinds: dict[str, list[LevelKinds]] = {}
        self.cites: dict[str, set] = {}
        self.languages: list[str] = []
        self.sequence = 0

    def work(self, title: str, *, levels: list[str] | None = None, separator: str = ".", language: str | None = None,
             author: str | None = None, authors: list[str] | None = None, date=None, date_text: str | None = None,
             categories: list[str] | None = None, aliases=(), titles: dict | None = None, comments_on=(),
             dependence: str | None = None, description: str | None = None, name: str | None = None,
             kinds: dict | None = None, keep_cites: bool = False, joiner: str = " ") -> str:
        """Add a work; returns the name to cite it by (and to pass to text()). levels are the citation levels'
        names, outermost first; kinds ({level name: "folio"}) overrides the kinds worked out from the labels.
        date is a year (negative = BCE) or [earliest, latest]. keep_cites lets page_citations() use it later.
        joiner goes between the name and the address in citations (" " by default; ":" gives "MN 1:3.4")."""
        authors = authors or ([author] if author else [])
        name = name or title
        if name in self.works:
            name = f"{authors[0]}, {title}" if authors else title
            number = 2
            while name in self.works:
                name, number = f"{title} ({number})", number + 1
        self.works[name] = {
            "title": name, "titles": titles or {}, "language": language_code(language) if language else None,
            "aliases": sorted({a for a in [title, *aliases, *([f"{authors[0]}, {title}"] if authors else [])]
                               if a and a != name}),
            "authors": authors, "categories": categories if categories is not None else authors[:1],
            "date": [date, date] if isinstance(date, int) else date, "date_text": date_text,
            "comments_on": list(comments_on), "dependence": dependence, "description": description,
            "levels": list(levels or []), "separator": separator, "kinds": kinds or {}, "joiner": joiner}
        self.kinds[name] = []
        if keep_cites:
            self.cites[name] = set()
        return name

    def text(self, work: str, lang: str | None = None, title: str | None = None, segments=None, *,
             language: str | None = None, source: str | None = None, rank: int | None = None,
             cite: str = "cite", text: str = "text", notes: str = "notes", **extra) -> int:
        """One version (an edition or a translation) of a work, its segments in reading order. segments can be
        - (cite, text) or (cite, text, notes) tuples,
        - dicts, or a pandas DataFrame, with "cite", "text" and optionally "notes" (other names: cite=, text=, notes=).
        A cite is the labels: a list (["1", "3"]), a string split at the work's separator ("1.3"), or a number (3).
        lang (or language=) is the version's language code. Returns the number of segments written."""
        lang = language_code(lang or language)
        if work not in self.works:
            close = difflib.get_close_matches(work, list(self.works), n=3)
            raise ValueError(f"no work {work!r}: add it with corpus.work(...) first, and pass the name it returns"
                             + (f" (did you mean {', '.join(map(repr, close))}?)" if close else ""))
        if not title:
            raise ValueError(f"text() for {work!r} needs a title: the name of this edition or translation")
        if segments is None:
            raise ValueError(f"text() for {work!r} needs segments: a list of (cite, text) or a DataFrame")
        if lang not in self.languages:
            self.languages.append(lang)
        info, kinds = self.works[work], self.kinds[work]
        header = {"work": work, "lang": lang, "title": title, "source": source or title, **extra}
        if rank is not None:
            header["rank"] = rank
        count = 0
        path = self.writer.text_path(text_file_name(self.sequence, work, lang, title))
        self.sequence += 1
        with TextWriter(path, header) as out:
            for number, (labels, body, note) in enumerate(rows(segments, cite, text, notes), 1):
                labels = cite_labels(labels, info["separator"], f"{work} ({title}), segment {number}")
                if not isinstance(body, str) or not body.strip():
                    continue
                while len(kinds) < len(labels):
                    kinds.append(LevelKinds())
                for kind, label in zip(kinds, labels):
                    kind.add(label)
                if work in self.cites:
                    self.cites[work].add(tuple(labels))
                out.segment(work, labels, body.strip(), note if isinstance(note, str) else "")
                count += 1
        return count

    def citation(self, work: str, scheme: str, labels: list[str], ref: str, **extra) -> None:
        self.writer.citation({"work": work, "scheme": scheme, "labels": labels, "ref": ref, **extra})

    def link(self, source: str, target: str, kind: str = "") -> None:
        self.writer.link({"from": source, "to": target, "type": kind})

    def topic(self, slug: str, name: str, sources: list[str], description: str = "", names=(), related=()) -> None:
        self.writer.topic({"slug": slug, "name": {"en": name}, "names": [name, *names], "description": description,
                           "sources": [{"ref": ref} for ref in sources],
                           "links": [{"topic": other, "relation": "related-to"} for other in related]})

    def page_citations(self, work: str, scheme: str) -> int:
        """For a work whose innermost level is folio-like (327a): cite it without the outer levels, by section
        (`Republic 327a` = `Republic 1.327a`) and by page (`Republic 327` = `Republic 1.327a-327e`)."""
        separator, pages = self.works[work]["separator"], {}
        for cite in self.cites.get(work, ()):
            if len(cite) >= 2 and FOLIO.match(cite[-1]):
                pages.setdefault((cite[:-1], cite[-1][:-1]), set()).add(cite[-1])
        for (parents, page), labels in sorted(pages.items()):
            prefix = f"{work} {separator.join(parents)}{separator}"
            for label in sorted(labels):
                self.citation(work, scheme, [label, f"{scheme} {label}"], prefix + label)
            first, last = min(labels), max(labels)
            self.citation(work, scheme, [page, f"{scheme} {page}"], prefix + first + (f"-{last}" if last != first else ""))
        return len(pages)

    def finish(self, profile: dict | None = None, instructions: str | None = None, answer_prompt: str | None = None,
               profile_dir: Path | None = None) -> None:
        """Write works.jsonl and corpus.json. The profile, instructions and answer prompt can be given as values or
        copied from profile_dir (profile.json, instructions.md, answer_prompt.md)."""
        for name, info in self.works.items():
            kinds = self.kinds[name]
            levels = []
            for depth in range(max(len(info["levels"]), len(kinds))):
                level = {"name": info["levels"][depth] if depth < len(info["levels"]) else f"level {depth + 1}"}
                kind = info["kinds"].get(level["name"]) or (kinds[depth].kind if depth < len(kinds) else None)
                if kind:
                    level["kind"] = kind
                levels.append(level)
            record = {k: v for k, v in info.items()
                      if k not in ("levels", "separator", "kinds", "joiner") and v not in (None, [], {})}
            record["parts"] = [{"name": name, "levels": levels, "separator": info["separator"],
                                **({"joiner": info["joiner"]} if info["joiner"] != " " else {})}]
            self.writer.work(record)
        original = self.manifest["original_language"]
        languages = [original] + [lang for lang in self.languages if lang != original]
        self.writer.write_manifest({**self.manifest, "languages": languages})
        if profile_dir:
            for file in PROFILE_FILES:
                if (Path(profile_dir) / file).exists():
                    shutil.copy(Path(profile_dir) / file, self.writer.out / file)
        if profile is not None:
            (self.writer.out / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n")
        if instructions is not None:
            (self.writer.out / "instructions.md").write_text(instructions.strip() + "\n")
        if answer_prompt is not None:
            (self.writer.out / "answer_prompt.md").write_text(answer_prompt.strip() + "\n")
        self.writer.close()
        print(f"wrote {len(self.works)} works, {self.sequence} versions, languages {languages}", flush=True)
