"""Sefaria export (data/raw) -> corpus directory. Everything that knows Sefaria's file formats lives here."""
import csv
import json
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from genericCorpusMcpBuilder.corpus_format import CorpusWriter, TextWriter, text_file_name
from genericCorpusMcpBuilder.sources.sefaria.fetch import local_path_for, wanted_texts
from genericCorpusMcpBuilder.text import clean_display_text

FOLIO_LETTERS = "abcd"
LEVEL_KINDS = {"Talmud": "folio", "Folio": "folio", "Amud": "label"}
PAGE_SCHEMES = {"Vilna", "Venice", "Pages", "Daf", "Vavim and Amudim"}
SCHEMES_WITH_OWN_SUBDIVISIONS = PAGE_SCHEMES | {"Parasha"}
PARASHA_PREFIXES = ("parashat ", "parshat ", "parsha ", "פרשת ")
TOPIC_RELATIONS = {"related-to", "is-a", "is-category-of", "has-specific-dependence", "displays-under",
                   "temporally-contains", "temporally-contained-in"}
PROFILE_FILES = ("profile.json", "instructions.md", "answer_prompt.md")
MANIFEST = {
    "id": "sefaria", "name": "Sefaria", "source": "Sefaria export (gs://sefaria-export)",
    "license": "per version, as given by Sefaria (public domain, CC BY, CC BY-SA, CC BY-NC and others)",
    "languages": ["he", "en"], "original_language": "he",
}


# Sefaria numbers every level; these turn its numbers into the labels people cite (Talmud 145 -> 73a).

def section_label(number: int, address_type: str) -> str:
    match address_type:
        case "Talmud":
            return f"{(number + 1) // 2}{'a' if number % 2 else 'b'}"
        case "Folio":
            return f"{(number - 1) // 4 + 1}{FOLIO_LETTERS[(number - 1) % 4]}"
        case "Amud":
            return "a" if number == 1 else "b"
        case "Year":
            return str(number + 1240)
        case _:
            return str(number)


def section_number(label: str, address_type: str) -> int:
    match address_type:
        case "Talmud":
            daf, amud = (int(label[:-1]), label[-1]) if label[-1].isalpha() else (int(label), "a")
            return daf * 2 - (1 if amud == "a" else 0)
        case "Folio":
            daf, letter = (int(label[:-1]), label[-1]) if label[-1].isalpha() else (int(label), "a")
            return (daf - 1) * 4 + FOLIO_LETTERS.index(letter) + 1
        case "Amud":
            return 1 if label.lower() == "a" else 2
        case "Year":
            return int(label) - 1240
        case _:
            return int(label)


def padded_types(address_types: list[str], depth: int) -> list[str]:
    return [address_types[i] if i < len(address_types) else "Integer" for i in range(depth)]


def labels_for(numbers: list[int], address_types: list[str]) -> list[str]:
    return [section_label(n, t) for n, t in zip(numbers, padded_types(address_types, len(numbers)))]


def format_address(numbers: list[int], address_types: list[str]) -> str:
    return ":".join(labels_for(numbers, address_types))


def parse_address(address: str, address_types: list[str]) -> list[int]:
    tokens = address.split(":")
    return [section_number(t, k) for t, k in zip(tokens, padded_types(address_types, len(tokens)))]


# Schemas

def primary_text(titles: list[dict], lang: str) -> str | None:
    return next((t["text"] for t in titles if t.get("lang") == lang and t.get("primary")), None)


def node_titles(node: dict) -> tuple[str, str]:
    titles = node.get("titles", [])
    en = primary_text(titles, "en") or node.get("title") or node.get("key", "")
    he = primary_text(titles, "he") or node.get("heTitle") or ""
    return en, he


def leaf_nodes(node: dict, en_path: list[str], he_path: list[str], text_path: tuple):
    if "nodes" not in node:
        yield text_path, {
            "base": ", ".join(en_path),
            "he_base": ", ".join(p for p in he_path if p),
            "address_types": node.get("addressTypes", []),
            "section_names": node.get("sectionNames", []),
        }
        return
    for child in node["nodes"]:
        child_en, child_he = node_titles(child)
        if child.get("default"):
            yield from leaf_nodes(child, en_path, he_path, text_path + ("",))
        else:
            yield from leaf_nodes(child, en_path + [child_en], he_path + [child_he], text_path + (child_en,))


def work_nodes(schema: dict) -> dict[tuple, dict]:
    root = schema["schema"]
    root_en, root_he = node_titles(root)
    return dict(leaf_nodes(root, [schema["title"] or root_en], [schema.get("heTitle") or root_he], ()))


def english_name(value) -> str | None:
    return value.get("en") if isinstance(value, dict) else value


def base_title(entry) -> str:
    return entry if isinstance(entry, str) else (entry.get("en") or entry.get("title") or "")


