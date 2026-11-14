"""Texts in spreadsheets: CSV or TSV files with one row per segment, in reading order.

    corpus-mcp build --source table --raw my-texts/ --option name="My texts" --option original_language=la

Columns (header row, any order, case ignored): work, cite and text are required; cite is the labels joined by the
separator ("1.2", or "1:2" with --option separator=:). Optional: lang (the row's language, default the work's),
version (default "default"), notes, and for the work (taken from its first row): language (what it was written in),
author, date (a year; negative = BCE), levels (level names joined by the separator, e.g. "book.line")."""
import csv
from pathlib import Path

from genericCorpusMcpBuilder.sources.kit import SimpleCorpus, language_code

REQUIRED = ("work", "cite", "text")


def rows(raw: Path):
    files = [raw] if raw.is_file() else sorted([*raw.glob("*.csv"), *raw.glob("*.tsv")])
    if not files:
        raise SystemExit(f"no .csv or .tsv files in {raw} (give them with --raw)")
    for path in files:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle, delimiter="\t" if path.suffix == ".tsv" else ",")
            reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames or []]
            missing = [column for column in REQUIRED if column not in reader.fieldnames]
            if missing:
                raise SystemExit(f"{path.name}: missing column(s) {', '.join(missing)}")
            for number, row in enumerate(reader, 2):
                yield path.name, number, {k: (v or "").strip() for k, v in row.items() if k}


def convert(raw: Path, out: Path, name: str = "Texts", id: str = "table", original_language: str = "en",
            separator: str = ".", license: str = "", **_options) -> None:
    default = language_code(original_language)
    works, versions = {}, {}
    for file, number, row in rows(Path(raw)):
        if not row["work"] or not row["cite"]:
            print(f"{file}:{number}: no work or cite; row skipped", flush=True)
            continue
        works.setdefault(row["work"], row)
        language = language_code(works[row["work"]].get("language"), default)
        key = (row["work"], language_code(row.get("lang"), language), row.get("version") or "default")
        versions.setdefault(key, []).append((row["cite"].split(separator), row["text"], row.get("notes", "")))
    corpus = SimpleCorpus(out, id=id, name=name, original_language=default, source=f"tables in {Path(raw).name}",
                          license=license)
    names = {}
    for title, first in works.items():
        date = first.get("date")
        names[title] = corpus.work(title, levels=first["levels"].split(separator) if first.get("levels") else None,
                                   separator=separator, language=first.get("language") or default,
                                   author=first.get("author") or None,
                                   date=int(date) if date and date.lstrip("-").isdigit() else None)
    for (title, lang, version), segments in versions.items():
        corpus.text(names[title], lang, version, segments)
    corpus.finish()
