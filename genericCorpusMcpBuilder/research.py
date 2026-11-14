from dataclasses import dataclass, field

from genericCorpusMcpBuilder.corpus import Corpus, Passage, RefNotFound

RANK_FUSION_K = 60
SEMANTIC_CANDIDATES = 40
TOPIC_CANDIDATES = 20
KEYWORD_CANDIDATES = 15
MAX_SOURCES_PER_WORK = 3
CHANNEL_WEIGHTS = {"meaning": 1.0, "topic": 1.0, "words": 0.5, "topic by words": 0.3}
RERANK_POOL = 30
MAX_RECHECKED = 16
RERANK_TEXT_CHARS = 300
MAX_LIVE_EMBEDDINGS = 4
TOPIC_MARGIN = 0.06
MIN_TOPIC_SIMILARITY = 0.53
KEYWORD_TOPICS = 2
MAX_TOPICS = 5
RELATED_TOPICS_SHOWN = 5
SINGLE_GROUP = {"date": "Sources, earliest first", "work": None, "none": "Sources, most relevant first"}
UNDATED_GROUP = "Undated"


@dataclass
class Candidate:
    title: str
    loc_start: int
    loc_end: int
    score: float = 0.0
    found_by: set = field(default_factory=set)
    linked_from: str | None = None

    def overlaps(self, other: "Candidate") -> bool:
        return self.title == other.title and self.loc_start <= other.loc_end and other.loc_start <= self.loc_end


