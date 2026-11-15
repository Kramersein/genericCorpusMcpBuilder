"""Citations: labels per level, matched against the locations table (see docs/corpus-format.md, Citation model)."""
import itertools
import json
import re
import sqlite3
from dataclasses import dataclass
from functools import lru_cache

LABEL_SEPARATOR = "\x1f"        # joins labels in locations.cite and locations.cite_key
ORDER_SEPARATOR = "\x01"        # joins per-level keys in locations.order_key; sorts below every label character
ORDER_END = "\U0010ffff"        # appended to an end key so it covers everything under that position
NUMBER_WIDTH = 9
NUMERIC_LABEL = re.compile(r"^(\d+)([a-z]*)$")
VALID_INPUT = {"number": re.compile(r"^\d+$"), "folio": re.compile(r"^\d+[a-z]?$")}
FOLIO_SIDE = re.compile(r"^[a-z]$")
NUMBERED_KINDS = ("number", "folio")
DEFAULT_SEPARATOR = ":"


def level_kinds(levels: list[dict], depth: int) -> list[str]:
    return [(levels[i].get("kind") or "number") if i < len(levels) else "number" for i in range(depth)]


def cite_key(labels: list[str]) -> str:
    return LABEL_SEPARATOR.join(label.lower() for label in labels)


def numbered_key(label: str) -> str | None:
    match = NUMERIC_LABEL.match(label.lower())
    return f"{int(match.group(1)):0{NUMBER_WIDTH}d}{match.group(2)}" if match else None


def order_key(labels: list[str], kinds: list[str], rank_of=None) -> str | None:
    """Sortable reading-order key. Numbered levels sort by number; other labels by rank_of(labels up to that level),
    their first-seen position. Without rank_of, returns None when a level needs a rank."""
    keys = []
    for depth, (label, kind) in enumerate(zip(labels, kinds)):
        key = numbered_key(label) if kind in NUMBERED_KINDS else None
        if key is None:
            if rank_of is None:
                return None
            key = f"~{rank_of(tuple(labels[:depth + 1])):0{NUMBER_WIDTH}d}"
        keys.append(key)
    return ORDER_SEPARATOR.join(keys)


def format_range(base: str, separator: str, start: list[str], end: list[str], joiner: str = " ") -> str:
    if not start:
        return base
    address = separator.join(start)
    if end != start:
        shared = next((i for i, (a, b) in enumerate(zip(start, end)) if a != b), min(len(start), len(end)))
        address += "-" + separator.join(end[shared:])
    return f"{base}{joiner}{address}"


@dataclass(frozen=True)
class Part:
    base: str
    title: str
    kinds: tuple[str, ...]
    separator: str
    joiner: str = " "

    def kinds_for(self, depth: int) -> list[str]:
        return [self.kinds[i] if i < len(self.kinds) else "number" for i in range(depth)]


@dataclass(frozen=True)
class Resolution:
    part: Part
    start: list[str]
    end: list[str]
    loc_start: int | None
    loc_end: int | None

    @property
    def ref(self) -> str:
        return format_range(self.part.base, self.part.separator, self.start, self.end, self.part.joiner)


def normalize_label(label: str, kind: str) -> str:
    label = label.strip()
    if kind in NUMBERED_KINDS:
        label = label.lower()
    if kind == "folio" and label.isdigit():
        label += "a"
    return label


def valid_label(label: str, kind: str) -> bool:
    """Whether typed input can be a label of this kind. `7a` is not a number, so a citation like
    `Jerusalem Talmud Shabbat 7a` falls through to the other citation systems (Vilna 7a)."""
    pattern = VALID_INPUT.get(kind)
    return bool(label) and (pattern is None or pattern.match(label.lower()) is not None)


CUT, CUT_KEEPING_SEPARATOR, JOIN = range(3)


def token_splits(text: str, separator: str, free_labels: bool) -> list[list[str]]:
    """Ways to cut text into labels. Free-text labels may contain the separator, so for parts with such levels each
    separator may also end the label before it (`pr.2` = `pr.`, `2`) or belong to it (`pr..1`); most labels first."""
    pieces = text.split(separator)
    if not free_labels or len(pieces) == 1:
        return [pieces]
    splits = []
    for choices in itertools.product((CUT, CUT_KEEPING_SEPARATOR, JOIN), repeat=len(pieces) - 1):
        tokens = [pieces[0]]
        for choice, piece in zip(choices, pieces[1:]):
            if choice == JOIN:
                tokens[-1] += separator + piece
            else:
                if choice == CUT_KEEPING_SEPARATOR:
                    tokens[-1] += separator
                tokens.append(piece)
        if all(tokens) and tokens not in splits:
            splits.append(tokens)
    return sorted(splits, key=len, reverse=True)


