#!/bin/sh
# Copies the code and a built data directory to DESTINATION (e.g. a network share), ready for
# `sudo sh DESTINATION/deploy/install.sh` on the target machine.
#
#   sh deploy/stage.sh DESTINATION [DATA_DIR]      DATA_DIR defaults to $CORPUS_MCP_DATA or ./data
#
# The embedding model is copied too (from $HF_HOME, ~/.cache/huggingface or .tools/hf), so the target need not
# download it.
set -eu
PROJECT=$(cd "$(dirname "$0")/.." && pwd)
SHARE=${1:?usage: sh deploy/stage.sh DESTINATION [DATA_DIR]}
DATA=${2:-${CORPUS_MCP_DATA:-$PROJECT/data}}

mkdir -p "$SHARE/data"
cp -r "$PROJECT/genericCorpusMcpBuilder" "$PROJECT/deploy" "$PROJECT/pyproject.toml" "$PROJECT/uv.lock" "$PROJECT/README.md" "$SHARE/"
rm -rf "$SHARE/genericCorpusMcpBuilder/__pycache__"
if [ -x "$PROJECT/.tools/uv" ]; then
    mkdir -p "$SHARE/.tools"
    cp "$PROJECT/.tools/uv" "$SHARE/.tools/uv"
fi
cp "$DATA/corpus.db" "$SHARE/data/corpus.db"
[ -f "$DATA/precomputed.db" ] && cp "$DATA/precomputed.db" "$SHARE/data/precomputed.db"
if [ -f "$DATA/vectors/vectors.json" ]; then
    mkdir -p "$SHARE/data/vectors"
    cp "$DATA/vectors/vectors.f16" "$DATA/vectors/vectors.json" "$DATA/vectors/passages.db" "$SHARE/data/vectors/"
    for extra in topics.npy topics.json; do
        [ -f "$DATA/vectors/$extra" ] && cp "$DATA/vectors/$extra" "$SHARE/data/vectors/"
    done
    for hf in "${HF_HOME:-}" "$HOME/.cache/huggingface" "$PROJECT/.tools/hf"; do
        if [ -n "$hf" ] && [ -d "$hf/hub/models--BAAI--bge-m3" ]; then
            mkdir -p "$SHARE/data/hf/hub"
            cp -r "$hf/hub/models--BAAI--bge-m3" "$SHARE/data/hf/hub/"
            break
        fi
    done
fi
du -sh "$SHARE"
