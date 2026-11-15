"""Download Perseus texts (TEI XML with CTS metadata) from the PerseusDL GitHub repositories into raw/<repo>/data/...,
mirroring the repositories' layout."""
import datetime
import io
import json
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from genericCorpusMcpBuilder.sources.http import USER_AGENT, download

REPOSITORIES = {"greekLit": "PerseusDL/canonical-greekLit", "latinLit": "PerseusDL/canonical-latinLit"}
RAW_FILE = "https://raw.githubusercontent.com/{repository}/master/data/{path}"
TREE = "https://api.github.com/repos/{repository}/git/trees/master?recursive=1"
ARCHIVE = "https://codeload.github.com/{repository}/tar.gz/refs/heads/master"
CTS = "{http://chs.harvard.edu/xmlns/cts}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
# A starting set: Homer, Plato's Republic, Thucydides, Herodotus; Vergil's Aeneid, Cicero's De Officiis, Tacitus'
# Annals. Any CTS work id from the repositories can be given instead (works=tlg0031.tlg001,...).
DEFAULT_WORKS = {
    "greekLit": ["tlg0012.tlg001", "tlg0012.tlg002", "tlg0059.tlg030", "tlg0003.tlg001", "tlg0016.tlg001"],
    "latinLit": ["phi0690.phi003", "phi0474.phi055", "phi1351.phi005"],
}


def option_list(value, default: list[str]) -> list[str]:
    if value is None:
        return default
    return [item.strip() for item in (value.split(",") if isinstance(value, str) else value) if item.strip()]


def repository_of(work_id: str) -> str:
    """TLG numbers (tlg...) are Greek; the Latin repository uses PHI numbers (phi...) and others (stoa...)."""
    return "greekLit" if work_id.startswith("tlg") else "latinLit"


def works_of_group(repository_path: str, group: str, cache: dict = {}) -> list[str]:
    """The work ids of an author (textgroup) in a CTS repository on GitHub: one listing call per repository."""
    if repository_path not in cache:
        request = urllib.request.Request(TREE.format(repository=repository_path), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response:
            cache[repository_path] = [e["path"] for e in json.load(response)["tree"]]
    works = {path.split("/")[2] for path in cache[repository_path]
             if path.startswith(f"data/{group}/") and path.endswith("/__cts__.xml") and path.count("/") == 3}
    return sorted(f"{group}.{work}" for work in works)


def fetch_work(raw: Path, repository: str, work_id: str, translations: list[str],
               repositories: dict = REPOSITORIES) -> int:
    """Download one work (CTS id, e.g. tlg0012.tlg001) or, given an author id (tlg0012), all of that author's works.
    Returns the number of versions downloaded."""
    if "." not in work_id:
        return sum(fetch_work(raw, repository, work, translations, repositories)
                   for work in works_of_group(repositories[repository], work_id))
    group, work = work_id.split(".", 1)
    base = raw / repository / "data"
    url = lambda path: RAW_FILE.format(repository=repositories[repository], path=path)
    download(url(f"{group}/__cts__.xml"), base / group / "__cts__.xml")
    download(url(f"{group}/{work}/__cts__.xml"), base / group / work / "__cts__.xml")
    metadata = base / group / work / "__cts__.xml"
    if not metadata.exists():
        print(f"no such work in {repository}: {work_id}", flush=True)
        return 0
    count = 0
    for version in ET.parse(metadata).getroot():
        kind = version.tag.removeprefix(CTS)
        if kind not in ("edition", "translation"):
            continue
        if kind == "translation" and "all" not in translations and version.get(XML_LANG) not in translations:
            continue
        name = version.get("urn").rsplit(":", 1)[-1] + ".xml"
        download(url(f"{group}/{work}/{name}"), base / group / work / name)
        count += (base / group / work / name).exists()
    return count


def fetch_everything(raw: Path, repository: str) -> None:
    """The whole repository (hundreds of MB), extracted with tarfile's data filter (no paths outside raw)."""
    request = urllib.request.Request(ARCHIVE.format(repository=REPOSITORIES[repository]),
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as response:
        archive = tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz")
    target = raw / repository
    target.mkdir(parents=True, exist_ok=True)
    members = []
    for member in archive.getmembers():
        parts = member.name.split("/", 1)
        if len(parts) == 2 and parts[1].startswith("data/") and (member.isfile() or member.isdir()):
            member.name = parts[1]
            members.append(member)
    archive.extractall(target, members=members, filter="data")


def fetch(raw: Path, repositories=None, works=None, translations=None) -> None:
    """Options: repositories (greekLit,latinLit), works (CTS ids like tlg0012.tlg001, or "all"),
    translations (CTS language codes of translations to fetch, default eng; "all" for every language)."""
    raw = Path(raw)
    wanted = option_list(translations, ["eng"])
    chosen = option_list(repositories, list(REPOSITORIES))
    for repository in chosen:
        if repository not in REPOSITORIES:
            raise SystemExit(f"unknown Perseus repository {repository!r}; known: {', '.join(REPOSITORIES)}")
        work_ids = option_list(works, DEFAULT_WORKS[repository])
        if work_ids == ["all"]:
            print(f"downloading all of {REPOSITORIES[repository]}", flush=True)
            fetch_everything(raw, repository)
            continue
        for work_id in work_ids:
            if len(chosen) == 1 or repository_of(work_id) == repository:
                print(f"{work_id}: {fetch_work(raw, repository, work_id, wanted)} versions", flush=True)
    (raw / "fetch.json").write_text(json.dumps({
        "repositories": chosen, "works": works or "default", "translations": wanted,
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}, indent=2))
