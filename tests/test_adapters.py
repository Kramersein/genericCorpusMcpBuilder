"""The toolkit, the table and tei adapters, adapters loaded from a path, the scaffold, and check."""
import pytest

from genericCorpusMcpBuilder.build_corpus import build
from genericCorpusMcpBuilder.check import check
from genericCorpusMcpBuilder.corpus import Corpus
from genericCorpusMcpBuilder.scaffold import write_adapter
from genericCorpusMcpBuilder.sources import adapter
from genericCorpusMcpBuilder.sources.kit import SimpleCorpus, language_code, level_kind


def built(tmp_path, convert, raw=None, **options):
    convert(raw or tmp_path / "raw", tmp_path / "corpus", **options)
    assert check(tmp_path / "corpus") == 0
    build(tmp_path / "corpus", tmp_path / "corpus.db")
    return Corpus(str(tmp_path / "corpus.db"))


def test_level_kinds_and_language_codes():
    assert level_kind(["1", "10"]) is None and level_kind(["327a", "73b"]) == "folio"
    assert level_kind(["1", "pr."]) == "label"
    assert [language_code(c) for c in ("eng", "ger", "Latin", "grc", "")] == ["en", "de", "lat", "grc", "und"]


def test_simple_corpus_infers_kinds_names_and_page_citations(tmp_path):
    def convert(raw, out):
        corpus = SimpleCorpus(out, id="t", name="T", original_language="grc")
        plato = corpus.work("Apology", author="Plato", levels=["section"], keep_cites=True)
        xenophon = corpus.work("Apology", author="Xenophon", levels=["book", "section"])
        corpus.text(plato, "grc", "Burnet", [(["1", "17a"], "ὅτι μὲν ὑμεῖς"), (["1", "17b"], "ὦ ἄνδρες")])
        corpus.text(xenophon, "grc", "Marchant", [("1.1", "Σωκράτους δὲ ἄξιόν")])
        corpus.page_citations(plato, "Stephanus")
        corpus.finish()
    corpus = built(tmp_path, convert)
    assert corpus.resolve("Xenophon, Apology 1.1").ref == "Xenophon, Apology 1.1"
    assert corpus.resolve("Apology 17").ref == "Apology 1.17a-17b"
    levels = corpus.db.execute("SELECT levels FROM nodes WHERE base = 'Apology'").fetchone()[0]
    assert '"kind": "folio"' in levels and corpus.profile.languages["grc"]["script"] == "greek"


def test_table_adapter(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "texts.csv").write_text(
        "work,cite,text,lang,version,author,date,levels,language\n"
        "Confessions,1.1.1,\"Magnus es, domine\",lat,Skutella,Augustine,398,book.chapter.section,lat\n"
        "Confessions,1.1.1,\"Great art thou, O Lord\",en,Pusey,,,,\n")
    corpus = built(tmp_path, adapter("table").convert, original_language="lat")
    segment = corpus.get_text("Confessions 1.1.1")["segments"][0]
    assert segment["lat"] == "Magnus es, domine" and segment["en_version"] == "Pusey"
    assert corpus.work("Confessions")["comp_date"] == 398


def test_tei_adapter_groups_a_folder_into_one_work(tmp_path):
    pattern = ('<refsDecl n="CTS"><cRefPattern n="line" replacementPattern="#xpath(/tei:TEI/tei:text/tei:body/tei:div'
               "/tei:div[@n='$1']//tei:l[@n='$2'])\"/></refsDecl>")
    def tei(lang, title, lines):
        body = "".join(f'<l n="{n}">{t}</l>' for n, t in lines)
        return (f'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt><title>{title}</title>'
                f'<author>Homer</author></titleStmt></fileDesc><encodingDesc>{pattern}</encodingDesc></teiHeader>'
                f'<text><body><div xml:lang="{lang}"><div n="1">{body}</div></div></body></text></TEI>')
    folder = tmp_path / "raw" / "iliad"
    folder.mkdir(parents=True)
    (folder / "a-grc.xml").write_text(tei("grc", "Iliad", [("1", "μῆνιν ἄειδε θεὰ")]))
    (folder / "b-eng.xml").write_text(tei("eng", "The Iliad", [("1", "Sing, goddess, the anger")]))
    corpus = built(tmp_path, adapter("tei").convert, original_language="grc")
    segment = corpus.get_text("Iliad 1.1")["segments"][0]
    assert segment["grc"] == "μῆνιν ἄειδε θεὰ" and segment["en"] == "Sing, goddess, the anger"


def test_scaffolded_adapter_builds_from_its_path(tmp_path):
    write_adapter("my_texts", tmp_path)
    module = adapter(str(tmp_path / "my_texts"))
    module.fetch(tmp_path / "raw")
    corpus = built(tmp_path, module.convert)
    assert corpus.resolve("Gen. 1.2").ref == "Genesis 1.2" and corpus.profile.instructions.startswith("My Texts:")
    assert corpus.verify_citations([{"ref": "Genesis 1.1", "quote": "ברא אלהים"}])["all_verified"]


def test_check_reports_errors(tmp_path):
    out = tmp_path / "corpus"
    corpus = SimpleCorpus(out, id="bad", name="Bad", original_language="en")
    work = corpus.work("A", levels=["line"])
    corpus.text(work, "en", "v", [(["1"], "one"), (["1"], "again")])
    corpus.finish()
    (out / "profile.json").write_text('{"strata": [{"id": "x"}], "stratum_rules": [{"stratum": "y"}]}')
    assert check(out) == 1                        # the unknown stratum; the repeated citation is only a warning


def test_set_profile_replaces_instructions_without_rebuilding(tmp_path):
    from genericCorpusMcpBuilder.build_corpus import update_profile
    write_adapter("my_texts", tmp_path)
    module = adapter(str(tmp_path / "my_texts"))
    module.fetch(tmp_path / "raw")
    before = built(tmp_path, module.convert)
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "instructions.md").write_text("New instructions.")
    update_profile(tmp_path / "corpus.db", tmp_path / "new")
    after = Corpus(str(tmp_path / "corpus.db"))
    assert after.profile.instructions == "New instructions." and after.build_id == before.build_id
    assert after.profile.languages["he"]["search"] == "hebrew"          # profile.json gone: defaults by language
