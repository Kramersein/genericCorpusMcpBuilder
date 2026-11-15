"""SuttaCentral (suttacentral.net): the Pali discourses (suttas) with English translations, segment by segment, from
its bilara-data repository on GitHub (github.com/suttacentral/bilara-data).

    corpus-mcp build --source suttacentral --data data-suttas
    corpus-mcp build --source suttacentral --data data-suttas --option collections=dn,mn,sn,an,kn/dhp

Options (--option name=value):
    collections   which collections, comma-separated: dn, mn, sn, an, or a part of kn (kn/dhp, kn/snp, kn/thag,
                  ...). Default dn,mn
    suttas        only these suttas, comma-separated (mn1,mn2,sn1.1): for trying it out
    translations  language/translator, comma-separated (default en/sujato; e.g. en/sujato,de/sabbamitta)
    delay         seconds between downloads (default 0.3)

Every segment of the Pali has an id ("mn1:3.4" = Majjhima Nikāya 1, section 3, line 4), and the translations use the
same ids, so the Pali and English line up exactly; passages are cited the way SuttaCentral cites them ("MN 1:3.4",
or "mn1:3.4"). The Pali (Mahāsaṅgīti edition) is public domain; the translations made on SuttaCentral are
dedicated to the public domain (CC0; bilara-data's LICENSE.md).
"""
import json
import re
import urllib.request
from pathlib import Path

from genericCorpusMcpBuilder import SimpleCorpus, download, language_code, pause
from genericCorpusMcpBuilder.sources.http import USER_AGENT

REPOSITORY = "suttacentral/bilara-data"
BRANCH = "published"
FILES = f"https://raw.githubusercontent.com/{REPOSITORY}/{BRANCH}/"
CONTENTS = f"https://api.github.com/repos/{REPOSITORY}/contents/{{path}}?ref={BRANCH}"
TREE = f"https://api.github.com/repos/{REPOSITORY}/git/trees/{{sha}}?recursive=1"
ROOT = "root/pli/ms/sutta"

COLLECTIONS = {
    "dn": ("DN", "Dīgha Nikāya", "Long Discourses"),
    "mn": ("MN", "Majjhima Nikāya", "Middle Discourses"),
    "sn": ("SN", "Saṁyutta Nikāya", "Linked Discourses"),
    "an": ("AN", "Aṅguttara Nikāya", "Numbered Discourses"),
    "kn": ("KN", "Khuddaka Nikāya", "Minor Collection"),
}
# parts of the Minor Collection, cited by their own abbreviations (Dhp 1, Snp 1.1)
MINOR = {"kp": "Kp", "dhp": "Dhp", "ud": "Ud", "iti": "Iti", "snp": "Snp", "vv": "Vv", "pv": "Pv", "thag": "Thag",
         "thig": "Thig", "tha-ap": "Tha Ap", "thi-ap": "Thi Ap", "bv": "Bv", "cp": "Cp", "ja": "Ja", "mnd": "Mnd",
         "cnd": "Cnd", "ps": "Ps", "ne": "Ne", "pe": "Pe", "mil": "Mil"}
SUTTA_ID = re.compile(r"^([a-z-]+?)(\d.*)$")          # "mn1" -> ("mn", "1"), "sn1.1" -> ("sn", "1.1")
LEVELS = {1: ["line"], 2: ["section", "line"], 3: ["section", "paragraph", "line"], 4: ["part", "section", "paragraph", "line"]}


# ---------------------------------------------------------------- downloading

