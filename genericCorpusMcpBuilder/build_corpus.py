"""Corpus directory (docs/corpus-format.md) -> corpus.db. Knows nothing about any particular source."""
import datetime
import hashlib
import json
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from genericCorpusMcpBuilder.citations import (DEFAULT_SEPARATOR, LABEL_SEPARATOR, Locator, cite_key, level_kinds, order_key)
from genericCorpusMcpBuilder.corpus_format import iter_jsonl, optional_records, read_manifest, text_files
from genericCorpusMcpBuilder.profile import SEARCH_MODES, Profile
from genericCorpusMcpBuilder.text import latin_spelling, normalize_for_search, reversed_index_form, spaced_characters

LONG_LINK_SPAN = 500
LINK_BATCH = 100000
UNRANKED = 1000000
PROFILE_FILES = {"profile": "profile.json", "instructions": "instructions.md", "answer_prompt": "answer_prompt.md"}

SCHEMA_SQL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE works(
    title TEXT PRIMARY KEY, he_title TEXT, categories TEXT, category TEXT, authors TEXT,
    comp_date INTEGER, comp_date_end INTEGER, comp_date_text TEXT, era TEXT, dependence TEXT,
    collective_title TEXT, base_text_titles TEXT, description TEXT, language TEXT);
CREATE TABLE aliases(alias TEXT, title TEXT, PRIMARY KEY(alias, title)) WITHOUT ROWID;
CREATE TABLE nodes(base TEXT PRIMARY KEY, title TEXT, he_base TEXT, position INTEGER, levels TEXT, separator TEXT,
                   section_names TEXT, joiner TEXT);
CREATE TABLE versions(
    id INTEGER PRIMARY KEY, title TEXT, lang TEXT, version_title TEXT, version_source TEXT,
    sources TEXT, segment_count INTEGER DEFAULT 0, is_primary INTEGER DEFAULT 0, priority INTEGER, rank INTEGER,
    license TEXT);
CREATE TABLE staging.staged_segments(
    id INTEGER PRIMARY KEY, version_id INTEGER, base TEXT, cite TEXT, cite_key TEXT, order_key TEXT, ref TEXT,
    text TEXT, notes TEXT, text_hash BLOB);
"""

UNDECLARED_PARTS_SQL = f"""
INSERT INTO nodes(base, title, he_base, position, levels, separator, section_names, joiner)
    SELECT s.base, min(v.title), '', 0, '[]', '{DEFAULT_SEPARATOR}', '[]', ' '
    FROM staging.staged_segments s JOIN versions v ON v.id = s.version_id LEFT JOIN nodes n ON n.base = s.base
    WHERE n.base IS NULL GROUP BY s.base;
"""

DERIVED_SQL = """
CREATE TABLE locations(id INTEGER PRIMARY KEY, base TEXT, cite TEXT, cite_key TEXT, order_key TEXT, ref TEXT,
                       title TEXT);
INSERT INTO locations(base, cite, cite_key, order_key, ref, title)
    SELECT s.base, min(s.cite), s.cite_key, min(s.order_key), min(s.ref), n.title
    FROM staging.staged_segments s JOIN nodes n ON n.base = s.base
    GROUP BY s.base, s.cite_key
    ORDER BY n.title, n.position, s.base, min(s.order_key);
CREATE UNIQUE INDEX locations_by_cite ON locations(base, cite_key);
CREATE INDEX locations_by_order ON locations(base, order_key);
CREATE TABLE segments(id INTEGER PRIMARY KEY, version_id INTEGER, location_id INTEGER, text TEXT, notes TEXT,
                      text_hash BLOB);
INSERT INTO segments
    SELECT s.id, s.version_id, l.id, s.text, s.notes, s.text_hash
    FROM staging.staged_segments s JOIN locations l ON l.base = s.base AND l.cite_key = s.cite_key ORDER BY s.id;
CREATE INDEX segments_by_version_location ON segments(version_id, location_id);
CREATE INDEX segments_by_location ON segments(location_id);
CREATE TABLE links(
    id INTEGER PRIMARY KEY, ref1 TEXT, ref2 TEXT, link_type TEXT, category1 TEXT, category2 TEXT,
    title1 TEXT, title2 TEXT, start1 INTEGER, end1 INTEGER, start2 INTEGER, end2 INTEGER);
CREATE TABLE alt_refs(
    id INTEGER PRIMARY KEY, title TEXT, scheme TEXT, label TEXT, he_label TEXT, ref TEXT,
    loc_start INTEGER, loc_end INTEGER);
