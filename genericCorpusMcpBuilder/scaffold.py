"""`corpus-mcp new-adapter NAME`: a working adapter to start from (docs/adapters.md)."""
import json
import re
from pathlib import Path

TEMPLATE = '''"""{name}: <what this source is, where it comes from, its license>.

Build it with
    corpus-mcp build --source {path} --data data-{name}
and check what it wrote with
    corpus-mcp check data-{name}/corpus

This template works as it is: fetch() writes two example files into raw/ and convert() reads every raw/*.txt
(one segment per line: the citation, a tab, the text). Replace both with your source."""
from pathlib import Path

from genericCorpusMcpBuilder.sources.kit import SimpleCorpus

HERE = Path(__file__).parent


def fetch(raw: Path, **options) -> None:
    """Download the source into raw/ (leave fetch out entirely if your files are local: pass them with --raw).
    genericCorpusMcpBuilder.sources.http.download(url, path) downloads resumably."""
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "genesis.he.txt").write_text("1.1\\tבראשית ברא אלהים את השמים ואת הארץ\\n1.2\\tוהארץ היתה תהו ובהו\\n")
    (raw / "genesis.en.txt").write_text("1.1\\tIn the beginning God created the heaven and the earth.\\n"
                                       "1.2\\tAnd the earth was without form, and void.\\n")


def convert(raw: Path, out: Path, **options) -> None:
    """Read raw/ and write the corpus directory out/. options are the --option KEY=VALUE pairs."""
    corpus = SimpleCorpus(out, id="{name}", name="{title}", original_language="he",
                          source="<where the texts come from>", license="<license>")
    work = corpus.work("Genesis", levels=["chapter", "verse"], language="he", date=[-1000, -500],
                       aliases=["Gen."], categories=["Torah"])
    for path in sorted(Path(raw).glob("*.txt")):
        language = path.suffixes[0].lstrip(".")                 # genesis.en.txt -> en
        segments = [line.split("\\t", 1) for line in path.read_text().splitlines() if "\\t" in line]
        corpus.text(work, language, f"example {{language}}", segments, source=str(path.name))
    corpus.finish(profile_dir=HERE)                             # copies profile.json, instructions.md, answer_prompt.md
'''

PROFILE = {
    "languages": {"he": {"name": "Hebrew", "script": "hebrew", "search": "hebrew"},
                  "en": {"name": "English", "script": "latin", "search": "english"}},
    "periods": [{"label": "Before the common era", "until": -1}, {"label": "Common era"}],
    "research": {"group_by": "date"},
}

INSTRUCTIONS = """{title}: <one paragraph on what the texts are, for the AI>.

Answer from the texts, and only from passages you have read with these tools:
1. research_question(question, key_terms) first; key_terms in the original language.
2. Read the key passages in full with get_text(ref, context=2).
3. Answer with the passages from earliest to latest, each with its citation and a short quote with translation.
4. Before answering, pass every citation and quote to verify_citations and use the cite_as it returns.
"""


def write_adapter(name: str, directory: Path) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SystemExit("the adapter name must be a Python identifier, e.g. my_texts")
    folder = directory / name
    if folder.exists():
        raise SystemExit(f"{folder} exists already")
    folder.mkdir(parents=True)
    title = name.replace("_", " ").title()
    (folder / "__init__.py").write_text(TEMPLATE.format(name=name, title=title, path=folder))
    (folder / "profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False, indent=2) + "\n")
    (folder / "instructions.md").write_text(INSTRUCTIONS.format(title=title))
    print(f"wrote {folder}/__init__.py, profile.json and instructions.md\n"
          f"try it:  corpus-mcp build --source {folder} --data data-{name} --no-vectors\n"
          f"then:    corpus-mcp serve --data data-{name}", flush=True)
