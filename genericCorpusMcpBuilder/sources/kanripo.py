"""Kanripo, the Kanseki Repository (kanripo.org): premodern Chinese texts, one GitHub repository per text
(github.com/kanripo), licensed CC BY-SA 4.0.

    corpus-mcp build --source kanripo --data data-daoist                    the Daoist preset (below)
    corpus-mcp build --source kanripo --data data-kr --option texts=KR5c0126,KR3j0010

Options (--option name=value):
    texts     Kanripo ids, comma-separated (KR5c0126 is the Zhuangzi; look them up in the catalog,
              github.com/kanripo/KR-Catalog, or on www.kanripo.org). Default: the Daoist preset
    edition   which edition (a branch of each repository) to read: the repository's default unless given
              (e.g. tls, HFL, SBCK, WYG); a text without that edition falls back to its default
    delay     seconds between downloads (default 0.5)

A text's files are its juan (卷, scrolls); passages are cited as juan.paragraph ("Zhuangzi 1.3"), and juan and
headings can be named ("Zhuangzi 逍遙遊"). Editions transcribed from the Daozang (正統道藏) are unpunctuated and keep
the printed page's lines; their paragraphs are the sections between headings, split at page breaks. Page numbers
("Zuowanglun p001a") and the TLS references some texts carry ("ZHUANG 1.1.1") are citable too.

It downloads from raw.githubusercontent.com and asks the GitHub API twice per text for its editions and files (60 requests an
hour without a token; set GITHUB_TOKEN to raise that).
"""
import json
import os
import re
import urllib.request
from pathlib import Path

from genericCorpusMcpBuilder import SimpleCorpus, download, pause
from genericCorpusMcpBuilder.sources.http import USER_AGENT

FILES = "https://raw.githubusercontent.com/kanripo/{text}/{edition}/{name}"
API = "https://api.github.com/repos/kanripo/{text}"
CATALOG = "https://raw.githubusercontent.com/kanripo/KR-Catalog/master/KR/{section}.txt"
LICENSE = "CC BY-SA 4.0 (Kanseki Repository, https://www.kanripo.org)"

