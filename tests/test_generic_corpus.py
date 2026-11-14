"""A corpus with no profile, built from the format directly: the server must work with generic defaults."""
import json

import pytest

from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.corpus_format import CorpusWriter, TextWriter
from genericCorpusMcpBuilder.research import Researcher

WORKS = [
    {"title": "Republic", "authors": ["Plato"], "date": [-375, -375], "categories": ["Philosophy"],
     "aliases": ["Rep."], "parts": [{"name": "Republic", "levels": [{"name": "Book"}, {"name": "Section"}],
                                     "separator": "."}]},
    {"title": "Iliad", "authors": ["Homer"], "date": [-750, -700], "categories": ["Epic"],
     "parts": [{"name": "Iliad", "levels": [{"name": "Book"}, {"name": "Line"}], "separator": "."}]},
    {"title": "Gospel of John", "date": [90, 110], "categories": ["New Testament"], "language": "grc",
     "parts": [{"name": "John", "levels": [{"name": "Chapter"}, {"name": "Verse"}], "separator": ":"}]},
    {"title": "City of God", "authors": ["Augustine"], "date": [413, 426], "categories": ["Church Fathers"],
     "language": "lat", "parts": [{"name": "City of God", "levels": [{"name": "Book"}, {"name": "Chapter"}],
                                   "separator": "."}]},
    {"title": "Scholia on the Iliad", "date": [300, 900], "categories": ["Epic"], "dependence": "Commentary",
     "comments_on": ["Iliad"], "parts": [{"name": "Scholia on the Iliad", "separator": ".",
                                          "levels": [{"name": "Book"}, {"name": "Line"}, {"name": "Note"}]}]},
]
TEXTS = {
    ("Republic", "grc"): [(["1", "1"], "Κατέβην χθὲς εἰς Πειραιᾶ μετὰ Γλαύκωνος τοῦ Ἀρίστωνος"),
                          (["1", "2"], "προσευξόμενός τε τῇ θεῷ")],
    ("Republic", "en"): [(["1", "1"], "I went down yesterday to the Piraeus with Glaucon the son of Ariston"),
                         (["1", "2"], "that I might offer up my prayers to the goddess")],
    ("Iliad", "grc"): [(["1", "1"], "μῆνιν ἄειδε θεὰ Πηληϊάδεω Ἀχιλῆος"),
                       (["1", "2"], "οὐλομένην, ἣ μυρί᾽ Ἀχαιοῖς ἄλγε᾽ ἔθηκε")],
    ("Iliad", "en"): [(["1", "1"], "Sing, goddess, the anger of Peleus' son Achilles"),
                      (["1", "2"], "and its devastation, which put pains thousandfold upon the Achaeans")],
    ("Scholia on the Iliad", "grc"): [(["1", "1", "1"], "μῆνιν: τὴν ἐπίμονον ὀργήν")],
    ("Gospel of John", "grc"): [(["1", "1"], "Ἐν ἀρχῇ ἦν ὁ λόγος, καὶ ὁ λόγος ἦν πρὸς τὸν θεόν")],
    ("Gospel of John", "lat"): [(["1", "1"], "In principio erat Verbum, et Verbum erat apud Deum")],
    ("Gospel of John", "en"): [(["1", "1"], "In the beginning was the Word, and the Word was with God")],
    ("City of God", "lat"): [(["1", "1"], "Gloriosissimam ciuitatem Dei siue in hoc temporum cursu")],
    ("City of God", "en"): [(["1", "1"], "The glorious city of God, whether in this passing of time")],
}
PARTS = {"Gospel of John": "John"}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("generic")
    writer = CorpusWriter(root / "corpus", {"id": "mini-greek", "name": "Mini Greek", "source": "test",
                                            "languages": ["grc", "lat", "en"], "original_language": "grc"})
    for work in WORKS:
        writer.work(work)
    for number, ((title, lang), segments) in enumerate(TEXTS.items()):
        with TextWriter(writer.text_path(f"{number:03d}.jsonl"), {"work": title, "lang": lang, "title": f"{title} {lang}"}) as out:
            for cite, text in segments:
                out.segment(PARTS.get(title, title), cite, text)
    writer.citation({"work": "Republic", "scheme": "Stephanus", "labels": ["327a", "Stephanus 327a"],
                     "ref": "Republic 1.1-2"})
    writer.link({"from": "Iliad 1.1", "to": "Scholia on the Iliad 1.1.1", "type": "commentary"})
    writer.close()
    build(root / "corpus", root / "corpus.db")
    return Corpus(str(root / "corpus.db"))


