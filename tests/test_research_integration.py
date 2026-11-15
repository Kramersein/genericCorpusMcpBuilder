import os
from pathlib import Path

import pytest

from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.research import Researcher

DATABASE = Path(os.environ.get("CORPUS_MCP_TEST_DB", Path(__file__).parent.parent / "data" / "corpus.db"))
pytestmark = pytest.mark.skipif(not DATABASE.exists(), reason=f"built corpus not found at {DATABASE}")


@pytest.fixture(scope="module")
def corpus():
    return Corpus(str(DATABASE))


def all_refs(dossier: dict) -> list[str]:
    return [source["ref"] for layer in dossier["layers"] for source in layer["sources"]]


def test_key_terms_alone_reach_the_relevant_code(corpus):
    dossier = Researcher(corpus).research("Can I use an umbrella on Shabbat?", ["אהל עראי", "פריסת סדין"])
    assert any(ref.startswith("Shulchan Arukh, Orach Chayim 315") for ref in all_refs(dossier))


def test_layers_run_from_earliest_to_latest(corpus):
    dossier = Researcher(corpus).research("Can I use an umbrella on Shabbat?", ["אהל עראי"])
    order = {stratum["label"]: number for number, stratum in enumerate(corpus.profile.strata)}
    numbers = [order[layer["layer"]] for layer in dossier["layers"]]
    assert numbers == sorted(numbers) and len(numbers) > 1
    assert dossier["build"] == corpus.build_id


def test_keyword_hits_skip_commentary_noise(corpus):
    dossier = Researcher(corpus).research("Can I use an umbrella on Shabbat?", ["אהל"])
    assert "Classic commentaries" not in [layer["layer"] for layer in dossier["layers"]]


def test_commentary_on_shulchan_arukh_includes_mishnah_berurah(corpus):
    works = [c["work"] for c in corpus.commentaries("Shulchan Arukh, Orach Chayim 315:7", per_work=1)["commentaries"]]
    assert "Mishnah Berurah" in works


def test_batch_verification_reports_failures(corpus):
    report = corpus.verify_citations([{"ref": "Moed Katan 8b", "quote": "אין מערבין שמחה בשמחה"},
                                      {"ref": "Berakhot 2a", "quote": "this sentence is not in the Talmud"}])
    assert report["all_verified"] is False and report["failed"] == ["Berakhot 2a"]


def test_context_widens_within_the_same_section(corpus):
    text = corpus.get_text("Moed Katan 8b:9", ("en",), context=1)
    assert text["ref"] == "Moed Katan 8b:8-10" and text["requested_ref"] == "Moed Katan 8b:9"


def test_hebrew_search_rejects_words_that_only_end_like_the_query(corpus):
    refs = [r["ref"] for r in corpus.search("מפני השלום", exact_phrase=True, limit=20)]
    assert "Psalms 3:1" not in refs


@pytest.mark.skipif(not Corpus(str(DATABASE)).has_table("topics") if DATABASE.exists() else True,
                    reason="topics not loaded")
def test_topic_lookup_by_name(corpus):
    explored = Researcher(corpus).explore_topic("Honoring Parents")
    assert explored.get("layers") and explored["sources_in_topic"] > 10


@pytest.mark.parametrize("limit", [1, 3, 4, 8])
def test_research_never_returns_more_sources_than_asked(corpus, limit):
    dossier = Researcher(corpus).research("Must I honor my parents if they treat me badly?", ["כיבוד אב ואם"],
                                          max_sources=limit)
    assert 0 < len(all_refs(dossier)) <= limit
