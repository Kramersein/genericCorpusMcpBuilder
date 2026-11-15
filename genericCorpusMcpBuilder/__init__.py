"""genericCorpusMcpBuilder: build an MCP server over a corpus of texts (see README.md)."""
import os

# ONNX Runtime (semantic search) sends usage telemetry to Microsoft unless this is set before it is imported.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")


# For adapters: from genericCorpusMcpBuilder import SimpleCorpus, download, html_to_text, read_tei, read_usfx
_SHORTCUTS = {"SimpleCorpus": "sources.kit", "language_code": "sources.kit", "html_to_text": "sources.kit",
              "download": "sources.http", "read_tei": "sources.tei", "read_usfx": "sources.usfx"}


def __getattr__(name):
    if name in _SHORTCUTS:
        import importlib
        return getattr(importlib.import_module(f"{__name__}.{_SHORTCUTS[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
