import inspect
import os
import threading
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from genericCorpusMcpBuilder.corpus import Corpus, RefNotFound
from genericCorpusMcpBuilder.precompute import Precomputed
from genericCorpusMcpBuilder.research import Researcher

DATA_DIR = Path(os.environ.get("CORPUS_MCP_DATA", "data")).expanduser()
TRANSPORT = os.environ.get("CORPUS_MCP_TRANSPORT", "http")
HOST = os.environ.get("CORPUS_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("CORPUS_MCP_PORT", "8765"))
# DNS-rebinding protection: Host and Origin headers the HTTP server accepts. Add your public name when serving
# behind a reverse proxy, e.g. CORPUS_MCP_ALLOWED_HOSTS=127.0.0.1:*,localhost:*,mcp.example.org
ALLOWED_HOSTS = [h for h in os.environ.get("CORPUS_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*").split(",") if h]
ALLOWED_ORIGINS = [o for o in os.environ.get("CORPUS_MCP_ALLOWED_ORIGINS", "").split(",") if o]

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False, idempotent_hint=True)

corpus = Corpus(str(DATA_DIR / "corpus.db"))
profile = corpus.profile
precomputed = Precomputed(DATA_DIR / "precomputed.db", corpus.build_id)
server = MCPServer(corpus.manifest.get("corpus_id") or "corpus", instructions=profile.instructions, version="0.4.0")
semantic_state = {"index": None, "loaded": False}
semantic_lock = threading.Lock()
LANGUAGES = ", ".join(f"{code} ({lang['name']})" for code, lang in profile.languages.items())
TextLanguage = Literal[("both", *profile.languages)]
SearchLanguage = Literal[("auto", *profile.languages)]


HAS_TOPICS = corpus.has_table("topics") and corpus.db.execute("SELECT 1 FROM topics LIMIT 1").fetchone() is not None
HAS_LINKS = corpus.db.execute("SELECT 1 FROM links LIMIT 1").fetchone() is not None


def tool(when: bool = True, **options):
    """Register a tool (only if `when`: tools without data in this corpus are left out), with the corpus
    profile's description if it has one, else the docstring."""
    def register(fn):
        if not when:
            return fn
        description = profile.tool_description(fn.__name__, inspect.cleandoc(fn.__doc__ or ""))
        return server.tool(description=description, annotations=READ_ONLY, **options)(fn)
    return register


def semantic():
    with semantic_lock:
        if not semantic_state["loaded"]:
            semantic_state["loaded"] = True
            if (DATA_DIR / "vectors" / "vectors.json").exists():
                from genericCorpusMcpBuilder.vectors import SemanticIndex
                semantic_state["index"] = SemanticIndex(str(DATA_DIR / "vectors"), corpus.build_id)
                semantic_state["index"].embed("warm up")
        return semantic_state["index"]


def researcher() -> Researcher:
    return Researcher(corpus, semantic(), precomputed)


def failure(error: Exception) -> dict:
    return {"error": str(error)}


def chosen_languages(language: str) -> tuple[str, ...] | None:
    return None if language == "both" else (language,)


@tool()
def research_question(question: str, key_terms: list[str] | None = None, max_sources: int = 12) -> dict:
    """START HERE for any question. Finds primary sources for a question in plain language by meaning, by curated
    topics and by key_terms, and returns them grouped (by period or stratum) with short excerpts, matching topics
    and next steps. key_terms: 2-4 words in the original language that a scholar would search for; they sharpen
    results a lot. Excerpts are leads: read sources in full with get_text before relying on them."""
    try:
        return researcher().research(question, key_terms, max(3, min(max_sources, 25)))
    except ValueError as error:
        return failure(error)


@tool(when=HAS_TOPICS)
def explore_topic(name: str, max_sources: int = 25) -> dict:
    """A curated topic: its description, related topics, and its sources grouped with short excerpts. Use it to give
    an overview of a subject or to find sources research_question missed."""
    return researcher().explore_topic(name, max(5, min(max_sources, 60)))


@tool()
def get_text(ref: str, language: TextLanguage = "both", english_version: str | None = None, context: int = 0) -> dict:
    """Exact text of a reference or range, a passage named in another citation system, or words of one segment
    (ref@first-last). context=N adds N segments before and after, to read a passage in context. Returns the original
    and the translations per segment with the version used for each line, other names for the passage, a fingerprint
    of the exact text and the build id. language: "both" or a language code; english_version picks a translation
    (see get_work_info)."""
    try:
        return corpus.get_text(ref, chosen_languages(language), english_version, max(0, min(context, 10)))
    except RefNotFound as error:
        return failure(error)


