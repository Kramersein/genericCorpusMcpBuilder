"""Citation input helpers: word spans (`ref@n-m`) and Hebrew-written addresses."""
import re

WORD_SPAN = re.compile(r"^(.*?)\s*@\s*(\d+)(?:\s*-\s*(\d+))?$")


def split_word_span(ref: str) -> tuple[str, tuple[int, int] | None]:
    match = WORD_SPAN.match(ref.strip())
    if not match:
        return ref.strip(), None
    first = int(match.group(2))
    return match.group(1), (first, int(match.group(3) or first))


GEMATRIA = dict(zip("אבגדהוזחטיכלמנסעפצקרשתךםןףץ",
                    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 200, 300, 400,
                     20, 40, 50, 80, 90]))
HEBREW_SECTION_WORDS = re.compile(r"(?:^|\s)(?:דף|עמוד|סימן|סי'|סעיף|סע'|פרק|פ'|הלכה|הל'|משנה|פסוק|ס\"ק|סק|אות)(?=\s|$)")
HEBREW_AMUD = {"ע\"א": "a", "עא": "a", "ע\"ב": "b", "עב": "b", ".": "a", ":": "b"}
HEBREW_NUMERAL = re.compile(r"[א-ת]+[\"'׳״]?[א-ת]?")


def hebrew_numeral_value(token: str) -> int:
    letters = re.sub(r"[\"'׳״]", "", token)
    return sum(GEMATRIA[ch] for ch in letters if ch in GEMATRIA)


def hebrew_address_to_english(address: str, kinds: list[str]) -> str:
    """`עג:` -> `73b`, `סימן רנ"ג ס"ק כ"ה` -> `253:25`. kinds are the part's level kinds (citations.Part.kinds)."""
    folio = kinds[:1] == ["folio"]
    text = HEBREW_SECTION_WORDS.sub(" ", " " + address).replace(",", " ").strip()
    amud = None
    for marker in ('ע"ב', "עב", 'ע"א', "עא"):
        if text.endswith(marker) or f" {marker} " in f" {text} ":
            amud = HEBREW_AMUD[marker]
            text = re.sub(rf"\s*{marker}\s*", " ", text).strip()
            break
    if amud is None and text and text[-1] in ".:" and folio:
        amud = HEBREW_AMUD[text[-1]]
        text = text[:-1].strip()
    numbers = [hebrew_numeral_value(t) for t in HEBREW_NUMERAL.findall(text)]
    numbers += [int(t) for t in re.findall(r"\d+", text)]
    if not numbers:
        return ""
    parts = [str(n) for n in numbers]
    if folio:
        parts[0] += amud or "a"
    return ":".join(parts)
