"""Assemble your own corpus from a list: a CSV or TSV file with one row per text, from Project Gutenberg, Standard
Ebooks, Wikisource or your own files. Rows with the same "work" become versions of one work (an original and its translations).

    corpus-mcp build --source collection --option list=my-texts.csv --data data-mine [--option contact=EMAIL]

The list (a header row, then one row per text; columns in any order, case ignored):

    source       gutenberg, standardebooks, wikisource or file                                     (required)
    id           the Gutenberg ebook number, the Standard Ebooks repository or book page             (required)
                 (jane-austen_pride-and-prejudice), the Wikisource page title, or the file's path
                 (relative to the list); a file can be .txt (paragraphs separated by empty lines),
                 .html, or TEI .xml
    lang         the text's language; for Wikisource also which Wikisource (la = la.wikisource.org)
    work         the work it is a version of: rows with the same work are shown side by side (default: its title)
    version      a name for this version (default: from the source, e.g. "Project Gutenberg #1342")
    title, author, date, language, categories, aliases
                 the work's details, when the source's are missing or wrong. date is a year (negative = BCE);
                 language is the language the work was written in (default: the first row's); categories and
                 aliases are separated by ";"

Example:

    source,id,work,lang,version,author,date
    wikisource,Commentarii de Bello Gallico,Gallic War,la,Latin text,Julius Caesar,-50
    gutenberg,10657,Gallic War,en,McDevitte and Bohn,,
    gutenberg,1342,,,,,
    standardebooks,marcus-aurelius_meditations_george-long,Meditations,en,George Long,,
    file,notes/sermon.txt,Sermon on Mercy,en,my transcription,,1650

Wikisource rows need --option contact=EMAIL-OR-URL (Wikimedia asks for it; it is sent only to them).
"""
import csv
import json
import re
from collections import Counter
from pathlib import Path

from genericCorpusMcpBuilder import SimpleCorpus, chapters_from_blocks, html_blocks, language_code, read_tei
from genericCorpusMcpBuilder.sources import gutenberg, standardebooks, wikisource
from genericCorpusMcpBuilder.sources.kit import heading_number, section_labels

SOURCES = ("gutenberg", "standardebooks", "wikisource", "file")


