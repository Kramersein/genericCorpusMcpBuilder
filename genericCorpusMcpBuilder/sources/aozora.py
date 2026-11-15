"""Aozora Bunko (青空文庫, www.aozora.gr.jp): Japanese literature transcribed by volunteers, chosen by work or author.
Only works out of copyright are used (the catalog marks each work and person).

    corpus-mcp build --source aozora --data data-aozora --option authors=芥川竜之介,夏目漱石
    corpus-mcp build --source aozora --data data-aozora --option works=128,773 --option authors=879

Options (--option name=value):
    works    work ids (作品ID; the number in a card's address: .../card128.html is 128), comma-separated
    authors  authors, by name (芥川竜之介, or Akutagawa Ryunosuke) or person id (人物ID: .../cards/000879/ is 879):
             all their works that are out of copyright
    limit    at most this many works per author (default: all)
    delay    seconds between downloads (default 2)

It downloads the catalog (作家別作品一覧, CC BY 4.0) once, then each work's XHTML file, 2 seconds apart, once. Ruby
readings (ふりがな) and the transcribers' notes ［＃…］ are left out of the text; characters Aozora shows as images
(外字) are restored from their JIS X 0213 codes where they have one. Aozora's rules (取り扱い規準) let works out of
copyright be copied and shared freely, and ask that each file's credits be kept: they are kept in each version's
license note (底本, 入力, 校正).
"""
import csv
import html
import io
import json
import re
import zipfile
from pathlib import Path

from genericCorpusMcpBuilder import SimpleCorpus, chapters_from_blocks, download, pause
from genericCorpusMcpBuilder.sources.kit import section_labels

CATALOG = "https://www.aozora.gr.jp/index_pages/list_person_all_extended_utf8.zip"
SITE = "https://www.aozora.gr.jp/"
HEADING = re.compile(r'<h([3-5])[^>]*class="[^"]*midashi[^"]*"[^>]*>(.*?)</h\1>', re.S)
GAIJI = re.compile(r'<img[^>]*class="gaiji"[^>]*>', re.S)
JIS_CODE = re.compile(r"(?:第[34]水準)?([12])-(\d{1,2})-(\d{1,2})")
NOTE = re.compile(r"［＃[^］]*］")


# ---------------------------------------------------------------- the catalog

