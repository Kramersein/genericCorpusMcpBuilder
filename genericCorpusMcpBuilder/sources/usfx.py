"""Reading USFX Bibles (the XML format of eBible.org and seven1m/open-bibles): <c id="1"/> starts a chapter,
<v id="1"/> a verse, <ve/> ends it. Notes, cross references and headings are left out."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

LEFT_OUT = {"f", "x", "fig", "h", "toc", "id", "ide", "rem", "cl", "languageCode", "table"}
WHITESPACE = re.compile(r"\s+")


def events(element: ET.Element):
    """("c", n), ("v", n), ("ve",) and ("text", s) in document order."""
    if element.text:
        yield ("text", element.text)
    for child in element:
        if child.tag == "c":
            yield ("c", child.get("id"))
        elif child.tag == "v":
            yield ("v", child.get("id"))
        elif child.tag == "ve":
            yield ("ve",)
        elif child.tag not in LEFT_OUT:
            yield from events(child)
        if child.tail:
            yield ("text", child.tail)


def read_usfx(path: Path, books: set[str] | None = None) -> dict[str, list[tuple[list[str], str]]]:
    """Book code (MAT, JHN, 1CO ...) -> [(["chapter", "verse"], text), ...] in reading order."""
    out = {}
    for book in ET.parse(path).getroot().iter("book"):
        code = book.get("id")
        if books is not None and code not in books:
            continue
        verses, chapter, verse, parts = [], None, None, []

        def close():
            text = WHITESPACE.sub(" ", "".join(parts)).strip()
            if chapter and verse and text:
                verses.append(([chapter, verse], text))

        for event in events(book):
            if event[0] == "c":
                close()
                chapter, verse, parts = event[1], None, []
            elif event[0] == "v":
                close()
                verse, parts = event[1], []
            elif event[0] == "ve":
                close()
                verse, parts = None, []
            elif verse:
                parts.append(event[1])
        close()
        out[code] = verses
    return out