def parse_address(address: str, part: Part) -> list[tuple[list[str], list[str]]]:
    """`1:3-5` -> [(["1", "3"], ["1", "5"])]; several readings when labels may contain the separator; [] if no
    reading fits this part."""
    if not address:
        return [([], [])]
    start_text, _, end_text = address.partition("-")
    free = "label" in part.kinds
    readings = []
    for start_tokens in token_splits(start_text, part.separator, free):
        for end_tokens in token_splits(end_text, part.separator, free) if end_text else [None]:
            reading = parse_reading(start_tokens, end_tokens, part)
            if reading and reading not in readings:
                readings.append(reading)
    return readings


def parse_reading(start_tokens: list[str], end_tokens: list[str] | None,
                  part: Part) -> tuple[list[str], list[str]] | None:
    start = [normalize_label(t, k) for t, k in zip(start_tokens, part.kinds_for(len(start_tokens)))]
    end = start
    if end_tokens:
        prefix = start[:max(0, len(start) - len(end_tokens))]
        kinds = part.kinds_for(len(prefix) + len(end_tokens))
        end = list(prefix)
        for depth, token in enumerate(end_tokens, len(prefix)):
            token = normalize_label(token, kinds[depth])
            page = NUMERIC_LABEL.match(start[depth]) if depth < len(start) else None
            if kinds[depth] == "folio" and FOLIO_SIDE.match(token) and page:
                token = page.group(1) + token  # 73a-b -> 73a-73b
            end.append(token)
    labels = [(label, kind) for labels in (start, end) for label, kind in zip(labels, part.kinds_for(len(labels)))]
    return (start, end) if all(valid_label(label, kind) for label, kind in labels) else None


class Locator:
    """Resolves citations against a database with `nodes` and `locations` tables."""

    def __init__(self, db):
        self.db = db
        self.part = lru_cache(maxsize=100000)(self.load_part)

    def load_part(self, base: str) -> Part | None:
        try:
            row = self.db.execute("SELECT title, levels, separator, joiner FROM nodes WHERE base = ?", (base,)).fetchone()
        except sqlite3.OperationalError:              # built before names could end in ":"
            row = self.db.execute("SELECT title, levels, separator, ' ' FROM nodes WHERE base = ?", (base,)).fetchone()
        if not row:
            return None
        levels = json.loads(row[1] or "[]")
        return Part(base, row[0], tuple(level.get("kind") or "number" for level in levels),
                    row[2] or DEFAULT_SEPARATOR, row[3] or " ")

    def split(self, citation: str) -> tuple[Part, list[tuple[list[str], list[str]]]] | None:
        """Longest known part name first; the rest (no spaces) must parse as labels of that part."""
        citation = citation.strip()
        whole = self.part(citation)
        if whole:
            return whole, [([], [])]
        candidate = citation
        while " " in candidate:
            candidate = candidate.rsplit(" ", 1)[0]
            address = citation[len(candidate) + 1:]
            part = self.part(candidate) if address and not any(c.isspace() for c in address) else None
            readings = parse_address(address, part) if part else []
            if readings:
                return part, readings
        # "MN 1:3.4": a colon can also end the name, when no space does (SuttaCentral cites this way)
        for position in [i for i, c in enumerate(citation) if c == ":"][::-1]:
            part = self.part(citation[:position].strip())
            readings = parse_address(citation[position + 1:].strip(), part) if part else []
            if readings:
                return part, readings
        return None

    def span(self, part: Part, start: list[str], end: list[str]) -> tuple[int | None, int | None]:
        if not start:
            row = self.db.execute("SELECT min(id), max(id) FROM locations WHERE base = ?", (part.base,)).fetchone()
            return row[0], row[1]
        low, high = order_key(start, part.kinds_for(len(start))), order_key(end, part.kinds_for(len(end)))
        if low is not None and high is not None:
            row = self.db.execute(
                "SELECT min(id), max(id) FROM locations WHERE base = ? AND order_key >= ? AND order_key <= ?",
                (part.base, low, high + ORDER_END)).fetchone()
            first, last = row[0], row[1]
        else:
            first = self.prefix_bound("min", part.base, start)
            last = self.prefix_bound("max", part.base, end)
        if first is None or last is None or first > last:
            return None, None
        return first, last

    def prefix_bound(self, bound: str, base: str, labels: list[str]) -> int | None:
        key = cite_key(labels)
        return self.db.execute(
            f"SELECT {bound}(id) FROM locations WHERE base = ? AND cite_key >= ? AND cite_key <= ?",
            (base, key, key + LABEL_SEPARATOR + ORDER_END)).fetchone()[0]

    def resolve(self, citation: str) -> Resolution | None:
        split = self.split(citation)
        if not split:
            return None
        part, readings = split
        for start, end in readings:
            first, last = self.span(part, start, end)
            if first is not None:
                return Resolution(part, start, end, first, last)
        start, end = readings[0]
        return Resolution(part, start, end, None, None)
