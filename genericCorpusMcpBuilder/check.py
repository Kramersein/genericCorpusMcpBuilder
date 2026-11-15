"""Check a corpus directory (docs/corpus-format.md) before building it: errors stop the build, warnings are
reported. Run alone with `corpus-mcp check DIR`; `corpus-mcp build` runs it after converting."""
import collections
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from genericCorpusMcpBuilder.citations import VALID_INPUT
from genericCorpusMcpBuilder.corpus_format import iter_jsonl, text_files
from genericCorpusMcpBuilder.profile import SCRIPT_LETTERS, SEARCH_MODES

KINDS = {"number", "folio", "label"}
PROFILE_KEYS = {"languages", "eras", "periods", "strata", "stratum_rules", "research", "trace", "embedding",
                "source_url", "citation_scheme_preference", "tool_descriptions"}
EXAMPLES = 3


class Report:
    def __init__(self):
        self.found = collections.defaultdict(list)          # (severity, problem) -> examples
        self.counts = collections.Counter()

    def add(self, severity: str, problem: str, example: str = "") -> None:
        self.counts[(severity, problem)] += 1
        if len(self.found[(severity, problem)]) < EXAMPLES and example:
            self.found[(severity, problem)].append(example)

    def items(self) -> list[tuple]:
        """(severity, problem, example) for each occurrence, examples where kept (for merging reports)."""
        out = []
        for key, count in self.counts.items():
            examples = self.found[key]
            out += [(*key, examples[i] if i < len(examples) else "") for i in range(count)]
        return out

    def print(self) -> int:
        errors = 0
        for (severity, problem), count in sorted(self.counts.items()):
            errors += count if severity == "error" else 0
            examples = "; ".join(self.found[(severity, problem)])
            print(f"{severity}: {problem} ({count}){': ' + examples if examples else ''}", flush=True)
        return errors


def records(path: Path, report: Report):
    try:
        yield from iter_jsonl(path)
    except (ValueError, UnicodeDecodeError) as error:
        report.add("error", f"{path.name} is not valid JSON lines", str(error)[:80])


SHARED = {}


def share(parts: dict, work_languages: dict, languages: list) -> None:
    SHARED.update(parts=parts, work_languages=work_languages, languages=languages)


def known_part(ref: str, parts: dict) -> bool:
    candidate = ref.strip()
    while True:
        if candidate in parts or candidate.split(":", 1)[0] in parts:    # "MN 1:3.4" (a ":" joiner)
            return True
        if " " not in candidate:
            return False
        candidate = candidate.rsplit(" ", 1)[0]


def check_text(path: Path) -> tuple[list[tuple], str | None]:
    """One text file's problems, and the work it is an original of (if it is)."""
    parts, work_languages, languages = SHARED["parts"], SHARED["work_languages"], SHARED["languages"]
    report = Report()
    lines = records(path, report)
    first = next(lines, None)
    version = (first or {}).get("version")
    if not version:
        report.add("error", "text file does not start with a {\"version\": ...} line", path.name)
        return report.items(), None
    if version.get("work") not in work_languages:
        report.add("error", "version of a work not in works.jsonl", f"{path.name}: {version.get('work')}")
    if version.get("lang") not in languages:
        report.add("error", "version language is not in corpus.json languages", f"{path.name}: {version.get('lang')}")
    original_of = version.get("work") if version.get("lang") == work_languages.get(version.get("work")) else None
    seen, count = set(), 0
    for segment in lines:
        count += 1
        part, cite = segment.get("part"), segment.get("cite")
        if not isinstance(cite, list) or not all(isinstance(label, str) and label for label in cite):
            report.add("error", "cite must be a list of non-empty strings", f"{path.name}: {cite!r}")
            continue
        if not (segment.get("text") or "").strip():
            report.add("warning", "segment without text (left out)", f"{part} {'.'.join(cite)}")
        if part not in parts:
            report.add("warning", "segment in a part not declared in works.jsonl (gets numbered levels)", str(part))
        kinds = parts.get(part, [])
        for depth, label in enumerate(cite):
            kind = kinds[depth] if depth < len(kinds) else "number"
            pattern = VALID_INPUT.get(kind)
            if pattern and not pattern.match(label.lower()):
                report.add("warning", f"label does not fit its level kind '{kind}' (readers cannot type it; "
                                      "declare the level as label)", f"{part} {'/'.join(cite)}")
                break
        key = (part, tuple(label.lower() for label in cite))
        if key in seen:
            report.add("warning", "citation repeated within one version (later text dropped)",
                       f"{path.name}: {part} {'/'.join(cite)}")
        seen.add(key)
    if not count:
        report.add("warning", "version without segments", path.name)
    return report.items(), original_of