# The Daoist preset: (Kanripo id, name, other names, date [earliest, latest] or None, author, comments on, group)
DAOIST = [
    ("KR5c0057", "Laozi", ["Dao De Jing", "Daodejing", "Tao Te Ching", "老子", "道德經"], [-400, -250], "Laozi", None,
     "Classics"),
    ("KR5c0126", "Zhuangzi", ["Chuang Tzu", "Nanhua zhenjing", "莊子", "南華真經"], [-350, -130], "Zhuang Zhou", None,
     "Classics"),
    ("KR5c0124", "Liezi", ["Lieh Tzu", "Chongxu zhide zhenjing", "列子", "沖虛至德真經"], [200, 400], "Lie Yukou", None,
     "Classics"),
    ("KR5c0118", "Wenzi", ["Tongxuan zhenjing", "文子", "通玄真經"], [-200, 100], None, None, "Classics"),
    ("KR5c0048", "Wenshi zhenjing", ["Guanyinzi", "Guan Yin Zi", "文始真經", "關尹子"], [800, 1200], "Yin Xi", None,
     "Classics"),
    ("KR5f0018", "Huainanzi", ["Huai Nan Zi", "Huainan honglie jie", "淮南子", "淮南鴻烈解"], [-139, -139], "Liu An",
     None, "Classics"),
    ("KR5f0019", "Baopuzi neipian", ["Baopuzi", "Pao Pu Tzu", "抱朴子", "抱朴子內篇"], [317, 330], "Ge Hong", None,
     "Classics"),
    ("KR5c0065", "Heshang Gong commentary", ["Heshanggong", "Laozi Heshang Gong zhangju", "河上公"], [100, 300],
     "Heshang Gong", "Laozi", "Commentaries"),
    ("KR5c0073", "Wang Bi commentary", ["Wang Bi", "Laozi Wang Bi zhu", "王弼注"], [240, 249], "Wang Bi", "Laozi",
     "Commentaries"),
    ("KR5c0138", "Guo Xiang commentary", ["Guo Xiang", "Zhuangzi zhu", "郭象注"], [300, 312], "Guo Xiang", "Zhuangzi",
     "Commentaries"),
    ("KR5e0002", "Taiping jing", ["Scripture of Great Peace", "太平經"], [100, 600], None, None, "Scriptures"),
    ("KR5b0015", "Huangting neijing jing", ["Inner Scripture of the Yellow Court", "黃庭內景經"], [300, 400], None,
     None, "Scriptures"),
    ("KR5b0016", "Huangting waijing jing", ["Outer Scripture of the Yellow Court", "黃庭外景經"], [200, 350], None,
     None, "Scriptures"),
    ("KR5a0031", "Yinfu jing", ["Huangdi yinfu jing", "Scripture of the Hidden Talisman", "陰符經"], [500, 750], None,
     None, "Scriptures"),
    ("KR5c0001", "Qingjing jing", ["Scripture of Clarity and Stillness", "清靜經"], [700, 900], None, None,
     "Scriptures"),
    ("KR5d0016", "Zhouyi cantong qi", ["Cantong qi", "Ts'an t'ung ch'i", "周易參同契"], [100, 700], "Wei Boyang", None,
     "Alchemy and meditation"),
    ("KR5d0059", "Zuowang lun", ["Sitting in Forgetfulness", "坐忘論"], [700, 735], "Sima Chengzhen", None,
     "Alchemy and meditation"),
    ("KR5d0049", "Tianyinzi", ["天隱子"], [700, 800], "Sima Chengzhen", None, "Alchemy and meditation"),
    ("KR5d0067", "Huashu", ["Book of Transformations", "化書"], [930, 950], "Tan Qiao", None,
     "Alchemy and meditation"),
    ("KR5d0036", "Zhen'gao", ["Zhengao", "Declarations of the Perfected", "真誥"], [499, 499], "Tao Hongjing", None,
     "Scriptures"),
    ("KR5a0306", "Liexian zhuan", ["Biographies of Immortals", "列仙傳"], [-50, 200], "Liu Xiang", None,
     "Hagiography"),
    ("KR5c0317", "Shenxian zhuan", ["Traditions of Divine Transcendents", "神仙傳"], [317, 343], "Ge Hong", None,
     "Hagiography"),
]
EDITIONS = {"HFL": "正統道藏・涵芬樓版", "ZTDZ": "正統道藏・三家本", "tls": "Thesaurus Linguae Sericae",
            "CHANT": "CHANT (漢達文庫)", "SBCK": "四部叢刊", "WYG": "文淵閣四庫全書", "CK-KZ": "重刊道藏輯要",
            "YP-C": "原版道藏輯要", "SKQS": "四庫全書"}
# Rough years for the dynasties the catalog gives (only to order results when a text has no date in the preset)
DYNASTIES = {"周": (-1046, -256), "戦國": (-475, -221), "戰國": (-475, -221), "秦": (-221, -206), "漢": (-206, 220),
             "西漢": (-206, 9), "東漢": (25, 220), "漢後期": (100, 220), "三國": (220, 280), "晉": (266, 420),
             "東晉": (317, 420), "六朝": (220, 589), "南北朝": (420, 589), "隋": (581, 618), "唐": (618, 907),
             "唐前半": (618, 760), "唐後半": (760, 907), "五代": (907, 960), "宋": (960, 1279), "北宋": (960, 1127),
             "南宋": (1127, 1279), "金": (1115, 1234), "元": (1271, 1368), "明": (1368, 1644), "清": (1644, 1912)}
PAGE_BREAK = re.compile(r"<pb:[^>]*?_(\d+)-(\w+)>")
JUAN_FILE = re.compile(r"^(KR\w+?)_(\d+)\.txt$")


# ---------------------------------------------------------------- downloading

def ask_github(url):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(raw, text, edition=None, delay="0.5"):
    """Download one text's juan files (and its table of contents) into raw/<id>/ (each once)."""
    folder = Path(raw) / text
    listing = folder / "files.json"
    if not listing.exists():
        branches = [b["name"] for b in ask_github(API.format(text=text) + "/branches?per_page=100")]
        first = edition if edition in branches else "master" if "master" in branches else branches[0]
        # some repositories keep the text only in an edition's branch (master holds just a Readme): try those next
        for branch in [first] + [b for b in branches if b != first and not b.startswith("_")]:
            names = [entry["name"] for entry in ask_github(API.format(text=text) + f"/contents?ref={branch}")
                     if JUAN_FILE.match(entry["name"]) or entry["name"] == "Readme.org"]
            if any(JUAN_FILE.match(name) for name in names):
                break
        folder.mkdir(parents=True, exist_ok=True)
        listing.write_text(json.dumps({"edition": branch, "editions": branches, "files": names}), encoding="utf-8")
    info = json.loads(listing.read_text(encoding="utf-8"))
    for name in info["files"]:
        if download(FILES.format(text=text, edition=info["edition"], name=name), folder / info["edition"] / name):
            pause(delay)
    print(f"{text}: {len(info['files'])} files ({info['edition']})", flush=True)