CREATE TABLE alt_labels(title TEXT, label TEXT, alt_ref_id INTEGER);
"""

INDEX_SQL = f"""
CREATE INDEX links_from_1 ON links(start1);
CREATE INDEX links_from_2 ON links(start2);
CREATE INDEX long_links_from_1 ON links(start1) WHERE end1 - start1 > {LONG_LINK_SPAN};
CREATE INDEX long_links_from_2 ON links(start2) WHERE end2 - start2 > {LONG_LINK_SPAN};
CREATE INDEX aliases_by_title ON aliases(title);
CREATE INDEX alt_refs_by_location ON alt_refs(title, loc_start);
CREATE INDEX alt_labels_by_label ON alt_labels(title, label);
"""

TOPICS_SQL = """
CREATE TABLE topics(slug TEXT PRIMARY KEY, en TEXT, he TEXT, description TEXT, source_count INTEGER);
CREATE TABLE topic_sources(slug TEXT, rank INTEGER, ref TEXT, title TEXT, loc_start INTEGER, loc_end INTEGER,
                           relevance REAL);
CREATE TABLE topic_links(slug TEXT, related TEXT, relation TEXT);
CREATE VIRTUAL TABLE topics_fts USING fts5(slug UNINDEXED, names, description,
                                           tokenize='porter unicode61 remove_diacritics 2');
"""


def content_hash(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


def index_form(text: str, mode: str) -> str:
    normalized = normalize_for_search(text)
    if mode == "hebrew":
        return reversed_index_form(normalized)
    if mode == "cjk":
        return spaced_characters(normalized)
    return latin_spelling(normalized) if mode == "latin" else normalized


def read_profile(corpus: Path, manifest: dict) -> dict:
    """profile.json, instructions.md and answer_prompt.md as meta values (all optional)."""
    stored = {key: (corpus / name).read_text() for key, name in PROFILE_FILES.items() if (corpus / name).exists()}
    stored["profile"] = json.dumps(json.loads(stored.get("profile", "{}")), ensure_ascii=False)
    stored["languages"] = json.dumps(manifest.get("languages") or [])
    stored["original_language"] = default_language(manifest)
    return stored


def default_language(manifest: dict) -> str:
    return manifest.get("original_language") or (manifest.get("languages") or ["en"])[0]


def update_profile(db_path: Path, source: Path) -> None:
    """Replace a built corpus's profile.json, instructions.md and answer_prompt.md (from the folder source) without
    rebuilding: the text, and so the build id, stay the same. A file missing from source removes that setting."""
    from genericCorpusMcpBuilder.check import Report, check_profile
    db = sqlite3.connect(db_path)
    meta = dict(db.execute("SELECT key, value FROM meta"))
    report = Report()
    check_profile(Path(source), json.loads(meta.get("languages") or "[]"), report)
    if report.print():
        raise SystemExit(f"{source}/profile.json has errors (above); nothing changed")
    for key, name in PROFILE_FILES.items():
        path = Path(source) / name
        if path.exists():
            value = path.read_text()
            if key == "profile":
                value = json.dumps(json.loads(value), ensure_ascii=False)
            db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
            print(f"updated {name}", flush=True)
        elif key in meta:
            db.execute("DELETE FROM meta WHERE key = ?", (key,))
            print(f"removed {name} (not in {source}; the default applies)", flush=True)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('profile_updated_at', ?)",
               (datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),))
    db.commit()
    db.close()


def create_search_tables(db: sqlite3.Connection, profile: Profile) -> None:
    for lang in profile.languages:
        mode = profile.search_mode(lang)
        db.execute(f"CREATE VIRTUAL TABLE {profile.fts_table(lang)} USING fts5(body, content='', {SEARCH_MODES[mode]})")


# Works

def load_works(corpus: Path, db: sqlite3.Connection, default_language: str) -> dict[str, tuple]:
    """works.jsonl; a work's "language" (what it was written in) defaults to corpus.json original_language."""
    parts = {}
    for work in iter_jsonl(corpus / "works.jsonl"):
        title, date = work["title"], work.get("date") or [None]
        titles = work.get("titles") or {}
        categories = work.get("categories") or []
        db.execute("INSERT OR REPLACE INTO works VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            title, titles.get("he"), json.dumps(categories), (categories or [""])[0],
            json.dumps(work.get("authors") or []), date[0], date[-1], work.get("date_text"), work.get("era"),
            work.get("dependence"), work.get("collective_title"), json.dumps(work.get("comments_on") or []),
            work.get("description"), work.get("language") or default_language))
        aliases = {a.strip().lower() for a in [title, *titles.values(), *(work.get("aliases") or [])] if a and a.strip()}
        db.executemany("INSERT OR IGNORE INTO aliases VALUES (?,?)", [(a, title) for a in aliases])
        for position, part in enumerate(work.get("parts") or [{"name": title}]):
            levels = part.get("levels") or []
            separator = part.get("separator") or DEFAULT_SEPARATOR
            joiner = part.get("joiner") or " "            # between the name and the address: "MN 1:3.4" has ":"
            db.execute("INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?,?)", (
                part["name"], title, (part.get("titles") or {}).get("he", ""), position, json.dumps(levels),
                separator, json.dumps([level.get("name") for level in levels]), joiner))
            parts[part["name"]] = (levels, separator, joiner)
    db.commit()
    return parts


