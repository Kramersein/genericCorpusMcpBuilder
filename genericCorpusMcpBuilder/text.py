import html
import re
import unicodedata

FOOTNOTE = re.compile(r'<sup[^>]*class="footnote-marker"[^>]*>.*?</sup>\s*<i[^>]*class="footnote"[^>]*>(.*?)</i>', re.S)
STRAY_FOOTNOTE_MARKER = re.compile(r'<sup[^>]*class="footnote-marker"[^>]*>.*?</sup>', re.S)
BOLD = re.compile(r"</?(?:b|strong)\b[^>]*>", re.I)
LINE_BREAK = re.compile(r"<br\s*/?>", re.I)
ANY_TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")

HEBREW_MARKS = re.compile(r"[֑-ְ֯-ׇֽֿׁׂׅׄ]")
HEBREW_WORD_BREAKS = re.compile(r"[־׀׃׆]")
QUOTE_MARKS_INSIDE_WORDS = re.compile(r"""(?<=\w)["'׳״‘’“”](?=\w)""")
PUNCTUATION = re.compile(r"[^\w\s]")
ELISION_MARKS = re.compile(r"[\u02BC\u1FBD\u1FBF\u2019']")   # Greek ἄλγεʼ, δʼ: the mark is not part of the word
LATIN_SPELLING = str.maketrans("vj", "ui")                       # uirum = virum, iam = jam


def clean_display_text(raw: str) -> tuple[str, str]:
    notes = [ANY_TAG.sub("", n).strip() for n in FOOTNOTE.findall(raw)]
    without_notes = STRAY_FOOTNOTE_MARKER.sub("", FOOTNOTE.sub("", raw))
    marked = LINE_BREAK.sub(" ", BOLD.sub("**", without_notes))
    text = WHITESPACE.sub(" ", html.unescape(ANY_TAG.sub("", marked))).strip()
    return text.replace("** **", " ").replace("****", ""), "\n".join(n for n in notes if n)


def strip_accents(text: str) -> str:
    """Drop combining marks: Greek accents and breathings, Latin diacritics, Arabic vowel signs."""
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def normalize_for_search(text: str) -> str:
    text = HEBREW_MARKS.sub("", text.replace("**", ""))
    text = HEBREW_WORD_BREAKS.sub(" ", text)
    text = strip_accents(ELISION_MARKS.sub("", text))
    text = QUOTE_MARKS_INSIDE_WORDS.sub("", text)
    text = PUNCTUATION.sub(" ", text)
    return WHITESPACE.sub(" ", text).strip().casefold()


def latin_spelling(normalized: str) -> str:
    return normalized.translate(LATIN_SPELLING)


HEBREW_PREFIX_LETTERS = set("והבכלמשד")
MAX_PREFIX_LETTERS = 3
MIN_CORE_LENGTH = 3
MIN_EXPANDABLE_LENGTH = 2
PLURAL_ENDINGS = ("ים", "ין")


def unify_plural_ending(word: str) -> str:
    return word[:-1] if word.endswith(PLURAL_ENDINGS) and len(word) >= 3 else word


def strip_prefix_letters(word: str) -> str:
    stripped = 0
    while stripped < MAX_PREFIX_LETTERS and word[:1] in HEBREW_PREFIX_LETTERS and len(word) > MIN_CORE_LENGTH:
        word, stripped = word[1:], stripped + 1
    return word


def reversed_index_form(normalized: str) -> str:
    return " ".join(unify_plural_ending(w)[::-1] for w in normalized.split())


def reversed_query_terms(normalized: str) -> list[str]:
    cores = [strip_prefix_letters(unify_plural_ending(w))[::-1] for w in normalized.split()]
    return [f'"{core}"*' if len(core) >= MIN_EXPANDABLE_LENGTH else f'"{core}"' for core in cores]


def is_prefixed_form(shorter: str, longer: str) -> bool:
    extra = longer[:len(longer) - len(shorter)]
    return longer.endswith(shorter) and len(extra) <= MAX_PREFIX_LETTERS and set(extra) <= HEBREW_PREFIX_LETTERS


def words_match(query_word: str, text_word: str, fold=None) -> bool:
    """Same word, allowing Hebrew prefix letters and -ים/-ין; fold (e.g. latin_spelling) unifies spellings first."""
    if fold:
        query_word, text_word = fold(query_word), fold(text_word)
    a, b = unify_plural_ending(query_word), unify_plural_ending(text_word)
    return a == b or is_prefixed_form(a, b) or is_prefixed_form(b, a)


def display_words(text: str) -> list[str]:
    return text.replace("**", "").split()


def word_units(text: str) -> list[tuple[int, str]]:
    return [(position, part) for position, word in enumerate(display_words(text), 1)
            for part in normalize_for_search(word).split()]


def find_words(units: list[tuple[int, str]], needle: list[str], fold=None) -> tuple[int, int] | None:
    width = len(needle)
    for start in range(len(units) - width + 1):
        window = units[start:start + width]
        if width and all(words_match(query, word, fold) for query, (_, word) in zip(needle, window)):
            return window[0][0], window[-1][0]
    return None
