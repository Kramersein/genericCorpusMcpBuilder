import pytest

from genericCorpusMcpBuilder.refs import split_word_span
from genericCorpusMcpBuilder.text import display_words, find_words, normalize_for_search, word_units

MOED_KATAN = "**מַאי טַעְמָא?** אָמַר רַבִּי חֲנִינָא: לְפִי שֶׁאֵין מְעָרְבִין שִׂמְחָה בְּשִׂמְחָה."


def test_display_words_ignore_bold_markers_and_number_from_one():
    words = display_words(MOED_KATAN)
    assert words[0] == "מַאי"
    assert words[1] == "טַעְמָא?"
    assert len(words) == 10


def test_find_words_returns_word_positions_of_a_prefixed_quote():
    needle = normalize_for_search("אין מערבין שמחה בשמחה").split()
    assert find_words(word_units(MOED_KATAN), needle) == (7, 10)


def test_find_words_returns_none_when_absent():
    assert find_words(word_units(MOED_KATAN), ["שלש", "שהן", "שש"]) is None


def test_find_words_skips_punctuation_only_tokens():
    assert find_words(word_units("אמר – רבי חנינא"), ["אמר", "רבי"]) == (1, 3)


def test_maqaf_joined_words_map_back_to_one_display_word():
    assert find_words(word_units("וְחֹ֖שֶׁךְ עַל־פְּנֵ֣י תְה֑וֹם"), ["פני", "תהום"]) == (2, 3)


@pytest.mark.parametrize("ref, expected", [
    ("Moed Katan 8b:9@7-10", ("Moed Katan 8b:9", (7, 10))),
    ("Moed Katan 8b:9@7", ("Moed Katan 8b:9", (7, 7))),
    ("Moed Katan 8b:9", ("Moed Katan 8b:9", None)),
])
def test_split_word_span(ref, expected):
    assert split_word_span(ref) == expected
