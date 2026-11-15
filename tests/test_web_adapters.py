"""The Gutenberg and Wikisource adapters on saved pages shaped like the real ones (no network)."""
import json

from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.check import check
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.sources import gutenberg, wikisource
from genericCorpusMcpBuilder.sources.kit import heading_number, section_labels


def built(tmp_path, convert):
    convert(tmp_path / "raw", tmp_path / "corpus")
    assert check(tmp_path / "corpus") == 0
    build(tmp_path / "corpus", tmp_path / "corpus.db")
    return Corpus(str(tmp_path / "corpus.db"))


def test_chapter_labels_from_headings():
    assert section_labels(["PREFACE.", "Chapter I.", "CHAPTER II.", "CHAPTER XLIV."]) == ["PREFACE", "1", "2", "44"]
    assert section_labels(["THE FIRST BOOK", "THE SECOND BOOK", "HIS FIRST BOOK"]) == ["1", "2", "HIS FIRST BOOK"]
    assert section_labels(["BOOK ONE", "CHAPTER I", "BOOK TWO", "CHAPTER I"]) == ["1", "2", "3", "4"]  # repeats
    assert heading_number("Mild Days") is None and heading_number("III. The Storm") == 3


GUTENBERG_HTML = """<html><body><section class="pg-boilerplate" id="pg-header"><h2>The Project Gutenberg eBook of X</h2>
<p>This ebook is for the use of anyone anywhere.</p></section>
<h1>PRIDE AND PREJUDICE</h1><p>By Jane Austen</p>
<div class="chapter"><h2><a id="c1"></a><span class="pagenum">{1}</span>Chapter I.</h2>
<p><img alt="I" src="i.jpg">T is a truth universally acknowledged, that a single man in possession of a good fortune,
must be in want of a wife.</p><div class="figcenter"><img alt="a picture" src="p.jpg"><p class="caption">Mr. Bennet</p></div>
<p>However little known the feelings or views of such a man may be on his first entering a neighbourhood.</p></div>
<div class="chapter"><h2>CHAPTER II.</h2><p>Mr. Bennet was among the earliest of those who waited on Mr. Bingley.</p></div>
<h3>Paragraphs with First Lines</h3><p>IT is a truth universally</p>
<footer class="pg-boilerplate" id="pg-footer"><h2>THE FULL PROJECT GUTENBERG LICENSE</h2><p>legal text</p></footer>
</body></html>"""


def test_gutenberg(tmp_path):
    raw = tmp_path / "raw"
    (raw / "books").mkdir(parents=True)
    (raw / "books" / "1342.html").write_text(GUTENBERG_HTML)
    (raw / "pg_catalog.csv").write_text("Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves\n"
                                        '1342,Text,1998-06-01,Pride and Prejudice,en,"Austen, Jane, 1775-1817",'
                                        "England -- Fiction,PR,Best Books Ever Listings\n")
    corpus = built(tmp_path, gutenberg.convert)
    first = corpus.get_text("Pride and Prejudice 1.1")["segments"][0]["en"]
    assert first.startswith("IT is a truth universally acknowledged")              # drop cap kept
    assert corpus.get_text("Pride and Prejudice 1")["segments"][-1]["en"].startswith("However little")
    assert corpus.resolve("PG 1342 2.1").ref == "Pride and Prejudice 2.1"
    texts = " ".join(s["en"] for s in corpus.get_text("Pride and Prejudice")["segments"])
    assert "LICENSE" not in texts and "Mr. Bennet\n" not in texts and "{1}" not in texts and "First Lines" not in texts
    assert corpus.work("Pride and Prejudice")["authors"] == ["Jane Austen"]


def wiki_page(title, body):
    return {"title": title, "html": f'<div class="mw-parser-output">{body}</div>'}


HEADER = '<div class="ws-header ws-noexport"><a href="/wiki/Author:Jane_Austen">Jane Austen</a> Volume I</div>'


def test_wikisource(tmp_path):
    raw = tmp_path / "raw"
    pages = {
        "Pride and Prejudice": wiki_page("Pride and Prejudice", '<ul><li><a href="/wiki/Pride_and_Prejudice_(1813)">1813</a>'
                                         '</li><li><a href="/wiki/Pride_and_Prejudice_(1817)">1817</a></li></ul>'),
        "Pride and Prejudice (1813)": wiki_page("Pride and Prejudice (1813)", HEADER +
            '<a href="/wiki/Pride_and_Prejudice_(1813)/Volume_1">Volume 1</a>'
            '<a href="/wiki/Pride_and_Prejudice_(1813)/Volume_1/Chapter_1">Chapter 1</a>'
            '<a href="/wiki/Pride_and_Prejudice_(1813)/Volume_1/Chapter_2">Chapter 2</a>'),
        "Pride and Prejudice (1813)/Volume 1/Chapter 1": wiki_page("x", HEADER + '<h2>CHAPTER I.</h2>'
            '<p><span class="pagenum ws-pagenum">1</span>It is a truth universally acknowledged<sup class="reference">'
            '<a href="#n1">[1]</a></sup>, that a single man.</p><p>However little known.</p>'),
        "Pride and Prejudice (1813)/Volume 1/Chapter 2": wiki_page("x", "<p>Mr. Bennet was among the earliest.</p>"),
    }
    for title, answer in pages.items():
        path = wikisource.page_file(raw, "en", title)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer))
    (raw / "works.json").write_text(json.dumps({"lang": "en", "works": ["Pride and Prejudice (1813)", "Pride and Prejudice"]}))
    corpus = built(tmp_path, wikisource.convert)
    segment = corpus.get_text("Pride and Prejudice (1813) 1.1.1")["segments"][0]["en"]
    assert segment == "It is a truth universally acknowledged, that a single man."           # no page number, no [1]
    assert corpus.resolve("Pride and Prejudice 1.2.1").ref == "Pride and Prejudice (1813) 1.2.1"
    assert corpus.work("Pride and Prejudice (1813)")["authors"] == ["Jane Austen"]
    assert corpus.work("Pride and Prejudice (1813)")["comp_date"] == 1813
    assert [r[0] for r in corpus.db.execute("SELECT title FROM works")] == ["Pride and Prejudice (1813)"]  # versions page skipped


def test_wikisource_needs_contact(tmp_path, monkeypatch):
    import pytest
    monkeypatch.delenv("CORPUS_MCP_CONTACT", raising=False)
    with pytest.raises(SystemExit, match="contact"):
        wikisource.fetch(tmp_path, works="Pride and Prejudice (1813)")
