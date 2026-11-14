import json
import sqlite3
import sys
from pathlib import Path

from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.research import Researcher


class Precomputed:
    def __init__(self, path: Path, build_id: str):
        self.db = None
        if path.exists():
            db = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
            stored_build = db.execute("SELECT value FROM meta WHERE key = 'build_id'").fetchone()
            if stored_build and stored_build[0] == build_id:
                self.db = db

    def topic_dossier(self, slug: str) -> dict | None:
        if not self.db:
            return None
        row = self.db.execute("SELECT dossier FROM topic_dossiers WHERE slug = ?", (slug,)).fetchone()
        return json.loads(row[0]) if row else None


def build(corpus_path: str, out_path: str) -> None:
    corpus = Corpus(corpus_path)
    researcher = Researcher(corpus)
    out = Path(out_path)
    out.unlink(missing_ok=True)
    db = sqlite3.connect(out)
    db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
                     "CREATE TABLE topic_dossiers(slug TEXT PRIMARY KEY, dossier TEXT) WITHOUT ROWID;")
    db.execute("INSERT INTO meta VALUES ('build_id', ?)", (corpus.build_id,))
    if not corpus.has_table("topics"):
        db.commit()
        print("no topics in this corpus; nothing to precompute", flush=True)
        return
    slugs = [row[0] for row in corpus.db.execute("SELECT slug FROM topics ORDER BY slug")]
    for done, slug in enumerate(slugs, 1):
        dossier = researcher.explore_topic(slug)
        db.execute("INSERT INTO topic_dossiers VALUES (?,?)", (slug, json.dumps(dossier, ensure_ascii=False)))
        if done % 500 == 0:
            db.commit()
            print(f"topic dossiers {done}/{len(slugs)}", flush=True)
    db.commit()
    db.execute("VACUUM")
    print(f"precomputed {len(slugs)} topic dossiers for build {corpus.build_id}", flush=True)


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
