"""The New Testament and church fathers: the Greek New Testament (SBLGNT) with the Clementine Vulgate and an English
Bible, and Greek and Latin fathers from Open Greek and Latin (First1KGreek, CSEL). All openly licensed.

Options (corpus-mcp build --source church --option KEY=VALUE):
  fathers       CTS author or work ids (tlg1443, stoa0040, tlg2018.tlg002); default a starting set; none = Bible only
  english       dra (Douay-Rheims 1899, default) or web (World English Bible)
  translations  languages of the fathers' translations to fetch (default eng)"""
from pathlib import Path


def fetch(raw: Path, **options) -> None:
    from genericCorpusMcpBuilder.sources.church.fetch import fetch as fetch_all
    fetch_all(Path(raw), **options)


def convert(raw: Path, out: Path, **options) -> None:
    from genericCorpusMcpBuilder.sources.church.convert import convert as convert_all
    convert_all(Path(raw), Path(out), **options)
