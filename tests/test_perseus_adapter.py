"""The Perseus adapter on small TEI files shaped like the real ones (no network)."""
import pytest

from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.sources.perseus.convert import convert

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><encodingDesc><refsDecl n="CTS">{patterns}</refsDecl>
</encodingDesc></teiHeader><text><body><div type="{kind}" n="{urn}">{body}</div></body></text></TEI>"""
PATTERN = '<cRefPattern n="{name}" replacementPattern="#xpath(/tei:TEI/tei:text/tei:body/tei:div{path})"/>'


def tei(kind, urn, levels, body):
    patterns = "".join(PATTERN.format(name=name, path=path) for name, path in levels)
    return HEADER.format(patterns=patterns, kind=kind, urn=urn, body=body)


BOOK = ("book", "/tei:div[@n='$1']")
FILES = {
    "greekLit/data/tlg0012/__cts__.xml": '<textgroup xmlns="http://chs.harvard.edu/xmlns/cts"><groupname xml:lang="eng">Homer</groupname></textgroup>',
    "greekLit/data/tlg0012/tlg001/__cts__.xml": """<work xmlns="http://chs.harvard.edu/xmlns/cts" urn="urn:cts:greekLit:tlg0012.tlg001" xml:lang="grc">
        <title xml:lang="eng">Iliad</title>
        <edition urn="urn:cts:greekLit:tlg0012.tlg001.perseus-grc2"><label xml:lang="grc">Ἰλιάς</label><description xml:lang="mul">Monro and Allen</description></edition>
        <translation urn="urn:cts:greekLit:tlg0012.tlg001.perseus-eng3" xml:lang="eng"><description xml:lang="eng">Murray, translator</description></translation></work>""",
    "greekLit/data/tlg0012/tlg001/tlg0012.tlg001.perseus-grc2.xml": tei("edition", "grc2", [("line", "/tei:div[@n='$1']//tei:l[@n='$2']"), BOOK],
        '<div n="1"><milestone unit="card" n="1"/><l n="1">μῆνιν ἄειδε θεὰ</l><l n="2">οὐλομένην<note>a note</note></l><l n="3">πολλὰς δʼ ἰφθίμους</l></div>'),
    "greekLit/data/tlg0012/tlg001/tlg0012.tlg001.perseus-eng3.xml": tei("translation", "eng3", [("card", "/tei:div[@n='$1']/tei:div[@n='$2']"), BOOK],
        '<div n="1"><div n="1"><p>The wrath sing, goddess,<milestone unit="line" n="2"/> that destructive wrath</p></div><div n="3"><p>and many souls</p></div></div>'),
    "greekLit/data/tlg0059/__cts__.xml": '<textgroup xmlns="http://chs.harvard.edu/xmlns/cts"><groupname xml:lang="eng">Plato</groupname></textgroup>',
    "greekLit/data/tlg0059/tlg030/__cts__.xml": """<work xmlns="http://chs.harvard.edu/xmlns/cts" urn="urn:cts:greekLit:tlg0059.tlg030" xml:lang="grc">
        <title xml:lang="eng">Republic</title><edition urn="urn:cts:greekLit:tlg0059.tlg030.perseus-grc2"/>
        <translation urn="urn:cts:greekLit:tlg0059.tlg030.perseus-eng2" xml:lang="eng"/></work>""",
    "greekLit/data/tlg0059/tlg030/tlg0059.tlg030.perseus-grc2.xml": tei("edition", "grc2", [("section", "/tei:div[@n='$1']/tei:div[@n='$2']"), BOOK],
        '<div n="1"><div n="327"><p><milestone unit="page" resp="Stephanus" n="327"/><milestone unit="section" resp="Stephanus" n="327a"/>κατέβην χθὲς εἰς Πειραιᾶ <milestone unit="section" resp="Stephanus" n="327b"/>προσευξάμενοι δὲ</p></div>'
        '<div n="328"><p><milestone unit="section" resp="Stephanus" n="328a"/>καὶ ὁ Ἀδείμαντος</p></div></div>'),
    "greekLit/data/tlg0059/tlg030/tlg0059.tlg030.perseus-eng2.xml": tei("translation", "eng2", [("chapter", "/tei:div[@n='$1']"), ("section", "/tei:div[@n='$1']/tei:div[@n='$2']")],
        '<div n="1"><p><milestone unit="section" n="327a"/>I went down yesterday to the Peiraeus <milestone unit="section" n="327b"/>After we had said our prayers</p></div>'),
}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("perseus")
    for name, content in FILES.items():
        path = root / "raw" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    convert(root / "raw", root / "corpus")
    build(root / "corpus", root / "corpus.db")
    return Corpus(str(root / "corpus.db"))


def test_lines_and_aligned_translation_cards(corpus):
    segments = corpus.get_text("Iliad 1.1-3")["segments"]
    assert [s["ref"] for s in segments] == ["Iliad 1.1", "Iliad 1.2", "Iliad 1.3"]
    assert segments[0]["grc"] == "μῆνιν ἄειδε θεὰ" and segments[0]["en"].startswith("The wrath sing")
    assert segments[1]["grc"] == "οὐλομένην" and segments[1]["grc_notes"] == "a note" and "en" not in segments[1]
    assert segments[2]["en"] == "and many souls"


def test_titles_aliases_and_dates(corpus):
    assert corpus.resolve("Homer, Iliad 1.2").ref == "Iliad 1.2" and corpus.resolve("Ἰλιάς 1.1").ref == "Iliad 1.1"
    work = corpus.work("Iliad")
    assert work["authors"] == ["Homer"] and work["comp_date"] == -750 and work["language"] == "grc"


def test_stephanus_sections_split_and_cited_both_ways(corpus):
    assert corpus.get_text("Republic 1.327a")["segments"][0]["grc"] == "κατέβην χθὲς εἰς Πειραιᾶ"
    assert corpus.resolve("Republic 327b").ref == "Republic 1.327b"
    assert corpus.resolve("Republic 327").ref == "Republic 1.327a-327b"


def test_translation_with_a_wrong_pattern_is_read_from_milestones(corpus):
    assert corpus.get_text("Republic 1.327b")["segments"][0]["en"] == "After we had said our prayers"
