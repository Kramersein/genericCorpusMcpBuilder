import os
from pathlib import Path

import pytest

from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.text import normalize_for_search

DATABASE = Path(os.environ.get("CORPUS_MCP_TEST_DB", Path(__file__).parent.parent / "data" / "corpus.db"))
pytestmark = pytest.mark.skipif(not DATABASE.exists(), reason=f"built corpus not found at {DATABASE}")


@pytest.fixture(scope="module")
def corpus():
    return Corpus(str(DATABASE))


def test_quote_with_prefix_gets_word_exact_citation(corpus):
    report = corpus.verify_citation("Moed Katan 8b", "אין מערבין שמחה בשמחה")
    assert report["cite_as"] == "Moed Katan 8b:9@29-32"
    words = corpus.get_text(report["cite_as"], ("he",))["segments"][0]["he_words"]
    assert normalize_for_search(words) == "שאין מערבין שמחה בשמחה"


def test_fabricated_quote_is_rejected(corpus):
    assert corpus.verify_citation("Moed Katan 8b", "שמחה גדולה היא להיות בשמחה תמיד")["quote_found"] is False


def test_quote_across_verses(corpus):
    report = corpus.verify_citation("Genesis 1:1-2", "בראשית ברא אלהים את השמים ואת הארץ והארץ היתה תהו ובהו")
    assert report["cite_as"] == "Genesis 1:1@1 - Genesis 1:2@4"


@pytest.mark.parametrize("ref, expected", [
    ("שבת עג:", "Shabbat 73b"),
    ('שו"ע או"ח שח:א', "Shulchan Arukh, Orach Chayim 308:1"),
    ("פרשת וישב 3", "Genesis 37:23-36"),
    ("Jerusalem Talmud Shabbat, Vilna 7a", "Jerusalem Talmud Shabbat 1:2:6-11"),
])
def test_refs_in_other_citation_systems_resolve(corpus, ref, expected):
    assert corpus.resolve(ref).ref == expected


def test_bare_page_prefers_vilna_and_warns_about_venice(corpus):
    cited_as = corpus.resolve("Jerusalem Talmud Shabbat 7a").cited_as
    assert cited_as.startswith("Vilna 7a") and "Venice 7a" in cited_as


def test_find_quote_reaches_the_talmudic_source(corpus):
    refs = [r["ref"] for r in corpus.find_quote("כל ישראל ערבים זה בזה", 5)]
    assert "Shevuot 39a:22" in refs


def test_trace_reaches_the_rambam(corpus):
    earlier = corpus.trace("Shulchan Arukh, Orach Chayim 308:1", 1, corpus.profile.trace["categories"])["earlier_sources"]
    assert any(e["to"].startswith("Mishneh Torah, Sabbath") for e in earlier)


def test_manifest_records_word_numbering_rule(corpus):
    assert "word_numbering" in corpus.about()