def fetch(raw, texts=None, edition=None, delay="0.5", **options):
    raw = Path(raw)
    ids = [t.strip() for t in str(texts).split(",") if t.strip()] if texts else [entry[0] for entry in DAOIST]
    for section in sorted({text[:4] for text in ids}):           # the catalog: titles, dynasties
        download(CATALOG.format(section=section), raw / "catalog" / f"{section}.txt")
    for text in ids:
        fetch_text(raw, text, edition, delay)
    (raw / "texts.json").write_text(json.dumps({"texts": ids, "preset": None if texts else "daoist"}),
                                    encoding="utf-8")


# ---------------------------------------------------------------- reading

def read_catalog(raw):
    """Kanripo id -> {"title", "dynasty", "author", ...properties} from the downloaded catalog files."""
    entries, current = {}, None
    for path in sorted((Path(raw) / "catalog").glob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            heading = re.match(r"^\*\*\* (KR\w+) (.*)$", line)
            if heading:
                title, _, rest = heading.group(2).partition("-")
                dynasty, _, author = rest.partition("-")
                current = entries[heading.group(1)] = {"title": title.strip(), "dynasty": dynasty.strip(),
                                                       "author": author.strip()}
                continue
            prop = re.match(r"^:([A-Z_]+):\s*(.*)$", line)
            if prop and current is not None and prop.group(1) not in ("PROPERTIES", "END"):
                current.setdefault(prop.group(1).lower(), prop.group(2).strip())
            elif line.startswith("*") and not line.startswith("***"):
                current = None
    return entries


def table_of_contents(path):
    """Readme.org's juan names: {"KR5c0126_001.txt": "逍遙遊第一"}."""
    if not path.exists():
        return {}
    links = re.findall(r"\[\[file:([^\]]+)\]\[([^\]]+)\]\]", path.read_text(encoding="utf-8"))
    return {name: re.sub(r"[〈〉]", "", label).strip() for name, label in links}


def is_heading(line):
    """A Daozang heading: a short line indented with full-width spaces ("　　　體道章第一")."""
    return line.startswith("　　") and len(line.strip()) <= 30


def read_juan(text):
    """A juan's paragraphs, in order: dicts with "text", "page" (where it starts), "heading" (True for headings),
    and "reference" (a TLS reference given just before it, e.g. "ZHUANG 1.1.1")."""
    lines = text.splitlines()
    body = [line for line in lines if not line.startswith("#")]
    punctuated = any(not line.strip() for line in body[1:-1])     # paragraphs separated by empty lines
    paragraphs, current, page, reference = [], [], None, None
    start_page = None

    def finish():
        nonlocal current, start_page, reference
        joined = "".join(current).strip()
        if joined:
            paragraphs.append({"text": joined, "page": start_page, "heading": False, "reference": reference})
            reference = None
        current, start_page = [], None

    for line in lines:
        if line.startswith("# src:"):
            reference = line[len("# src:"):].split(";")[0].strip() or None
            continue
        if line.startswith("#"):
            continue
        for match in PAGE_BREAK.finditer(line):
            page = match.group(2).lstrip("0") or "0"         # "001-002a" (juan 1) -> page "2a"
            if not punctuated:                    # unpunctuated Daozang text: a page break ends a paragraph
                finish()
        line = PAGE_BREAK.sub("", line).replace("¶", "").rstrip()
        stripped = line.strip()
        if not stripped:
            if punctuated:
                finish()
            continue
        if stripped.startswith("*") or (not punctuated and is_heading(line)):
            finish()
            heading = re.sub(r"^\*+\s*(\d+\s+)?", "", stripped).strip()
            if heading:
                paragraphs.append({"text": heading, "page": page, "heading": True, "reference": None})
            continue
        if start_page is None:
            start_page = page
        current.append(stripped)
    finish()
    return paragraphs


def numbered(paragraphs):
    """Paragraph labels: headings before the juan's first text share "0" (as a title), the rest count from 1, so
    "Laozi 1.1" is the chapter's first words, not its heading."""
    labels, number = [], 0
    for paragraph in paragraphs:
        if paragraph["heading"] and number == 0:
            labels.append("0")
        else:
            number += 1
            labels.append(str(number))
    return labels


def merge_titles(paragraphs):
    """Several headings before the first text become one title paragraph."""
    merged = []
    for paragraph in paragraphs:
        if merged and paragraph["heading"] and all(p["heading"] for p in merged):
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + " " + paragraph["text"]}
        else:
            merged.append(paragraph)
    return merged


