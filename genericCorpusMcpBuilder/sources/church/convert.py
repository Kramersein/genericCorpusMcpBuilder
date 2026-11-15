"""The church corpus (raw/ from fetch.py) -> corpus directory: the New Testament in Greek (the original) with the
Vulgate and an English Bible as translations, and church fathers in Greek or Latin as their originals."""
import json
import re
import sys
from pathlib import Path

from genericCorpusMcpBuilder.sources.kit import SimpleCorpus
from genericCorpusMcpBuilder.sources.perseus.convert import convert_work, read_work
from genericCorpusMcpBuilder.sources.usfx import read_usfx

# SBLGNT code, USFX code, title, group, abbreviations people cite by
BOOKS = [
    ("Matt", "MAT", "Matthew", "Gospels", ["Mt", "Matt.", "Gospel of Matthew"]),
    ("Mark", "MRK", "Mark", "Gospels", ["Mk", "Mk.", "Mar", "Gospel of Mark"]),
    ("Luke", "LUK", "Luke", "Gospels", ["Lk", "Lk.", "Luk", "Gospel of Luke"]),
    ("John", "JHN", "John", "Gospels", ["Jn", "Jn.", "Jhn", "Gospel of John"]),
    ("Acts", "ACT", "Acts", "Acts", ["Acts of the Apostles", "Ac", "Act"]),
    ("Rom", "ROM", "Romans", "Pauline letters", ["Rom.", "Ro"]),
    ("1Cor", "1CO", "1 Corinthians", "Pauline letters", ["1 Cor", "1 Cor.", "I Corinthians", "First Corinthians"]),
    ("2Cor", "2CO", "2 Corinthians", "Pauline letters", ["2 Cor", "2 Cor.", "II Corinthians", "Second Corinthians"]),
    ("Gal", "GAL", "Galatians", "Pauline letters", ["Gal.", "Ga"]),
    ("Eph", "EPH", "Ephesians", "Pauline letters", ["Eph.", "Ephes."]),
    ("Phil", "PHP", "Philippians", "Pauline letters", ["Phil.", "Php"]),
    ("Col", "COL", "Colossians", "Pauline letters", ["Col."]),
    ("1Thess", "1TH", "1 Thessalonians", "Pauline letters", ["1 Thess", "1 Thess.", "I Thessalonians"]),
    ("2Thess", "2TH", "2 Thessalonians", "Pauline letters", ["2 Thess", "2 Thess.", "II Thessalonians"]),
    ("1Tim", "1TI", "1 Timothy", "Pauline letters", ["1 Tim", "1 Tim.", "I Timothy"]),
    ("2Tim", "2TI", "2 Timothy", "Pauline letters", ["2 Tim", "2 Tim.", "II Timothy"]),
    ("Titus", "TIT", "Titus", "Pauline letters", ["Tit", "Tit."]),
    ("Phlm", "PHM", "Philemon", "Pauline letters", ["Philem.", "Phm"]),
    ("Heb", "HEB", "Hebrews", "Hebrews", ["Heb."]),
    ("Jas", "JAS", "James", "Catholic letters", ["Jas.", "Jam"]),
    ("1Pet", "1PE", "1 Peter", "Catholic letters", ["1 Pet", "1 Pet.", "I Peter"]),
    ("2Pet", "2PE", "2 Peter", "Catholic letters", ["2 Pet", "2 Pet.", "II Peter"]),
    ("1John", "1JN", "1 John", "Catholic letters", ["1 Jn", "1 Jn.", "I John"]),
    ("2John", "2JN", "2 John", "Catholic letters", ["2 Jn", "2 Jn.", "II John"]),
    ("3John", "3JN", "3 John", "Catholic letters", ["3 Jn", "3 Jn.", "III John"]),
    ("Jude", "JUD", "Jude", "Catholic letters", ["Jud"]),
    ("Rev", "REV", "Revelation", "Revelation", ["Rev.", "Apocalypse", "Revelation of John", "Apoc."]),
]
APPARATUS_SIGNS = re.compile("[⸀-⸅]")       # SBLGNT's ⸀ ⸁ ⸂ ⸃ ⸄ ⸅ point into an apparatus not included
WHITESPACE = re.compile(r"\s+")
VERSE_LINE = re.compile(r"^(\S+) (\d+):(\d+)\t(.*)$")
BIBLE_VERSIONS = {"lat-clementine": ("lat", "Clementine Vulgate", "Biblia Sacra Vulgatae Editionis (Clementine, 1592), "
                                     "public domain; text from the Clementine Text Project via seven1m/open-bibles"),
                  "eng-dra": ("en", "Douay-Rheims 1899", "Douay-Rheims Bible, 1899 American edition, public domain; "
                              "via seven1m/open-bibles"),
                  "eng-web": ("en", "World English Bible", "World English Bible, public domain; via seven1m/open-bibles")}