def check_profile(directory: Path, languages: list[str], report: Report) -> None:
    """profile.json (if any): known keys, languages, search modes, scripts, strata named by the rules."""
    if (directory / "profile.json").exists():
        try:
            profile = json.loads((directory / "profile.json").read_text())
        except ValueError as error:
            report.add("error", "profile.json is not valid JSON", str(error)[:80])
            profile = {}
        for key in profile:
            if key not in PROFILE_KEYS:
                report.add("warning", "unknown profile.json key (ignored)", key)
        for code, settings in (profile.get("languages") or {}).items():
            if code not in languages:
                report.add("warning", "profile language not in corpus.json languages", code)
            if settings.get("search", "plain") not in SEARCH_MODES:
                report.add("error", "unknown search mode", f"{code}: {settings.get('search')}")
            if settings.get("script", "latin") not in SCRIPT_LETTERS:
                report.add("error", "unknown script", f"{code}: {settings.get('script')}")
        strata = {s.get("id") for s in profile.get("strata") or []}
        for rule in profile.get("stratum_rules") or []:
            if rule.get("stratum") not in strata:
                report.add("error", "stratum rule names an unknown stratum", str(rule.get("stratum")))


def check(corpus: Path) -> int:
    """Prints what it finds; returns the number of errors."""
    corpus, report = Path(corpus), Report()
    if not (corpus / "corpus.json").exists():
        print(f"error: {corpus} has no corpus.json", flush=True)
        return 1
    manifest = json.loads((corpus / "corpus.json").read_text())
    languages = manifest.get("languages") or []
    for key in ("id", "name", "languages"):
        if not manifest.get(key):
            report.add("error", f"corpus.json has no {key}")
    if manifest.get("original_language") and manifest["original_language"] not in languages:
        report.add("error", "corpus.json original_language is not in languages", manifest["original_language"])

    works, parts = {}, {}
    if not (corpus / "works.jsonl").exists():
        report.add("error", "no works.jsonl")
    for work in records(corpus / "works.jsonl", report) if (corpus / "works.jsonl").exists() else []:
        title = work.get("title")
        if not title:
            report.add("error", "a work has no title", json.dumps(work)[:80])
            continue
        if title in works:
            report.add("error", "work title used twice", title)
        works[title] = work
        if work.get("language") and work["language"] not in languages:
            report.add("error", "work language is not in corpus.json languages", f"{title}: {work['language']}")
        for part in work.get("parts") or [{"name": title}]:
            if part.get("name") in parts:
                report.add("error", "part name used twice (citations would be ambiguous)", part.get("name", ""))
            parts[part.get("name")] = [level.get("kind") or "number" for level in part.get("levels") or []]
            for level in part.get("levels") or []:
                if (level.get("kind") or "number") not in KINDS:
                    report.add("error", "unknown level kind", f"{part.get('name')}: {level.get('kind')}")

    files = text_files(corpus)
    if not files:
        report.add("error", "no texts/*.jsonl files")
    originals = collections.Counter()
    context = (parts, {t: w.get("language") or manifest.get("original_language") for t, w in works.items()}, languages)
    with ProcessPoolExecutor(initializer=share, initargs=context) as pool:
        for found, original_of in pool.map(check_text, files, chunksize=8):
            for severity, problem, example in found:
                report.add(severity, problem, example)
            if original_of:
                originals[original_of] += 1
    for title in works:
        if not originals[title]:
            report.add("warning", "work without a version in its original language", title)

    for name in ("citations", "links", "topics"):
        path = corpus / f"{name}.jsonl"
        for entry in records(path, report) if path.exists() else []:
            refs = [entry.get("ref")] if name == "citations" else [entry.get("from"), entry.get("to")] \
                if name == "links" else [s.get("ref") for s in entry.get("sources") or []]
            for ref in refs:
                if not ref or not known_part(ref, parts):
                    report.add("warning", f"{name} ref does not start with a known part name (will be skipped)",
                               str(ref)[:60])

    check_profile(corpus, languages, report)
    errors = report.print()
    total = sum(1 for _ in files)
    print(f"checked {len(works)} works, {len(parts)} parts, {total} versions: "
          f"{errors} error(s), {sum(c for (s, _), c in report.counts.items() if s == 'warning')} warning(s)", flush=True)
    return errors