# Texts

PARTS: dict[str, tuple] = {}
SEARCH_MODE_OF: dict[str, str] = {}


def share_parts(parts: dict[str, tuple], modes: dict[str, str]) -> None:
    PARTS.update(parts)
    SEARCH_MODE_OF.update(modes)


def parse_text_file(path: str) -> tuple[dict, list[tuple]]:
    records = iter_jsonl(Path(path))
    version = next(records)["version"]
    lang, rows, seen, duplicates = version["lang"], [], set(), 0
    mode = SEARCH_MODE_OF.get(lang, "plain")
    for segment in records:
        base, labels, text = segment["part"], [str(label) for label in segment["cite"]], segment["text"]
        if (base, cite_key(labels)) in seen:          # a citation is unique within a version: keep the first
            duplicates += 1
            continue
        seen.add((base, cite_key(labels)))
        levels, separator, joiner = PARTS.get(base, ([], DEFAULT_SEPARATOR, " "))
        kinds = level_kinds(levels, len(labels))
        ref = f"{base}{joiner}{separator.join(labels)}" if labels else base
        rows.append((base, labels, kinds, LABEL_SEPARATOR.join(labels), cite_key(labels), order_key(labels, kinds),
                     ref, text, segment.get("notes") or "", content_hash(text), index_form(text, mode)))
    version["duplicates"] = duplicates
    return version, rows


class LabelRanks:
    """First-seen order of labels in levels that are not numbered (see order_key)."""

    def __init__(self):
        self.rank, self.next_rank = {}, {}

    def of(self, base: str, path: tuple) -> int:
        key = (base, tuple(label.lower() for label in path))
        if key not in self.rank:
            parent = key[:1] + (key[1][:-1],)
            self.rank[key] = self.next_rank.get(parent, 0)
            self.next_rank[parent] = self.rank[key] + 1
        return self.rank[key]


