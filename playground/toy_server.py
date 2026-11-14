"""Toy MCP server: three verses and three tools, for testing the chat loop and prompts without the real corpus.

Used by `chat.py --mcp toy` (in-process). Can also run standalone over HTTP:
    .venv/bin/python playground/toy_server.py      # serves http://127.0.0.1:8766/mcp
"""
import re

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

VERSES = {
    "Genesis 1:1": {
        "he": "בראשית ברא אלהים את השמים ואת הארץ",
        "en": "In the beginning God created the heavens and the earth.",
        "aliases": ["bereshit 1:1", "bereishit 1:1", "gen 1:1", "בראשית א:א"],
    },
    "Leviticus 19:18": {
        "he": "לא תקם ולא תטר את בני עמך ואהבת לרעך כמוך אני ה'",
        "en": "You shall not take vengeance or bear a grudge against the children of your people; "
              "love your neighbor as yourself: I am the LORD.",
        "aliases": ["vayikra 19:18", "lev 19:18", "ויקרא יט:יח"],
    },
    "Deuteronomy 6:4": {
        "he": "שמע ישראל ה' אלהינו ה' אחד",
        "en": "Hear, O Israel: the LORD is our God, the LORD is one.",
        "aliases": ["devarim 6:4", "deut 6:4", "דברים ו:ד"],
    },
}

INSTRUCTIONS = """\
Toy corpus with exactly three verses (Genesis 1:1, Leviticus 19:18, Deuteronomy 6:4).
Only quote text returned by get_text. If a ref is not in the corpus, say so; do not supply it from memory."""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)
server = MCPServer("toyTorah", instructions=INSTRUCTIONS, version="0.0.1")


def resolve(ref: str) -> str | None:
    key = " ".join(ref.lower().split())
    for canonical, verse in VERSES.items():
        if key == canonical.lower() or key in verse["aliases"]:
            return canonical
    return None


def strip_marks(text: str) -> str:
    """Drop Hebrew vowels/cantillation and punctuation so searches match unvocalized text."""
    return re.sub(r"[֑-ׇ]|[^\w\s]", "", text).lower()


@server.tool(annotations=READ_ONLY)
def list_refs() -> list[str]:
    """List every ref in the corpus."""
    return list(VERSES)


@server.tool(annotations=READ_ONLY)
def get_text(ref: str) -> dict:
    """Fetch the Hebrew and English of a verse, e.g. "Genesis 1:1", "Devarim 6:4" or "דברים ו:ד"."""
    canonical = resolve(ref)
    if canonical is None:
        return {"error": f"'{ref}' is not in the corpus", "available": list(VERSES)}
    verse = VERSES[canonical]
    return {"ref": canonical, "he": verse["he"], "en": verse["en"]}


@server.tool(annotations=READ_ONLY)
def search(query: str) -> list[dict]:
    """Find verses containing all the words of the query, in Hebrew or English."""
    words = strip_marks(query).split()
    hits = []
    for canonical, verse in VERSES.items():
        haystack = strip_marks(verse["he"] + " " + verse["en"])
        if words and all(w in haystack for w in words):
            hits.append({"ref": canonical, "he": verse["he"], "en": verse["en"]})
    return hits


if __name__ == "__main__":
    server.run("streamable-http", host="127.0.0.1", port=8766, streamable_http_path="/mcp")
