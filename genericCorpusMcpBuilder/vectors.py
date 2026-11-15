import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

from genericCorpusMcpBuilder.citations import LABEL_SEPARATOR
from genericCorpusMcpBuilder.profile import Profile
from genericCorpusMcpBuilder.text import HEBREW_MARKS

MODEL_NAME = "BAAI/bge-m3"
MAX_SEQUENCE_TOKENS = 512
MAX_PASSAGE_CHARS = 1200
BATCH_SIZE = 16
CHECKPOINT_EVERY = 2048
SCORE_CHUNK = 16384


def is_embedded_work(title: str, categories: list[str], dependence: str | None, settings: dict) -> bool:
    """profile.json "embedding": works whose title starts with title_prefixes, plus base texts (not commentaries)
    whose main category is in base_text_categories (all base texts if that is not set) or whose categories start
    with one of category_paths."""
    if settings["title_prefixes"] and title.startswith(tuple(settings["title_prefixes"])):
        return True
    if dependence:
        return False
    wanted = settings["base_text_categories"]
    paths = settings["category_paths"]
    if wanted is None and not paths:
        return True
    return (wanted is not None and categories[:1] in [[c] for c in wanted]) or any(
        categories[:len(path)] == path for path in paths)


def embedding_versions(corpus: sqlite3.Connection) -> list[tuple]:
    """One version per embedded work: its primary translation if it covers enough of the original, else the
    original."""
    profile = Profile.from_manifest(dict(corpus.execute("SELECT key, value FROM meta")))
    settings = profile.embedding
    chosen = []
    for title, categories, dependence, category, language in corpus.execute(
            "SELECT title, categories, dependence, category, language FROM works ORDER BY title"):
        if not is_embedded_work(title, json.loads(categories), dependence, settings):
            continue
        original = language or profile.original_language
        preferred = settings.get("prefer_languages") or []          # e.g. ["en"]: the model knows English best
        translations = sorted(profile.translations_for(original),
                              key=lambda l: preferred.index(l) if l in preferred else len(preferred))
        primaries = {lang: (vid, count) for vid, lang, count in corpus.execute(
            "SELECT id, lang, segment_count FROM versions WHERE title = ? AND is_primary = 1", (title,))}
        original_count = primaries.get(original, (None, 0))[1]
        translation = next((primaries[l] + (l,) for l in translations if l in primaries), None)
        if translation and translation[1] >= settings["translation_coverage"] * original_count:
            chosen.append((translation[0], translation[2], title, category))
        elif original in primaries:
            chosen.append((primaries[original][0], original, title, category))
    return chosen


def section_of(cite: str) -> str:
    return cite.rsplit(LABEL_SEPARATOR, 1)[0] if LABEL_SEPARATOR in cite else ""


def passages(corpus: sqlite3.Connection):
    for version_id, lang, title, category in embedding_versions(corpus):
        current = None
        for base, addr, ref, text in corpus.execute(
                "SELECT l.base, l.cite, l.ref, s.text FROM segments s JOIN locations l ON l.id = s.location_id "
                "WHERE s.version_id = ? ORDER BY s.location_id", (version_id,)):
            text = HEBREW_MARKS.sub("", text.replace("**", ""))
            same_section = current and current["base"] == base and current["section"] == section_of(addr)
            if same_section and len(current["text"]) + len(text) <= MAX_PASSAGE_CHARS:
                current["text"] += " " + text
                current["end_ref"] = ref
                continue
            if current:
                yield current
            current = {"base": base, "section": section_of(addr), "start_ref": ref, "end_ref": ref,
                       "text": text, "lang": lang, "title": title, "category": category}
        if current:
            yield current


def write_passages(corpus_path: str, passages_path: Path) -> list[str]:
    if passages_path.exists():
        index = sqlite3.connect(passages_path)
        return [text for (text,) in index.execute("SELECT text FROM passages ORDER BY id")]
    corpus = sqlite3.connect(f"file:{corpus_path}?mode=ro", uri=True)
    index = sqlite3.connect(passages_path)
    index.execute("CREATE TABLE passages(id INTEGER PRIMARY KEY, start_ref TEXT, end_ref TEXT, title TEXT, "
                  "category TEXT, lang TEXT, text TEXT)")
    all_passages = list(passages(corpus))
    index.executemany("INSERT INTO passages VALUES (?,?,?,?,?,?,?)", [
        (i, p["start_ref"], p["end_ref"], p["title"], p["category"], p["lang"], p["text"])
        for i, p in enumerate(all_passages)])
    index.commit()
    return [p["text"] for p in all_passages]