def load_texts(corpus: Path, db: sqlite3.Connection, parts: dict[str, tuple], profile: Profile) -> None:
    files = [str(p) for p in text_files(corpus)]
    ranks, next_segment_id = LabelRanks(), 1
    modes = {lang: profile.search_mode(lang) for lang in profile.languages}
    with ProcessPoolExecutor(initializer=share_parts, initargs=(parts, modes)) as pool:
        for done, (version, rows) in enumerate(pool.map(parse_text_file, files, chunksize=4), 1):
            if version["duplicates"]:
                print(f"{Path(files[done - 1]).name}: dropped {version['duplicates']} segments whose citation "
                      "repeats an earlier one in the same version", flush=True)
            version_id = db.execute(
                "INSERT INTO versions(title, lang, version_title, version_source, sources, segment_count, rank, license) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (version["work"], version["lang"], version.get("title"), version.get("source"),
                 json.dumps(version.get("merged_from") or []), len(rows),
                 version.get("rank"), version.get("license"))).lastrowid
            ids = range(next_segment_id, next_segment_id + len(rows))
            next_segment_id += len(rows)
            db.executemany("INSERT INTO staging.staged_segments VALUES (?,?,?,?,?,?,?,?,?,?)", [
                (segment_id, version_id, base, cite, key,
                 order if order is not None else order_key(labels, kinds, lambda path, b=base: ranks.of(b, path)),
                 ref, text, notes, digest)
                for segment_id, (base, labels, kinds, cite, key, order, ref, text, notes, digest, _)
                in zip(ids, rows)])
            if version["lang"] not in profile.languages:
                raise SystemExit(f"{files[done - 1]}: language {version['lang']!r} is not in corpus.json languages")
            db.executemany(f"INSERT INTO {profile.fts_table(version['lang'])}(rowid, body) VALUES (?,?)",
                           [(segment_id, row[-1]) for segment_id, row in zip(ids, rows)])
            if done % 500 == 0:
                db.commit()
                print(f"texts {done}/{len(files)}", flush=True)
    db.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY title, lang
                                          ORDER BY coalesce(rank, ?), segment_count DESC, id) AS position
            FROM versions)
        UPDATE versions SET priority = ranked.position, is_primary = (ranked.position = 1)
        FROM ranked WHERE ranked.id = versions.id""", (UNRANKED,))
    db.executescript(UNDECLARED_PARTS_SQL)
    db.commit()


# Links, other citation systems, topics

def load_links(corpus: Path, db: sqlite3.Connection, locator: Locator) -> None:
    categories = dict(db.execute("SELECT title, category FROM works"))
    loaded = unresolved = 0
    batch = []
    for link in optional_records(corpus, "links"):
        one, two = locator.resolve(link["from"]), locator.resolve(link["to"])
        if not one or not two or one.loc_start is None or two.loc_start is None:
            unresolved += 1
            continue
        batch.append((link["from"], link["to"], link.get("type") or "", categories.get(one.part.title),
                      categories.get(two.part.title), one.part.title, two.part.title,
                      one.loc_start, one.loc_end, two.loc_start, two.loc_end))
        if len(batch) == LINK_BATCH:
            loaded += flush_links(db, batch)
    loaded += flush_links(db, batch)
    print(f"links {loaded}; skipped (an end is not in the corpus): {unresolved}", flush=True)


def flush_links(db: sqlite3.Connection, batch: list) -> int:
    count = len(batch)
    db.executemany("INSERT INTO links(ref1, ref2, link_type, category1, category2, title1, title2, "
                   "start1, end1, start2, end2) VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
    db.commit()
    batch.clear()
    if count:
        print(f"links +{count}", flush=True)
    return count


def load_citations(corpus: Path, db: sqlite3.Connection, locator: Locator) -> None:
    loaded = 0
    for entry in optional_records(corpus, "citations"):
        labels = entry.get("labels") or []
        resolved = locator.resolve(entry["ref"])
        if not labels or not resolved or resolved.loc_start is None:
            continue
        he_label = (entry.get("titles") or {}).get("he") or ""
        alt_id = db.execute(
            "INSERT INTO alt_refs(title, scheme, label, he_label, ref, loc_start, loc_end) VALUES (?,?,?,?,?,?,?)",
            (entry["work"], entry["scheme"], labels[0], he_label, entry["ref"], resolved.loc_start,
             resolved.loc_end)).lastrowid
        names = {label.lower() for label in labels} | ({he_label.lower()} if he_label else set())
        rows = [(entry["work"], name, alt_id) for name in names]
        rows += [("", name.lower(), alt_id) for name in entry.get("global_labels") or []]
        db.executemany("INSERT INTO alt_labels VALUES (?,?,?)", rows)
        loaded += 1
    db.commit()
    print(f"alternative citation entries: {loaded}", flush=True)


def load_topics(corpus: Path, db: sqlite3.Connection, locator: Locator) -> None:
    if not (corpus / "topics.jsonl").exists():
        return
    db.executescript(TOPICS_SQL)
    loaded = 0
    for topic in iter_jsonl(corpus / "topics.jsonl"):
        slug, sources, name = topic["slug"], topic.get("sources") or [], topic.get("name") or {}
        description = topic.get("description") or ""
        db.execute("INSERT INTO topics VALUES (?,?,?,?,?)", (slug, name.get("en"), name.get("he"), description,
                                                            len(sources)))
        db.execute("INSERT INTO topics_fts VALUES (?,?,?)", (slug, " ; ".join(topic.get("names") or []), description))
        rows = []
        for rank, source in enumerate(sources, 1):
            resolved = locator.resolve(source["ref"])
            if resolved and resolved.loc_start is not None:
                rows.append((slug, rank, source["ref"], resolved.part.title, resolved.loc_start, resolved.loc_end,
                             source.get("relevance") or 0.0))
        db.executemany("INSERT INTO topic_sources VALUES (?,?,?,?,?,?,?)", rows)
        db.executemany("INSERT INTO topic_links VALUES (?,?,?)",
                       [(slug, link["topic"], link.get("relation") or "") for link in topic.get("links") or []])
        loaded += 1
    db.executescript("CREATE INDEX topic_sources_by_slug ON topic_sources(slug, rank);"
                     "CREATE INDEX topic_sources_by_location ON topic_sources(loc_start);"
                     "CREATE INDEX topic_links_by_slug ON topic_links(slug);")
    db.commit()
    print(f"topics: {loaded}", flush=True)


def code_fingerprint() -> str:
    digest = hashlib.blake2b(digest_size=16)
    for source in sorted(Path(__file__).parent.rglob("*.py")):
        digest.update(source.read_bytes())
    return digest.hexdigest()


def content_fingerprint(db: sqlite3.Connection) -> str:
    """Every version and the hash of every segment's text, in order: two different corpora never share a build id."""
    digest = hashlib.blake2b(digest_size=16)
    for row in db.execute("SELECT title, lang, version_title FROM versions ORDER BY id"):
        digest.update("|".join(str(v) for v in row).encode())
    for (text_hash,) in db.execute("SELECT text_hash FROM segments ORDER BY id"):
        digest.update(text_hash)
    return digest.hexdigest()


