"""Download the church corpus: the Greek New Testament (SBLGNT), the Clementine Vulgate and an English Bible
(seven1m/open-bibles, USFX), and Greek and Latin church fathers from Open Greek and Latin (First1KGreek, CSEL;
TEI/CTS like Perseus)."""
import datetime
import json
from pathlib import Path

from genericCorpusMcpBuilder.sources.http import download
from genericCorpusMcpBuilder.sources.perseus.fetch import fetch_work, option_list

SBLGNT = "https://raw.githubusercontent.com/Faithlife/SBLGNT/master/data/sblgnt/text/{code}.txt"
OPEN_BIBLES = "https://raw.githubusercontent.com/seven1m/open-bibles/master/{name}.usfx.xml"
FATHERS_REPOSITORIES = {"first1k": "OpenGreekAndLatin/First1KGreek", "csel": "OpenGreekAndLatin/csel-dev"}
# English Bibles in open-bibles (public domain): dra = Douay-Rheims 1899 (Catholic, from the Vulgate), web = World
# English Bible
ENGLISH_BIBLES = {"dra": "eng-dra", "web": "eng-web"}
LATIN_BIBLE = "lat-clementine"
SBLGNT_BOOKS = ["Matt", "Mark", "Luke", "John", "Acts", "Rom", "1Cor", "2Cor", "Gal", "Eph", "Phil", "Col", "1Thess",
                "2Thess", "1Tim", "2Tim", "Titus", "Phlm", "Heb", "Jas", "1Pet", "2Pet", "1John", "2John", "3John",
                "Jude", "Rev"]
# A starting set; author ids (tlg1443) bring all of an author's works, work ids (tlg2018.tlg002) one work.
DEFAULT_FATHERS = [
    "tlg1271.tlg001",    # Clement of Rome, First Letter to the Corinthians
    "tlg1443",           # Ignatius of Antioch, letters
    "tlg1622",           # Polycarp, To the Philippians
    "tlg1484",           # Martyrdom of Polycarp
    "tlg0645",           # Justin Martyr
    "tlg1447",           # Irenaeus
    "tlg2035",           # Athanasius
    "tlg2018.tlg002",    # Eusebius, Church History (with an English translation)
    "stoa0275",          # Tertullian
    "stoa0104a",         # Cyprian
    "stoa0203",          # Minucius Felix, Octavius
    "stoa0171",          # Lactantius
]


def repository_of(work_id: str) -> str:
    return "first1k" if work_id.startswith("tlg") else "csel"


def fetch(raw: Path, fathers=None, english=None, translations=None, **_options) -> None:
    """Options: fathers (CTS author or work ids, default a starting set; "none" for the Bible only), english
    (dra or web, default dra), translations (languages of the fathers' translations to fetch, default eng)."""
    raw = Path(raw)
    for code in SBLGNT_BOOKS:
        download(SBLGNT.format(code=code), raw / "sblgnt" / f"{code}.txt")
    english_name = ENGLISH_BIBLES[(english or "dra").lower()]
    for name in (LATIN_BIBLE, english_name):
        download(OPEN_BIBLES.format(name=name), raw / "bibles" / f"{name}.usfx.xml")
    wanted = option_list(translations, ["eng"])
    chosen = [] if fathers == "none" else option_list(fathers, DEFAULT_FATHERS)
    for work_id in chosen:
        count = fetch_work(raw, repository_of(work_id), work_id, wanted, FATHERS_REPOSITORIES)
        print(f"{work_id}: {count} versions", flush=True)
    (raw / "fetch.json").write_text(json.dumps({
        "fathers": chosen, "english": english_name, "translations": wanted,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}, indent=2))