def read_catalog(raw):
    """Rows of the catalog, one per (work, person): 作品ID, 作品名, 姓, 名, 役割フラグ, 作品著作権フラグ, ..."""
    with zipfile.ZipFile(Path(raw) / "catalog.zip") as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        text = archive.read(name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def works_in_catalog(rows):
    """作品ID -> {"rows": [...], "free": True if the work and everyone credited are out of copyright}."""
    works = {}
    for row in rows:
        work = works.setdefault(row["作品ID"].lstrip("0"), {"rows": [], "free": True})
        work["rows"].append(row)
        if row["作品著作権フラグ"] != "なし" or row["人物著作権フラグ"] != "なし":
            work["free"] = False
    return works


def person_matches(row, wanted):
    full = row["姓"] + row["名"]
    roman = f"{row['名ローマ字']} {row['姓ローマ字']}".strip().lower()
    roman_reversed = f"{row['姓ローマ字']} {row['名ローマ字']}".strip().lower()
    wanted = wanted.strip()
    return wanted.lstrip("0") == row["人物ID"].lstrip("0") or wanted.replace(" ", "").replace("　", "") == full or \
        wanted.lower() in (roman, roman_reversed)


def choose(works, work_ids=(), authors=(), limit=None):
    """The work ids to use, in order: the works named, then each author's works (authors' own works first, then
    translations), skipping any work still in copyright."""
    chosen, skipped = [], []
    for work_id in work_ids:
        work_id = re.sub(r"\D", "", work_id.rsplit("card", 1)[-1]).lstrip("0")
        if work_id not in works:
            print("no work", work_id, "in the catalog", flush=True)
        elif not works[work_id]["free"]:
            skipped.append(work_id)
        elif work_id not in chosen:
            chosen.append(work_id)
    for author in authors:
        mine = [(row["役割フラグ"] != "著者", row["作品名読み"].lstrip("「『（"), work_id) for work_id, work in works.items()
                for row in work["rows"] if person_matches(row, author)]
        if not mine:
            print("no author", repr(author), "in the catalog", flush=True)
        count = 0
        for _, _, work_id in sorted(mine):
            if not works[work_id]["free"]:
                skipped.append(work_id)
                continue
            if work_id not in chosen and (not limit or count < int(limit)):
                chosen.append(work_id)
                count += 1
    if skipped:
        print(f"left out {len(set(skipped))} work(s) still in copyright", flush=True)
    return chosen


# ---------------------------------------------------------------- downloading

def fetch(raw, works=None, authors=None, limit=None, delay="2", **options):
    raw = Path(raw)
    if not works and not authors:
        raise SystemExit("say which works: --option authors=芥川竜之介 or --option works=128,773 "
                         "(the number in a card's address, www.aozora.gr.jp/cards/000879/card128.html)")
    download(CATALOG, raw / "catalog.zip")
    catalog = works_in_catalog(read_catalog(raw))
    split = lambda value: [v.strip() for v in str(value or "").split(",") if v.strip()]
    chosen = choose(catalog, split(works), split(authors), limit)
    for number, work_id in enumerate(chosen, 1):
        url = catalog[work_id]["rows"][0]["XHTML/HTMLファイルURL"]
        if not url.startswith(SITE):
            print(f"{work_id}: no XHTML file on Aozora's site ({url or 'none'})", flush=True)
            continue
        if download(url, raw / "files" / f"{work_id}.html"):
            pause(delay)
        if number % 20 == 0:
            print(f"  {number}/{len(chosen)}", flush=True)
    (raw / "works.json").write_text(json.dumps(chosen), encoding="utf-8")
    print(f"{len(chosen)} works", flush=True)


# ---------------------------------------------------------------- reading a file

def decode(data):
    declared = re.search(rb'encoding="([^"]+)"', data[:200])
    encoding = (declared.group(1).decode() if declared else "shift_jis").lower()
    return data.decode("cp932" if encoding in ("shift_jis", "sjis", "x-sjis") else encoding, errors="replace")


def gaiji_character(tag):
    """An image of a character not in Shift_JIS -> the character, from the JIS X 0213 code in its description
    ("※（「火＋畢」、第3水準1-87-64）"); else its description."""
    alt = html.unescape(re.search(r'alt="([^"]*)"', tag).group(1)) if 'alt="' in tag else "※"
    code = JIS_CODE.search(alt)
    if code:
        plane, row, cell = (int(n) for n in code.groups())
        data = (b"" if plane == 1 else b"\x8f") + bytes([0xA0 + row, 0xA0 + cell])
        try:
            return data.decode("euc_jis_2004")
        except UnicodeDecodeError:
            pass
    return alt


def plain(markup):
    markup = re.sub(r"<rt>.*?</rt>|<rp>.*?</rp>", "", markup, flags=re.S)   # ruby readings
    markup = GAIJI.sub(lambda m: gaiji_character(m.group(0)), markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return NOTE.sub("", text).strip().strip("　").strip()


def read_work(markup):
    """(blocks, credits): the text as ("h3"/"h4"/"p", text) blocks, and the credit lines (底本, 入力, 校正)."""
    start = markup.find('<div class="main_text">')
    end = markup.find('<div class="bibliographical_information">')
    body = markup[start:end if end > 0 else None] if start >= 0 else ""
    blocks = []
    position = 0
    for heading in list(HEADING.finditer(body)) + [None]:
        stop = heading.start() if heading else len(body)
        for line in re.split(r"<br\s*/?>|</div>|<div[^>]*>", body[position:stop]):
            text = plain(line)
            if text:
                blocks.append(("p", text))
        if heading:
            text = plain(heading.group(2))
            if text:
                blocks.append((f"h{heading.group(1)}", text))
            position = heading.end()
    information = markup[end:] if end > 0 else ""
    information = information[:information.find("</div>")] if "</div>" in information else information
    lines = [plain(line) for line in re.split(r"<br\s*/?>", information)]
    credits = [line for line in lines if re.match(r"^(底本|入力|校正)：", line)]
    return blocks, credits


def segments_of(blocks):
    """(segments, levels): chapter.paragraph when the text has headings, else paragraph numbers."""
    if not any(tag.startswith("h") for tag, _ in blocks):
        return [([str(n)], text) for n, (tag, text) in enumerate(blocks, 1)], ["paragraph"]
    first_heading = next(i for i, (tag, _) in enumerate(blocks) if tag.startswith("h"))
    opening = [text for tag, text in blocks[:first_heading]]
    chapters = chapters_from_blocks(blocks)
    segments = [(["0", str(n)], text) for n, text in enumerate(opening, 1)]     # text before the first heading
    for label, (heading, paragraphs) in zip(section_labels([h for h, _ in chapters]), chapters):
        segments += [([label, str(n)], text) for n, text in enumerate(paragraphs, 1)]
    return segments, ["chapter", "paragraph"]


def first_year(text):
    found = re.search(r"(1[5-9]\d\d)", text or "")
    return int(found.group(1)) if found else None


# ---------------------------------------------------------------- converting

def convert(raw, out, **options):
    raw = Path(raw)
    catalog = works_in_catalog(read_catalog(raw))
    corpus = SimpleCorpus(out, id="aozora", name="Aozora Bunko (青空文庫)", original_language="ja",
                          source="Aozora Bunko (https://www.aozora.gr.jp), transcribed by volunteers",
                          license="Works out of copyright: free to copy and share (Aozora Bunko's 取り扱い規準); "
                                  "catalog data CC BY 4.0")
    for work_id in json.loads((raw / "works.json").read_text(encoding="utf-8")):
        path = raw / "files" / f"{work_id}.html"
        if not path.exists():
            print("skipped", work_id, "(not downloaded)", flush=True)
            continue
        rows = catalog[work_id]["rows"]
        row = rows[0]
        blocks, credits = read_work(decode(path.read_bytes()))
        segments, levels = segments_of(blocks)
        if not segments:
            print("skipped", work_id, "(no text found)", flush=True)
            continue
        authors = [r["姓"] + r["名"] for r in rows if r["役割フラグ"] == "著者"] or [row["姓"] + row["名"]]
        translators = [r["姓"] + r["名"] for r in rows if r["役割フラグ"] == "翻訳者"]
        title = row["作品名"] + (f" {row['副題']}" if row["副題"] else "")
        roman = [f"{r['名ローマ字']} {r['姓ローマ字']}".strip() for r in rows if r["役割フラグ"] == "著者"]
        work = corpus.work(title, levels=levels, language="ja", authors=authors,
                           aliases=[a for a in [row["作品名"], row["作品名読み"], row["原題"], f"aozora {work_id}"] if a],
                           date=first_year(row["初出"]), date_text=row["初出"] or None,
                           categories=authors[:1] + [r for r in roman[:1] if r],
                           description=f"{row['文字遣い種別']}" + (f"; tr. {'、'.join(translators)}" if translators
                                                                   else ""))
        corpus.text(work, "ja", "青空文庫" + (f" ({'、'.join(translators)}訳)" if translators else ""), segments,
                    source=row["図書カードURL"],
                    license="public domain (Aozora Bunko). " + " ".join(credits))
        print(f"{title} ({authors[0]}): {len(segments)} paragraphs", flush=True)
    corpus.finish(profile={
        "languages": {"ja": {"name": "Japanese", "script": "japanese", "search": "cjk"}},
        "periods": [{"label": "Edo and earlier (to 1867)", "until": 1867},
                    {"label": "Meiji (1868-1912)", "until": 1912},
                    {"label": "Taishō (1912-1926)", "until": 1926},
                    {"label": "Shōwa and later (1926-)"}],
        "research": {"group_by": "date"},
    }, instructions=INSTRUCTIONS)


INSTRUCTIONS = """Japanese literature from Aozora Bunko (青空文庫): works out of copyright, transcribed by volunteers,
in Japanese only (any translation you give is your own and must be labelled as such). Many are in the old
orthography (旧字旧仮名: 云ふ, ゐる); search with both old and new forms when a term finds little.

Passages are cited as chapter.paragraph ("羅生門 3" is paragraph 3 of a work without chapters; chapter 0 is the
text before the first heading). Answer from the texts, and only from passages you have read with these tools:
research_question or search first, with key terms in Japanese, then get_text(ref, context=2) on the passages you
rely on, and verify_citations on every quote before you answer. Quote in Japanese with your translation, name the
work and author, and keep your interpretation apart from the text."""
