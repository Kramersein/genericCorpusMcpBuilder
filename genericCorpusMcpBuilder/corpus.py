import difflib
import hashlib
import itertools
import json
import re
import sqlite3
from dataclasses import dataclass
from functools import cached_property, lru_cache

from genericCorpusMcpBuilder.citations import LABEL_SEPARATOR, Locator, format_range
from genericCorpusMcpBuilder.profile import Profile
from genericCorpusMcpBuilder.refs import hebrew_address_to_english, split_word_span
from genericCorpusMcpBuilder.text import (display_words, find_words, latin_spelling, normalize_for_search, reversed_query_terms,
                           search_words, spaced_characters, word_units)

HEBREW_LETTER = re.compile(r"[א-ת]")
MAX_LOCATIONS_PER_REQUEST = 300
MAX_QUOTE_MATCHES = 2000
LONG_LINK_SPAN = 500
PREFIX_VARIANT_MATCH = "same words apart from prefix letters (ו/ה/ב/כ/ל/מ/ש/ד) or -ים/-ין"
UNKNOWN_ERA_RANK = 9
TRACE_MAX_WORKS = 25
TRACE_PASSAGES_PER_WORK = 3
CLOSE_LABEL_CUTOFF = 0.85
STOPWORDS = set("""a an and are as at be but by can could did do does for from had has have how i if in into is it its
may me might must my no not of on or our shall should so than that the their them then there these they this to
too us was we what when where which who whom why will with would you your about am any been being both each else
ever if just more most much need only other own same some such very allowed permitted ok okay""".split())


class RefNotFound(ValueError):
    pass


@dataclass(frozen=True)
class Passage:
    title: str
    ref: str
    loc_start: int | None
    loc_end: int | None
    cited_as: str | None = None

    @property
    def exists(self) -> bool:
        return self.loc_start is not None


def is_hebrew(text: str) -> bool:
    return bool(HEBREW_LETTER.search(text))


def fts_query(text: str, as_phrase: bool, mode: str = "plain") -> str:
    normalized = normalize_for_search(text)
    if mode == "latin":
        normalized = latin_spelling(normalized)
    if mode == "cjk":                    # each space-separated part of the query is a sequence of characters
        parts = [normalized] if as_phrase else normalized.split()
        terms = [f'"{spaced_characters(part)}"' for part in parts if spaced_characters(part)]
        if not terms:
            raise ValueError("query has no searchable words")
        return " ".join(terms)
    if mode == "hebrew":
        terms = reversed_query_terms(normalized)
        joiner = " + " if as_phrase else " "
    else:
        tokens = normalized.split()
        terms = [f'"{" ".join(tokens)}"'] if as_phrase else [f'"{t}"' for t in tokens]
        joiner = " "
    if not terms:
        raise ValueError("query has no searchable words")
    return joiner.join(terms)


def fingerprint(parts: list[bytes]) -> str:
    digest = hashlib.blake2b(digest_size=16)
    for part in parts:
        digest.update(part)
    return digest.hexdigest()


