# genericCorpusMcpBuilder server image. The data directory is not baked in: mount it at /data (build it with `corpus-mcp build`,
# which this image can also run). The embedding model is downloaded to /cache on first start.
#
#   docker build -t corpus-mcp .
#   docker build -t corpus-mcp:lite --build-arg EXTRAS= .     # no semantic search, ~1.5 GB smaller
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ARG EXTRAS="--extra semantic"
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project $EXTRAS
COPY genericCorpusMcpBuilder ./genericCorpusMcpBuilder
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev $EXTRAS

RUN useradd --uid 1000 --create-home corpusmcp && install -d -o corpusmcp -g corpusmcp /data /cache
USER corpusmcp

ENV PATH=/app/.venv/bin:$PATH \
    CORPUS_MCP_DATA=/data \
    CORPUS_MCP_HOST=0.0.0.0 \
    CORPUS_MCP_PORT=8765 \
    HF_HOME=/cache/hf
VOLUME ["/data", "/cache"]
EXPOSE 8765
ENTRYPOINT ["corpus-mcp"]
CMD ["serve"]