def write_manifest(manifest: dict, stored_profile: dict, db: sqlite3.Connection) -> None:
    exported, version = manifest.get("source_exported_at", "unknown"), manifest.get("source_version", "unknown")
    code = code_fingerprint()
    build_id = hashlib.blake2b(f"{manifest.get('id', '')}|{exported}|{version}|{code}|{content_fingerprint(db)}".encode(),
                               digest_size=6).hexdigest()
    counts = {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
              for name in ("works", "versions", "locations", "segments", "links", "alt_refs", "topics")
              if db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone()}
    db.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
        ("build_id", build_id), ("corpus_id", manifest.get("id", "")), ("corpus_name", manifest.get("name", "")),
        ("source", manifest.get("source", "")), ("source_exported_at", exported), ("source_version", version),
        ("license", manifest.get("license", "")), ("original_language", default_language(manifest)),
        ("built_at", datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")),
        ("code_fingerprint", code), ("content_fingerprint", content_fingerprint(db)), ("hash_algorithm", "blake2b-128 over segment display text, UTF-8"),
        ("word_numbering", "words are the whitespace-separated tokens of a segment's display text with ** removed, "
                           "numbered from 1 (a Chinese character or kana counts as a word, with any punctuation after it); "
                           "ref@n-m counts words in the text in the work's original language"),
        ("counts", json.dumps(counts)),
        *stored_profile.items(),
    ])
    db.commit()
    print(f"build {build_id}: {counts}", flush=True)


def build(corpus: Path, db_path: Path) -> None:
    corpus, db_path = Path(corpus), Path(db_path)
    manifest = read_manifest(corpus)
    staging = db_path.with_name(db_path.name + ".staging")
    db_path.unlink(missing_ok=True)
    staging.unlink(missing_ok=True)
    db = sqlite3.connect(db_path)
    # segments are staged in a separate file, deleted after use, so corpus.db is left without free pages
    db.execute("ATTACH DATABASE ? AS staging", (str(staging),))
    db.executescript("PRAGMA page_size=16384; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; "
                     "PRAGMA staging.journal_mode=OFF; PRAGMA staging.synchronous=OFF; "
                     "PRAGMA cache_size=-4000000; PRAGMA temp_store=MEMORY;")
    db.executescript(SCHEMA_SQL)
    stored_profile = read_profile(corpus, manifest)
    profile = Profile.from_manifest({**stored_profile, "corpus_name": manifest.get("name")})
    create_search_tables(db, profile)
    parts = load_works(corpus, db, default_language(manifest))
    stored_profile["original_languages"] = json.dumps([language for language, in db.execute(
        "SELECT language FROM works GROUP BY language ORDER BY count(*) DESC, language")])
    print(f"works {db.execute('SELECT count(*) FROM works').fetchone()[0]}, parts {len(parts)}", flush=True)
    load_texts(corpus, db, parts, profile)
    print("deriving locations and segments", flush=True)
    db.executescript(DERIVED_SQL)
    db.commit()
    db.execute("DETACH DATABASE staging")
    staging.unlink()
    locator = Locator(db)
    load_links(corpus, db, locator)
    load_citations(corpus, db, locator)
    load_topics(corpus, db, locator)
    db.executescript(INDEX_SQL)
    print("collecting query planner statistics", flush=True)
    db.execute("ANALYZE")  # without these SQLite scans all links instead of using the partial indexes
    write_manifest(manifest, stored_profile, db)
    db.close()


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