def build(corpus_path: str, out_dir: str) -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(len(os.sched_getaffinity(0)))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    texts = write_passages(corpus_path, out / "passages.db")
    print(f"{len(texts)} passages", flush=True)

    model = SentenceTransformer(MODEL_NAME, device="cpu")
    model.max_seq_length = MAX_SEQUENCE_TOKENS
    dimensions = model.get_sentence_embedding_dimension()
    vectors_path, done_path = out / "vectors.f16", out / "vectors.done.npy"
    vectors = np.memmap(vectors_path, dtype=np.float16, mode="r+" if vectors_path.exists() else "w+",
                        shape=(len(texts), dimensions))
    done = np.load(done_path) if done_path.exists() else np.zeros(len(texts), dtype=bool)
    remaining = sorted(np.flatnonzero(~done).tolist(), key=lambda i: len(texts[i]))
    began = time.time()
    for offset in range(0, len(remaining), CHECKPOINT_EVERY):
        chunk = remaining[offset:offset + CHECKPOINT_EVERY]
        embedded = model.encode([texts[i] for i in chunk], batch_size=BATCH_SIZE,
                                normalize_embeddings=True, convert_to_numpy=True)
        vectors[chunk] = embedded.astype(np.float16)
        vectors.flush()
        done[chunk] = True
        np.save(done_path, done)
        finished = offset + len(chunk)
        rate = finished / (time.time() - began)
        hours_left = (len(remaining) - finished) / rate / 3600
        print(f"{finished}/{len(remaining)} passages, {rate:.1f}/s, ~{hours_left:.1f}h left", flush=True)
    (out / "vectors.json").write_text(json.dumps({"model": MODEL_NAME, "count": len(texts),
                                                 "dimensions": dimensions}))
    print("done", flush=True)


TOPIC_DESCRIPTION_CHARS = 300


def build_topic_vectors(corpus_path: str, out_dir: str) -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(len(os.sched_getaffinity(0)))
    corpus = sqlite3.connect(f"file:{corpus_path}?mode=ro", uri=True)
    if not corpus.execute("SELECT 1 FROM sqlite_master WHERE name = 'topics'").fetchone():
        print("no topics in this corpus; no topic vectors", flush=True)
        return
    rows = corpus.execute("SELECT slug, en, he, description FROM topics ORDER BY slug").fetchall()
    texts = [f"{en or ''} ({he or ''}). {(description or '')[:TOPIC_DESCRIPTION_CHARS]}" for _, en, he, description in rows]
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    vectors = model.encode(texts, batch_size=BATCH_SIZE, normalize_embeddings=True, show_progress_bar=False)
    out = Path(out_dir)
    np.save(out / "topics.npy", vectors.astype(np.float16))
    (out / "topics.json").write_text(json.dumps([slug for slug, *_ in rows]))
    print(f"{len(rows)} topic vectors", flush=True)


ONNX_DIR = "onnx"


def export_onnx(vectors_dir: str, model_name: str = MODEL_NAME) -> None:
    """The embedding model as ONNX (vectors/onnx/: model.onnx with its weights, tokenizer.json), so serving needs
    only onnxruntime and tokenizers. The graph includes the model's pooling (first token) and normalization."""
    import torch
    from sentence_transformers import SentenceTransformer

    out = Path(vectors_dir) / ONNX_DIR
    out.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(model_name, device="cpu")
    transformer, tokenizer = model[0].auto_model.eval(), model.tokenizer

    class Encoder(torch.nn.Module):
        def forward(self, input_ids, attention_mask):
            first = transformer(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state[:, 0]
            return torch.nn.functional.normalize(first, dim=-1)

    sample = tokenizer(["a short text", "and a somewhat longer one"], padding=True, return_tensors="pt")
    batch, length = torch.export.Dim("batch"), torch.export.Dim("length", max=MAX_SEQUENCE_TOKENS)
    torch.onnx.export(Encoder(), (sample["input_ids"], sample["attention_mask"]), str(out / "model.onnx"),
                      input_names=["input_ids", "attention_mask"], output_names=["sentence_embedding"],
                      dynamic_shapes=({0: batch, 1: length}, {0: batch, 1: length}), external_data=True,
                      dynamo=True)
    tokenizer.backend_tokenizer.save(str(out / "tokenizer.json"))
    (out / "onnx.json").write_text(json.dumps({"model": model_name, "max_tokens": MAX_SEQUENCE_TOKENS,
                                               "pad_id": tokenizer.pad_token_id, "pad_token": tokenizer.pad_token}))
    print(f"exported {model_name} to {out}", flush=True)


class OnnxEncoder:
    """encode() like SentenceTransformer's, from the exported model (no torch)."""

    def __init__(self, directory: Path):
        import onnxruntime
        from tokenizers import Tokenizer

        onnxruntime.disable_telemetry_events()     # it would otherwise report to Microsoft
        settings = json.loads((directory / "onnx.json").read_text())
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=settings["max_tokens"])
        self.tokenizer.enable_padding(pad_id=settings["pad_id"], pad_token=settings["pad_token"])
        options = onnxruntime.SessionOptions()
        # Without prepacking and the memory arena the weights stay in the memory-mapped model.onnx.data (file pages
        # the OS can reclaim): ~0.3 GB of the process's own memory instead of ~1.5 GB, same vectors, ~70 ms a query.
        options.add_session_config_entry("session.disable_prepacking", "1")
        options.enable_cpu_mem_arena = False
        threads = int(os.environ.get("OMP_NUM_THREADS") or 0)
        if threads:
            options.intra_op_num_threads = threads
        self.session = onnxruntime.InferenceSession(str(directory / "model.onnx"), options,
                                                    providers=["CPUExecutionProvider"])

    def encode(self, texts: list[str], batch_size: int = BATCH_SIZE, **_ignored) -> np.ndarray:
        vectors = []
        for start in range(0, len(texts), batch_size):
            encoded = self.tokenizer.encode_batch(texts[start:start + batch_size])
            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            vectors.append(self.session.run(["sentence_embedding"], {"input_ids": ids, "attention_mask": mask})[0])
        return np.concatenate(vectors) if vectors else np.zeros((0, 0), dtype=np.float32)


