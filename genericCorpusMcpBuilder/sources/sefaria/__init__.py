"""Sefaria (sefaria.org): Tanakh, Talmud, codes, commentaries and responsa, from Sefaria's public export."""
from pathlib import Path


def fetch(raw: Path, **_options) -> None:
    from genericCorpusMcpBuilder.sources.sefaria.fetch import fetch as fetch_all
    fetch_all(Path(raw))


def convert(raw: Path, out: Path, **_options) -> None:
    from genericCorpusMcpBuilder.sources.sefaria.convert import convert as convert_all
    convert_all(Path(raw), Path(out))