def read_list(path):
    """The rows of the list, each a dict with lower-case column names and stripped values."""
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"no list at {path}: give it with --option list=my-texts.csv")
    with open(path, newline="", encoding="utf-8-sig") as handle:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        rows = []
        for number, row in enumerate(csv.DictReader(handle, delimiter=delimiter), 2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            if not any(row.values()):
                continue
            if row.get("source", "").lower() not in SOURCES or not row.get("id"):
                raise SystemExit(f"{path.name} line {number}: needs a source ({', '.join(SOURCES)}) and an id, "
                                 f"got source={row.get('source')!r} id={row.get('id')!r}")
            row["source"] = row["source"].lower()
            row["line"] = number
            if row["source"] == "file":                 # paths are relative to the list
                row["id"] = str((path.parent / row["id"]).resolve())
            rows.append(row)
    return rows


def fetch(raw, list=None, contact=None, delay=None, mirror=gutenberg.MIRROR, **options):
    raw = Path(raw)
    rows = read_list(list or "")
    if any(row["source"] == "gutenberg" for row in rows):
        gutenberg.fetch_catalog(raw / "gutenberg")
    if any(row["source"] == "wikisource" for row in rows):
        contact = wikisource.require_contact(contact)
    for row in rows:
        if row["source"] == "gutenberg":
            gutenberg.fetch_book(raw / "gutenberg", row["id"], mirror, delay or "2")
        elif row["source"] == "standardebooks":
            standardebooks.fetch_book(raw / "standardebooks", row["id"], delay or "0.5")
        elif row["source"] == "wikisource":
            wikisource.fetch_edition(raw / "wikisource", row.get("lang") or "en", row["id"], contact, delay or "1")
        elif not Path(row["id"]).exists():
            print(f"line {row['line']}: no file {row['id']}", flush=True)
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "collection.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- your own files

def text_file_blocks(text):
    """Paragraphs separated by empty lines; a short line on its own that looks like a heading ("CHAPTER IV",
    "Book Two", or all capitals) starts a chapter."""
    blocks = []
    for paragraph in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        looks_like_heading = len(paragraph) <= 80 and (heading_number(paragraph) is not None or
                                                       (paragraph.isupper() and len(paragraph.split()) <= 8))
        blocks.append(("h2" if looks_like_heading else "p", paragraph))
    return blocks


def read_file(row):
    path = Path(row["id"])
    if not path.exists():
        return None
    if path.suffix.lower() == ".xml":
        tei = read_tei(path, report=lambda message: print(message, flush=True))
        if not tei or not tei.segments:
            return None
        return {"title": tei.title or path.stem, "author": tei.author or None,
                "language": language_code(tei.language or row.get("lang") or "en"), "levels": tei.level_names,
                "segments": tei.segments, "version": path.name, "source": path.name, "license": tei.license or None}
    raw_text = path.read_text(encoding="utf-8")
    blocks = html_blocks(raw_text) if path.suffix.lower() in (".html", ".htm") else text_file_blocks(raw_text)
    has_headings = any(tag.startswith("h") for tag, _ in blocks)
    segments = []
    if has_headings:
        chapters = chapters_from_blocks(blocks)
        for label, (heading, paragraphs) in zip(section_labels([h for h, _ in chapters]), chapters):
            for number, paragraph in enumerate(paragraphs, 1):
                segments.append(([label, str(number)], paragraph))
        levels = ["chapter", "paragraph"]
    else:
        for number, (tag, paragraph) in enumerate(blocks, 1):
            segments.append(([str(number)], paragraph))
        levels = ["paragraph"]
    if not segments:
        return None
    return {"title": path.stem.replace("_", " "), "author": None, "language": language_code(row.get("lang") or "en"),
            "levels": levels, "segments": segments, "version": path.name, "source": path.name, "license": None}


# ---------------------------------------------------------------- converting

def item_for(row, raw, catalog):
    """One row's text, read from what fetch saved (nothing is downloaded here)."""
    if row["source"] == "gutenberg":
        found = gutenberg.book(raw / "gutenberg", row["id"], catalog)
        if found:
            found["year"] = found["years"][0] if found.get("years") else None
        return found
    if row["source"] == "standardebooks":
        return standardebooks.book(raw / "standardebooks", row["id"])
    if row["source"] == "wikisource":
        return wikisource.edition(raw / "wikisource", row.get("lang") or "en", row["id"])
    return read_file(row)


def split_list(value):
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def convert(raw, out, **options):
    raw = Path(raw)
    rows = json.loads((raw / "collection.json").read_text(encoding="utf-8"))
    catalog = gutenberg.read_catalog(raw / "gutenberg") if (raw / "gutenberg" / "pg_catalog.csv").exists() else {}

    # read every row, then group the rows by work
    groups = {}
    for row in rows:
        item = item_for(row, raw, catalog)
        if item is None:
            print(f"line {row['line']}: skipped {row['source']} {row['id']!r} (not downloaded or no text)", flush=True)
            continue
        key = row.get("work") or row.get("title") or item["title"]
        groups.setdefault(key, []).append((row, item))

    languages = Counter((rows_items[0][0].get("language") and language_code(rows_items[0][0]["language"]))
                        or rows_items[0][1]["language"] for rows_items in groups.values())
    sources = sorted({row["source"] for row in rows})
    corpus = SimpleCorpus(out, id="collection", name="Collection", original_language=languages.most_common(1)[0][0]
                          if languages else "en", source="assembled from " + ", ".join(sources),
                          license="per version (see each version's license)")

    for key, members in groups.items():
        def first(column, item_key=None):
            """The first row that sets this column, else the first source that knows it."""
            for row, item in members:
                if row.get(column):
                    return row[column]
            for row, item in members:
                if item.get(item_key or column):
                    return item[item_key or column]
            return None

        date = first("date", "year")
        language = language_code(first("language") or members[0][1]["language"])
        levels = max((item["levels"] for _, item in members), key=len)
        aliases = []
        for row, item in members:
            aliases += split_list(row.get("aliases"))
            if item["title"] != key:
                aliases.append(item["title"])
        title = next((row["title"] for row, _ in members if row.get("title")), key)   # the list's name wins
        if title != key:
            aliases.append(key)
        work = corpus.work(title, levels=levels, language=language, author=first("author"),
                           date=int(date) if str(date or "").lstrip("-").isdigit() else None,
                           categories=split_list(first("categories")) or None, aliases=aliases)
        for row, item in members:
            lang = language_code(row.get("lang")) if row.get("lang") else item["language"]
            if row["source"] == "gutenberg":
                gutenberg.name_chapters(corpus, work, item["headings"])
            elif row["source"] == "standardebooks":
                standardebooks.name_divisions(corpus, work, ".", item["headings"])
            extra = {"license": item["license"]} if item.get("license") else {}
            corpus.text(work, lang, row.get("version") or item["version"], item["segments"], source=item["source"],
                        **extra)
        print(f"{work}: {len(members)} version(s)", flush=True)

    corpus.finish(profile={"research": {"group_by": "work"}}, instructions=INSTRUCTIONS)


INSTRUCTIONS = """A collection of texts assembled from Project Gutenberg, Standard Ebooks, Wikisource and other
sources; a work may have several versions (an original and translations), shown side by side.

Answer from the texts, and only from passages you have read with these tools: research_question or search first,
then get_text(ref, context=2) on the passages you rely on, and verify_citations on every quote before you answer.
Quote and cite, name the version you quote (get_text shows it for each line), and keep your interpretation apart
from the text."""
