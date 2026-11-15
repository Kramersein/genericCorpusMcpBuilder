"""genericCorpusMcpBuilder: build an MCP server over a corpus of texts (see README.md)."""
import os

# ONNX Runtime (semantic search) sends usage telemetry to Microsoft unless this is set before it is imported.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
