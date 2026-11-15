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
# Chinese characters and Japanese kana: written without spaces, so each character is a unit of its own (a "word" for
# search, quotes and @n-m citations). Korean Hangul separates words with spaces and is left as words.
CJK_CHARACTER = re.compile("[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
                           "\U00020000-\U0003134f]")
# Combining marks that are letters' vowels or voicing, not accents: kept (Japanese が, Devanagari, Thai, Tibetan ...)
KEEP_MARKS_AFTER = re.compile("[\u0900-\u0dff\u0e00-\u0eff\u0f00-\u0fff\u1000-\u109f\u1780-\u17ff"
                              "\u3040-\u30ff]")


def clean_display_text(raw: str) -> tuple[str, str]:
    notes = [ANY_TAG.sub("", n).strip() for n in FOOTNOTE.findall(raw)]
    without_notes = STRAY_FOOTNOTE_MARKER.sub("", FOOTNOTE.sub("", raw))
    marked = LINE_BREAK.sub(" ", BOLD.sub("**", without_notes))
    text = WHITESPACE.sub(" ", html.unescape(ANY_TAG.sub("", marked))).strip()
    return text.replace("** **", " ").replace("****", ""), "\n".join(n for n in notes if n)


def strip_accents(text: str) -> str:
    """Drop combining marks: Greek accents and breathings, Latin and Cyrillic diacritics, Arabic vowel signs. Marks
    that are part of the letter in Indic, Thai, Tibetan and similar scripts, and Japanese voicing marks, are kept."""
    decomposed = unicodedata.normalize("NFD", text)
    kept, base = [], ""
    for c in decomposed:
        if unicodedata.category(c) != "Mn":
            base = c
            kept.append(c)
        elif KEEP_MARKS_AFTER.match(base or " "):
            kept.append(c)
    return unicodedata.normalize("NFC", "".join(kept))


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


def tokens(text: str) -> list[str]:
    """Whitespace-separated words; a Chinese character or kana is a token of its own, and punctuation after it
    joins it ("之，" is one token), as "beginning," is one word. Without such characters this is text.split()."""
    if not CJK_CHARACTER.search(text):
        return text.split()
    out, attached = [], False
    for c in text:
        if c.isspace():
            attached = False
        elif CJK_CHARACTER.match(c) or not attached:
            out.append(c)
            attached = True
        else:
            out[-1] += c
    return out


def display_words(text: str) -> list[str]:
    return tokens(text.replace("**", ""))


def search_words(text: str) -> list[str]:
    """The normalized units of a query or quote, as word_units counts them in a text."""
    return [word for _, word in word_units(text)]


def spaced_characters(normalized: str) -> str:
    """Chinese characters and kana separated by spaces: what the cjk search mode indexes and searches."""
    return WHITESPACE.sub(" ", CJK_CHARACTER.sub(lambda m: f" {m.group(0)} ", normalized)).strip()


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
