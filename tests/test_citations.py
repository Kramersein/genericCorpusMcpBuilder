import json
import sqlite3

import pytest

from genericCorpusMcpBuilder.citations import (LABEL_SEPARATOR, Locator, cite_key, level_kinds, order_key)

PARTS = {
    "Shabbat": ([{"name": "Daf", "kind": "folio"}, {"name": "Line"}], ":"),
    "Iliad": ([{"name": "Book"}, {"name": "Line"}], "."),
    "Odes": ([{"name": "Poem", "kind": "label"}, {"name": "Line"}], "."),
}
SEGMENTS = {
    "Shabbat": [["2a", "1"], ["2a", "2"], ["2b", "1"], ["73a", "4"], ["73b", "1"], ["74a", "1"]],
    "Iliad": [["1", "1"], ["1", "2"], ["1", "10"], ["2", "1"]],
    "Odes": [["pr.", "1"], ["pr.", "2"], ["Epode", "1"], ["1", "1"]],
}


@pytest.fixture(scope="module")
def locator():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE nodes(base TEXT PRIMARY KEY, title TEXT, levels TEXT, separator TEXT)")
    db.execute("CREATE TABLE locations(id INTEGER PRIMARY KEY, base TEXT, cite TEXT, cite_key TEXT, order_key TEXT)")
    for base, (levels, separator) in PARTS.items():
        db.execute("INSERT INTO nodes VALUES (?,?,?,?)", (base, base, json.dumps(levels), separator))
        ranks = {}
        rank_of = lambda path: ranks.setdefault(path, len([p for p in ranks if p[:-1] == path[:-1]]))
        rows = [(labels, order_key(labels, level_kinds(levels, len(labels)), rank_of)) for labels in SEGMENTS[base]]
        for labels, key in sorted(rows, key=lambda row: row[1]):
            db.execute("INSERT INTO locations(base, cite, cite_key, order_key) VALUES (?,?,?,?)",
                       (base, LABEL_SEPARATOR.join(labels), cite_key(labels), key))
    return Locator(db)


def cites(locator, citation):
    resolution = locator.resolve(citation)
    if not resolution or resolution.loc_start is None:
        return None
    rows = locator.db.execute("SELECT cite FROM locations WHERE id BETWEEN ? AND ? ORDER BY id",
                              (resolution.loc_start, resolution.loc_end)).fetchall()
    return [row[0].replace(LABEL_SEPARATOR, " ") for row in rows]


def test_numbers_sort_numerically(locator):
    assert cites(locator, "Iliad 1") == ["1 1", "1 2", "1 10"]


@pytest.mark.parametrize("citation, expected", [
    ("Shabbat 73a:4", ["73a 4"]),
    ("Shabbat 2", ["2a 1", "2a 2"]),                       # a bare page number means side a
    ("Shabbat 2a-b", ["2a 1", "2a 2", "2b 1"]),
    ("Shabbat 2b-73a", ["2b 1", "73a 4"]),
    ("Shabbat 73A:4", ["73a 4"]),
    ("Iliad 1.2-10", ["1 2", "1 10"]),
    ("Iliad 1.2-2.1", ["1 2", "1 10", "2 1"]),
    ("Iliad 1.3-9", None),                                  # a valid range with no segments in it
    ("Iliad", ["1 1", "1 2", "1 10", "2 1"]),
])
def test_numbered_citations(locator, citation, expected):
    assert cites(locator, citation) == expected


def test_free_labels_keep_first_seen_order_and_match_by_prefix(locator):
    assert cites(locator, "Odes pr.") == ["pr. 1", "pr. 2"]
    assert cites(locator, "Odes epode.1") == ["Epode 1"]
    assert cites(locator, "Odes pr.2-Epode.1") == ["pr. 2", "Epode 1"]


def test_display_ref(locator):
    assert locator.resolve("Shabbat 2-3").ref == "Shabbat 2a-3a"
    assert locator.resolve("Iliad 1.2-10").ref == "Iliad 1.2-10"


@pytest.mark.parametrize("citation", ["Iliad one.1", "Unknown 1:1", "Shabbat x"])
def test_unparseable_or_unknown(locator, citation):
    assert locator.resolve(citation) is None


def test_letters_are_not_numbers(locator):
    assert locator.resolve("Iliad 1a.1") is None   # left for other citation systems to resolve