def text_dates(catalog_entry):
    years = DYNASTIES.get((catalog_entry.get("dynasty") or "").rstrip("?"))
    return list(years) if years else None


# ---------------------------------------------------------------- converting

def convert(raw, out, **options):
    raw = Path(raw)
    settings = json.loads((raw / "texts.json").read_text(encoding="utf-8"))
    daoist = settings.get("preset") == "daoist"
    preset = {entry[0]: entry for entry in DAOIST}
    catalog = read_catalog(raw)
    corpus = SimpleCorpus(out, id="kanripo-daoist" if daoist else "kanripo",
                          name="Daoist texts (Kanseki Repository)" if daoist else "Kanseki Repository texts",
                          original_language="lzh", source="Kanseki Repository (github.com/kanripo)", license=LICENSE)
    names = {}
    for text in settings["texts"]:
        folder = raw / text
        if not (folder / "files.json").exists():
            print("skipped", text, "(not downloaded)", flush=True)
            continue
        info = json.loads((folder / "files.json").read_text(encoding="utf-8"))
        edition_folder = folder / info["edition"]
        entry = catalog.get(text, {})
        chinese = entry.get("title") or text
        _, name, aliases, date, author, comments_on, group = preset.get(
            text, (text, chinese, [], None, entry.get("author") or None, None, entry.get("dynasty") or "Texts"))
        contents = table_of_contents(edition_folder / "Readme.org")
        juan_files = sorted((p for p in edition_folder.glob("*.txt") if JUAN_FILE.match(p.name)),
                            key=lambda p: int(JUAN_FILE.match(p.name).group(2)))
        if not juan_files:
            print("skipped", text, "(no text files)", flush=True)
            continue
        work = corpus.work(name, levels=["juan", "paragraph"], language="lzh", author=author,
                           titles={"lzh": chinese}, aliases=[a for a in [*aliases, chinese, text] if a != name],
                           date=date or text_dates(entry), date_text=entry.get("dynasty") or None,
                           categories=[group] + ([entry["dz"]] if entry.get("dz") else []),
                           comments_on=[names[comments_on]] if comments_on in names else
                           ([comments_on] if comments_on else []),
                           dependence="Commentary" if comments_on else None)
        names[name] = work
        segments, base_edition, tls_prefixes = [], None, set()
        for path in juan_files:
            juan = str(int(JUAN_FILE.match(path.name).group(2)))
            raw_text = path.read_text(encoding="utf-8")
            found = re.search(r"#\+PROPERTY: BASEEDITION\s+(\S+)", raw_text)
            base_edition = base_edition or (found.group(1) if found else None)
            paragraphs = merge_titles(read_juan(raw_text))
            labels = numbered(paragraphs)
            for label, paragraph in zip(labels, paragraphs):
                segments.append(([juan, label], paragraph["text"]))
                if paragraph["reference"]:                # "ZHUANG 1.1.1": TLS's citation of this passage
                    prefix, _, address = paragraph["reference"].partition(" ")
                    tls_prefixes.add(prefix)
                    corpus.citation(work, "TLS", [address or prefix, paragraph["reference"]], f"{work} {juan}.{label}")
            label = contents.get(path.name)
            if label and not re.fullmatch(rf"{re.escape(chinese)}\s*\d+", label):
                for form in {label, re.sub(r"第[一二三四五六七八九十百〇]+$", "", label).strip()}:
                    if form:
                        corpus.citation(work, "juan", [form], f"{work} {juan}")
            # headings name the paragraphs up to the next heading; pages name the paragraphs that start on them
            headings = [i for i, p in enumerate(paragraphs) if p["heading"]] + [len(paragraphs)]
            for here, after in zip(headings, headings[1:]):
                first, last = labels[here], labels[max(here, after - 1)]
                corpus.citation(work, "heading", [paragraphs[here]["text"]],
                                f"{work} {juan}.{first}" + (f"-{last}" if last != first else ""))
            pages = {}
            for label, paragraph in zip(labels, paragraphs):
                if paragraph["page"]:
                    pages.setdefault(paragraph["page"], []).append(label)
            for page, numbers in pages.items():
                corpus.citation(work, "page", [f"p{juan}:{page}"] if len(juan_files) > 1 else [f"p{page}"],
                                f"{work} {juan}.{numbers[0]}" + (f"-{numbers[-1]}" if len(numbers) > 1 else ""))
        if tls_prefixes:                                  # "ZHUANG 1.1.1" names the Zhuangzi
            corpus.works[work]["aliases"] = sorted(set(corpus.works[work]["aliases"]) | tls_prefixes)
        edition = base_edition or info["edition"]
        corpus.text(work, "lzh", f"Kanripo {edition}" + (f" ({EDITIONS[edition]})" if edition in EDITIONS else ""),
                    segments, source=f"https://github.com/kanripo/{text}/tree/{info['edition']}", license=LICENSE)
        print(f"{name} ({chinese}): {len(juan_files)} juan, {len(segments)} paragraphs", flush=True)

    corpus.finish(profile={
        "languages": {"lzh": {"name": "Classical Chinese", "script": "han", "search": "cjk"}},
        "periods": [{"label": "Warring States and earlier (to 221 BCE)", "until": -221},
                    {"label": "Qin and Han (221 BCE-220 CE)", "until": 220},
                    {"label": "Six Dynasties (220-589)", "until": 589},
                    {"label": "Sui and Tang (589-907)", "until": 907},
                    {"label": "Song and later"}],
        "research": {"group_by": "date"},
    }, instructions=DAOIST_INSTRUCTIONS if daoist else INSTRUCTIONS)