def write_float32(vectors_dir: str) -> None:
    """vectors.f32 next to vectors.f16: what the server memory-maps for fast search."""
    directory = Path(vectors_dir)
    meta = json.loads((directory / "vectors.json").read_text())
    stored = np.memmap(directory / "vectors.f16", dtype=np.float16, mode="r", shape=(meta["count"], meta["dimensions"]))
    out = np.memmap(directory / "vectors.f32.part", dtype=np.float32, mode="w+", shape=stored.shape)
    for start in range(0, len(stored), SCORE_CHUNK):
        out[start:start + SCORE_CHUNK] = stored[start:start + SCORE_CHUNK]
    out.flush()
    del out
    (directory / "vectors.f32.part").rename(directory / "vectors.f32")
    print(f"wrote vectors.f32 ({stored.shape[0]} x {stored.shape[1]})", flush=True)


def locate_passages(corpus_path: str, vectors_dir: str) -> None:
    from genericCorpusMcpBuilder.corpus import Corpus, RefNotFound

    corpus = Corpus(corpus_path)
    index = sqlite3.connect(Path(vectors_dir) / "passages.db")
    columns = [row[1] for row in index.execute("PRAGMA table_info(passages)")]
    for column in ("loc_start", "loc_end"):
        if column not in columns:
            index.execute(f"ALTER TABLE passages ADD COLUMN {column} INTEGER")
    located, missing = [], 0
    for passage_id, start_ref, end_ref in index.execute("SELECT id, start_ref, end_ref FROM passages").fetchall():
        try:
            first, last = corpus.resolve(start_ref), corpus.resolve(end_ref)
            located.append((first.loc_start, last.loc_end, passage_id))
        except RefNotFound:
            missing += 1
    index.executemany("UPDATE passages SET loc_start = ?, loc_end = ? WHERE id = ?", located)
    index.commit()
    meta_path = Path(vectors_dir) / "vectors.json"
    meta = json.loads(meta_path.read_text())
    meta["located_for_build"] = corpus.build_id
    meta_path.write_text(json.dumps(meta))
    print(f"located {len(located)} passages for build {corpus.build_id}; {missing} not found", flush=True)
    write_float32(vectors_dir)