def test_text_in_original_and_translation(corpus):
    text = corpus.get_text("Iliad 1.1-2")
    assert [s["ref"] for s in text["segments"]] == ["Iliad 1.1", "Iliad 1.2"]
    assert text["segments"][0]["grc"].startswith("μῆνιν") and text["segments"][0]["en_version"] == "Iliad en"
    assert text["source_url"] is None


def test_other_citation_system_and_alias(corpus):
    assert corpus.resolve("Republic 327a").ref == "Republic 1.1-2"
    assert corpus.resolve("Rep. 1.2").ref == "Republic 1.2"


def test_search_picks_language_by_script(corpus):
    assert [hit["ref"] for hit in corpus.search("Πειραιᾶ")] == ["Republic 1.1"]
    assert [hit["ref"] for hit in corpus.search("Piraeus")] == ["Republic 1.1"]


def test_word_span_counts_original_words(corpus):
    assert corpus.get_text("Iliad 1.1@1-2")["segments"][0]["grc_words"] == "μῆνιν ἄειδε"
    report = corpus.verify_citations([{"ref": "Iliad 1.1", "quote": "ἄειδε θεὰ"}])
    assert report["all_verified"] and report["results"][0]["cite_as"] == "Iliad 1.1@2-3"


def test_commentary_and_generic_research(corpus):
    assert corpus.commentaries("Iliad 1.1")["commentaries"][0]["work"] == "Scholia on the Iliad"
    research = Researcher(corpus).research("the anger of Achilles", ["μῆνιν"])
    assert [layer["layer"] for layer in research["layers"]] == ["Sources, earliest first"]
    assert research["layers"][0]["sources"][0]["ref"].startswith("Iliad 1.1")


def test_no_corpus_specific_tools_or_instructions(corpus):
    assert corpus.profile.trace is None
    assert "Mini Greek" in corpus.profile.instructions and "Shulchan" not in corpus.profile.instructions


@pytest.mark.parametrize("query, expected", [("Πειραια", "Republic 1.1"), ("μηνιν αειδε", "Iliad 1.1"),
                                             ("ΜΗΝΙΝ", "Iliad 1.1"), ("μυρί\u02bc Ἀχαιοῖς", "Iliad 1.2")])   # text has ᾽, query ʼ
def test_greek_search_ignores_accents_breathings_and_case(corpus, query, expected):
    assert expected in [hit["ref"] for hit in corpus.search(query)]


def test_unaccented_quote_verifies_against_accented_text(corpus):
    report = corpus.verify_citations([{"ref": "Iliad 1.2", "quote": "μυρι αχαιοις αλγε"}])
    assert report["all_verified"], report


def test_each_work_has_its_own_original_language(corpus):
    john = corpus.get_text("John 1:1")["segments"][0]
    assert john["lat_version"] == "Gospel of John lat" and "grc_version" not in john      # Vulgate is a translation
    augustine = corpus.get_text("City of God 1.1@2-3")["segments"][0]
    assert augustine["lat_words"] == "ciuitatem Dei" and "lat_version" not in augustine   # here Latin is the original
    report = corpus.verify_citations([{"ref": "City of God 1.1", "quote": "civitatem Dei"}])["results"][0]
    assert report["quote_found"] and report["words_counted_in"] == "lat original"


def test_latin_spelling_and_shared_script(corpus):
    assert "City of God 1.1" in [hit["ref"] for hit in corpus.search("civitatem")]      # ciuitatem in the text
    hits = corpus.search("Verbum")                                                     # Latin and English both
    assert {hit["lang"] for hit in hits} == {"lat"} and hits[0]["ref"] == "John 1:1"
    assert {hit["lang"] for hit in corpus.search("beginning")} == {"en"}
