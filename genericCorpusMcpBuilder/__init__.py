"""genericCorpusMcpBuilder: build an MCP server over a corpus of texts (see README.md)."""
import os

# The project was called torahMcp: its TORAH_MCP_* settings still work where no CORPUS_MCP_* setting is given.
for _name, _value in list(os.environ.items()):
    if _name.startswith("TORAH_MCP_"):
        os.environ.setdefault("CORPUS_MCP_" + _name.removeprefix("TORAH_MCP_"), _value)
