import json
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from genericCorpusMcpBuilder.sources.http import download, is_complete

BUCKET = "https://storage.googleapis.com/sefaria-export"
BOOKS_JSON = "https://raw.githubusercontent.com/Sefaria/Sefaria-Export/master/books.json"
NON_ENGLISH_VERSION_TAG = re.compile(r"\[[a-z]{2,3}\]")


def local_path_for(url: str, raw_dir: Path) -> Path:
    return raw_dir / urllib.parse.unquote(url.removeprefix(BUCKET + "/"))


def schema_url(title: str) -> str:
    return f"{BUCKET}/schemas/{urllib.parse.quote(title.replace(' ', '_'))}.json"


def wanted_texts(books: list[dict]) -> list[dict]:
    hebrew_merged = [b for b in books if b["language"] == "Hebrew" and b["versionTitle"] == "merged"]
    english_versions = [
        b for b in books
        if b["language"] == "English" and not NON_ENGLISH_VERSION_TAG.search(b["versionTitle"])
    ]
    return hebrew_merged + english_versions


def plan_downloads(books_index: dict, raw_dir: Path) -> list[tuple[str, Path]]:
    texts = wanted_texts(books_index["books"])
    titles = sorted({b["title"] for b in texts})
    plan = [(b["json_url"], local_path_for(b["json_url"], raw_dir)) for b in texts]
    plan += [(schema_url(t), raw_dir / "schemas" / f"{t}.json") for t in titles]
    plan += [(f["url"], raw_dir / f["path"]) for f in books_index["special_files"]]
    return plan


TOPICS_API = "https://www.sefaria.org/api"
TOPIC_REQUEST_INTERVAL = 0.25


TOPIC_WORKERS = 4


def fetch_topic(raw_dir: Path, slug: str) -> None:
    destination = raw_dir / "topics" / f"{slug}.json"
    if not is_complete(destination):
        download(f"{TOPICS_API}/v2/topics/{urllib.parse.quote(slug)}?with_refs=1&with_links=1", destination)
        time.sleep(TOPIC_REQUEST_INTERVAL)


def fetch_topics(raw_dir: Path) -> None:
    download(f"{TOPICS_API}/topics?limit=0", raw_dir / "topics-index.json")
    slugs = [t["slug"] for t in json.loads((raw_dir / "topics-index.json").read_text())]
    print(f"{len(slugs)} topics", flush=True)
    with ThreadPoolExecutor(max_workers=TOPIC_WORKERS) as pool:
        for done, _ in enumerate(pool.map(lambda slug: fetch_topic(raw_dir, slug), slugs), 1):
            if done % 250 == 0:
                print(f"topics {done}/{len(slugs)}", flush=True)
    print("topics done", flush=True)


def fetch_export(raw_dir: Path) -> None:
    download(BOOKS_JSON, raw_dir / "books.json")
    books_index = json.loads((raw_dir / "books.json").read_text())
    plan = plan_downloads(books_index, raw_dir)
    print(f"{len(plan)} files", flush=True)
    with ThreadPoolExecutor(max_workers=16) as pool:
        for done, _ in enumerate(pool.map(lambda job: download(*job), plan), 1):
            if done % 500 == 0:
                print(f"{done}/{len(plan)}", flush=True)
    print("done", flush=True)


def fetch(raw_dir: Path) -> None:
    """Everything the Sefaria adapter needs: the export (~10 GB) and the topics (API, ~35 min). Resumable."""
    fetch_export(raw_dir)
    fetch_topics(raw_dir)


def main() -> None:
    if sys.argv[1:2] == ["--topics"]:
        fetch_topics(Path(sys.argv[2]))
        return
    fetch_export(Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw"))


if __name__ == "__main__":
    main()
