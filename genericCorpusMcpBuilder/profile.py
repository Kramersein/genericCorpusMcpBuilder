"""Per-corpus settings: profile.json in a corpus directory (docs/corpus-format.md, Profile), stored in corpus.db's
meta table at build time. Everything has a default, so a corpus without a profile gets a neutral server: sources
grouped by period or date, searched in the corpus's languages, no corpus-specific tools."""
import json
import re
import urllib.parse

SCRIPT_LETTERS = {
    "hebrew": re.compile(r"[֐-׿יִ-ﭏ]"),
    "greek": re.compile(r"[Ͱ-ϡϰ-Ͽἀ-῿]"),
    "coptic": re.compile(r"[Ⲁ-⳿Ϣ-ϯ]"),
    "arabic": re.compile(r"[؀-ۿݐ-ݿ]"),
    "syriac": re.compile(r"[܀-ݏ]"),
    "cyrillic": re.compile(r"[Ѐ-ԯⷠ-ⷿꙀ-ꚟ]"),
    "armenian": re.compile(r"[԰-֏]"),
    "georgian": re.compile(r"[Ⴀ-ჿⴀ-⴯]"),
    "ethiopic": re.compile(r"[ሀ-᎟ⶀ-⷟]"),
    "devanagari": re.compile(r"[ऀ-ॿ]"),
    "tibetan": re.compile(r"[ༀ-࿿]"),
    "thai": re.compile(r"[฀-๿]"),
    "han": re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134F]"),
    "japanese": re.compile(r"[぀-ヿㇰ-ㇿ]"),      # kana; Japanese text also uses han
    "hangul": re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]"),
    "latin": re.compile(r"[A-Za-zÀ-ɏ]"),
}
# A query in one script may be in a language written with another: Chinese characters are also Japanese.
SCRIPT_ALSO_USED_BY = {"han": ["japanese"], "japanese": ["han"]}
# How a language's text is indexed. "hebrew" also strips prefix letters (ו/ה/ב/כ/ל/מ/ש/ד) and unifies -ים/-ין.
SEARCH_MODES = {
    "hebrew": "tokenize='unicode61 remove_diacritics 0', prefix='2 3 4'",
    "english": "tokenize='porter unicode61 remove_diacritics 2'",
    "plain": "tokenize='unicode61 remove_diacritics 2'",
    "latin": "tokenize='unicode61 remove_diacritics 2'",   # also folds v/u and j/i
    # Chinese and Japanese, written without spaces: indexed character by character, searched as character sequences
    "cjk": "tokenize='unicode61 remove_diacritics 0'",
}
# Defaults for common language codes (ISO 639-1/-3); a profile's "languages" overrides them.
KNOWN_LANGUAGES = {
    "en": {"name": "English", "script": "latin", "search": "english"},
    "eng": {"name": "English", "script": "latin", "search": "english"},
    "he": {"name": "Hebrew", "script": "hebrew", "search": "hebrew"},
    "heb": {"name": "Hebrew", "script": "hebrew", "search": "hebrew"},
    "arc": {"name": "Aramaic", "script": "hebrew", "search": "hebrew"},
    "grc": {"name": "Ancient Greek", "script": "greek"},
    "el": {"name": "Greek", "script": "greek"},
    "ell": {"name": "Greek", "script": "greek"},
    "la": {"name": "Latin", "script": "latin", "search": "latin"},
    "pli": {"name": "Pali", "script": "latin"},
    "pi": {"name": "Pali", "script": "latin"},
    "lat": {"name": "Latin", "script": "latin", "search": "latin"},
    "ar": {"name": "Arabic", "script": "arabic"},
    "ara": {"name": "Arabic", "script": "arabic"},
    "syc": {"name": "Syriac", "script": "syriac"},
    "syr": {"name": "Syriac", "script": "syriac"},
    "cop": {"name": "Coptic", "script": "coptic"},
    "ru": {"name": "Russian", "script": "cyrillic"},
    "rus": {"name": "Russian", "script": "cyrillic"},
    "cu": {"name": "Church Slavonic", "script": "cyrillic"},
    "chu": {"name": "Church Slavonic", "script": "cyrillic"},
    "hy": {"name": "Armenian", "script": "armenian"},
    "xcl": {"name": "Classical Armenian", "script": "armenian"},
    "ka": {"name": "Georgian", "script": "georgian"},
    "gez": {"name": "Ge'ez", "script": "ethiopic"},
    "am": {"name": "Amharic", "script": "ethiopic"},
    "sa": {"name": "Sanskrit", "script": "devanagari"},
    "hi": {"name": "Hindi", "script": "devanagari"},
    "bo": {"name": "Tibetan", "script": "tibetan"},
    "th": {"name": "Thai", "script": "thai"},
    "zh": {"name": "Chinese", "script": "han", "search": "cjk"},
    "lzh": {"name": "Classical Chinese", "script": "han", "search": "cjk"},
    "ja": {"name": "Japanese", "script": "japanese", "search": "cjk"},
    "jpn": {"name": "Japanese", "script": "japanese", "search": "cjk"},
    "ko": {"name": "Korean", "script": "hangul"},
    "kor": {"name": "Korean", "script": "hangul"},
}
DEFAULT_NEXT_STEPS = [
    "Read the most relevant sources in full with get_text(ref, context=2) before relying on them.",
    "Use get_commentary(ref) on a key passage to see how commentators understood it.",
    "If the sources found are off-target, call again with key_terms in the original language.",
    "Before answering, pass every citation and quote you will use to verify_citations.",
]
DEFAULT_INSTRUCTIONS = """\
Primary sources from {name}, with their translations and cross-references.

Answer from the sources, and only from sources you have actually read through these tools:
1. research_question(question, key_terms) first; key_terms are 2-4 words in the original language.
2. Read the key sources in full with get_text(ref, context=2), and get_commentary where commentaries exist.
3. Answer with the sources from earliest to latest, each with its citation and a short quote with translation.
4. Before answering, pass every citation and quote to verify_citations and use the cite_as it returns.

Never attribute a statement to a source you have not retrieved. Keep the quoted source, the translation (name it,
or say it is your own) and your explanation visibly separate."""
DEFAULT_ANSWER_PROMPT = """\
Answer this question from the primary sources, for someone without background in them:

{question}

Use research_question, then get_text with context on the sources you rely on, and verify_citations on every
citation before you answer. Give a short answer first, then the sources from earliest to latest with their verified
citations (cite_as) and short quotes with translation. Do not cite anything you did not retrieve and verify."""