def part_record(node: dict) -> dict:
    types, names = node["address_types"], node["section_names"]
    levels = []
    for i in range(max(len(types), len(names))):
        level = {"name": names[i] if i < len(names) else types[i]}
        kind = LEVEL_KINDS.get(types[i]) if i < len(types) else None
        if kind:
            level["kind"] = kind
        levels.append(level)
    record = {"name": node["base"], "levels": levels, "separator": ":"}
    if node["he_base"]:
        record["titles"] = {"he": node["he_base"]}
    return record


def work_record(schema: dict, nodes: dict[tuple, dict]) -> dict:
    names = {schema["title"], schema.get("heTitle") or ""}
    names |= set(schema.get("titleVariants", [])) | set(schema.get("heTitleVariants", []))
    names |= {t["text"] for t in schema.get("schema", {}).get("titles", [])}
    record = {
        "title": schema["title"],
        "aliases": sorted({n.strip() for n in names if n and n.strip()}),
        "categories": schema.get("categories", []),
        "authors": [a.get("en") for a in schema.get("authors", [])],
        "date": schema.get("compDate") or None,
        "date_text": english_name(schema.get("compDateString")),
        "era": schema.get("era"),
        "dependence": schema.get("dependence"),
        "comments_on": [base_title(t) for t in schema.get("base_text_titles", [])],
        "collective_title": english_name(schema.get("collective_title")),
        "description": schema.get("enShortDesc"),
        "parts": [part_record(n) for n in nodes.values()],
    }
    if schema.get("heTitle"):
        record["titles"] = {"he": schema["heTitle"]}
    return record


def load_schemas(raw_dir: Path) -> dict[str, dict]:
    schemas = {}
    for schema_file in sorted((raw_dir / "schemas").glob("*.json")):
        try:
            schema = json.loads(schema_file.read_text())
        except ValueError:
            continue
        if "schema" in schema:
            schemas[schema["title"]] = schema
    return schemas


# Texts

def jagged_leaves(value, numbers: list[int]):
    if isinstance(value, str):
        if value.strip():
            yield numbers, value
    elif isinstance(value, list):
        for i, child in enumerate(value, 1):
            yield from jagged_leaves(child, numbers + [i])


def text_sections(text, nodes: dict[tuple, dict], title: str, path: tuple = ()):
    if isinstance(text, dict):
        for key, value in text.items():
            yield from text_sections(value, nodes, title, path + (key,))
        return
    node = nodes.get(path) or {"base": ", ".join([title] + [p for p in path if p]), "address_types": []}
    yield node, text


def is_english_priority_list(path: Path, lang: str) -> bool:
    return lang == "en" and path.name == "merged.json"


def read_priority(path: str) -> tuple[str, list[str]] | None:
    try:
        document = json.loads(Path(path).read_text())
    except ValueError:
        return None
    return document["title"], [v[0].strip() for v in document.get("versions", [])]


def convert_text(job: tuple) -> str | None:
    source, lang, nodes_by_path, destination, priority = job
    try:
        document = json.loads(Path(source).read_text())
    except ValueError as error:
        return f"skipped unreadable {source}: {error}"
    title = document["title"]
    version_title = document.get("versionTitle")
    version = {"work": title, "lang": lang, "title": version_title, "source": document.get("versionSource"),
               "merged_from": document.get("versions", [])}
    if lang == "en" and version_title and version_title.strip() in priority:
        version["rank"] = priority.index(version_title.strip())
    with TextWriter(Path(destination), version) as out:
        for node, section in text_sections(document["text"], nodes_by_path, title):
            for numbers, raw in jagged_leaves(section, []):
                display, notes = clean_display_text(raw)
                if display:
                    out.segment(node["base"], labels_for(numbers, node["address_types"]), display, notes)
    return None


def convert_texts(raw_dir: Path, writer: CorpusWriter, nodes_by_title: dict[str, dict]) -> None:
    books = json.loads((raw_dir / "books.json").read_text())["books"]
    available = []
    for book in wanted_texts(books):
        path = local_path_for(book["json_url"], raw_dir)
        if path.exists():
            available.append((book, path, "he" if book["language"] == "Hebrew" else "en"))
    with ProcessPoolExecutor() as pool:
        priority_files = [str(p) for _, p, lang in available if is_english_priority_list(p, lang)]
        priorities = dict(filter(None, pool.map(read_priority, priority_files, chunksize=8)))
        jobs = []
        for book, path, lang in available:
            if is_english_priority_list(path, lang):
                continue
            name = text_file_name(len(jobs), book["title"], lang, book.get("versionTitle", ""))
            jobs.append((str(path), lang, nodes_by_title.get(book["title"], {}), str(writer.text_path(name)),
                         priorities.get(book["title"], [])))
        for done, problem in enumerate(pool.map(convert_text, jobs, chunksize=4), 1):
            if problem:
                print(problem, flush=True)
            if done % 500 == 0:
                print(f"texts {done}/{len(jobs)}", flush=True)


# Links, other citation systems, topics

