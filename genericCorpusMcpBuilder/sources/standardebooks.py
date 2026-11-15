"""Standard Ebooks (standardebooks.org): carefully proofread public-domain books, each kept in its own repository on
GitHub (github.com/standardebooks), chosen by the repository's name or the book's web address.

    corpus-mcp build --source standardebooks --data data-se \
        --option books=jane-austen_pride-and-prejudice,marcus-aurelius_meditations_george-long

Options (--option name=value):
    books   comma-separated: repository names (jane-austen_pride-and-prejudice) or book pages
            (https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice)
    delay   seconds between downloads (default 0.5)

Each book's chapter files are read in reading order (the book's content.opf); the file names give the citation
("chapter-1-1-3.xhtml" in War and Peace is book 1, part 1, chapter 3), then the paragraph: `War and Peace 1.1.3.2`.
Title pages, the imprint, the colophon, the table of contents and the endnotes are left out, and so are note markers
and page breaks. Standard Ebooks dedicates its own work to the public domain (CC0); the texts are public domain in the
USA, and may not be elsewhere.
"""
import json
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from genericCorpusMcpBuilder import SimpleCorpus, download, html_blocks, pause
from genericCorpusMcpBuilder.sources.kit import heading_number

FILES = "https://raw.githubusercontent.com/standardebooks/{book}/master/src/epub/"
# spine files that are not the book's text
NOT_TEXT = {"titlepage", "imprint", "halftitlepage", "colophon", "uncopyright", "toc", "loi", "endnotes",
            "copyright-page"}
# inline parts of a page that are not text: note markers, page breaks
SKIP_ATTRIBUTES = {"epub:type": ["noteref", "pagebreak"]}
OPF = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def repository_name(book):
    """'https://standardebooks.org/ebooks/jane-austen/pride-and-prejudice' -> 'jane-austen_pride-and-prejudice'."""
    book = book.strip().rstrip("/")
    if "/ebooks/" in book:
        book = "_".join(book.split("/ebooks/", 1)[1].split("/"))
    return book.rsplit("/", 1)[-1]


# ---------------------------------------------------------------- downloading

def read_opf(path):
    """The book's details and its spine (file names in reading order) from content.opf."""
    root = ElementTree.parse(path).getroot()
    metadata = root.find("opf:metadata", OPF)
    texts = lambda tag: [" ".join((e.text or "").split()) for e in metadata.findall(f"dc:{tag}", OPF)]
    contributors = {e.get("id", ""): " ".join((e.text or "").split()) for e in metadata.findall("dc:contributor", OPF)}
    files = {item.get("id"): item.get("href") for item in root.find("opf:manifest", OPF)}
    spine = [files[ref.get("idref")] for ref in root.find("opf:spine", OPF) if ref.get("idref") in files]
    return {"title": (texts("title") or [""])[0], "authors": texts("creator"),
            "language": (texts("language") or ["en"])[0].split("-")[0].lower(),
            "subjects": texts("subject"), "identifier": (texts("identifier") or [""])[0],
            "translators": [name for key, name in contributors.items() if key.startswith("translator")],
            "spine": spine}


def is_text(href):
    return Path(href).stem not in NOT_TEXT


def fetch_book(raw, book, delay="0.5"):
    """Download one book's content.opf and text files into raw/<book>/ (each once). False if there is no such book."""
    book = repository_name(book)
    folder = Path(raw) / book
    base = FILES.format(book=book)
    if not download(base + "content.opf", folder / "content.opf") and not (folder / "content.opf").exists():
        print("no such book on github.com/standardebooks:", book, flush=True)
        return False
    spine = [href for href in read_opf(folder / "content.opf")["spine"] if is_text(href)]
    for href in spine:
        if download(base + href, folder / href):
            pause(delay)
    print(f"{book}: {len(spine)} files", flush=True)
    return True


def fetch(raw, books=None, delay="0.5", **options):
    raw = Path(raw)
    if not books:
        raise SystemExit("say which books: --option books=jane-austen_pride-and-prejudice (repository names at "
                         "github.com/standardebooks, or book pages at standardebooks.org/ebooks/...)")
    names = [repository_name(b) for b in str(books).split(",") if b.strip()]
    for name in names:
        fetch_book(raw, name, delay)
    (raw / "books.json").write_text(json.dumps(names), encoding="utf-8")


# ---------------------------------------------------------------- converting

