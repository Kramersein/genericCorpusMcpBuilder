"""Reading and writing corpus directories (docs/corpus-format.md)."""
import json
import re
from pathlib import Path

UNSAFE_FILE_CHARACTERS = re.compile(r"[^\w.,'() -]+")
MAX_FILE_STEM = 120


def iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def dump(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


def text_file_name(sequence: int, *parts: str) -> str:
    stem = UNSAFE_FILE_CHARACTERS.sub("_", " ".join(p for p in parts if p))[:MAX_FILE_STEM].strip()
    return f"{sequence:06d} {stem}.jsonl"


class TextWriter:
    """One version: the header line, then segments in reading order."""

    def __init__(self, path: Path, version: dict):
        self.handle = open(path, "w", encoding="utf-8")
        self.handle.write(dump({"version": version}))
        self.count = 0

    def segment(self, part: str, cite: list[str], text: str, notes: str = "") -> None:
        record = {"part": part, "cite": cite, "text": text}
        if notes:
            record["notes"] = notes
        self.handle.write(dump(record))
        self.count += 1

    def close(self) -> None:
        self.handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class CorpusWriter:
    def __init__(self, out: Path, manifest: dict | None = None):
        self.out = Path(out)
        (self.out / "texts").mkdir(parents=True, exist_ok=True)
        self.handles = {}
        if manifest is not None:
            self.write_manifest(manifest)

    def write_manifest(self, manifest: dict) -> None:
        (self.out / "corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    def write(self, file: str, record: dict) -> None:
        if file not in self.handles:
            self.handles[file] = open(self.out / f"{file}.jsonl", "w", encoding="utf-8")
        self.handles[file].write(dump(record))

    def work(self, record: dict) -> None:
        self.write("works", record)

    def citation(self, record: dict) -> None:
        self.write("citations", record)

    def link(self, record: dict) -> None:
        self.write("links", record)

    def topic(self, record: dict) -> None:
        self.write("topics", record)

    def text_path(self, name: str) -> Path:
        return self.out / "texts" / name

    def close(self) -> None:
        for handle in self.handles.values():
            handle.close()


def read_manifest(corpus: Path) -> dict:
    return json.loads((Path(corpus) / "corpus.json").read_text())


def text_files(corpus: Path) -> list[Path]:
    return sorted((Path(corpus) / "texts").glob("*.jsonl"))


def optional_records(corpus: Path, name: str):
    path = Path(corpus) / f"{name}.jsonl"
    return iter_jsonl(path) if path.exists() else iter(())