SBLGNT_SOURCE = ("SBL Greek New Testament (SBLGNT), Copyright 2010 Society of Biblical Literature and Logos Bible "
                 "Software, CC BY 4.0, https://sblgnt.com (apparatus signs removed)")
# Approximate dates (for ordering only) and abbreviations for the default fathers
FATHER_DATES = {"tlg1271": (96, "c. 96 CE"), "tlg1443": (110, "c. 110 CE"), "tlg1622": (130, "early 2nd c. CE"),
                "tlg1484": (160, "mid 2nd c. CE"), "tlg0645": (155, "c. 150-160 CE"), "tlg1447": (180, "c. 180 CE"),
                "tlg2035": (350, "4th c. CE"), "tlg2018": (320, "c. 300-325 CE"), "stoa0275": (205, "c. 195-220 CE"),
                "stoa0104a": (252, "c. 250 CE"), "stoa0203": (200, "c. 200 CE"), "stoa0171": (310, "c. 305-315 CE"),
                "stoa0040": (410, "c. 390-430 CE"), "stoa0162": (400, "c. 380-420 CE"), "stoa0022": (385, "c. 375-397 CE")}
FATHER_ALIASES = {"tlg1271.tlg001": ["1 Clement", "1 Clem.", "Clement, To the Corinthians"],
                  "tlg2018.tlg002": ["Eusebius, Church History", "Eus. HE", "Historia Ecclesiastica", "Hist. eccl."],
                  "stoa0203.stoa001": ["Octavius", "Minucius Felix, Octavius"]}
MANIFEST = {"id": "church", "name": "New Testament and Church Fathers",
            "source": "SBLGNT (Faithlife/SBLGNT), Clementine Vulgate and English Bibles (seven1m/open-bibles), "
                      "First1KGreek and CSEL (Open Greek and Latin)",
            "license": "CC BY-SA 4.0 as a whole. Parts: SBL Greek New Testament, Copyright 2010 Society of Biblical "
                       "Literature and Logos Bible Software, CC BY 4.0 (sblgnt.com); First1KGreek and CSEL (Open Greek "
                       "and Latin), CC BY-SA 4.0; Clementine Vulgate, Douay-Rheims and World English Bible, public "
                       "domain."}


def sblgnt_book(path: Path) -> tuple[str, list[tuple[list[str], str]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    verses = []
    for line in lines[1:]:
        match = VERSE_LINE.match(line)
        if match:
            text = WHITESPACE.sub(" ", APPARATUS_SIGNS.sub("", match.group(4))).strip()
            verses.append(([match.group(2), match.group(3)], text))
    return lines[0].strip(), verses


def convert(raw: Path, out: Path, **_options) -> None:
    raw, out = Path(raw), Path(out)
    fetched = json.loads((raw / "fetch.json").read_text()) if (raw / "fetch.json").exists() else {}
    corpus = SimpleCorpus(out, original_language="grc", exported_at=fetched.get("fetched_at", "unknown"),
                          version="master branches of the source repositories", **MANIFEST)
    bibles = {path.name.removesuffix(".usfx.xml"): read_usfx(path, {usfx for _, usfx, *_ in BOOKS})
              for path in sorted((raw / "bibles").glob("*.usfx.xml"))}
    for code, usfx, title, group, abbreviations in BOOKS:
        path = raw / "sblgnt" / f"{code}.txt"
        if not path.exists():
            print(f"skipped {title}: {path} missing", flush=True)
            continue
        greek_title, greek = sblgnt_book(path)
        work = corpus.work(title, levels=["chapter", "verse"], separator=":", language="grc", date=[50, 100],
                           date_text="1st century CE", categories=["New Testament", group],
                           titles={"grc": greek_title}, aliases=[code, usfx, *abbreviations])
        corpus.text(work, "grc", "SBLGNT", greek, source=SBLGNT_SOURCE, rank=0, license="CC BY 4.0, https://sblgnt.com")
        for name, verses in bibles.items():
            lang, version, source = BIBLE_VERSIONS.get(name, ("en", name, name))
            corpus.text(work, lang, version, verses.get(usfx, []), source=source, license="public domain")
    works = [read_work(cts.parent, ["en"]) for cts in sorted(raw.glob("*/data/*/*/__cts__.xml"))]
    works = [w for w in works if any(v["kind"] == "edition" for v in w["versions"])]
    taken = [title for _, _, title, _, _ in BOOKS] + [w["title"] for w in works]
    for work in works:
        name = work["title"] if taken.count(work["title"]) == 1 else f"{work['author']}, {work['title']}"
        group = "Greek fathers" if work["language"] == "grc" else "Latin fathers"
        convert_work(corpus, work, name, dates=FATHER_DATES, aliases=FATHER_ALIASES,
                     categories=["Church Fathers", group] + ([work["author"]] if work["author"] else []))
    corpus.finish(profile_dir=Path(__file__).parent)


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
