"""Perseus Digital Library (github.com/PerseusDL): Greek and Latin texts with English translations, as TEI XML.

Options (corpus-mcp build --source perseus --option KEY=VALUE):
  repositories  greekLit,latinLit (default both)
  works         CTS work ids, e.g. tlg0012.tlg001,phi0690.phi003 (default: a starting set; "all" = everything)
  translations  CTS language codes of translations, e.g. eng,ger (default eng; "all" = every language)"""
from pathlib import Path


def fetch(raw: Path, **options) -> None:
    from genericCorpusMcpBuilder.sources.perseus.fetch import fetch as fetch_texts
    fetch_texts(Path(raw), **options)


def convert(raw: Path, out: Path, **options) -> None:
    from genericCorpusMcpBuilder.sources.perseus.convert import convert as convert_texts
    convert_texts(Path(raw), Path(out), **options)
