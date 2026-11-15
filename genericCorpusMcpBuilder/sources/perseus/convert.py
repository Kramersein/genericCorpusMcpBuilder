"""Perseus (raw/<repository>/data/<group>/<work>/, see fetch.py) -> corpus directory.

The generic parts live in genericCorpusMcpBuilder/sources/tei.py (citation structure, text, Stephanus-style sections) and
genericCorpusMcpBuilder/sources/kit.py (level kinds, languages, page citations); this file reads Perseus's CTS metadata
(__cts__.xml: author, title, editions and translations) and adds what Perseus does not have: approximate dates and
the abbreviations people cite by."""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from genericCorpusMcpBuilder.sources.kit import SimpleCorpus, language_code
from genericCorpusMcpBuilder.sources.tei import clean, read_tei

CTS = "{http://chs.harvard.edu/xmlns/cts}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
# Approximate dates (traditional, for ordering only) and common abbreviations for the default works and their authors.
AUTHOR_DATES = {"tlg0012": (-750, "8th c. BCE (traditional)"), "tlg0020": (-700, "c. 700 BCE"),
                "tlg0016": (-430, "c. 430 BCE"), "tlg0003": (-400, "c. 400 BCE"), "tlg0059": (-375, "c. 375 BCE"),
                "tlg0032": (-370, "4th c. BCE"), "tlg0086": (-335, "c. 335 BCE"), "tlg0085": (-470, "5th c. BCE"),
                "tlg0011": (-440, "5th c. BCE"), "tlg0006": (-420, "5th c. BCE"), "tlg0019": (-410, "5th c. BCE"),
                "tlg0007": (100, "c. 100 CE"), "tlg0031": (80, "1st c. CE"), "phi0690": (-20, "c. 29-19 BCE"),
                "phi0474": (-44, "1st c. BCE"), "phi0448": (-50, "1st c. BCE"), "phi0893": (-20, "1st c. BCE"),
                "phi0959": (5, "c. 1-8 CE"), "phi0914": (10, "c. 27 BCE-17 CE"), "phi1351": (115, "c. 115-120 CE")}
ALIASES = {"tlg0012.tlg001": ["Il.", "Homer, Iliad"], "tlg0012.tlg002": ["Od.", "Homer, Odyssey"],
           "tlg0059.tlg030": ["Rep.", "Resp.", "Plato, Republic"], "tlg0003.tlg001": ["Thuc.", "Thucydides"],
           "tlg0016.tlg001": ["Hdt.", "Herodotus"], "phi0690.phi003": ["Aen.", "Verg. A.", "Vergil, Aeneid"],
           "phi0474.phi055": ["Off.", "Cic. Off.", "Cicero, De Officiis"], "phi1351.phi005": ["Ann.", "Tac. Ann."]}


def read_work(work_dir: Path, translations: list[str] | None) -> dict:
    root = ET.parse(work_dir / "__cts__.xml").getroot()
    group_file = work_dir.parent / "__cts__.xml"
    group = ET.parse(group_file).getroot() if group_file.exists() else None
    author = next((clean(g.text or "") for g in group.iter(CTS + "groupname")), "") if group is not None else ""
    titles = {language_code(t.get(XML_LANG)): clean(t.text or "") for t in root.iter(CTS + "title")}
    original = language_code(root.get(XML_LANG))
    versions = []
    for kind in ("edition", "translation"):
        for version in root.iter(CTS + kind):
            urn = version.get("urn")
            path = work_dir / (urn.rsplit(":", 1)[-1] + ".xml")
            # some editions do not state their language: an edition is in the work's language
            lang = language_code(version.get(XML_LANG)) if version.get(XML_LANG) else (
                original if kind == "edition" else "und")
            if path.exists() and (kind == "edition" or translations is None or lang in translations):
                versions.append({"kind": kind, "urn": urn, "path": path, "lang": lang,
                                 "description": next((clean(d.text or "") for d in version.iter(CTS + "description")), ""),
                                 "label": next((clean(l.text or "") for l in version.iter(CTS + "label")), "")})
    edition_label = next((v["label"] for v in versions if v["kind"] == "edition" and v["lang"] == original), "")
    if edition_label and original not in titles:
        titles[original] = edition_label                     # e.g. Ἰλιάς
    work_id = root.get("urn", "").rsplit(":", 1)[-1]
    return {"id": work_id, "urn": root.get("urn"), "author": author, "language": original,
            "title": titles.get("en") or next(iter(titles.values()), work_id), "titles": titles, "versions": versions}