def convert_links(raw_dir: Path, writer: CorpusWriter) -> None:
    count = 0
    for csv_path in sorted((raw_dir / "links").glob("links[0-9]*.csv")):
        with open(csv_path, newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            for row in reader:
                if len(row) >= 7:
                    writer.link({"from": row[0], "to": row[1], "type": row[2]})
                    count += 1
    print(f"links {count}", flush=True)


def alt_node_labels(node: dict) -> tuple[list[str], str]:
    en, he = node_titles(node)
    parts = [part.strip() for part in en.split(";") if part.strip()]
    named_first = parts[1:] + parts[:1] if len(parts) > 1 and parts[0].lower().startswith("chapter") else parts
    variants = [t["text"] for t in node.get("titles", []) if t.get("text") and t.get("lang") != "he"]
    return list(dict.fromkeys(named_first + [en] + variants)), he


def alt_entries(scheme: str, node: dict):
    labels, he_label = alt_node_labels(node)
    if node.get("wholeRef"):
        yield labels, he_label, node["wholeRef"]
    types = node.get("addressTypes") or ["Integer"]
    sub_refs = node.get("refs", []) if scheme in SCHEMES_WITH_OWN_SUBDIVISIONS else []
    for index, sub_ref in enumerate(sub_refs):
        address = format_address([index + 1 + node.get("offset", 0)], types)
        if scheme in PAGE_SCHEMES:
            sub_labels = [f"{scheme} {address}", address] + [f"{label} {address}" for label in labels]
        else:
            sub_labels = [f"{label} {address}" for label in labels] + [f"{scheme} {address}"]
        yield sub_labels, f"{he_label} {address}".strip(), sub_ref
    for child in node.get("nodes", []):
        yield from alt_entries(scheme, child)


def convert_citations(schemas: dict[str, dict], writer: CorpusWriter) -> None:
    count = 0
    for title, schema in schemas.items():
        is_base_text = not schema.get("dependence")
        for scheme, structure in (schema.get("alts") or {}).items():
            for child in structure.get("nodes", []):
                for labels, he_label, ref in alt_entries(scheme, child):
                    if not labels:
                        continue
                    record = {"work": title, "scheme": scheme, "labels": labels, "ref": ref}
                    if he_label:
                        record["titles"] = {"he": he_label}
                    if scheme == "Parasha" and is_base_text:
                        names = {label.lower() for label in labels} | ({he_label.lower()} if he_label else set())
                        record["global_labels"] = sorted(prefix + name for name in names for prefix in PARASHA_PREFIXES)
                    writer.citation(record)
                    count += 1
    print(f"alternative citation entries {count}", flush=True)


def convert_topics(raw_dir: Path, writer: CorpusWriter) -> None:
    count = 0
    for topic_file in sorted((raw_dir / "topics").glob("*.json")):
        try:
            topic = json.loads(topic_file.read_text())
        except ValueError:
            continue
        slug = topic.get("slug")
        sources = [r for r in (topic.get("refs") or {}).get("about", {}).get("refs", []) if not r.get("is_sheet")]
        if not slug or not sources:
            continue
        writer.topic({
            "slug": slug,
            "name": {"en": english_name(topic.get("primaryTitle")), "he": (topic.get("primaryTitle") or {}).get("he")},
            "names": [t["text"] for t in topic.get("titles", []) if t.get("text")],
            "description": english_name(topic.get("description")) or english_name(topic.get("categoryDescription")) or "",
            "sources": [{"ref": r["ref"], "relevance": (r.get("order") or {}).get("pr") or 0.0} for r in sources],
            "links": [{"topic": link["topic"], "relation": relation}
                      for relation, group in (topic.get("links") or {}).items() if relation in TOPIC_RELATIONS
                      for link in group.get("links", [])],
        })
        count += 1
    print(f"topics with sources {count}", flush=True)


def convert(raw_dir: Path, out: Path) -> None:
    raw_dir, out = Path(raw_dir), Path(out)
    export_file = raw_dir / "last_export.txt"
    manifest = {**MANIFEST,
                "source_exported_at": export_file.read_text().strip() if export_file.exists() else "unknown",
                "source_version": json.loads((raw_dir / "books.json").read_text()).get("generated_at", "unknown")}
    writer = CorpusWriter(out, manifest)
    for name in PROFILE_FILES:
        shutil.copy(Path(__file__).parent / name, out / name)
    schemas = load_schemas(raw_dir)
    nodes_by_title = {}
    for title, schema in schemas.items():
        nodes_by_title[title] = work_nodes(schema)
        writer.work(work_record(schema, nodes_by_title[title]))
    print(f"works {len(schemas)}", flush=True)
    convert_texts(raw_dir, writer, nodes_by_title)
    convert_links(raw_dir, writer)
    convert_citations(schemas, writer)
    if (raw_dir / "topics").exists():
        convert_topics(raw_dir, writer)
    writer.close()


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