@tool(when=HAS_LINKS)
def get_commentary(ref: str, works: list[str] | None = None, per_work: int = 2) -> dict:
    """What the commentators say on a passage: excerpts from each commentary work, earliest first. works narrows to
    specific titles."""
    try:
        return corpus.commentaries(ref, works, per_work=max(1, min(per_work, 5)))
    except RefNotFound as error:
        return failure(error)


if profile.trace:
    def trace_sources(ref: str, depth: int = 2) -> dict:
        try:
            return corpus.trace(ref, max(1, min(depth, 3)), profile.trace.get("categories"))
        except RefNotFound as error:
            return failure(error)

    trace_sources.__name__ = trace_sources.__qualname__ = profile.trace.get("tool", "trace_sources")
    server.tool(name=trace_sources.__name__, annotations=READ_ONLY,
                description=profile.trace.get("description") or
                "Follow the link graph from a ref to earlier sources and later works, by era and date. depth 1-3."
                )(trace_sources)


@tool()
def verify_citations(citations: list[dict]) -> dict:
    """CALL BEFORE ANSWERING. Checks a list of citations, each {"ref": ..., "quote": optional}: the ref must exist
    and the quote must appear there, ignoring accents and punctuation. Returns all_verified, the failures, and for
    each match a word-exact citation (cite_as), the original words, and the build and text fingerprint it was
    verified against. For a failed quote it gives the closest match and where else the quote appears."""
    return corpus.verify_citations(citations[:50])


@tool()
def find_quote(quote: str, limit: int = 20) -> list[dict] | dict:
    """Where does this phrase come from? Finds a quotation anywhere in the corpus, earliest work first, with a
    word-exact citation for each occurrence. Dates follow the corpus's attributions, so check the era before
    calling something the earliest source."""
    try:
        return corpus.find_quote(quote, limit)
    except ValueError as error:
        return failure(error)


@tool()
def search(query: str, categories: list[str] | None = None, works: list[str] | None = None,
           language: SearchLanguage = "auto",
           exact_phrase: bool = False, limit: int = 20) -> list[dict] | dict:
    """Keyword search when you know the words: all words must appear (exact_phrase for word order). language "auto"
    searches the languages written in the query's script; categories and works (exact titles) narrow it."""
    try:
        return corpus.search(query, categories, works, None if language == "auto" else language, exact_phrase, limit)
    except ValueError as error:
        return failure(error)


@tool()
def semantic_search(question: str, categories: list[str] | None = None, limit: int = 15) -> list[dict] | dict:
    """Passages by meaning only, across the main works. research_question already uses this; call it directly for a
    narrower follow-up (e.g. one category)."""
    index = semantic()
    if not index:
        return {"error": "semantic index not installed on this server; use research_question or search"}
    return index.search(question, categories, limit)


@tool(when=HAS_LINKS)
def get_links(ref: str, categories: list[str] | None = None, link_types: list[str] | None = None,
              limit: int = 200) -> list[dict] | dict:
    """All cross-references of a ref (refs only): commentaries, quotations and other links. link_types e.g.
    ["commentary"], ["quotation"]."""
    try:
        return corpus.links(ref, categories, link_types, limit)
    except RefNotFound as error:
        return failure(error)


@tool()
def get_work_info(title: str) -> dict:
    """About a work: titles, author, era and date, what it comments on, available versions, sections, and the other
    citation systems it can be cited by."""
    try:
        return corpus.work_details(title)
    except RefNotFound as error:
        return failure(error)


@tool()
def list_works(category: str | None = None, name_contains: str | None = None, limit: int = 100) -> list[dict]:
    """List works by category or part of the title, oldest first."""
    return corpus.list_works(category, name_contains, limit)


@tool()
def about_corpus() -> dict:
    """The build this server holds: corpus, source and export date, build id, code fingerprint, languages, hashing
    and word-numbering rules, counts. Cite the build id when saying a source was verified."""
    return corpus.about()


@server.prompt(name="answer_question", title="Answer from primary sources",
               description=f"Answer a question from the primary sources of {profile.name} with verified citations")
def answer_question(question: str) -> str:
    return profile.answer_prompt.replace("{question}", question)


def main() -> None:
    threading.Thread(target=semantic, daemon=True).start()
    if TRANSPORT == "stdio":
        server.run("stdio")
        return
    server.run(
        "streamable-http", host=HOST, port=PORT, streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(allowed_hosts=ALLOWED_HOSTS, allowed_origins=ALLOWED_ORIGINS),
    )


if __name__ == "__main__":
    main()