class Researcher:
    """Finds sources for a question; what counts as a stratum, which links to follow and how results are grouped come
    from the corpus profile (genericCorpusMcpBuilder/profile.py)."""

    def __init__(self, corpus: Corpus, semantic=None, precomputed=None):
        self.corpus = corpus
        self.semantic = semantic
        self.precomputed = precomputed
        self.profile = corpus.profile
        self.settings = corpus.profile.research

    def stratum(self, title: str) -> int:
        return self.profile.stratum_index(self.corpus.work(title))

    def stratum_id(self, title: str) -> str | None:
        return self.profile.stratum_id(self.corpus.work(title))

    def group_key(self, work: dict):
        """(sort key, label) of the group a source is shown in."""
        group_by = self.settings["group_by"]
        if group_by == "strata" and self.profile.strata:
            index = self.profile.stratum_index(work)
            return index, self.profile.stratum_label(index)
        if group_by == "date" and self.profile.periods:
            index = self.profile.period_index(work)
            if index is None:
                return len(self.profile.periods), UNDATED_GROUP
            return index, self.profile.periods[index]["label"]
        if group_by == "work":
            return self.corpus.earliest_first(work), work["title"]
        return 0, SINGLE_GROUP.get(group_by) or SINGLE_GROUP["date"]

    def grouped(self, described: list[tuple[dict, dict]]) -> list[dict]:
        """Sources by group; within a group earliest work first (by date, then era), unless grouped by relevance."""
        groups: dict = {}
        for work, source in described:
            key, label = self.group_key(work)
            groups.setdefault((key, label), []).append((work, source))
        keep_order = self.settings["group_by"] == "none"
        return [{"layer": label,
                 "sources": [source for _, source in (
                     members if keep_order else sorted(members, key=lambda m: self.corpus.earliest_first(m[0])))]}
                for (_, label), members in sorted(groups.items(), key=lambda item: item[0][0])]

    def semantic_channel(self, question: str) -> list[Candidate]:
        self.best_meaning_score = 0.0
        if not self.semantic:
            return []
        candidates = []
        hits = self.semantic.search(question, limit=SEMANTIC_CANDIDATES)
        self.best_meaning_score = max((h["score"] for h in hits), default=0.0)
        for hit in hits:
            if hit.get("loc_start") is not None:
                candidates.append(Candidate(hit["title"], hit["loc_start"], hit["loc_end"], found_by={"meaning"}))
                continue
            try:
                start, end = self.corpus.resolve(hit["start_ref"]), self.corpus.resolve(hit["end_ref"])
            except RefNotFound:
                continue
            if start.exists and end.exists:
                candidates.append(Candidate(start.title, start.loc_start, end.loc_end, found_by={"meaning"}))
        return candidates

    def matching_topics(self, question: str, key_terms: list[str]) -> list[dict]:
        by_words = [{**t, "matched_by": "words"}
                    for t in self.corpus.match_topics(" ".join([question] + key_terms), KEYWORD_TOPICS)]
        if not self.semantic or getattr(self.semantic, "topic_vectors", None) is None:
            return by_words
        ranked = self.semantic.search_topics(question, limit=5)
        best = ranked[0][1] if ranked else 0.0
        by_meaning = [{**dict(self.corpus.topic(slug)), "matched_by": "meaning"} for slug, score in ranked
                      if score >= max(best - TOPIC_MARGIN, MIN_TOPIC_SIMILARITY) and self.corpus.topic(slug)]
        merged = {t["slug"]: t for t in by_words + by_meaning}
        return sorted(merged.values(), key=lambda t: t["matched_by"] != "meaning")[:MAX_TOPICS]

    def topic_channel(self, topics: list[dict]) -> list[Candidate]:
        per_topic = [[Candidate(r["title"], r["loc_start"], r["loc_end"], found_by={f"topic: {t['en']}"})
                      for r in self.corpus.topic_sources(t["slug"], TOPIC_CANDIDATES)] for t in topics[:3]]
        interleaved = [c for rank in zip(*per_topic) for c in rank] if per_topic else []
        return interleaved or [c for group in per_topic for c in group]

    def keyword_channel(self, terms: list[str]) -> list[Candidate]:
        candidates = []
        settings, originals = self.settings, self.profile.original_languages
        for term in terms:
            try:
                hits = self.corpus.search(term, categories=settings["keyword_categories"],
                                          exact_phrase=self.profile.language_of(term) in originals,
                                          limit=KEYWORD_CANDIDATES, base_texts_only=settings["keyword_base_texts_only"],
                                          dependent_categories=tuple(settings["keyword_dependent_categories"]))
            except ValueError:
                continue
            for hit in hits:
                wanted = settings["keyword_strata"]
                if wanted is not None and self.stratum_id(hit["title"]) not in wanted:
                    continue
                passage = self.corpus.resolve(hit["ref"])
                candidates.append(Candidate(passage.title, passage.loc_start, passage.loc_end,
                                            found_by={f"words: {term}"}))
        return candidates

    def fuse(self, channels: list[tuple[str, list[Candidate]]]) -> list[Candidate]:
        merged: list[Candidate] = []
        for name, channel in channels:
            for rank, candidate in enumerate(channel, 1):
                candidate.score = CHANNEL_WEIGHTS[name] / (RANK_FUSION_K + rank)
                existing = next((m for m in merged if m.overlaps(candidate)), None)
                if existing:
                    existing.score += candidate.score
                    existing.found_by |= candidate.found_by
                else:
                    merged.append(candidate)
        return sorted(merged, key=lambda c: -c.score)

    def merge_scored(self, candidates: list[Candidate], additions: list[Candidate]) -> list[Candidate]:
        for addition in additions:
            existing = next((c for c in candidates if c.overlaps(addition)), None)
            if existing:
                existing.score += addition.score
                existing.found_by |= addition.found_by
            else:
                candidates.append(addition)
        return sorted(candidates, key=lambda c: -c.score)

    def follow_links(self, candidates: list[Candidate]) -> list[Candidate]:
        """The profile's follow_links rules: e.g. from Talmud and Rambam hits to the Shulchan Arukh passages that
        cite them, or from a statute to the regulations under it."""
        added = []
        for rule in self.settings["follow_links"]:
            targets, link_types = tuple(rule.get("to_title_prefix") or ("",)), rule.get("link_types")
            for candidate in candidates[:rule.get("candidates", 10)]:
                if rule.get("from_strata") and self.stratum_id(candidate.title) not in rule["from_strata"]:
                    continue
                for link in self.corpus.links_for_range(candidate.loc_start, candidate.loc_end):
                    if not (link["other_title"] or "").startswith(targets):
                        continue
                    if link_types is not None and link["link_type"] not in link_types:
                        continue
                    added.append(Candidate(link["other_title"], link["other_start"], link["other_end"],
                                           score=candidate.score * rule.get("score_factor", 0.8), found_by={"linked"},
                                           linked_from=self.corpus.ref_for_range(candidate.loc_start, candidate.loc_end)))
        return added

    def major_commentaries(self, candidate: Candidate, rule: dict) -> list[str]:
        prefixes = tuple(rule.get("commentary_prefixes") or ("",))
        refs = [link["other"] for link in self.corpus.links_for_range(candidate.loc_start, candidate.loc_end)
                if link["link_type"] == "commentary" and (link["other_title"] or "").startswith(prefixes)]
        return list(dict.fromkeys(refs))[:rule.get("limit", 8)]

    def candidate_text(self, candidate: Candidate) -> tuple[str, str]:
        excerpt = self.corpus.excerpt(Passage(candidate.title, "", candidate.loc_start, candidate.loc_end),
                                      RERANK_TEXT_CHARS, max_locations=3)
        original = self.corpus.original_of(candidate.title)
        for lang in self.profile.translations_for(original) + [original]:
            if excerpt.get(lang):
                return excerpt[lang], lang
        return "", original

    def precomputed_similarity(self, question: str, candidate: Candidate) -> tuple[float, str] | None:
        lookup = getattr(self.semantic, "range_similarity", None)
        return lookup(question, candidate.loc_start, candidate.loc_end) if lookup else None

    def drop_off_topic(self, question: str, candidates: list[Candidate]) -> list[Candidate]:
        if not self.semantic:
            return candidates
        pool = candidates[:RERANK_POOL]
        unchecked = [c for c in pool if "meaning" not in c.found_by][:MAX_RECHECKED]
        judged = {id(c): self.precomputed_similarity(question, c) for c in unchecked}
        needing_text = [c for c in unchecked if judged[id(c)] is None][:MAX_LIVE_EMBEDDINGS]
        texts = [self.candidate_text(c) for c in needing_text]
        for candidate, score, (_, lang) in zip(needing_text, self.semantic.similarities(question, [t for t, _ in texts]), texts):
            judged[id(candidate)] = (score, lang)
        known = [v for v in judged.values() if v]
        reference = self.best_meaning_score or max((score for score, _ in known), default=0.0)
        rejected = {key for key, value in judged.items()
                    if value and value[0] < reference - self.profile.relevance_margin(value[1])}
        return [c for c in pool if id(c) not in rejected] + candidates[RERANK_POOL:]

    def choose(self, candidates: list[Candidate], limit: int) -> list[Candidate]:
        chosen, per_work = [], {}
        for stratum, reserved in self.settings["reserve"].items():
            for candidate in [c for c in candidates if self.stratum_id(c.title) == stratum][:reserved]:
                chosen.append(candidate)
                per_work[candidate.title] = per_work.get(candidate.title, 0) + 1
        for candidate in candidates:
            if any(c.overlaps(candidate) for c in chosen) or per_work.get(candidate.title, 0) >= MAX_SOURCES_PER_WORK:
                continue
            chosen.append(candidate)
            per_work[candidate.title] = per_work.get(candidate.title, 0) + 1
            if len(chosen) == limit:
                break
        return chosen

    def describe(self, candidate: Candidate, excerpt_chars: int) -> dict:
        work = self.corpus.work(candidate.title)
        passage = Passage(candidate.title, self.corpus.ref_for_range(candidate.loc_start, candidate.loc_end),
                          candidate.loc_start, candidate.loc_end)
        source = {"ref": passage.ref, "work": work["title"], "he_title": work.get("he_title"),
                  "author": ", ".join(a for a in work.get("authors", []) if a) or None, "era": work.get("era_name"),
                  "composed": work.get("comp_date"), "found_by": sorted(candidate.found_by)}
        if candidate.linked_from:
            source["linked_from"] = candidate.linked_from
        source.update({k: v for k, v in self.corpus.excerpt(passage, excerpt_chars).items() if k != "ref"})
        for rule in self.settings["major_commentaries"]:
            if work["title"].startswith(tuple(rule.get("on_title_prefix") or ("",))):
                source["major_commentaries"] = self.major_commentaries(candidate, rule)
                break
        return source

    def research(self, question: str, key_terms: list[str] | None = None, max_sources: int = 12,
                 excerpt_chars: int = 350) -> dict:
        topics = self.matching_topics(question, key_terms or [])
        channels = [("meaning", self.semantic_channel(question)),
                    ("topic", self.topic_channel([t for t in topics if t["matched_by"] == "meaning"])),
                    ("topic by words", self.topic_channel([t for t in topics if t["matched_by"] == "words"])),
                    ("words", self.keyword_channel(key_terms or []))]
        if not self.semantic and not key_terms:
            channels.append(("words", self.keyword_channel([question])))
        fused = self.fuse(channels)
        fused = self.merge_scored(fused, self.follow_links(fused))
        chosen = self.choose(self.drop_off_topic(question, fused), max_sources)
        layers = self.grouped([(self.corpus.work(c.title), self.describe(c, excerpt_chars)) for c in chosen])
        return {
            "question": question, "build": self.corpus.build_id,
            "search_channels": {"meaning": bool(self.semantic), "topics": bool(topics), "key_terms": key_terms or []},
            "topics": [{"slug": t["slug"], "name": t["en"], "he": t["he"], "sources": t["source_count"],
                        "description": (t["description"] or "")[:300],
                        "related": [r["en"] for r in self.corpus.related_topics(t["slug"], RELATED_TOPICS_SHOWN)]}
                       for t in topics[:5]],
            "layers": layers,
            "next_steps": self.settings["next_steps"],
        }

    def explore_topic(self, name: str, max_sources: int = 25, excerpt_chars: int = 250) -> dict:
        topic = self.corpus.topic(name)
        stored = self.precomputed.topic_dossier(topic["slug"]) if topic and self.precomputed else None
        if stored and max_sources == 25 and excerpt_chars == 250:
            return stored
        if not topic:
            return {"error": f"no topic matching {name!r}",
                    "suggestions": [t["en"] for t in self.corpus.match_topics(name, 8)]}
        described = []
        for row in self.corpus.topic_sources(topic["slug"], max_sources):
            candidate = Candidate(row["title"], row["loc_start"], row["loc_end"], found_by={"topic"})
            described.append((self.corpus.work(row["title"]), self.describe(candidate, excerpt_chars)))
        return {
            "topic": topic["en"], "he": topic["he"], "slug": topic["slug"], "description": topic["description"],
            "sources_in_topic": topic["source_count"], "build": self.corpus.build_id,
            "related_topics": self.corpus.related_topics(topic["slug"]),
            "layers": self.grouped(described),
        }
