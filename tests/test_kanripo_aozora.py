"""The Kanripo and Aozora Bunko adapters on saved files shaped like the real ones (no network)."""
import csv
import io
import json
import zipfile

from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.check import check
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.sources import aozora, kanripo


def built(tmp_path, convert):
    convert(tmp_path / "raw", tmp_path / "corpus")
    assert check(tmp_path / "corpus") == 0
    build(tmp_path / "corpus", tmp_path / "corpus.db")
    return Corpus(str(tmp_path / "corpus.db"))


ZHUANGZI = """# -*- mode: mandoku-view -*-
#+TITLE: 莊子
#+PROPERTY: BASEEDITION CHANT
# src: ZHUANG 1.1.1; Guo Qingfan 4
<pb:KR5c0126_CHANT_001-1a>¶
** 1 逍遙遊第一

北冥有魚，¶
其名為鯤。¶

# src: ZHUANG 1.1.2; Guo Qingfan 4
<pb:KR5c0126_CHANT_001-2a>¶
齊諧者，¶
志怪者也。¶
"""
ZUOWANG = """# -*- mode: mandoku-view; -*-
#+TITLE: 坐忘論
#+PROPERTY: BASEEDITION HFL
<pb:KR5d0059_HFL_001-001a>¶
坐忘論¶
　　　　司馬承禎子微撰¶
夫人之所貴者生生之所貴者道人之有道¶
若魚之有水¶
　　　敬信一¶
<pb:KR5d0059_HFL_001-001b>¶
夫信者道之根敬者德之蒂¶
<pb:KR5d0059_HFL_001-002a>¶
莊云同於大通¶
"""
CATALOG = """# -*- mode: org; -*-
** KR5c ZB5c 洞神部
*** KR5c0126 莊子--
*** KR5d0059 坐忘論--司馬承禎
:PROPERTIES:
:KR_ID: KR5d0059
:DZ: DZ1036
:END:
"""


def test_kanripo(tmp_path):
    raw = tmp_path / "raw"
    for text, edition, files in [("KR5c0126", "master", {"KR5c0126_001.txt": ZHUANGZI}),
                                 ("KR5d0059", "master", {"KR5d0059_001.txt": ZUOWANG})]:
        folder = raw / text / edition
        folder.mkdir(parents=True)
        for name, body in files.items():
            (folder / name).write_text(body, encoding="utf-8")
        (folder / "Readme.org").write_text(
            "".join(f"[[file:{name}][〈逍遙遊〉第一]]\n" for name in files if text == "KR5c0126"), encoding="utf-8")
        (raw / text / "files.json").write_text(json.dumps({"edition": edition, "editions": [edition],
                                                           "files": list(files)}))
    (raw / "catalog").mkdir()
    (raw / "catalog" / "KR5c.txt").write_text(CATALOG, encoding="utf-8")
    (raw / "texts.json").write_text(json.dumps({"texts": ["KR5c0126", "KR5d0059"], "preset": "daoist"}))
    corpus = built(tmp_path, kanripo.convert)

    assert corpus.get_text("Zhuangzi 1.1")["segments"][0]["lzh"] == "北冥有魚，其名為鯤。"   # not the heading
    assert corpus.get_text("Zhuangzi 1.0")["segments"][0]["lzh"] == "逍遙遊第一"
    assert corpus.resolve("ZHUANG 1.1.2").ref == "Zhuangzi 1.2"                 # TLS reference
    assert corpus.resolve("莊子 逍遙遊").ref.startswith("Zhuangzi 1")
    assert corpus.resolve("Chuang Tzu 1.1").ref == "Zhuangzi 1.1"
    # an unpunctuated Daozang text: headings by indentation, paragraphs split at page breaks
    texts = [s["lzh"] for s in corpus.get_text("Zuowang lun 1")["segments"]]
    assert texts[:3] == ["坐忘論", "司馬承禎子微撰", "夫人之所貴者生生之所貴者道人之有道若魚之有水"]
    assert corpus.resolve("Zuowang lun 敬信一").ref == "Zuowang lun 1.4-6"
    assert corpus.resolve("Zuowang lun p1b").ref == "Zuowang lun 1.5"
    report = corpus.verify_citations([{"ref": "Zuowang lun 1.6", "quote": "同於大通"}])["results"][0]
    assert report["quote_found"]
    assert corpus.work("Zuowang lun")["authors"] == ["Sima Chengzhen"]
    assert [h["ref"] for h in corpus.search("鯤")] == ["Zhuangzi 1.1"]


