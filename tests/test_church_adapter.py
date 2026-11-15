"""The church adapter and the USFX reader on small files shaped like the real ones (no network)."""
from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.check import check
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.sources.church.convert import convert
from genericCorpusMcpBuilder.sources.usfx import read_usfx

USFX = """<?xml version="1.0" encoding="UTF-8"?><usfx><book id="JHN"><h>{title}</h><toc level="1">{title}</toc>
<c id="1"/><p><v id="1"/>{v1}<f>a note</f><ve/><v id="2"/>{v2}<ve/></p></book></usfx>"""


def test_usfx_reader_skips_notes_and_headings(tmp_path):
    path = tmp_path / "lat.usfx.xml"
    path.write_text(USFX.format(title="Joannes", v1="In principio erat Verbum", v2="Hoc erat in principio"))
    assert read_usfx(path)["JHN"] == [(["1", "1"], "In principio erat Verbum"), (["1", "2"], "Hoc erat in principio")]


def test_new_testament_in_greek_with_latin_and_english(tmp_path):
    raw = tmp_path / "raw"
    (raw / "sblgnt").mkdir(parents=True)
    (raw / "bibles").mkdir()
    (raw / "sblgnt" / "John.txt").write_text("ΚΑΤΑ ΙΩΑΝΝΗΝ\nJohn 1:1\tἘν ἀρχῇ ἦν ὁ ⸀λόγος, \n"
                                             "John 1:2\tοὗτος ἦν ἐν ἀρχῇ πρὸς τὸν θεόν. \n")
    (raw / "bibles" / "lat-clementine.usfx.xml").write_text(
        USFX.format(title="Joannes", v1="In principio erat Verbum", v2="Hoc erat in principio apud Deum."))
    (raw / "bibles" / "eng-dra.usfx.xml").write_text(
        USFX.format(title="John", v1="In the beginning was the Word", v2="The same was in the beginning with God."))
    convert(raw, tmp_path / "corpus")
    assert check(tmp_path / "corpus") == 0
    build(tmp_path / "corpus", tmp_path / "corpus.db")
    corpus = Corpus(str(tmp_path / "corpus.db"))
    segment = corpus.get_text("Jn 1:1")["segments"][0]
    assert segment["grc"] == "Ἐν ἀρχῇ ἦν ὁ λόγος,"                     # apparatus sign removed
    assert segment["lat_version"] == "Clementine Vulgate" and segment["en"] == "In the beginning was the Word"
    assert corpus.original_of("John") == "grc" and "SBLGNT" in corpus.get_text("John 1:1")["versions"][0]["source"]
    report = corpus.verify_citations([{"ref": "John 1:1", "quote": "erat Verbum"}])["results"][0]
    assert report["quote_found"] and report["words_counted_in"] == "Latin version"