def ask_github(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def folder_files(path):
    """Every .json file under a folder of the repository (two API requests, however many files)."""
    parent, name = path.rsplit("/", 1)
    entry = next((e for e in ask_github(CONTENTS.format(path=parent)) if e["name"] == name), None)
    if entry is None:
        return []
    tree = ask_github(TREE.format(sha=entry["sha"]))
    return [f"{path}/{item['path']}" for item in tree["tree"] if item["path"].endswith(".json")]


def sutta_of(file_path):
    """'root/pli/ms/sutta/mn/mn1_root-pli-ms.json' -> 'mn1'; range files ('an1.1-10') keep their range."""
    return Path(file_path).name.split("_", 1)[0]


def wanted(file_path, suttas):
    if not suttas:
        return True
    name = sutta_of(file_path)
    return any(name == s or name.startswith(s + ".") or name.startswith(s + "-") for s in suttas)


def fetch(raw, collections="dn,mn", translations="en/sujato", suttas=None, delay="0.3", **options):
    raw = Path(raw)
    only = [s.strip().lower().replace(" ", "") for s in str(suttas).split(",")] if suttas else []
    collections = [c.strip().lower() for c in str(collections).split(",") if c.strip()]
    translations = [t.strip() for t in str(translations).split(",") if t.strip()]
    for collection in collections:
        folders = [f"{ROOT}/{collection}"] + [f"translation/{t}/sutta/{collection}" for t in translations]
        for folder in folders:
            files = [f for f in folder_files(folder) if wanted(f, only)]
            print(f"{folder}: {len(files)} files", flush=True)
            for number, file in enumerate(files, 1):
                if download(FILES + file, raw / file):
                    pause(delay)
                if number % 100 == 0:
                    print(f"  {number}/{len(files)}", flush=True)
    (raw / "settings.json").write_text(json.dumps({"collections": collections, "translations": translations,
                                                   "suttas": only}), encoding="utf-8")


# ---------------------------------------------------------------- converting

def by_sutta(segments):
    """Split a file's segments ({"mn1:1.1": text}) by sutta: a range file (an1.1-10) holds several."""
    suttas = {}
    for segment_id, text in segments.items():
        sutta, _, address = segment_id.partition(":")
        suttas.setdefault(sutta, []).append((address.split("."), text))
    return suttas


def cite_name(sutta):
    """'mn1' -> 'MN 1', 'sn12.2' -> 'SN 12.2', 'dhp1-20' -> 'Dhp 1-20'."""
    match = SUTTA_ID.match(sutta)
    if not match:
        return sutta
    prefix, number = match.groups()
    abbreviation = (COLLECTIONS.get(prefix) or (MINOR.get(prefix),))[0] or prefix.capitalize()
    return f"{abbreviation} {number}"


def heading_lines(lines):
    """The title lines at the top of a sutta (section 0): '0.1', '0.2', ..."""
    return [(labels, text.strip()) for labels, text in lines if labels[0] == "0" and text.strip()]


def sutta_title(lines):
    """The sutta's own title: the last heading line, unless it only names the chapter (vagga) it is in."""
    headings = heading_lines(lines)
    if len(headings) < 2:
        return None
    labels, title = headings[-1]
    if re.match(r"^\d+\.\s", title) and re.search(r"vagg|chapter", title, re.IGNORECASE):
        return None
    return re.sub(r"^\d+\.\s*", "", title).strip() or None


def read_translations(raw, translations, collection):
    """{sutta: {translation: {segment id: text}}} for every translation file downloaded for the collection."""
    found = {}
    for translation in translations:
        for path in sorted((raw / "translation" / translation / "sutta" / collection).rglob("*.json")):
            segments = json.loads(path.read_text(encoding="utf-8"))
            for sutta, lines in by_sutta(segments).items():
                found.setdefault(sutta, {})[translation] = lines
    return found


def sutta_order(path):
    """Files in canonical order: mn2 before mn10, sn1.2 before sn1.10."""
    return [int(n) if n.isdigit() else n for n in re.split(r"(\d+)", str(path))]


def translator_name(translation):
    lang, translator = translation.split("/", 1)
    return lang, f"{translator.capitalize()} ({language_code(lang)})"


def convert(raw, out, **options):
    raw = Path(raw)
    settings = json.loads((raw / "settings.json").read_text(encoding="utf-8"))
    corpus = SimpleCorpus(out, id="suttacentral", name="SuttaCentral: Pali discourses", original_language="pli",
                          source=f"SuttaCentral bilara-data (github.com/{REPOSITORY}, branch {BRANCH})",
                          license="Pali root texts: public domain; SuttaCentral translations: CC0 1.0 (public domain "
                                  "dedication)")
    for collection in settings["collections"]:
        top = collection.split("/")[0]
        abbreviation, pali_name, english_name = COLLECTIONS.get(top, (top.upper(), top, top))
        translations = read_translations(raw, settings["translations"], collection)
        works = 0
        for path in sorted((raw / ROOT / collection).rglob("*.json"), key=sutta_order):
            for sutta, lines in by_sutta(json.loads(path.read_text(encoding="utf-8"))).items():
                name = cite_name(sutta)
                depth = max(len(labels) for labels, _ in lines)
                title = sutta_title(lines)
                english = {t: lines_ for t, lines_ in translations.get(sutta, {}).items()}
                english_title = next((sutta_title(lines_) for t, lines_ in english.items() if t.startswith("en/")),
                                     None)
                aliases = [sutta, name.replace(" ", ""), f"{pali_name} {name.split(' ', 1)[-1]}"]
                if top in COLLECTIONS and top != "kn":
                    aliases.append(f"{english_name} {name.split(' ', 1)[-1]}")
                aliases += [t for t in (title, english_title) if t]
                work = corpus.work(name, joiner=":", levels=LEVELS.get(depth, [f"level {n}" for n in range(1, depth + 1)]),
                                   separator=".", language="pli", aliases=aliases,
                                   titles={k: v for k, v in (("pli", title), ("en", english_title)) if v},
                                   categories=[pali_name] + ([collection] if "/" in collection else []),
                                   date=[-450, -100], date_text="early Buddhist (dating debated)",
                                   description=english_title or title)
                corpus.text(work, "pli", "Mahāsaṅgīti (Pali)", lines, rank=0,
                            source=f"https://suttacentral.net/{sutta}/pli/ms", license="public domain")
                for translation, translated in english.items():
                    lang, version = translator_name(translation)
                    corpus.text(work, lang, version, translated, rank=1,
                                source=f"https://suttacentral.net/{sutta}/{translation}",
                                license="CC0 1.0 (SuttaCentral)")
                works += 1
        print(f"{collection}: {works} suttas", flush=True)

    strata = [{"id": key, "label": f"{pali} ({english})"} for key, (abbr, pali, english) in COLLECTIONS.items()]
    corpus.finish(profile={
        "languages": {"pli": {"name": "Pali", "script": "latin", "search": "plain"},
                      "en": {"name": "English", "script": "latin", "search": "english"}},
        "strata": strata,
        "stratum_rules": [{"stratum": key, "category": [pali]} for key, (abbr, pali, english) in COLLECTIONS.items()],
        "research": {"group_by": "strata"},
        "embedding": {"prefer_languages": ["en"]},
    }, instructions=INSTRUCTIONS)


INSTRUCTIONS = """The Pali discourses of the Buddha (suttas) from SuttaCentral: the Long (DN), Middle (MN), Linked
(SN) and Numbered (AN) Discourses and parts of the Minor Collection, in Pali with English translations (by default
Bhikkhu Sujato's), aligned segment by segment.

Passages are cited as SuttaCentral cites them: "MN 1:3.4" (or "mn1:3.4") is Majjhima Nikāya 1, section 3, line 4;
section 0 holds the titles. A sutta can also be named by its Pali or English title ("Mūlapariyāyasutta").

Answer from the suttas, and only from passages you have read with these tools: research_question or search first
(key terms in Pali or English: "dukkha", "anattā", "jhāna"; diacritics are optional), then get_text(ref, context=3)
on the passages you rely on, and verify_citations on every quote before you answer. Quote the Pali where it matters,
name the translation you quote, and keep your interpretation apart from the text. The relative age of the texts is
debated; do not present the collections as a chronology."""