class Corpus:
    def __init__(self, db_path: str):
        self.db = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA mmap_size=17179869184")
        self.locator = Locator(self.db)
        self.profile = Profile.from_manifest(self.manifest)

    @cached_property
    def manifest(self) -> dict:
        return {key: value for key, value in self.db.execute("SELECT key, value FROM meta")}

    @property
    def build_id(self) -> str:
        return self.manifest.get("build_id", "unknown")

    def title_for_alias(self, alias: str) -> str | None:
        row = self.db.execute(
            "SELECT a.title FROM aliases a JOIN works w ON w.title = a.title WHERE a.alias = ? "
            "ORDER BY (w.dependence IS NOT NULL), length(a.title) LIMIT 1", (alias.strip(" ,").lower(),)).fetchone()
        return row["title"] if row else None

    def title_cuts(self, text: str):
        cut_points = sorted({m.start() for m in re.finditer(r"[ ,]", text)} | {len(text)}, reverse=True)
        for cut in cut_points:
            title = self.title_for_alias(text[:cut])
            if title:
                yield title, text[cut:]

    def canonical_spelling(self, text: str) -> str | None:
        return next((title + rest for title, rest in self.title_cuts(text)), None)

    def hebrew_ref_to_english(self, ref: str) -> str | None:
        for title, rest in self.title_cuts(ref):
            part = self.locator.part(title)
            address = hebrew_address_to_english(rest, list(part.kinds) if part else [])
            if part and address:
                address = address.replace(":", part.separator)
            return f"{title} {address}".strip()
        return None

    def ref_for_range(self, loc_start: int, loc_end: int) -> str:
        first, last = (self.db.execute("SELECT base, cite, ref FROM locations WHERE id = ?", (i,)).fetchone()
                       for i in (loc_start, loc_end))
        if loc_start == loc_end:
            return first["ref"]
        if first["base"] != last["base"]:
            return f"{first['ref']} - {last['ref']}"
        part = self.locator.part(first["base"])
        labels = lambda cite: cite.split(LABEL_SEPARATOR) if cite else []
        return format_range(first["base"], part.separator, labels(first["cite"]), labels(last["cite"]))

    def resolve_primary(self, candidate: str) -> Passage | str | None:
        resolution = self.locator.resolve(candidate)
        if not resolution:
            return None
        return Passage(resolution.part.title, resolution.ref, resolution.loc_start, resolution.loc_end)

    def alternative_matches(self, title: str, label: str) -> list[sqlite3.Row]:
        rows = self.db.execute(
            "SELECT r.title, r.scheme, min(r.label) AS label, min(r.loc_start) AS loc_start, max(r.loc_end) AS loc_end "
            "FROM alt_labels l JOIN alt_refs r ON r.id = l.alt_ref_id WHERE l.title = ? AND l.label = ? "
            "GROUP BY r.title, r.scheme", (title, label)).fetchall()
        rank = {scheme: i for i, scheme in enumerate(self.profile.citation_scheme_preference)}
        return sorted(rows, key=lambda r: (rank.get(r["scheme"], -1), r["title"]))

    def close_label(self, title: str, label: str) -> str | None:
        labels = [r[0] for r in self.db.execute("SELECT DISTINCT label FROM alt_labels WHERE title = ?", (title,))]
        close = difflib.get_close_matches(label, labels, n=1, cutoff=CLOSE_LABEL_CUTOFF)
        return close[0] if close else None

    def resolve_alternative(self, text: str, allow_close: bool = True) -> Passage | None:
        attempts = [("", text.strip().lower())] + [(title, rest.strip(" ,").lower()) for title, rest in self.title_cuts(text)]
        for allow_close in (False, True) if allow_close else (False,):
            for title, label in attempts:
                if not label or (allow_close and not title):
                    continue
                matched_label = self.close_label(title, label) if allow_close else label
                matches = self.alternative_matches(title, matched_label) if matched_label else []
                if not matches:
                    continue
                best = matches[0]
                cited_as = scheme_label(best["scheme"], best["label"])
                if allow_close:
                    cited_as += f" (closest name to {label!r})"
                others = [f"{scheme_label(m['scheme'], m['label'])} = {self.ref_for_range(m['loc_start'], m['loc_end'])}"
                          for m in matches[1:]]
                if others:
                    cited_as += f"; not to be confused with {'; '.join(others)}"
                return Passage(best["title"], self.ref_for_range(best["loc_start"], best["loc_end"]),
                               best["loc_start"], best["loc_end"], cited_as=cited_as)
        return None

    def resolve(self, ref: str) -> Passage:
        ref = " ".join(ref.replace("_", " ").split())
        problems = []
        hebrew_candidate = self.hebrew_ref_to_english(ref) if is_hebrew(ref) else None
        missing = None
        for candidate in dict.fromkeys(filter(None, (ref, self.canonical_spelling(ref), hebrew_candidate))):
            result = self.resolve_primary(candidate)
            if isinstance(result, Passage) and result.exists:
                return result
            if isinstance(result, Passage):
                missing = missing or result        # parses, but no such passage: another citation system may have it
            elif result:
                problems.append(result)
        alternative = self.resolve_alternative(ref, allow_close=missing is None)
        if alternative:
            return alternative
        if missing:
            return missing
        detail = "; ".join(problems) or f"suggestions: {self.suggest_titles(ref)}"
        raise RefNotFound(f"unknown reference {ref!r} ({detail})")

    def suggest_titles(self, text: str, limit: int = 8) -> list[str]:
        head = re.sub(r"[\d:ab\- ]+$", "", text).strip().lower()
        rows = self.db.execute(
            "SELECT DISTINCT title FROM aliases WHERE alias LIKE ? LIMIT ?", (f"%{head}%", limit)).fetchall()
        if rows:
            return [r["title"] for r in rows]
        titles = [r["title"] for r in self.db.execute("SELECT title FROM works")]
        return difflib.get_close_matches(text, titles, n=limit, cutoff=0.5)

    def versions(self, title: str, lang: str, version_title: str | None) -> list[sqlite3.Row]:
        if version_title:
            return self.db.execute("SELECT * FROM versions WHERE title = ? AND lang = ? AND version_title = ?",
                                   (title, lang, version_title)).fetchall()
        return self.db.execute("SELECT * FROM versions WHERE title = ? AND lang = ? ORDER BY priority",
                               (title, lang)).fetchall()

    def work(self, title: str) -> dict:
        return dict(self.cached_work(title))

    @lru_cache(maxsize=20000)
    def cached_work(self, title: str) -> tuple:
        return tuple(self.load_work(title).items())

    def load_work(self, title: str) -> dict:
        row = self.db.execute("SELECT * FROM works WHERE title = ?", (title,)).fetchone()
        if not row:
            return {"title": title}
        work = dict(row)
        for field in ("categories", "authors", "base_text_titles"):
            work[field] = json.loads(work[field] or "[]")
        work["era_name"] = self.profile.era_name(work.get("era"))
        return work

    def alternative_citations(self, title: str, location_id: int) -> list[str]:
        rows = self.db.execute(
            "SELECT scheme, label FROM alt_refs WHERE title = ? AND loc_start <= ? AND loc_end >= ? "
            "ORDER BY loc_end - loc_start DESC", (title, location_id, location_id)).fetchall()
        return list(dict.fromkeys(scheme_label(r["scheme"], r["label"]) for r in rows))

    def passage_for_range(self, title: str, loc_start: int, loc_end: int) -> Passage:
        return Passage(title, self.ref_for_range(loc_start, loc_end), loc_start, loc_end)

    def with_context(self, passage: Passage, context: int) -> Passage:
        base = self.db.execute("SELECT base FROM locations WHERE id = ?", (passage.loc_start,)).fetchone()["base"]
        row = self.db.execute("SELECT min(id), max(id) FROM locations WHERE base = ? AND id BETWEEN ? AND ?",
                              (base, passage.loc_start - context, passage.loc_end + context)).fetchone()
        return Passage(passage.title, self.ref_for_range(row[0], row[1]), row[0], row[1], passage.cited_as)

    def get_text(self, ref: str, languages: tuple[str, ...] | None = None, version_title: str | None = None,
                 context: int = 0) -> dict:
        ref, word_span = split_word_span(ref)
        passage = self.resolve(ref)
        if word_span and passage.exists and passage.loc_start != passage.loc_end:
            raise RefNotFound(f"a word span (@) needs a single segment, but {passage.ref} is a range")
        if context and passage.exists:
            requested = passage.ref
            passage = self.with_context(passage, context)
            result = self.passage_text(passage, languages, version_title)
            return {**result, "requested_ref": requested}
        return self.passage_text(passage, languages, version_title, word_span)

    def passage_text(self, passage: Passage, languages: tuple[str, ...] | None = None, version_title: str | None = None,
                     word_span: tuple[int, int] | None = None, max_locations: int = MAX_LOCATIONS_PER_REQUEST) -> dict:
        languages = tuple(languages or self.profile.languages)
        original = self.original_of(passage.title)
        result = {"ref": passage.ref, "cited_as": passage.cited_as, "work": self.work(passage.title),
                  "source_url": self.profile.source_url(passage.ref), "build": self.build_id, "found": passage.exists}
        if not passage.exists:
            return {**result, "segments": []}
        loc_end = min(passage.loc_end, passage.loc_start + max_locations - 1)
        by_location: dict[int, dict] = {}
        versions_used, hashes = [], {lang: [] for lang in languages}
        for lang in languages:
            for version in self.versions(passage.title, lang, version_title if lang != original else None):
                rows = self.db.execute(
                    "SELECT location_id, text, notes, text_hash FROM segments WHERE version_id = ? "
                    "AND location_id BETWEEN ? AND ? ORDER BY location_id",
                    (version["id"], passage.loc_start, loc_end)).fetchall()
                filled = [r for r in rows if lang not in by_location.get(r["location_id"], {})]
                if not filled:
                    continue
                used = {"lang": lang, "version_title": version["version_title"], "source": version["version_source"],
                        "merged_from": json.loads(version["sources"] or "[]")}
                if "license" in version.keys() and version["license"]:
                    used["license"] = version["license"]
                versions_used.append(used)
                for row in filled:
                    entry = by_location.setdefault(row["location_id"], {})
                    entry[lang] = row["text"]
                    if lang != original:
                        entry[f"{lang}_version"] = version["version_title"]
                    if row["notes"]:
                        entry[f"{lang}_notes"] = row["notes"]
                    hashes[lang].append((row["location_id"], row["text_hash"]))
        refs = dict(self.db.execute("SELECT id, ref FROM locations WHERE id BETWEEN ? AND ?",
                                    (passage.loc_start, loc_end)).fetchall())
        segments = [{"ref": refs[location], **by_location[location]} for location in sorted(by_location)]
        if word_span and segments and original in segments[0]:
            first, last = word_span
            words = display_words(segments[0][original])
            if not 1 <= first <= last <= len(words):
                raise RefNotFound(f"{passage.ref} has {len(words)} words in the original; @{first}-{last} is out of range")
            segments[0][f"{original}_words"] = " ".join(words[first - 1:last])
            result["ref"] = f"{passage.ref}@{first}-{last}" if last != first else f"{passage.ref}@{first}"
        return {
            **result, "found": bool(segments), "truncated": loc_end < passage.loc_end,
            "also_cited_as": self.alternative_citations(passage.title, passage.loc_start),
            "text_fingerprint": {lang: fingerprint([h for _, h in sorted(pairs)]) for lang, pairs in hashes.items() if pairs},
            "versions": versions_used, "segments": segments,
        }

    def search(self, query: str, categories: list[str] | None = None, titles: list[str] | None = None,
               lang: str | None = None, exact_phrase: bool = False, limit: int = 20,
               base_texts_only: bool = False, dependent_categories: tuple[str, ...] = ()) -> list[dict]:
        """Keyword search in lang, or in every corpus language written in the query's script (merged in turn).
        base_texts_only leaves out commentaries, except works whose main category is in dependent_categories."""
        languages = [lang] if lang else self.profile.languages_for(query)
        found = [self.search_language(query, l, categories, titles, exact_phrase, limit, base_texts_only,
                                      dependent_categories) for l in languages]
        if len(found) == 1:
            return found[0]
        merged = [hit for rank in itertools.zip_longest(*found) for hit in rank if hit]
        return merged[:limit]

    def search_language(self, query: str, lang: str, categories, titles, exact_phrase: bool, limit: int,
                        base_texts_only: bool, dependent_categories) -> list[dict]:
        if lang not in self.profile.languages:
            raise ValueError(f"unknown language {lang!r}; this corpus has {', '.join(self.profile.languages)}")
        table, mode = self.profile.fts_table(lang), self.profile.search_mode(lang)
        filters, params = [], [fts_query(query, exact_phrase, mode)]
        if categories:
            filters.append("(" + " OR ".join("w.categories LIKE ?" for _ in categories) + ")")
            params += [f'%"{c}"%' for c in categories]
        if titles:
            filters.append(f"v.title IN ({','.join('?' * len(titles))})")
            params += titles
        if base_texts_only:
            exceptions = "".join(f" OR w.category = ?" for _ in dependent_categories)
            filters.append(f"(w.dependence IS NULL{exceptions})")
            params += list(dependent_categories)
        where = "".join(f" AND {f}" for f in filters)
        rows = self.db.execute(
            f"SELECT l.ref, s.text, v.title, v.version_title, w.category, w.era, w.comp_date "
            f"FROM {table} f JOIN segments s ON s.id = f.rowid JOIN locations l ON l.id = s.location_id "
            f"JOIN versions v ON v.id = s.version_id LEFT JOIN works w ON w.title = v.title "
            f"WHERE {table} MATCH ?{where} ORDER BY f.rank LIMIT ?", params + [limit * 3]).fetchall()
        needle = search_words(query)
        seen, results = set(), []
        for row in rows:
            if row["ref"] in seen or (mode == "hebrew" and not hebrew_words_present(row["text"], needle, exact_phrase)):
                continue
            seen.add(row["ref"])
            results.append({**dict(row), "lang": lang, "source_url": self.profile.source_url(row["ref"])})
            if len(results) == limit:
                break
        return results

    def find_quote(self, quote: str, limit: int = 20) -> list[dict]:
        words = search_words(quote)
        candidates = [{**r, "match": "exact"} for r in self.search(quote, exact_phrase=True, limit=MAX_QUOTE_MATCHES)]
        if not candidates and len(words) >= 12:
            for label, part in (("opening words", words[:8]), ("closing words", words[-8:])):
                candidates += [{**r, "match": label}
                               for r in self.search(" ".join(part), exact_phrase=True, limit=MAX_QUOTE_MATCHES)]
        literal = f" {normalize_for_search(quote)} "
        for candidate in candidates:
            if candidate["match"] == "exact" and literal not in f" {normalize_for_search(candidate['text'])} ":
                candidate["match"] = PREFIX_VARIANT_MATCH
            span = find_words(word_units(candidate["text"]), words, self.spelling_fold(candidate["lang"]))
            if span:
                candidate["cite_as"] = word_span_ref(candidate["ref"], span[0], candidate["ref"], span[1])
                candidate["matched_words"] = " ".join(display_words(candidate["text"])[span[0] - 1:span[1]])
        return sorted(candidates, key=self.earliest_first)[:limit]

    def verify_citation(self, ref: str, quote: str | None = None) -> dict:
        try:
            passage = self.get_text(ref)
        except RefNotFound as error:
            return {"ref_exists": False, "error": str(error), "build": self.build_id}
        report = {"ref_exists": passage["found"], "ref": passage["ref"], "cited_as": passage["cited_as"],
                  "work": passage["work"]["title"], "source_url": passage["source_url"],
                  "verified_against": {"build": self.build_id, "text_fingerprint": passage.get("text_fingerprint")}}
        if not quote or not passage["found"]:
            return report
        candidates = self.profile.languages_for(quote)
        lang = next((l for l in candidates if any(l in s for s in passage["segments"])), candidates[0])
        wanted_words = search_words(quote)
        units = [(segment["ref"], position, word) for segment in passage["segments"]
                 for position, word in word_units(segment.get(lang, ""))]
        span = find_words([(i, word) for i, (_, _, word) in enumerate(units)], wanted_words, self.spelling_fold(lang))
        if span:
            matched = units[span[0]:span[1] + 1]
            return {**report, "quote_found": True, **describe_match(
                quote, matched, passage["segments"], lang, self.counted_in(lang, passage["work"]["title"]))}
        other = self.quote_in_other_versions(ref, passage, lang, wanted_words)
        if other:
            version, segments, matched = other
            default = next((v["version_title"] for v in passage["versions"] if v["lang"] == lang), None)
            found = describe_match(quote, matched, segments, lang,
                                   f"{self.counted_in(lang, passage['work']['title'])} {version!r}")
            return {**report, "quote_found": True, **found, "version": version,
                    "note": f"not in the default {lang} version ({default}); found in {version}. "
                            f"Name that version when quoting, and count words in it for {found['cite_as']}."}
        best_ratio, best_ref = 0.0, passage["segments"][0]["ref"]
        for segment in passage["segments"]:
            ratio = best_window_ratio(wanted_words, search_words(segment.get(lang, "")))
            if ratio > best_ratio:
                best_ratio, best_ref = ratio, segment["ref"]
        report.update(quote_found=False, closest_similarity=round(best_ratio, 3), closest_at=best_ref,
                      found_elsewhere=[r["ref"] for r in self.find_quote(quote, limit=10)])
        return report

    def quote_in_other_versions(self, ref: str, passage: dict, lang: str, wanted_words: list[str]):
        """A quote missing from the default translation may be in another version of that language (Genesis 1:1
        "In the beginning" is in the KJV, not in the default JPS). Returns (version, segments, matched units)."""
        if lang == self.original_of(passage["work"]["title"]):
            return None
        located = self.resolve(split_word_span(ref)[0])
        if not located.exists:
            return None
        used = {v["version_title"] for v in passage["versions"] if v["lang"] == lang}
        last = min(located.loc_end, located.loc_start + MAX_LOCATIONS_PER_REQUEST - 1)
        for version in self.versions(located.title, lang, None):
            if version["version_title"] in used:
                continue
            rows = self.db.execute(
                "SELECT l.ref, s.text FROM segments s JOIN locations l ON l.id = s.location_id "
                "WHERE s.version_id = ? AND s.location_id BETWEEN ? AND ? ORDER BY s.location_id",
                (version["id"], located.loc_start, last)).fetchall()
            segments = [{"ref": r["ref"], lang: r["text"]} for r in rows]
            units = [(s["ref"], position, word) for s in segments for position, word in word_units(s[lang])]
            span = find_words([(i, word) for i, (_, _, word) in enumerate(units)], wanted_words,
                              self.spelling_fold(lang))
            if span:
                return version["version_title"], segments, units[span[0]:span[1] + 1]
        return None

    def links_for_range(self, loc_start: int, loc_end: int) -> list[dict]:
        found = {}
        for here, there in (("1", "2"), ("2", "1")):
            columns = (f"id, ref{there} AS other, title{there} AS other_title, category{there} AS category, "
                       f"link_type, start{there} AS other_start, end{there} AS other_end")
            rows = self.db.execute(
                f"SELECT {columns} FROM links WHERE start{here} BETWEEN ? AND ? AND end{here} >= ? "
                f"UNION SELECT {columns} FROM links WHERE end{here} - start{here} > {LONG_LINK_SPAN} "
                f"AND start{here} <= ? AND end{here} >= ?",
                (loc_start - LONG_LINK_SPAN, loc_end, loc_start, loc_end, loc_start)).fetchall()
            for row in rows:
                found[(row["id"], there)] = dict(row)
        return list(found.values())

    def commentaries(self, ref: str, works: list[str] | None = None, max_works: int = 15, per_work: int = 2,
                     excerpt_chars: int = 500) -> dict:
        passage = self.resolve(ref)
        if not passage.exists:
            return {"ref": passage.ref, "commentaries": []}
        by_work: dict[str, list[dict]] = {}
        for link in sorted(self.links_for_range(passage.loc_start, passage.loc_end), key=lambda l: l["other_start"]):
            if link["link_type"] != "commentary" or (works and link["other_title"] not in works):
                continue
            by_work.setdefault(link["other_title"], []).append(link)
        ordered = sorted(by_work, key=lambda title: self.earliest_first(self.work(title)))[:max_works]
        commentaries = []
        for title in ordered:
            work = self.work(title)
            items = [self.excerpt(self.passage_for_range(title, l["other_start"], l["other_end"]), excerpt_chars)
                     for l in by_work[title][:per_work]]
            commentaries.append({"work": title, "he_title": work.get("he_title"), "era": work.get("era_name"),
                                 "composed": work.get("comp_date"), "comments_on_this_passage": len(by_work[title]),
                                 "items": items})
        return {"ref": passage.ref, "build": self.build_id, "commentary_works_found": len(by_work),
                "commentaries": commentaries}

    def excerpt(self, passage: Passage, max_chars: int = 400, max_locations: int = 4) -> dict:
        text = self.passage_text(passage, max_locations=max_locations)
        item = {"ref": passage.ref}
        for lang in self.profile.languages:
            joined = " ".join(s[lang] for s in text["segments"] if lang in s)
            if joined:
                item[lang] = joined if len(joined) <= max_chars else joined[:max_chars].rsplit(" ", 1)[0] + " …"
        for lang in self.profile.translations_for(self.original_of(passage.title)):
            used = [s[f"{lang}_version"] for s in text["segments"] if f"{lang}_version" in s]
            if used:
                item[f"{lang}_version"] = used[0]
        if not any(lang in item for lang in self.profile.languages):
            item["missing_text"] = True
        return item

    def verify_citations(self, citations: list[dict]) -> dict:
        results = [self.verify_citation(c.get("ref", ""), c.get("quote")) for c in citations]
        failed = [r.get("ref") or c.get("ref") for c, r in zip(citations, results)
                  if not r.get("ref_exists") or (c.get("quote") and not r.get("quote_found"))]
        return {"all_verified": not failed, "failed": failed, "build": self.build_id, "results": results}

    @cached_property
    def version_columns(self) -> set[str]:
        return {row[1] for row in self.db.execute("PRAGMA table_info(versions)")}

    def has_table(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None

    def match_topics(self, text: str, limit: int = 5) -> list[dict]:
        if not self.has_table("topics_fts"):
            return []
        words = [w for w in normalize_for_search(text).split() if w not in STOPWORDS and len(w) > 1]
        if not words:
            return []
        rows = self.db.execute(
            "SELECT t.slug, t.en, t.he, t.description, t.source_count, bm25(topics_fts, 0, 10.0, 1.0) AS score "
            "FROM topics_fts f JOIN topics t ON t.slug = f.slug WHERE topics_fts MATCH ? "
            "ORDER BY score LIMIT ?", (" OR ".join(f'"{w}"' for w in words), limit)).fetchall()
        return [dict(r) for r in rows]

    def topics_for_terms(self, terms: list[str], limit: int = 5) -> list[dict]:
        """Topics whose name and a key term overlap as a phrase covering most of both, so that the key term
        כיבוד אב ואם finds Honoring Parents but its word אב alone does not find the month of Av."""
        if not self.has_table("topics_fts"):
            return []
        found = {}
        for term in terms:
            words = [w for w in normalize_for_search(term).split() if w]
            if not words:
                continue
            rows = self.db.execute(
                "SELECT t.slug, t.en, t.he, t.description, t.source_count, f.names, "
                "bm25(topics_fts, 0, 10.0, 1.0) AS score FROM topics_fts f JOIN topics t ON t.slug = f.slug "
                "WHERE topics_fts MATCH ? ORDER BY score LIMIT 50",
                ("names : (" + " OR ".join(f'"{w}"' for w in words) + ")",)).fetchall()
            for row in rows:
                names = [normalize_for_search(name).split() for name in (row["names"] or "").split(" ; ")]
                if row["slug"] not in found and any(phrases_overlap(words, name) for name in names if name):
                    found[row["slug"]] = {k: row[k] for k in ("slug", "en", "he", "description", "source_count", "score")}
        return sorted(found.values(), key=lambda t: t["score"])[:limit]

    def topic(self, name: str) -> sqlite3.Row | None:
        if not self.has_table("topics"):
            return None
        row = self.db.execute("SELECT * FROM topics WHERE slug = ? OR lower(en) = ? OR he = ?",
                              (name.strip().lower().replace(" ", "-"), name.strip().lower(), name.strip())).fetchone()
        if row:
            return row
        matches = self.match_topics(name, 1)
        return self.db.execute("SELECT * FROM topics WHERE slug = ?", (matches[0]["slug"],)).fetchone() if matches else None

    def topic_sources(self, slug: str, limit: int) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM topic_sources WHERE slug = ? ORDER BY rank LIMIT ?", (slug, limit)).fetchall()

    def related_topics(self, slug: str, limit: int = 30) -> list[dict]:
        rows = self.db.execute(
            "SELECT l.relation, t.slug, t.en, t.he, t.source_count FROM topic_links l JOIN topics t ON t.slug = l.related "
            "WHERE l.slug = ? GROUP BY t.slug ORDER BY (l.relation = 'related-to') DESC, t.source_count DESC LIMIT ?",
            (slug, limit)).fetchall()
        return [dict(r) for r in rows]

    def links(self, ref: str, categories: list[str] | None = None, link_types: list[str] | None = None,
              limit: int = 200) -> list[dict]:
        passage = self.resolve(ref)
        if not passage.exists:
            return []
        found = self.links_for_range(passage.loc_start, passage.loc_end)
        if categories:
            found = [f for f in found if f["category"] in categories]
        if link_types:
            found = [f for f in found if f["link_type"] in link_types]
        found.sort(key=lambda f: f["other_start"])
        return [{key: f[key] for key in ("other", "other_title", "category", "link_type")} for f in found[:limit]]

    def trace(self, ref: str, depth: int = 2, categories: list[str] | None = None, per_level: int = 60,
              max_works: int = TRACE_MAX_WORKS, per_work: int = TRACE_PASSAGES_PER_WORK) -> dict:
        """Follow the link graph from a passage to earlier and later works (only works in categories, if given)."""
        root = self.resolve(ref)
        root_work = self.work(root.title)
        frontier = [(root.ref, root.loc_start, root.loc_end)] if root.exists else []
        visited, edges = {root.ref}, []
        for level in range(1, depth + 1):
            next_frontier = []
            for current_ref, loc_start, loc_end in frontier:
                for link in self.links_for_range(loc_start, loc_end):
                    if link["other"] in visited or (categories and link["category"] not in categories):
                        continue
                    visited.add(link["other"])
                    other_work = self.work(link["other_title"])
                    edges.append({
                        "level": level, "from": current_ref, "to": link["other"], "link_type": link["link_type"],
                        "work": other_work["title"], "category": other_work.get("category"),
                        "era": other_work.get("era_name"), "composed": other_work.get("comp_date"),
                        "relation": self.relative_age(root_work, other_work),
                    })
                    next_frontier.append((link["other"], link["other_start"], link["other_end"]))
            frontier = next_frontier[:per_level]
        edges.sort(key=lambda e: (e["composed"] is None, e["composed"] or 0))
        result = {"root": root.ref, "root_work": root.title, "root_era": root_work.get("era_name"),
                  "root_composed": root_work.get("comp_date"), "build": self.build_id, "links_found": len(edges)}
        for key, relation in (("earlier_sources", "earlier"), ("later_works", "later"),
                              ("undated_or_contemporary", "unknown")):
            works = group_by_work([e for e in edges if e["relation"] == relation], per_work)
            result[key] = works[:max_works]
            if len(works) > max_works:
                result.setdefault("not_shown", {})[key] = f"{len(works) - max_works} more works"
        return result

    def list_works(self, category: str | None = None, name_contains: str | None = None, limit: int = 100) -> list[dict]:
        filters, params = [], []
        if category:
            filters.append("categories LIKE ?")
            params.append(f'%"{category}"%')
        if name_contains:
            filters.append("(title LIKE ? OR he_title LIKE ?)")
            params += [f"%{name_contains}%"] * 2
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = self.db.execute(
            f"SELECT title, he_title, categories, era, comp_date, comp_date_text, description FROM works{where} "
            f"ORDER BY comp_date IS NULL, comp_date LIMIT ?", params + [limit]).fetchall()
        return [{**dict(r), "categories": json.loads(r["categories"])} for r in rows]

    def work_details(self, title: str) -> dict:
        resolved_title = self.resolve(title).title
        work = self.work(resolved_title)
        columns = "lang, version_title, version_source, segment_count, priority" + (
            ", license" if "license" in self.version_columns else "")
        work["versions"] = [{k: v for k, v in dict(r).items() if v is not None or k != "license"} for r in self.db.execute(
            f"SELECT {columns} FROM versions WHERE title = ? ORDER BY lang DESC, priority", (resolved_title,))]
        work["sections"] = [
            {"base": r["base"], "he": r["he_base"], "section_names": json.loads(r["section_names"])}
            for r in self.db.execute("SELECT * FROM nodes WHERE title = ? ORDER BY position LIMIT 200", (resolved_title,))]
        work["citation_schemes"] = {
            r["scheme"]: {"entries": r["entries"], "examples": r["examples"].split("|")[:6]}
            for r in self.db.execute(
                "SELECT scheme, count(*) AS entries, group_concat(label, '|') AS examples FROM "
                "(SELECT scheme, label FROM alt_refs WHERE title = ? ORDER BY loc_start) GROUP BY scheme",
                (resolved_title,))}
        return work

    def about(self) -> dict:
        hidden = ("profile", "instructions", "answer_prompt")
        return {**{k: v for k, v in self.manifest.items() if k not in hidden},
                "counts": json.loads(self.manifest.get("counts", "{}")),
                "original_languages": self.profile.original_languages,
                "languages": {code: lang["name"] for code, lang in self.profile.languages.items()}}

    def spelling_fold(self, lang: str):
        return latin_spelling if self.profile.search_mode(lang) == "latin" else None

    def original_of(self, title: str) -> str:
        """The language the work was written in; word citations (@n-m) count its words."""
        return self.work(title).get("language") or self.profile.original_language

    def counted_in(self, lang: str, title: str) -> str:
        if lang == self.original_of(title):
            return f"{lang} original"
        return f"{self.profile.languages.get(lang, {}).get('name', lang)} version"

    def era_rank(self, era: str | None) -> int | None:
        return self.profile.era_rank.get(era)

    def earliest_first(self, result: dict) -> tuple:
        return (result.get("comp_date") is None, result.get("comp_date") or 0,
                self.era_rank(result.get("era")) or UNKNOWN_ERA_RANK)

    def relative_age(self, root: dict, other: dict) -> str:
        root_rank, other_rank = self.era_rank(root.get("era")), self.era_rank(other.get("era"))
        if root.get("comp_date") and other.get("comp_date"):
            if other["comp_date"] < root["comp_date"]:
                return "earlier"
            if other["comp_date"] > root["comp_date"]:
                return "later"
        if root_rank and other_rank and root_rank != other_rank:
            return "earlier" if other_rank < root_rank else "later"
        return "unknown"


def group_by_work(edges: list[dict], per_work: int) -> list[dict]:
    """Links found by trace, one entry per work (earliest first) with how many passages and the first few."""
    works: dict[str, dict] = {}
    for edge in edges:
        work = works.setdefault(edge["work"], {
            "work": edge["work"], "category": edge["category"], "era": edge["era"], "composed": edge["composed"],
            "links": 0, "link_types": [], "passages": []})
        work["links"] += 1
        if edge["link_type"] and edge["link_type"] not in work["link_types"]:
            work["link_types"].append(edge["link_type"])
        if len(work["passages"]) < per_work:
            work["passages"].append({"ref": edge["to"], "from": edge["from"], "level": edge["level"]})
    return list(works.values())


def phrases_overlap(term: list[str], name: list[str]) -> bool:
    """A topic name matches a key term if the whole term occurs in the name (הוצאה in a name about carrying out
    between domains), or the name occurs in the term and covers more than half of it (not אב in כיבוד אב ואם).
    Prefix letters and -ים/-ין are allowed."""
    if len(term) <= len(name):
        return find_words(list(enumerate(name, 1)), term) is not None
    return 2 * len(name) > len(term) and find_words(list(enumerate(term, 1)), name) is not None


def hebrew_words_present(text: str, needle: list[str], as_phrase: bool) -> bool:
    units = word_units(text)
    if as_phrase:
        return find_words(units, needle) is not None
    return all(find_words(units, [word]) is not None for word in needle)


def scheme_label(scheme: str, label: str) -> str:
    return label if label.lower().startswith(scheme.lower()) else f"{scheme}: {label}"


def word_span_ref(first_ref: str, first_word: int, last_ref: str, last_word: int) -> str:
    if first_ref != last_ref:
        return f"{first_ref}@{first_word} - {last_ref}@{last_word}"
    return f"{first_ref}@{first_word}" + (f"-{last_word}" if last_word != first_word else "")


def describe_match(quote: str, matched: list[tuple[str, int, str]], segments: list[dict], lang: str,
                   counted_in: str) -> dict:
    (first_ref, first_word, _), (last_ref, last_word, _) = matched[0], matched[-1]
    words_by_ref = {s["ref"]: display_words(s.get(lang, "")) for s in segments}
    original = []
    for ref in dict.fromkeys(ref for ref, _, _ in matched):
        start = first_word if ref == first_ref else 1
        end = last_word if ref == last_ref else len(words_by_ref[ref])
        original += words_by_ref[ref][start - 1:end]
    literal = search_words(quote) == [word for _, _, word in matched]
    return {
        "match": ("exact" if literal else PREFIX_VARIANT_MATCH) + (", spans segments" if first_ref != last_ref else ""),
        "at": first_ref, "cite_as": word_span_ref(first_ref, first_word, last_ref, last_word),
        "matched_words": " ".join(original), "words_counted_in": counted_in,
    }


def best_window_ratio(wanted: list[str], haystack: list[str]) -> float:
    if not wanted or not haystack:
        return 0.0
    width = len(wanted)
    step = max(1, width // 4)
    best = 0.0
    for start in range(0, max(1, len(haystack) - width + 1), step):
        window = haystack[start:start + width]
        best = max(best, difflib.SequenceMatcher(None, wanted, window).ratio())
    return best
