"""Any folder of TEI XML files that declare their citation structure (refsDecl/cRefPattern, as Perseus, First1KGreek
and many digital editions do).

    corpus-mcp build --source tei --raw my-editions/ --option name="My editions" --option original_language=lat

Layout: each subfolder of the folder is one work and its files are that work's versions (the original and its
translations); a file directly in the folder is a work of its own. Title, author and language come from each file's
header. The original is the version in original_language (default: the language of the work's first file by name)."""
import collections
from pathlib import Path

from genericCorpusMcpBuilder.sources.kit import SimpleCorpus, language_code
from genericCorpusMcpBuilder.sources.tei import read_tei


def work_groups(raw: Path) -> list[list[Path]]:
    groups = [[path] for path in sorted(raw.glob("*.xml"))]
    groups += [sorted(folder.rglob("*.xml")) for folder in sorted(p for p in raw.iterdir() if p.is_dir())]
    return [group for group in groups if group]


def convert(raw: Path, out: Path, name: str = "TEI editions", id: str = "tei", original_language: str | None = None,
            license: str = "", **_options) -> None:
    raw = Path(raw)
    if not raw.is_dir():
        raise SystemExit(f"the tei source reads a folder of TEI files: give it with --raw (not found: {raw})")
    report = lambda message: print(message, flush=True)
    texts = [[t for t in (read_tei(path, report) for path in group) if t and t.segments] for group in work_groups(raw)]
    texts = [group for group in texts if group]
    languages = collections.Counter(language_code(t.language) for group in texts for t in group)
    default = language_code(original_language) if original_language else (languages.most_common(1)[0][0] if languages else "und")
    corpus = SimpleCorpus(out, id=id, name=name, original_language=default, source=f"TEI files from {raw.name}",
                          license=license)
    for group in texts:
        original = next((t for t in group if language_code(t.language) == default), group[0])
        work = corpus.work(original.title or original.path.stem, levels=original.level_names,
                           language=language_code(original.language, default), author=original.author or None,
                           keep_cites=True)
        ordered = [original] + [t for t in group if t is not original]
        for text in ordered:
            corpus.text(work, language_code(text.language, default), text.path.stem, text.segments,
                        source=text.title or text.path.name)
        schemes = collections.Counter()
        for text in group:
            schemes.update(text.split_schemes)
        if schemes:
            corpus.page_citations(work, schemes.most_common(1)[0][0])
    corpus.finish()
