"""Source adapters: each turns a source into a corpus directory (docs/corpus-format.md, docs/adapters.md).

An adapter is a module with convert(raw, out, **options) and, if it downloads its source, fetch(raw, **options).
Built-in ones are named here; any other can be given as a path to its .py file or package folder."""
import importlib
import importlib.util
import sys
from pathlib import Path

ADAPTERS = {
    "sefaria": "genericCorpusMcpBuilder.sources.sefaria",      # Sefaria's public export
    "perseus": "genericCorpusMcpBuilder.sources.perseus",      # Perseus Greek and Latin texts (GitHub)
    "church": "genericCorpusMcpBuilder.sources.church",        # New Testament (Greek, Latin, English) and church fathers
    "gutenberg": "genericCorpusMcpBuilder.sources.gutenberg",  # Project Gutenberg books by number (mirror)
    "wikisource": "genericCorpusMcpBuilder.sources.wikisource",  # Wikisource editions by page title (any language)
    "suttacentral": "genericCorpusMcpBuilder.sources.suttacentral",  # Pali suttas with translations (bilara-data)
    "standardebooks": "genericCorpusMcpBuilder.sources.standardebooks",  # Standard Ebooks books (GitHub)
    "kanripo": "genericCorpusMcpBuilder.sources.kanripo",      # premodern Chinese texts; Daoist preset (GitHub)
    "aozora": "genericCorpusMcpBuilder.sources.aozora",        # Aozora Bunko: Japanese public-domain literature
    "collection": "genericCorpusMcpBuilder.sources.collection",  # your own list (CSV/TSV) of the above and files
    "tei": "genericCorpusMcpBuilder.sources.tei_files",        # a folder of TEI XML files (--raw)
    "table": "genericCorpusMcpBuilder.sources.table",          # CSV/TSV files, one row per segment (--raw)
}


def adapter(name: str):
    if name in ADAPTERS:
        return importlib.import_module(ADAPTERS[name])
    path = Path(name).expanduser()
    if path.is_dir():
        path = path / "__init__.py"
    if path.suffix == ".py" and path.exists():
        module_name = f"corpus_adapter_{path.parent.name if path.name == '__init__.py' else path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path,
                                                      submodule_search_locations=[str(path.parent)]
                                                      if path.name == "__init__.py" else None)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if not hasattr(module, "convert"):
            raise SystemExit(f"{path}: an adapter needs a convert(raw, out, **options) function")
        return module
    raise SystemExit(f"unknown source {name!r}: use one of {', '.join(ADAPTERS)} or a path to an adapter .py file")
