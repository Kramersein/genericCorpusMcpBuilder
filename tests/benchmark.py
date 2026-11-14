import statistics
import sys
import time
from pathlib import Path

from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.research import Researcher

OPERATIONS = {
    "get_text segment": lambda c, r: c.get_text("Shulchan Arukh, Orach Chayim 308:1"),
    "get_text daf": lambda c, r: c.get_text("Shabbat 73a"),
    "get_text context": lambda c, r: c.get_text("Moed Katan 8b:9", context=2),
    "get_text hebrew ref": lambda c, r: c.get_text('שו"ע או"ח שח:א'),
    "get_text alt ref": lambda c, r: c.get_text("Parashat Noach 2"),
    "search hebrew common": lambda c, r: c.search("שבת", limit=20),
    "search hebrew phrase": lambda c, r: c.search("מפני השלום", exact_phrase=True, limit=20),
    "search english": lambda c, r: c.search("honor father mother", limit=20),
    "find_quote": lambda c, r: c.find_quote("אין מערבין שמחה בשמחה", 10),
    "verify_citations x3": lambda c, r: c.verify_citations([
        {"ref": "Moed Katan 8b", "quote": "אין מערבין שמחה בשמחה"},
        {"ref": "Shevuot 39a", "quote": "כל ישראל ערבים זה בזה"},
        {"ref": "Genesis 1:1", "quote": "בראשית ברא אלהים"}]),
    "get_links": lambda c, r: c.links("Shabbat 73a"),
    "get_commentary": lambda c, r: c.commentaries("Shulchan Arukh, Orach Chayim 263:1"),
    "trace_halakha depth 2": lambda c, r: c.trace("Shulchan Arukh, Orach Chayim 308:1", 2, c.profile.trace["categories"]),
    "research_question": lambda c, r: r.research("Why do we light two candles on Friday night?", ["נר שבת"]),
    "explore_topic": lambda c, r: r.explore_topic("Honoring Parents"),
}


def main() -> None:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    corpus = Corpus(str(data / "corpus.db"))
    semantic = None
    if (data / "vectors" / "vectors.json").exists():
        from genericCorpusMcpBuilder.vectors import SemanticIndex
        semantic = SemanticIndex(str(data / "vectors"), corpus.build_id)
        semantic.embed("warm up")
        print(f"semantic index loaded; located for this build: {semantic.located}")
    researcher = Researcher(corpus, semantic)
    for name, operation in OPERATIONS.items():
        operation(corpus, researcher)
        timings = []
        for _ in range(3):
            if semantic:
                semantic.cached_query = None
            began = time.perf_counter()
            operation(corpus, researcher)
            timings.append(time.perf_counter() - began)
        print(f"{name:24} {statistics.median(timings) * 1000:8.0f} ms")


if __name__ == "__main__":
    main()
