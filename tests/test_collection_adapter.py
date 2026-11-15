"""Assembling a corpus from a list: Wikisource and Gutenberg versions of one work side by side, plus local files."""
import json

from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.check import check
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.sources import collection, wikisource

LATIN = {"title": "Commentarii de Bello Gallico", "html": (
    '<div class="ws-header ws-noexport"><a href="/wiki/Author:Gaius_Iulius_Caesar">Caesar</a></div>'
    '<a href="/wiki/Commentarii_de_Bello_Gallico/Liber_I">I</a><a href="/wiki/Commentarii_de_Bello_Gallico/Liber_II">II</a>')}
LIBER = {"Liber I": "<p>Gallia est omnis divisa in partes tres.</p>", "Liber II": "<p>Cum esset Caesar in citeriore Gallia.</p>"}
ENGLISH = """<html><body><h2>BOOK I</h2><p>All Gaul is divided into three parts.</p>
<h2>BOOK II</h2><p>While Caesar was in winter quarters in Hither Gaul.</p></body></html>"""


def test_a_list_of_sources_becomes_one_corpus(tmp_path):
    raw = tmp_path / "raw"
    for title, answer in [("Commentarii de Bello Gallico", LATIN)] + [
            (f"Commentarii de Bello Gallico/{name}", {"title": name, "html": body}) for name, body in LIBER.items()]:
        path = wikisource.page_file(raw / "wikisource", "la", title)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer))
    (raw / "gutenberg" / "books").mkdir(parents=True)
    (raw / "gutenberg" / "books" / "10657.html").write_text(ENGLISH)
    (raw / "gutenberg" / "pg_catalog.csv").write_text(
        "Text#,Type,Issued,Title,Language,Authors,Subjects,LoCC,Bookshelves\n"
        '10657,Text,2004-01-01,The Gallic Wars,en,"Caesar, Julius, 100 BCE-44 BCE",Gaul -- History,DC,\n')
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "sermon.txt").write_text("CHAPTER I\n\nMercy is twice blest.\n\nIt blesseth him that gives.\n\n"
                                                    "CHAPTER II\n\nAnd him that takes.\n")
    (tmp_path / "notes" / "fragments.txt").write_text("A first fragment.\n\nA second fragment.\n")
    (tmp_path / "list.csv").write_text(
        "source,id,work,lang,version,author,date,aliases\n"
        "wikisource,Commentarii de Bello Gallico,Gallic War,la,Latin text,Julius Caesar,-50,BG;De Bello Gallico\n"
        "gutenberg,10657,Gallic War,en,McDevitte and Bohn,,,\n"
        "file,notes/sermon.txt,Sermon on Mercy,en,my transcription,,1650,\n"
        "file,notes/fragments.txt,,en,,,,\n")

    collection.fetch(raw, list=str(tmp_path / "list.csv"), contact="test@example.org")   # all already saved
    collection.convert(raw, tmp_path / "corpus")
    assert check(tmp_path / "corpus") == 0
    build(tmp_path / "corpus", tmp_path / "corpus.db")
    corpus = Corpus(str(tmp_path / "corpus.db"))

    segment = corpus.get_text("Gallic War 1.1")["segments"][0]
    assert segment["lat"] == "Gallia est omnis divisa in partes tres." and segment["en"].startswith("All Gaul")
    assert segment["en_version"] == "McDevitte and Bohn" and corpus.original_of("Gallic War") == "lat"
    assert corpus.resolve("BG 2.1").ref == "Gallic War 2.1"
    work = corpus.work("Gallic War")
    assert work["authors"] == ["Julius Caesar"] and work["comp_date"] == -50
    assert corpus.get_text("Sermon on Mercy 1.2")["segments"][0]["en"] == "It blesseth him that gives."
    assert corpus.get_text("fragments 2")["segments"][0]["en"] == "A second fragment."
