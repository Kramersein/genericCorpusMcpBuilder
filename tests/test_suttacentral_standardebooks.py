"""The SuttaCentral and Standard Ebooks adapters on saved files shaped like the real ones (no network)."""
import json

from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.check import check
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.sources import standardebooks, suttacentral


def built(tmp_path, convert):
    convert(tmp_path / "raw", tmp_path / "corpus")
    assert check(tmp_path / "corpus") == 0
    build(tmp_path / "corpus", tmp_path / "corpus.db")
    return Corpus(str(tmp_path / "corpus.db"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_suttacentral(tmp_path):
    raw = tmp_path / "raw"
    write_json(raw / "settings.json", {"collections": ["mn", "an"], "translations": ["en/sujato"], "suttas": []})
    write_json(raw / "root/pli/ms/sutta/mn/mn1_root-pli-ms.json", {
        "mn1:0.1": "Majjhima Nikāya 1 ", "mn1:0.2": "Mūlapariyāyasutta ", "mn1:1.1": "Evaṁ me sutaṁ—",
        "mn1:1.2": "ekaṁ samayaṁ bhagavā ukkaṭṭhāyaṁ viharati ", "mn1:3.1": "Pathaviṁ pathavito sañjānāti; "})
    write_json(raw / "translation/en/sujato/sutta/mn/mn1_translation-en-sujato.json", {
        "mn1:0.1": "Middle Discourses 1 ", "mn1:0.2": "The Root of All Things ", "mn1:1.1": "So I have heard. ",
        "mn1:1.2": "At one time the Buddha was staying near Ukkaṭṭhā ", "mn1:3.1": "They perceive earth as earth. "})
    write_json(raw / "root/pli/ms/sutta/an/an1/an1.1-2_root-pli-ms.json", {     # a range file: two suttas
        "an1.1:0.1": "Aṅguttara Nikāya 1 ", "an1.1:0.2": "1. Rūpādivagga ", "an1.1:1.1": "Evaṁ me sutaṁ—",
        "an1.2:1.1": "Nāhaṁ, bhikkhave, aññaṁ ekasaddampi samanupassāmi "})
    write_json(raw / "root/pli/ms/sutta/mn/mn10_root-pli-ms.json", {"mn10:1.1": "Evaṁ me sutaṁ—"})
    corpus = built(tmp_path, suttacentral.convert)

    assert corpus.resolve("mn1:3.1").ref == "MN 1:3.1"                 # SuttaCentral's own ids
    assert corpus.resolve("MN 1 1.1-2").ref == "MN 1:1.1-2"
    assert corpus.resolve("Middle Discourses 1:1.1").ref == "MN 1:1.1"
    assert corpus.resolve("The Root of All Things").ref == "MN 1"
    assert corpus.resolve("AN 1.2:1.1").ref == "AN 1.2:1.1"
    first = corpus.get_text("MN 1:1.1")["segments"][0]
    assert first["pli"] == "Evaṁ me sutaṁ—" and first["en"] == "So I have heard."
    english = corpus.verify_citations([{"ref": "MN 1:3.1", "quote": "perceive earth"}])["results"][0]
    assert english["quote_found"] and english["cite_as"] == "MN 1:3.1@2-3"    # same script as the Pali: still found
    pali = corpus.verify_citations([{"ref": "MN 1:1.1", "quote": "evam me sutam"}])["results"][0]
    assert pali["quote_found"]                                        # diacritics optional
    assert corpus.work("AN 1.1")["description"] is None                # a chapter name is not the sutta's title
    assert corpus.work("MN 1")["category"] == "Majjhima Nikāya"
    assert [hit["ref"] for hit in corpus.search("Ukkaṭṭhā")][:1] == ["MN 1:1.2"]


def test_suttacentral_names():
    assert suttacentral.cite_name("mn1") == "MN 1" and suttacentral.cite_name("sn12.2") == "SN 12.2"
    assert suttacentral.cite_name("dhp1-20") == "Dhp 1-20" and suttacentral.cite_name("thag1.1") == "Thag 1.1"


OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:identifier id="uid">https://standardebooks.org/ebooks/leo-tolstoy/war-and-peace/louise-maude_aylmer-maude</dc:identifier>
  <dc:title id="title">War and Peace</dc:title><dc:subject id="subject-1">Napoleonic Wars, 1800-1815 -- Fiction</dc:subject>
  <dc:language>en-US</dc:language><dc:creator id="author">Leo Tolstoy</dc:creator>
  <dc:contributor id="translator-1">Louise Maude</dc:contributor><dc:contributor id="translator-2">Aylmer Maude</dc:contributor>
 </metadata>
 <manifest>{items}</manifest>
 <spine>{refs}</spine>
</package>"""
PAGE = """<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"
 xmlns:epub="http://www.idpf.org/2007/ops"><body epub:type="bodymatter"><section epub:type="chapter">{}</section></body></html>"""
PAGES = {
    "titlepage.xhtml": "<h1>War and Peace</h1><p>By Leo Tolstoy</p>",
    "book-1.xhtml": '<h2><span epub:type="se:label">Book</span> <span epub:type="z3998:roman">I</span></h2>',
    "chapter-1-1-1.xhtml": '<h4 epub:type="z3998:roman">I</h4><p>“Well, Prince, so Genoa and Lucca are now just family '
                           'estates of the Buonapartes.<a href="endnotes.xhtml#note-1" epub:type="noteref">1</a>”</p>'
                           '<span epub:type="pagebreak" title="2"/><p>It was in July, 1805.</p>',
    "chapter-1-1-2.xhtml": "<h4>The Soirée</h4><p>Anna Pávlovna’s drawing room was gradually filling.</p>",
    "epilogue-1.xhtml": "<h2>First Epilogue</h2><p>Seven years had passed.</p>",
    "endnotes.xhtml": "<p>1. A note.</p>",
    "colophon.xhtml": "<p>War and Peace was produced by Standard Ebooks</p>",
}


def test_standardebooks(tmp_path):
    folder = tmp_path / "raw" / "leo-tolstoy_war-and-peace_louise-maude_aylmer-maude"
    (folder / "text").mkdir(parents=True)
    (folder / "content.opf").write_text(OPF.format(
        items="".join(f'<item href="text/{n}" id="{n}" media-type="application/xhtml+xml"/>' for n in PAGES),
        refs="".join(f'<itemref idref="{n}"/>' for n in PAGES)), encoding="utf-8")
    for name, body in PAGES.items():
        (folder / "text" / name).write_text(PAGE.format(body), encoding="utf-8")
    (tmp_path / "raw" / "books.json").write_text(json.dumps(
        [standardebooks.repository_name("https://standardebooks.org/ebooks/leo-tolstoy/war-and-peace/louise-maude_aylmer-maude")]))
    corpus = built(tmp_path, standardebooks.convert)

    first = corpus.get_text("War and Peace 1.1.1.1")["segments"][0]["en"]
    assert first == "“Well, Prince, so Genoa and Lucca are now just family estates of the Buonapartes.”"   # no marker
    assert corpus.get_text("War and Peace 1.1.1.2")["segments"][0]["en"] == "It was in July, 1805."  # no page number
    assert corpus.resolve("War and Peace The Soirée").ref == "War and Peace 1.1.2.1"   # its one paragraph
    assert corpus.resolve("War and Peace epilogue.1.1").exists
    everything = " ".join(s["en"] for s in corpus.get_text("War and Peace")["segments"])
    assert "produced by Standard Ebooks" not in everything and "A note" not in everything
    info = corpus.work("War and Peace")
    assert info["authors"] == ["Leo Tolstoy"]
    assert standardebooks.level_names(["text/book-1.xhtml", "text/part-1-1.xhtml", "text/chapter-1-1-1.xhtml"]) == \
        ["book", "part", "chapter"]