class SemanticIndex:
    def __init__(self, vectors_dir: str, corpus_build: str | None = None):
        directory = self.directory = Path(vectors_dir)
        meta = json.loads((directory / "vectors.json").read_text())
        # Memory-mapped, not copied into the process: the OS pages the vectors in and can drop them again under
        # memory pressure. vectors.f32 (written by the build) is used directly; without it, float16 is converted
        # a chunk at a time per query (about 10x slower search).
        shape = (meta["count"], meta["dimensions"])
        f32 = directory / "vectors.f32"
        if f32.exists() and f32.stat().st_size == shape[0] * shape[1] * 4:
            self.vectors = np.memmap(f32, dtype=np.float32, mode="r", shape=shape)
        else:
            self.vectors = np.memmap(directory / "vectors.f16", dtype=np.float16, mode="r", shape=shape)
        self.passages = sqlite3.connect(f"file:{directory / 'passages.db'}?mode=ro&immutable=1", uri=True,
                                        check_same_thread=False)
        self.passages.row_factory = sqlite3.Row
        columns = {row[1] for row in self.passages.execute("PRAGMA table_info(passages)")}
        self.located = (corpus_build is not None and meta.get("located_for_build") == corpus_build
                        and {"loc_start", "loc_end"} <= columns)
        wanted = "category, lang" + (", loc_start, loc_end" if self.located else "")
        rows = self.passages.execute(f"SELECT {wanted} FROM passages ORDER BY id").fetchall()   # not the texts
        self.categories = np.array([r["category"] for r in rows])
        self.languages = [r["lang"] for r in rows]
        if self.located:
            self.loc_start = np.array([r["loc_start"] if r["loc_start"] is not None else -1 for r in rows])
            self.loc_end = np.array([r["loc_end"] if r["loc_end"] is not None else -2 for r in rows])
            self.by_start = np.argsort(self.loc_start, kind="stable")
            self.sorted_starts = self.loc_start[self.by_start]
            self.max_span = int((self.loc_end - self.loc_start).max())
        self.model_name = meta["model"]
        self.model = None
        self.cached_query, self.cached_vector, self.cached_scores = None, None, None
        has_topics = (directory / "topics.npy").exists()
        self.topic_vectors = np.load(directory / "topics.npy").astype(np.float32) if has_topics else None
        self.topic_slugs = json.loads((directory / "topics.json").read_text()) if has_topics else []

    def load_model(self):
        """The exported ONNX model when the data directory has it and onnxruntime is installed (no torch needed);
        otherwise sentence-transformers. Both give the same vectors."""
        if self.model is None:
            onnx = self.directory / ONNX_DIR
            if (onnx / "model.onnx").exists() and importlib.util.find_spec("onnxruntime"):
                self.model = OnnxEncoder(onnx)
            else:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(self.model_name, device="cpu")
                self.model.max_seq_length = MAX_SEQUENCE_TOKENS
        return self.model

    def embed(self, query: str) -> np.ndarray:
        if query != self.cached_query:
            self.cached_vector = self.load_model().encode([query], normalize_embeddings=True)[0].astype(np.float32)
            self.cached_query, self.cached_scores = query, None
        return self.cached_vector

    def scores(self, query: str) -> np.ndarray:
        vector = self.embed(query)
        if self.cached_scores is None:
            if self.vectors.dtype == np.float32:
                self.cached_scores = np.asarray(self.vectors @ vector)
            else:
                scores = np.empty(len(self.vectors), dtype=np.float32)
                for start in range(0, len(self.vectors), SCORE_CHUNK):    # float16 -> float32 a chunk at a time
                    chunk = self.vectors[start:start + SCORE_CHUNK]
                    scores[start:start + len(chunk)] = chunk.astype(np.float32) @ vector
                self.cached_scores = scores
        return self.cached_scores

    def search_topics(self, query: str, limit: int = 5) -> list[tuple[str, float]]:
        if self.topic_vectors is None:
            return []
        scores = self.topic_vectors @ self.embed(query)
        return [(self.topic_slugs[i], float(scores[i])) for i in np.argsort(-scores)[:limit]]

    def range_similarity(self, query: str, loc_start: int, loc_end: int) -> tuple[float, str] | None:
        if not self.located:
            return None
        low = np.searchsorted(self.sorted_starts, loc_start - self.max_span, side="left")
        high = np.searchsorted(self.sorted_starts, loc_end, side="right")
        candidates = self.by_start[low:high]
        overlapping = candidates[self.loc_end[candidates] >= loc_start]
        if not len(overlapping):
            return None
        scores = self.scores(query)[overlapping]
        best = overlapping[int(np.argmax(scores))]
        return float(scores.max()), self.languages[best]

    def similarities(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        vectors = self.load_model().encode([HEBREW_MARKS.sub("", t) for t in texts], normalize_embeddings=True)
        return (vectors @ self.embed(query)).tolist()

    def search(self, query: str, categories: list[str] | None = None, limit: int = 15) -> list[dict]:
        scores = self.scores(query)
        if categories:
            scores = np.where(np.isin(self.categories, categories), scores, -1.0)
        top = np.argpartition(-scores, min(limit, len(scores) - 1))[:limit]
        top = top[np.argsort(-scores[top])]
        results = []
        for passage_id in top:
            row = dict(self.passages.execute("SELECT * FROM passages WHERE id = ?", (int(passage_id),)).fetchone())
            if not self.located:
                row.pop("loc_start", None), row.pop("loc_end", None)
            results.append({**row, "score": round(float(scores[passage_id]), 4)})
        return results


if __name__ == "__main__":
    if sys.argv[1] == "--export-onnx":
        export_onnx(sys.argv[2])
    elif sys.argv[1] == "--topics":
        build_topic_vectors(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "--locate":
        locate_passages(sys.argv[2], sys.argv[3])
    else:
        build(sys.argv[1], sys.argv[2])
