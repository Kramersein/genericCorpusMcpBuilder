"""Project Gutenberg (gutenberg.org): public-domain books, chosen by their ebook numbers.

    corpus-mcp build --source gutenberg --option books=1342,2680 --data data-books

Options (--option name=value):
    books    ebook numbers, comma-separated (1342 is Pride and Prejudice: www.gutenberg.org/ebooks/1342)
    mirror   where to download from (default https://gutenberg.pglaf.org, a Project Gutenberg mirror)
    delay    seconds between downloads (default 2)

Project Gutenberg's website is for people; programs must use its catalog files and mirrors, slowly
(www.gutenberg.org/policy/robot_access.html). So this adapter reads the weekly catalog (pg_catalog.csv) for titles,
authors and languages, and downloads each book's HTML from a mirror, 2 seconds apart, once (it is kept in raw/).
The Project Gutenberg License header and footer are removed. The texts are public domain in the USA; they may not
be elsewhere, so check before sharing a built corpus outside the USA.
"""
import csv
import re
from pathlib import Path

from genericCorpusMcpBuilder import SimpleCorpus, chapters_from_blocks, download, html_blocks, language_code, pause
from genericCorpusMcpBuilder.sources.kit import section_labels

CATALOG = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
MIRROR = "https://gutenberg.pglaf.org"
# Parts of the HTML that are not the book: the license header and footer, page numbers, illustrations, tables of
# contents
SKIP_IDS = ["pg-header", "pg-footer", "pg-start-separator", "pg-end-separator"]
SKIP_CLASSES = ["pagenum", "figcenter", "figleft", "figright", "caption", "toc", "pg-boilerplate", "footnotes"]
AUTHOR_YEARS = re.compile(r"(\d{3,4})\??\s*-\s*(\d{3,4})")
# Indexes at the end of many older Project Gutenberg HTML files repeat the first line of every paragraph: stop there
END_MATTER = re.compile(r"first lines|index of (?:first lines|paragraphs)", re.IGNORECASE)


def book_blocks(html):
    """The book's headings and paragraphs, without the license and without a first-lines index at the end."""
    blocks = html_blocks(html, SKIP_IDS, SKIP_CLASSES)
    for position, (tag, text) in enumerate(blocks):
        if tag.startswith("h") and END_MATTER.search(text):
            return blocks[:position]
    return blocks


def fetch_book(raw, number, mirror=MIRROR, delay="2"):
    """Download one book's HTML from the mirror into raw/books/ (once). Returns False if the mirror has none."""
    raw, number = Path(raw), str(number).strip()
    path = raw / "books" / f"{number}.html"
    if path.exists():
        return True
    # the HTML version keeps the chapter headings; older books only have the plain name
    for name in (f"pg{number}-images.html", f"pg{number}.html"):
        if download(f"{mirror}/cache/epub/{number}/{name}", path):
            print("downloaded", number, flush=True)
            pause(delay)
            return True
    print("not found on the mirror:", number, flush=True)
    return False


def fetch_catalog(raw):
    download(CATALOG, Path(raw) / "pg_catalog.csv")


def fetch(raw, books=None, mirror=MIRROR, delay="2", **options):
    raw = Path(raw)
    if not books:
        raise SystemExit("say which books: --option books=1342,2680 (the numbers in www.gutenberg.org/ebooks/1342)")
    fetch_catalog(raw)
    for number in str(books).split(","):
        fetch_book(raw, number, mirror, delay)
    (raw / "books.txt").write_text(str(books), encoding="utf-8")