MAIN = """<?xml version="1.0" encoding="Shift_JIS"?>
<html><body><div class="metadata"><h1 class="title">テスト</h1></div>
<div class="main_text"><br />
<h3 class="o-midashi"><a class="midashi_anchor" id="midashi10">一</a></h3><br />
　<ruby><rb>或日</rb><rp>（</rp><rt>あるひ</rt><rp>）</rp></ruby>の暮方の事である。［＃「暮方」に傍点］<br />
　<img src="x.png" alt="※(「陷のつくり＋炎」、第3水準1-87-64)" class="gaiji" />が燃える。<br />
<h3 class="o-midashi"><a class="midashi_anchor" id="midashi20">二</a></h3><br />
　下人は雨を待つてゐた。<br />
</div>
<div class="bibliographical_information"><hr /><br />
底本：「羅生門」阿蘭陀書房<br />入力：j.utiyama<br />校正：野口英司<br />1999年6月9日公開<br /></div>
</body></html>"""
COLUMNS = ["作品ID", "作品名", "作品名読み", "副題", "原題", "初出", "文字遣い種別", "作品著作権フラグ", "図書カードURL",
           "人物ID", "姓", "名", "姓ローマ字", "名ローマ字", "役割フラグ", "人物著作権フラグ", "XHTML/HTMLファイルURL"]


def catalog_zip(path, rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("list_person_all_extended_utf8.csv", "﻿" + out.getvalue())


def person(work_id, title, free="なし", role="著者", **extra):
    return {"作品ID": work_id, "作品名": title, "作品名読み": title, "副題": "", "原題": "",
            "初出": "「帝国文学」1915（大正4）年11月", "文字遣い種別": "旧字旧仮名", "作品著作権フラグ": "なし",
            "図書カードURL": f"https://www.aozora.gr.jp/cards/000879/card{work_id}.html", "人物ID": "000879",
            "姓": "芥川", "名": "竜之介", "姓ローマ字": "Akutagawa", "名ローマ字": "Ryunosuke", "役割フラグ": role,
            "人物著作権フラグ": free, "XHTML/HTMLファイルURL": f"https://www.aozora.gr.jp/cards/000879/files/{work_id}.html",
            **extra}


def test_aozora_choice_skips_works_in_copyright():
    works = aozora.works_in_catalog([person("000128", "羅生門"), person("000200", "訳書"),
                                     {**person("000200", "訳書", free="あり", role="翻訳者"), "人物ID": "009999"},
                                     {**person("000300", "後年"), "作品著作権フラグ": "あり"}])
    assert aozora.choose(works, authors=["Akutagawa Ryunosuke"]) == ["128"]
    assert aozora.choose(works, work_ids=["https://www.aozora.gr.jp/cards/000879/card128.html", "300"]) == ["128"]
    assert aozora.choose(works, authors=["芥川竜之介"]) == aozora.choose(works, authors=["879"]) == ["128"]


def test_aozora(tmp_path):
    raw = tmp_path / "raw"
    (raw / "files").mkdir(parents=True)
    catalog_zip(raw / "catalog.zip", [person("000128", "羅生門")])
    (raw / "files" / "128.html").write_bytes(MAIN.encode("cp932"))
    (raw / "works.json").write_text(json.dumps(["128"]))
    corpus = built(tmp_path, aozora.convert)

    first = corpus.get_text("羅生門 1.1")["segments"][0]["ja"]
    assert first == "或日の暮方の事である。"                                  # no ruby, no ［＃…］ note
    assert corpus.get_text("羅生門 1.2")["segments"][0]["ja"] == "燄が燃える。"        # 外字 restored
    assert corpus.get_text("羅生門 2.1")["segments"][0]["ja"] == "下人は雨を待つてゐた。"
    assert corpus.resolve("aozora 128 2.1").ref == "羅生門 2.1"
    info = corpus.work("羅生門")
    assert info["authors"] == ["芥川竜之介"] and info["comp_date"] == 1915
    version = corpus.get_work_info("羅生門")["versions"][0] if hasattr(corpus, "get_work_info") else None
    assert [h["ref"] for h in corpus.search("下人")] == ["羅生門 2.1"]
