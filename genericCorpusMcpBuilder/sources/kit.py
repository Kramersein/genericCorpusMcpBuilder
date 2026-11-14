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
import json
import re
import shutil
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
             kinds: dict | None = None, keep_cites: bool = False) -> str:
        """Add a work; returns the name to cite it by (and to pass to text()). levels are the citation levels'
        names, outermost first; kinds ({level name: "folio"}) overrides the kinds worked out from the labels.
        date is a year (negative = BCE) or [earliest, latest]. keep_cites lets page_citations() use it later."""
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
            "levels": list(levels or []), "separator": separator, "kinds": kinds or {}}
        self.kinds[name] = []
        if keep_cites:
            self.cites[name] = set()
        return name

    def text(self, work: str, lang: str, title: str, segments, *, source: str | None = None, rank: int | None = None,
             **extra) -> int:
        """One version of a work: segments are (cite, text) or (cite, text, notes) in reading order, cite a list of
        labels (or a string split at the work's separator). Returns the number of segments written."""
        lang = language_code(lang)
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
            for segment in segments:
                cite, text, notes = (list(segment) + [""])[:3]
                cite = cite.split(info["separator"]) if isinstance(cite, str) else [str(label) for label in cite]
                if not text or not text.strip():
                    continue
                while len(kinds) < len(cite):
                    kinds.append(LevelKinds())
                for kind, label in zip(kinds, cite):
                    kind.add(label)
                if work in self.cites:
                    self.cites[work].add(tuple(cite))
                out.segment(work, cite, text.strip(), notes or "")
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
            record = {k: v for k, v in info.items() if k not in ("levels", "separator", "kinds") and v not in (None, [], {})}
            record["parts"] = [{"name": name, "levels": levels, "separator": info["separator"]}]
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