def file_labels(href):
    """'text/chapter-1-1-3.xhtml' -> ('chapter', ['1', '1', '3']); 'text/preface.xhtml' -> ('preface', ['preface'])."""
    parts = Path(href).stem.split("-")
    numbers = []
    while parts and parts[-1].isdigit():
        numbers.insert(0, parts.pop())
    word = "-".join(parts) or "part"
    if not numbers:
        return word, [word]
    if word in ("chapter", "book", "part", "volume", "canto", "act", "scene", "letter", "section", "story", "poem",
                "article", "division", "essay", "sermon", "lecture", "meditation", "epistle"):
        return word, numbers
    return word, [word] + numbers                   # "epilogue-1" -> epilogue.1


def level_names(files):
    """Names for the citation levels, from the file names: War and Peace's book-1, part-1-1 and chapter-1-1-1 give
    book, part, chapter."""
    names = {}
    for href in files:
        word, labels = file_labels(href)
        if labels[0] != word:
            names.setdefault(len(labels), word)
    depth = max((len(file_labels(href)[1]) for href in files), default=1)
    return [names.get(level + 1, "part") for level in range(depth)]


def book(raw, name):
    """A downloaded book: its details, and its text as segments (file labels, then the paragraph). None if missing."""
    folder = Path(raw) / repository_name(name)
    if not (folder / "content.opf").exists():
        return None
    info = read_opf(folder / "content.opf")
    files = [href for href in info["spine"] if is_text(href) and (folder / href).exists()]
    segments, headings = [], []
    for href in files:
        word, labels = file_labels(href)
        blocks = html_blocks((folder / href).read_text(encoding="utf-8"), skip_attributes=SKIP_ATTRIBUTES)
        number = 0
        for tag, text in blocks:
            if tag.startswith("h") and number == 0:
                headings.append((labels, text))       # the file's own heading ("Book I", "The Ball")
                continue
            number += 1
            segments.append((labels + [str(number)], text))
    if not segments:
        return None
    translated = " and ".join(info["translators"])
    return {"title": info["title"], "authors": info["authors"], "author": (info["authors"] or [None])[0],
            "language": info["language"],
            "categories": info["subjects"][:3], "levels": level_names(files) + ["paragraph"],
            "segments": segments, "headings": headings,
            "version": "Standard Ebooks" + (f" (tr. {translated})" if translated else ""),
            "source": info["identifier"] or f"https://github.com/standardebooks/{folder.name}",
            "license": "CC0 1.0 (Standard Ebooks); the text is public domain in the USA"}


def name_divisions(corpus, work, separator, headings):
    """Chapters can also be cited by their headings, when the heading is more than a number and only used once."""
    seen = {}
    for labels, heading in headings:
        seen.setdefault(heading.lower(), []).append(labels)
    for labels, heading in headings:
        words = heading.split()
        if len(seen[heading.lower()]) == 1 and not (heading_number(heading) is not None and len(words) <= 2):
            corpus.citation(work, "heading", [heading.rstrip(". ")], f"{work} {separator.join(labels)}")


def convert(raw, out, **options):
    raw = Path(raw)
    names = json.loads((raw / "books.json").read_text(encoding="utf-8"))
    corpus = SimpleCorpus(out, id="standardebooks", name="Standard Ebooks",
                          original_language="en", source="Standard Ebooks (standardebooks.org), via GitHub",
                          license="CC0 1.0 (Standard Ebooks' work); the texts are public domain in the USA, check "
                                  "before use elsewhere")
    for name in names:
        found = book(raw, name)
        if found is None:
            print("skipped", name, "(not downloaded or no text)", flush=True)
            continue
        work = corpus.work(found["title"], levels=found["levels"], language=found["language"],
                           authors=found["authors"], categories=found["categories"] or found["authors"][:1],
                           aliases=[name])
        name_divisions(corpus, work, ".", found["headings"])
        corpus.text(work, found["language"], found["version"], found["segments"], source=found["source"],
                    license=found["license"])
        print(f"{work}: {len(found['segments'])} paragraphs", flush=True)
    corpus.finish(profile={"research": {"group_by": "work"}}, instructions=INSTRUCTIONS)


INSTRUCTIONS = """Books from Standard Ebooks: proofread editions of public-domain literature and other works, one
edition each (translations are named in the version). Passages are cited by the book's divisions and a paragraph
number ("Pride and Prejudice 3.2" = chapter 3, paragraph 2; "War and Peace 1.1.3.2" = book 1, part 1, chapter 3,
paragraph 2); chapters with titles can also be named by them.

Answer from the books, and only from passages you have read with these tools: research_question or search first,
then get_text(ref, context=2) on the passages you rely on, and verify_citations on every quote before you answer.
Quote and cite; say which book and chapter each passage is from, and keep your interpretation apart from the text."""
