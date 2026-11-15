"""Chinese and Japanese: written without spaces, so a character is a unit for search, quotes and @n-m citations."""
import pytest

from genericCorpusMcpBuilder import SimpleCorpus
from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.check import check
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.text import display_words, tokens


class DataFrameLike:
    """Stands in for a pandas DataFrame (pandas is not a dependency): to_dict("records") is all text() uses."""

    def __init__(self, **columns):
        self.columns = columns

    def to_dict(self, orient):
        return [dict(zip(self.columns, row)) for row in zip(*self.columns.values())]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("cjk")
    texts = SimpleCorpus(root / "corpus", id="cjk", name="Chinese and Japanese classics", original_language="lzh")
    analects = texts.work("Analects", levels=["book", "chapter"], language="lzh", author="Confucius",
                          titles={"lzh": "論語"}, aliases=["論語", "Lunyu"], date=-450)
    texts.text(analects, "lzh", "Chinese Text Project", DataFrameLike(
        ref=["1.1", "4.1"],
        chinese=["子曰：「學而時習之，不亦說乎？有朋自遠方來，不亦樂乎？人不知而不慍，不亦君子乎？」",
                 "子曰：「里仁為美。擇不處仁，焉得知？」"]), cite="ref", text="chinese")
    texts.text(analects, "en", "Legge 1861", [
        ("1.1", "The Master said, 'Is it not pleasant to learn with a constant perseverance and application?'"),
        ("4.1", "The Master said, 'It is virtuous manners which constitute the excellence of a neighborhood.'")])
    hojoki = texts.work("Hojoki", levels=["section"], language="ja", author="Kamo no Chōmei", titles={"ja": "方丈記"},
                        aliases=["方丈記"], date=1212)
    texts.text(hojoki, "ja", "Kōbunko", [(1, "ゆく河の流れは絶えずして、しかももとの水にあらず。"),
                                          (2, "よどみに浮かぶうたかたは、かつ消えかつ結びて、久しくとどまりたるためしなし。")])
    texts.finish()
    assert check(root / "corpus") == 0
    build(root / "corpus", root / "corpus.db")
    return Corpus(str(root / "corpus.db"))


def test_tokens_split_characters_but_not_other_scripts():
    assert tokens("學而時習之，不亦說乎") == ["學", "而", "時", "習", "之，", "不", "亦", "說", "乎"]
    assert tokens("In the beginning, God") == ["In", "the", "beginning,", "God"]
    assert display_words("ゆく河の") == ["ゆ", "く", "河", "の"]


@pytest.mark.parametrize("query, expected", [("仁", ["Analects 4.1"]), ("學而", ["Analects 1.1"]),
                                             ("不亦樂乎", ["Analects 1.1"]), ("河の流れ", ["Hojoki 1"]),
                                             ("うたかた", ["Hojoki 2"]), ("perseverance", ["Analects 1.1"])])
def test_search_finds_character_sequences(corpus, query, expected):
    assert [hit["ref"] for hit in corpus.search(query)] == expected


def test_characters_in_a_different_order_do_not_match(corpus):
    assert corpus.search("而學") == []


def test_quotes_and_word_spans_count_characters(corpus):
    report = corpus.verify_citations([{"ref": "Analects 1.1", "quote": "學而時習之"}])["results"][0]
    assert report["quote_found"] and report["cite_as"] == "Analects 1.1@3-7"
    assert corpus.get_text("Analects 1.1@3-7")["segments"][0]["lzh_words"] == "學 而 時 習 之，"
    japanese = corpus.verify_citations([{"ref": "Hojoki 1", "quote": "ゆく河の流れ"}])["results"][0]
    assert japanese["quote_found"] and japanese["words_counted_in"] == "ja original"


def test_titles_in_chinese_and_languages(corpus):
    assert corpus.resolve("論語 4.1").ref == "Analects 4.1" and corpus.resolve("方丈記 2").ref == "Hojoki 2"
    assert corpus.profile.languages_for("學而") == ["lzh"] and corpus.profile.languages_for("ゆく河") == ["ja"]