INSTRUCTIONS = """Premodern Chinese texts from the Kanseki Repository (kanripo.org), in Classical Chinese only: any
translation you give is your own and must be labelled as such.

Passages are cited as juan.paragraph ("Zhuangzi 1.3"); juan and section headings can also be named. Answer from the
texts, and only from passages you have read with these tools: research_question or search first, with key terms in
Classical Chinese (traditional characters; editions differ in variant forms, e.g. 真/眞, 為/爲, so try both), then
get_text(ref, context=2) on the passages you rely on, and verify_citations on every quote before you answer.
Quote in Chinese with your translation, cite, and keep your interpretation apart from the text. The texts are
CC BY-SA 4.0 (Kanseki Repository)."""

DAOIST_INSTRUCTIONS = """Daoist texts in Classical Chinese from the Kanseki Repository (kanripo.org): the Laozi (Dao De
Jing), Zhuangzi, Liezi, Wenzi, Huainanzi and Baopuzi; the Heshang Gong and Wang Bi commentaries on the Laozi and Guo
Xiang's on the Zhuangzi; scriptures (Taiping jing, the Yellow Court scriptures, Yinfu jing, Qingjing jing,
Zhen'gao), alchemical and meditation texts (Cantong qi, Zuowang lun, Tianyinzi, Huashu) and the lives of the
immortals (Liexian zhuan, Shenxian zhuan). There is no translation in this corpus: any translation you give is your
own and must be labelled as such.

Workflow:
1. research_question(question, key_terms) first, with key_terms in Classical Chinese, traditional characters (e.g.
   "無為", "自然", "坐忘", "守一", "丹"). Editions differ in variant characters (真/眞, 為/爲, 无/無): if a term finds
   little, try its variant.
2. Read the key passages in full with get_text(ref, context=2). Passages are cited as juan.paragraph: in the Laozi a
   juan is a chapter ("Laozi 1.1"), in the Zhuangzi a chapter too ("Zhuangzi 1.3", or by name: "Zhuangzi 逍遙遊").
   Daozang editions are unpunctuated: punctuate in your own reading, and say so.
3. Answer from earliest to latest, each source with its citation, a short quote in Chinese and your translation,
   clearly marked as yours. Keep the classical "philosophical" texts, the commentaries and the later religious
   scriptures distinct: say which kind of text a statement comes from.
4. Before answering, pass every citation and quote to verify_citations and use the cite_as it returns.

Dates are approximate (authorship and dating of most of these texts are debated); say so where it matters. The texts
are CC BY-SA 4.0 (Kanseki Repository); quote and cite rather than reproducing long stretches."""