def read_catalog(raw):
    """Ebook number -> its row of the catalog (Title, Authors, Language, Issued, Subjects, ...)."""
    catalog = {}
    with open(Path(raw) / "pg_catalog.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            catalog[row["Text#"]] = row
    return catalog


def author_and_years(authors):
    """'Austen, Jane, 1775-1817; Chapman, R. W., 1881-1960 [Editor]' -> ('Jane Austen', [1775, 1817])"""
    first = (authors or "").split(";")[0].strip()
    years = AUTHOR_YEARS.search(first)
    name_parts = [part.strip() for part in re.sub(r",?\s*\d.*$", "", first).split(",") if part.strip()]
    if len(name_parts) >= 2 and len(name_parts[0].split()) == 1:      # "Austen, Jane" -> "Jane Austen"
        name = f"{name_parts[1]} {name_parts[0]}"
    else:                                                            # "Marcus Aurelius, Emperor of Rome"
        name = name_parts[0] if name_parts else ""
    return name, ([int(years.group(1)), int(years.group(2))] if years else None)


def book(raw, number, catalog):
    """A downloaded book: its catalog details, and its text as segments (["chapter", "paragraph"] labels) with the
    chapter headings. None if it has no text."""
    raw, number = Path(raw), str(number).strip()
    path = raw / "books" / f"{number}.html"
    if not path.exists():
        return None
    row = catalog.get(number, {})
    chapters = chapters_from_blocks(book_blocks(path.read_text(encoding="utf-8")))
    if not chapters:
        return None
    author, years = author_and_years(row.get("Authors"))
    labels = section_labels([heading for heading, paragraphs in chapters])
    segments = []
    for label, (heading, paragraphs) in zip(labels, chapters):
        for number_in_chapter, paragraph in enumerate(paragraphs, 1):
            segments.append(([label, str(number_in_chapter)], paragraph))
    return {
        "number": number, "title": re.sub(r"\s+", " ", row.get("Title") or f"Gutenberg {number}").strip(),
        "author": author, "years": years,
        "language": language_code((row.get("Language") or "en").split(";")[0]),
        "categories": [s.strip() for s in (row.get("Bookshelves") or "").split(";") if s.strip()][:3],
        "description": (row.get("Subjects") or "").split(";")[0].strip() or None,
        "levels": ["chapter", "paragraph"], "segments": segments,
        "headings": [(label, heading) for label, (heading, paragraphs) in zip(labels, chapters)],
        "version": f"Project Gutenberg #{number}", "source": f"https://www.gutenberg.org/ebooks/{number}",
        "license": "public domain in the USA"}


def name_chapters(corpus, work, headings):
    """Chapters can also be cited by their heading ("Pride and Prejudice Chapter I")."""
    for label, heading in headings:
        if heading and heading.replace(".", " ").strip() != label:
            corpus.citation(work, "chapter heading", [heading.rstrip(". ")], f"{work} {label}")


def convert(raw, out, **options):
    raw = Path(raw)
    catalog = read_catalog(raw)
    corpus = SimpleCorpus(out, id="gutenberg", name="Project Gutenberg books", original_language="en",
                          source="Project Gutenberg (www.gutenberg.org), via a mirror",
                          license="Public domain in the USA (Project Gutenberg License texts removed); "
                                  "check before use outside the USA")
    for path in sorted((raw / "books").glob("*.html"), key=lambda p: int(p.stem)):
        found = book(raw, path.stem, catalog)
        if found is None:
            print("skipped", path.stem, "(no text found)")
            continue
        author, years = found["author"], found["years"]
        work = corpus.work(found["title"], levels=found["levels"], language=found["language"], author=author or None,
                           date=years, date_text=f"author {years[0]}-{years[1]}" if years else None,
                           aliases=[f"Gutenberg {found['number']}", f"PG {found['number']}"],
                           categories=found["categories"] or ([author] if author else None),
                           description=found["description"])
        name_chapters(corpus, work, found["headings"])
        corpus.text(work, found["language"], found["version"], found["segments"], source=found["source"],
                    license=found["license"])
        print(f"{found['title']}: {len(found['headings'])} chapters, {len(found['segments'])} paragraphs", flush=True)

    corpus.finish(profile={"research": {"group_by": "work"}}, instructions=INSTRUCTIONS)


INSTRUCTIONS = """Books from Project Gutenberg: literature and other public-domain works, each in one edition.
Passages are cited as chapter.paragraph ("Pride and Prejudice 3.2"); chapters can also be named by their heading.

Answer from the books, and only from passages you have read with these tools: research_question or search first,
then get_text(ref, context=2) on the passages you rely on, and verify_citations on every quote before you answer.
Quote and cite; say which book and chapter each passage is from, and keep your interpretation apart from the text."""