def convert(raw: Path, out: Path, translations=None, **_options) -> None:
    raw, out = Path(raw), Path(out)
    fetched = json.loads((raw / "fetch.json").read_text()) if (raw / "fetch.json").exists() else {}
    wanted = None if translations == "all" else (
        [language_code(t) for t in (translations.split(",") if isinstance(translations, str) else translations)]
        if translations else None)
    works = [read_work(cts.parent, wanted) for cts in sorted(raw.glob("*/data/*/*/__cts__.xml"))]
    works = [w for w in works if any(v["kind"] == "edition" for v in w["versions"])]
    titles = [w["title"] for w in works]
    corpus = SimpleCorpus(out, id="perseus", name="Perseus Digital Library (Greek and Latin texts)",
                          original_language="grc",
                          source="PerseusDL canonical-greekLit and canonical-latinLit (github.com/PerseusDL)",
                          license="CC BY-SA 4.0 for the Perseus texts; see each repository's license",
                          exported_at=fetched.get("fetched_at", "unknown"),
                          version="master branch, " + ", ".join(fetched.get("repositories") or []))
    for work in works:
        # a title shared by two authors' works is cited with the author: "Xenophon, Apology"
        name = work["title"] if titles.count(work["title"]) == 1 else f"{work['author']}, {work['title']}"
        convert_work(corpus, work, name)
    corpus.finish(profile_dir=Path(__file__).parent)


def convert_work(corpus: SimpleCorpus, work: dict, name: str, dates: dict | None = None,
                 aliases: dict | None = None, categories: list[str] | None = None) -> None:
    """One CTS work (from read_work) into the corpus. dates ({author id: (year, text)}) and aliases ({work id:
    [...]}) default to Perseus's tables; categories default to the author."""
    dates = AUTHOR_DATES if dates is None else dates
    aliases = ALIASES if aliases is None else aliases
    texts = [(version, read_tei(version["path"], report=lambda m: print(m, flush=True))) for version in work["versions"]]
    texts = [(version, text) for version, text in texts if text and text.segments]
    originals = [text for version, text in texts if version["lang"] == work["language"]]
    if not originals:
        print(f"skipped {work['id']} {work['title']}: no readable original", flush=True)
        return
    group = work["id"].split(".")[0]
    date, date_text = dates.get(group, (None, None))
    corpus.work(work["title"], name=name, levels=originals[0].level_names, language=work["language"],
                author=work["author"] or None, date=date, date_text=date_text,
                titles={k: v for k, v in work["titles"].items() if v != name},
                categories=categories, aliases=[work["id"], work["urn"], *work["titles"].values(), *aliases.get(work["id"], []),
                         *([f"{work['author']} {work['title']}"] if work["author"] else [])],
                description=texts[0][0]["description"] or None, keep_cites=True)
    ranks: dict[str, int] = {}
    for version, text in texts:
        rank = ranks[version["lang"]] = ranks.get(version["lang"], -1) + 1
        corpus.text(name, version["lang"], version["urn"].rsplit(":", 1)[-1], text.segments,
                    source=version["description"] or version["urn"], rank=rank, urn=version["urn"],
                    **({"license": text.license} if text.license else {}))
    schemes = {}
    for _, text in texts:
        for scheme, count in text.split_schemes.items():
            schemes[scheme] = schemes.get(scheme, 0) + count
    if schemes:
        corpus.page_citations(name, max(schemes, key=schemes.get))


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
