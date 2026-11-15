"""Wikisource (wikisource.org): transcribed texts in many languages, chosen by the page title of an edition.

    corpus-mcp build --source wikisource --data data-ws \
        --option works="Pride and Prejudice (1813)|The Prince (Marriott)" --option contact=you@example.org

Options (--option name=value):
    works    edition page titles, separated by | (titles can contain commas). On a "versions" page (several
             editions of one work) the adapter lists the editions; pick one.
    lang     which Wikisource: en (default), la, de, fr, zh, he, ... (the subdomain of xx.wikisource.org)
    contact  REQUIRED: an email address or web page where Wikimedia can reach you about your requests (their
             User-Agent policy). Or set CORPUS_MCP_CONTACT.
    delay    seconds between requests (default 1)

It uses the MediaWiki API one request at a time, with maxlag (it waits when Wikimedia's servers are busy), and keeps
every answer in raw/, so nothing is asked twice. An edition's main page links its chapters (subpages) in reading
order; their names give the citation (`Pride and Prejudice (1813) 1.3.2` = volume 1, chapter 3, paragraph 2).
Wikisource transcriptions are CC BY-SA 4.0; the works themselves are usually public domain.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from genericCorpusMcpBuilder import SimpleCorpus, cached_json, chapters_from_blocks, html_blocks, language_code, pause
from genericCorpusMcpBuilder.sources.kit import heading_number, section_labels

API = "https://{lang}.wikisource.org/w/api.php"
# Parts of a page that are not the text: the header box, navigation, page numbers, footnote markers and lists
SKIP_CLASSES = ["ws-noexport", "noprint", "wst-header", "ws-header", "mw-editsection", "reference", "references",
                "mw-references-wrap", "reflist", "pagenum", "ws-pagenum", "navbox", "licenseContainer", "licenseBanner"]
WIKI_LINK = re.compile(r'href="/wiki/([^"#?]+)')
YEAR_IN_TITLE = re.compile(r"\((?:[^)]*\D)?(\d{4})\)")


def user_agent(contact):
    return f"genericCorpusMcpBuilder/0.1 ({contact}; wikisource corpus adapter) Python-urllib bot"


def ask_wikisource(lang, title, contact, delay):
    """The rendered page (title, HTML) from the API, waiting and asking again while the servers are busy."""
    query = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "text", "redirects": 1,
                                    "format": "json", "formatversion": 2, "maxlag": 5})
    request = urllib.request.Request(API.format(lang=lang) + "?" + query, headers={"User-Agent": user_agent(contact)})
    for attempt in range(6):
        with urllib.request.urlopen(request, timeout=120) as response:
            answer = json.loads(response.read().decode("utf-8"))
        pause(delay)
        code = (answer.get("error") or {}).get("code")
        if code == "maxlag":                             # servers busy: wait longer each time
            time.sleep(5 * (attempt + 1))
            continue
        if code == "missingtitle":
            return {"missing": True}
        if code:
            raise SystemExit(f"Wikisource refused {title!r}: {answer['error'].get('info', code)}")
        return {"title": answer["parse"]["title"], "html": answer["parse"]["text"]}
    raise SystemExit("Wikisource stayed busy; run the same command again later (finished pages are kept)")


def page_file(raw, lang, title):
    return raw / lang / (re.sub(r"[^\w.-]+", "_", title)[:150] + ".json")


def page(raw, lang, title, contact, delay):
    """A page, from raw/ if it was downloaded before, else from the API (then saved)."""
    return cached_json(page_file(raw, lang, title), lambda: ask_wikisource(lang, title, contact, delay))


def saved_page(raw, lang, title):
    """A page fetch() saved, or {"missing": True}: converting never downloads."""
    path = page_file(raw, lang, title)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"missing": True}


def linked_titles(html):
    """The wiki pages a page links to, in order, as titles ("Pride and Prejudice (1813)/Volume 1")."""
    titles = []
    for link in WIKI_LINK.findall(html):
        title = urllib.parse.unquote(link).replace("_", " ")
        if title not in titles:
            titles.append(title)
    return titles


def chapter_pages(main_title, html):
    """The edition's subpages that hold text (not ones that only list others), in reading order."""
    subpages = [t for t in linked_titles(html) if t.startswith(main_title + "/")]
    return [t for t in subpages if not any(other.startswith(t + "/") for other in subpages)]


def labels_and_levels(main_title, subpage):
    """'X/Volume 1/Chapter 3' -> (["1", "3"], ["volume", "chapter"])"""
    parts = subpage[len(main_title) + 1:].split("/")
    labels, levels = [], []
    for part in parts:
        number = heading_number(part)
        labels.append(str(number) if number is not None else part)
        first_word = part.split()[0].lower() if part.split() else "part"
        levels.append(first_word if number is not None and not first_word[0].isdigit() else "part")
    return labels, levels


def require_contact(contact):
    contact = contact or os.environ.get("CORPUS_MCP_CONTACT")
    if not contact:
        raise SystemExit("Wikimedia asks automated clients for contact information: add --option contact=EMAIL-OR-URL "
                         "(or set CORPUS_MCP_CONTACT). It is sent only to Wikisource, in the User-Agent header.")
    return contact