def work_title(entry) -> str:
    return entry if isinstance(entry, str) else (entry.get("en") or entry.get("title") or "")


def rule_matches(rule: dict, work: dict) -> bool:
    categories, title = work.get("categories") or [""], work.get("title", "")
    if "category" in rule and categories[0] not in rule["category"]:
        return False
    if "title_prefix" in rule and not title.startswith(tuple(rule["title_prefix"])):
        return False
    if "comments_on_prefix" in rule and not any(
            work_title(t).startswith(tuple(rule["comments_on_prefix"])) for t in work.get("base_text_titles") or []):
        return False
    if "dependent" in rule and bool(work.get("dependence")) != rule["dependent"]:
        return False
    if "era" in rule and work.get("era") not in rule["era"]:
        return False
    return True


class Profile:
    def __init__(self, settings: dict | None, manifest: dict | None = None):
        self.settings = settings or {}
        manifest = manifest or {}
        self.name = manifest.get("corpus_name") or manifest.get("name") or "this corpus"
        codes = list(self.settings.get("languages") or {}) or json.loads(manifest.get("languages") or "[]") or ["en"]
        # Each work has its own original language; original_language is the default, original_languages all of them
        self.original_language = manifest.get("original_language") or codes[0]
        self.original_languages = json.loads(manifest.get("original_languages") or "[]") or [self.original_language]
        ordered = list(dict.fromkeys(self.original_languages + [self.original_language] + codes))
        self.languages = {c: {"name": c, "script": "latin", "search": "plain", **KNOWN_LANGUAGES.get(c, {}),
                              **(self.settings.get("languages") or {}).get(c, {})} for c in ordered}
        self.eras = self.settings.get("eras") or []
        self.era_rank = {era["code"]: rank for rank, era in enumerate(self.eras, 1)}
        self.era_names = {era["code"]: era.get("name", era["code"]) for era in self.eras}
        self.strata = self.settings.get("strata") or []
        self.stratum_rules = self.settings.get("stratum_rules") or []
        self.periods = self.settings.get("periods") or []
        research = self.settings.get("research") or {}
        self.research = {
            "group_by": research.get("group_by") or ("strata" if self.strata else "date"),
            "keyword_categories": research.get("keyword_categories"),
            "keyword_base_texts_only": research.get("keyword_base_texts_only", False),
            "keyword_dependent_categories": research.get("keyword_dependent_categories") or [],
            "keyword_strata": research.get("keyword_strata"),
            "follow_links": research.get("follow_links") or [],
            "reserve": research.get("reserve") or {},
            "major_commentaries": research.get("major_commentaries") or [],
            "relevance_margin": research.get("relevance_margin") or {},
            "next_steps": research.get("next_steps") or DEFAULT_NEXT_STEPS,
        }
        self.trace = self.settings.get("trace")
        self.embedding = {"title_prefixes": [], "base_text_categories": None, "category_paths": [],
                          "translation_coverage": 0.8, "prefer_languages": [], **(self.settings.get("embedding") or {})}
        self.tool_descriptions = self.settings.get("tool_descriptions") or {}
        self.citation_scheme_preference = self.settings.get("citation_scheme_preference") or []
        self.source_url_template = self.settings.get("source_url")
        self.instructions = (manifest.get("instructions") or "").strip() or DEFAULT_INSTRUCTIONS.format(name=self.name)
        self.answer_prompt = (manifest.get("answer_prompt") or "").strip() or DEFAULT_ANSWER_PROMPT

    @classmethod
    def from_manifest(cls, manifest: dict) -> "Profile":
        return cls(json.loads(manifest.get("profile") or "{}"), manifest)

    # Languages

    def translations_for(self, original: str) -> list[str]:
        """The other corpus languages, in which a work written in `original` can have translations."""
        return [c for c in self.languages if c != original]

    def search_mode(self, lang: str) -> str:
        return self.languages.get(lang, {}).get("search", "plain")

    def fts_table(self, lang: str) -> str:
        return "fts_" + re.sub(r"\W", "_", lang)

    def languages_for(self, text: str) -> list[str]:
        """The corpus languages written in the script that dominates text (several if they share it)."""
        counts = {script: len(pattern.findall(text)) for script, pattern in SCRIPT_LETTERS.items()}
        if counts["japanese"]:                      # any kana makes it Japanese, however many kanji it has
            counts["japanese"] += counts.pop("han")
        script = max(counts, key=counts.get) if any(counts.values()) else "latin"
        matching = [c for c, lang in self.languages.items() if lang["script"] == script]
        if not matching:
            matching = [c for c, lang in self.languages.items()
                        if lang["script"] in SCRIPT_ALSO_USED_BY.get(script, [])]
        return matching or [next((c for c in self.languages if c not in self.original_languages),
                                 self.original_language)]

    def language_of(self, text: str) -> str:
        return self.languages_for(text)[0]

    def relevance_margin(self, lang: str) -> float:
        margins = self.research["relevance_margin"]
        return margins.get(lang, margins.get("default", 0.12))

    # Works

    def era_name(self, code: str | None) -> str | None:
        return self.era_names.get(code)

    def stratum_index(self, work: dict) -> int:
        """Position of the work's stratum (0-based); works matching no rule go to the last stratum."""
        if not self.strata:
            return 0
        ids = [s["id"] for s in self.strata]
        for rule in self.stratum_rules:
            if rule_matches(rule, work):
                return ids.index(rule["stratum"])
        return len(ids) - 1

    def stratum_id(self, work: dict) -> str | None:
        return self.strata[self.stratum_index(work)]["id"] if self.strata else None

    def stratum_label(self, index: int) -> str:
        return self.strata[index].get("label", self.strata[index]["id"])

    def period_index(self, work: dict) -> int | None:
        year = work.get("comp_date")
        if year is None or not self.periods:
            return None
        for index, period in enumerate(self.periods):
            if year <= period.get("until", float("inf")):
                return index
        return len(self.periods) - 1

    def source_url(self, ref: str) -> str | None:
        if not self.source_url_template:
            return None
        return self.source_url_template.format(ref=urllib.parse.quote(ref.replace(" ", "_"), safe=",:_-'"))

    def tool_description(self, name: str, default: str) -> str:
        return self.tool_descriptions.get(name) or default
