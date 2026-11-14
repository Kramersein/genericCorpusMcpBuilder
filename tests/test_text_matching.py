import pytest

from genericCorpusMcpBuilder.refs import hebrew_address_to_english
from genericCorpusMcpBuilder.sources.sefaria.convert import format_address, parse_address
from genericCorpusMcpBuilder.text import (find_words, normalize_for_search, reversed_index_form, reversed_query_terms,
                           word_units, words_match)


def query_matches_text(query: str, text: str) -> bool:
    indexed = reversed_index_form(normalize_for_search(text)).split()
    terms = [t.strip('"*') for t in reversed_query_terms(normalize_for_search(query))]
    expandable = [t.endswith("*") for t in reversed_query_terms(normalize_for_search(query))]
    width = len(terms)
    return any(
        all(word.startswith(term) if star else word == term
            for term, star, word in zip(terms, expandable, indexed[start:start + width]))
        for start in range(len(indexed) - width + 1))


@pytest.mark.parametrize("query, text", [
    ("אין מערבין שמחה בשמחה", "לְפִי שֶׁאֵין מְעָרְבִין שִׂמְחָה בְּשִׂמְחָה"),
    ("כל ישראל ערבים זה בזה", "לָמַד שֶׁכׇּל יִשְׂרָאֵל עֲרֵבִין זֶה בָּזֶה"),
    ("שמחה גדולה", "בשמחה גדולה"),
    ("בשמחה גדולה", "שמחה גדולה"),
    ("מאימתי קורין את שמע", "מֵאֵימָתַי קוֹרִין אֶת שְׁמַע"),
    ('רמב"ם', "הרמב״ם"),
])
def test_prefix_and_plural_variants_match(query, text):
    assert query_matches_text(query, text)
    assert find_words(word_units(text), normalize_for_search(query).split())


@pytest.mark.parametrize("query_word, text_word", [("שמחה", "נמחה"), ("ישראל", "ישמעאל")])
def test_unrelated_words_do_not_match(query_word, text_word):
    assert not words_match(query_word, text_word)


def test_single_letters_are_not_expanded():
    assert reversed_query_terms("ה") == ['"ה"']


@pytest.mark.parametrize("numbers, types, text", [
    ([3], ["Talmud"], "2a"), ([4], ["Talmud"], "2b"), ([145, 4], ["Talmud", "Integer"], "73a:4"),
    ([5], ["Folio"], "2a"), ([308, 1], ["Siman", "Seif"], "308:1"),
])
def test_addresses_round_trip(numbers, types, text):
    assert format_address(numbers, types) == text
    assert parse_address(text, types) == numbers


@pytest.mark.parametrize("hebrew, kinds, english", [
    ("ב.", ["folio", "number"], "2a"), ("עג:", ["folio", "number"], "73b"),
    ('דף ב ע"ב', ["folio", "number"], "2b"), ('סימן רנ"ג ס"ק כ"ה', ["number", "number"], "253:25"),
])
def test_hebrew_addresses(hebrew, kinds, english):
    assert hebrew_address_to_english(hebrew, kinds) == english