def fetch_edition(raw, lang, title, contact, delay="1"):
    """Download an edition's main page and its chapter pages into raw/<lang>/ (each once)."""
    main = page(Path(raw), lang, title, contact, delay)
    if main.get("missing"):
        print(f"no page {title!r} on {lang}.wikisource.org", flush=True)
        return
    chapters = chapter_pages(main["title"], main["html"])
    print(f"{main['title']}: {len(chapters) or 'no'} subpages", flush=True)
    for number, chapter in enumerate(chapters, 1):
        page(Path(raw), lang, chapter, contact, delay)
        if number % 20 == 0:
            print(f"  {number}/{len(chapters)}", flush=True)


def fetch(raw, works=None, lang="en", contact=None, delay="1", **options):
    raw = Path(raw)
    contact = require_contact(contact)
    if not works:
        raise SystemExit('say which editions: --option works="Pride and Prejudice (1813)|The Prince (Marriott)"')
    titles = [w.strip() for w in str(works).split("|") if w.strip()]
    for title in titles:
        fetch_edition(raw, lang, title, contact, delay)
    (raw / "works.json").write_text(json.dumps({"lang": lang, "works": titles}, ensure_ascii=False), encoding="utf-8")


def edition(raw, lang, title):
    """A downloaded edition: its title, author and year, and its text as segments (labels from the page names, then
    the paragraph). None if it is missing, has no text, or is a "versions" page listing editions."""
    raw = Path(raw)
    main = saved_page(raw, lang, title)
    if main.get("missing"):
        return None
    main_title, html = main["title"], main["html"]
    chapters = chapter_pages(main_title, html)
    if not chapters:
        # a "versions" page links editions of the same title: "Pride and Prejudice (1813)", "(1817)", ...
        editions = [t for t in linked_titles(html) if t.startswith(main_title + " (") and "/" not in t]
        if editions:
            print(f"{main_title!r} lists editions; choose one: {' | '.join(editions[:10])}", flush=True)
            return None
    author = next((t.split(":", 1)[1] for t in linked_titles(html) if t.startswith("Author:")), None)
    year = YEAR_IN_TITLE.search(main_title)
    segments, levels = [], ["part"]
    if chapters:                                 # an edition with a subpage per chapter
        for chapter in chapters:
            text = saved_page(raw, lang, chapter)
            if text.get("missing"):
                continue
            labels, levels_here = labels_and_levels(main_title, chapter)
            if len(levels_here) > len(levels) or levels == ["part"]:
                levels = levels_here
            blocks = html_blocks(text["html"], skip_classes=SKIP_CLASSES)
            paragraphs = [block_text for tag, block_text in blocks if not tag.startswith("h")]
            for number, paragraph in enumerate(paragraphs, 1):
                segments.append((labels + [str(number)], paragraph))
    else:                                        # the whole text on one page, divided by headings
        found = chapters_from_blocks(html_blocks(html, skip_classes=SKIP_CLASSES))
        for label, (heading, paragraphs) in zip(section_labels([h for h, _ in found]), found):
            for number, paragraph in enumerate(paragraphs, 1):
                segments.append(([label, str(number)], paragraph))
        levels = ["chapter"]
    if not segments:
        return None
    return {"title": main_title, "author": author, "year": int(year.group(1)) if year else None,
            "language": language_code(lang), "levels": levels + ["paragraph"], "segments": segments,
            "pages": len(chapters) or 1, "version": f"Wikisource: {main_title}",
            "source": f"https://{lang}.wikisource.org/wiki/{urllib.parse.quote(main_title.replace(' ', '_'))}",
            "license": "CC BY-SA 4.0 (Wikisource)"}


def convert(raw, out, **options):
    raw = Path(raw)
    settings = json.loads((raw / "works.json").read_text(encoding="utf-8"))
    lang = settings["lang"]
    language = language_code(lang)
    corpus = SimpleCorpus(out, id=f"wikisource-{lang}", name=f"Wikisource ({lang})", original_language=language,
                          source=f"{lang}.wikisource.org", license="Wikisource transcriptions: CC BY-SA 4.0 "
                          "(https://creativecommons.org/licenses/by-sa/4.0/); the works are usually public domain")
    for title in settings["works"]:
        found = edition(raw, lang, title)
        if found is None:
            print("skipped", title)
            continue
        main_title = found["title"]
        work = corpus.work(main_title, levels=found["levels"], language=language, author=found["author"],
                           date=found["year"], aliases=[main_title.split(" (")[0]] if " (" in main_title else [])
        corpus.text(work, language, found["version"], found["segments"], source=found["source"],
                    license=found["license"])
        print(f"{main_title}: {found['pages']} pages, {len(found['segments'])} paragraphs", flush=True)
    corpus.finish(profile={"research": {"group_by": "work"}}, instructions=INSTRUCTIONS.format(lang=lang))


INSTRUCTIONS = """Texts transcribed on Wikisource ({lang}.wikisource.org), one edition each. Passages are cited by
the edition's own divisions and a paragraph number ("Pride and Prejudice (1813) 1.3.2" = volume 1, chapter 3,
paragraph 2).

Answer from the texts, and only from passages you have read with these tools: research_question or search first,
then get_text(ref, context=2) on the passages you rely on, and verify_citations on every quote before you answer.
Quote and cite, name the edition, and keep your interpretation apart from the text."""
