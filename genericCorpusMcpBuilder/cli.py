"""Command line: `corpus-mcp serve` runs the MCP server, `corpus-mcp build` builds a data directory from a source."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_DATA = "data"


def data_dir(value: str | None) -> Path:
    return Path(value or os.environ.get("CORPUS_MCP_DATA") or DEFAULT_DATA).expanduser().resolve()


def serve(args: argparse.Namespace) -> None:
    directory = data_dir(args.data)
    if not (directory / "corpus.db").exists():
        sys.exit(f"no corpus.db in {directory}; build it with `corpus-mcp build --data {directory}` "
                 "or point --data / CORPUS_MCP_DATA at an existing data directory")
    os.environ["CORPUS_MCP_DATA"] = str(directory)
    for name, value in (("CORPUS_MCP_HOST", args.host), ("CORPUS_MCP_PORT", args.port),
                        ("CORPUS_MCP_TRANSPORT", args.transport)):
        if value is not None:
            os.environ[name] = str(value)
    from genericCorpusMcpBuilder import server  # the corpus opens on import, so the environment must be set first
    server.main()


def step(description: str, *module_args: str) -> None:
    print(f"\n== {description}", flush=True)
    subprocess.run([sys.executable, "-m", *module_args], check=True)


def build(args: argparse.Namespace) -> None:
    directory = data_dir(args.data)
    directory.mkdir(parents=True, exist_ok=True)
    raw = Path(args.raw).expanduser().resolve() if args.raw else directory / "raw"
    converted = Path(args.corpus).expanduser().resolve() if args.corpus else directory / "corpus"
    db, vectors = str(directory / "corpus.db"), str(directory / "vectors")
    print(f"data directory: {directory}", flush=True)
    downloaded = False
    if not args.corpus:
        from genericCorpusMcpBuilder.sources import adapter
        source = adapter(args.source)
        options = dict(option.split("=", 1) for option in args.option)
        downloaded = hasattr(source, "fetch")          # adapters for local files (tei, table) only convert
        if downloaded and not args.skip_fetch:
            print(f"\n== downloading {args.source} into {raw} (resumable)", flush=True)
            source.fetch(raw, **options)
        print(f"\n== converting {raw} into the corpus format at {converted}", flush=True)
        if converted.exists():
            import shutil
            shutil.rmtree(converted)
        source.convert(raw, converted, **options)
    problems = check_corpus(converted)
    if problems:
        raise SystemExit(f"{converted} has {problems} problem(s) (listed above); fix the source or the adapter")
    step("building corpus.db", "genericCorpusMcpBuilder.build_corpus", str(converted), db)
    if not args.no_vectors:
        step("embedding passages (many hours on CPU, resumable)", "genericCorpusMcpBuilder.vectors", db, vectors)
        step("embedding topics", "genericCorpusMcpBuilder.vectors", "--topics", db, vectors)
        step("locating passages in this build", "genericCorpusMcpBuilder.vectors", "--locate", db, vectors)
        if not (Path(vectors) / "onnx" / "model.onnx").exists():
            step("exporting the embedding model to ONNX (serving then needs no torch)",
                 "genericCorpusMcpBuilder.vectors", "--export-onnx", vectors)
    step("precomputing topic dossiers", "genericCorpusMcpBuilder.precompute", db, str(directory / "precomputed.db"))
    if args.delete_raw and not args.corpus:
        import shutil
        if downloaded:                                 # never delete input the user supplied (tei, table)
            shutil.rmtree(raw, ignore_errors=True)
        shutil.rmtree(converted, ignore_errors=True)
    print(f"\ndone: serve it with `corpus-mcp serve --data {directory}`", flush=True)


def check_corpus(corpus: Path) -> int:
    from genericCorpusMcpBuilder.check import check
    print(f"\n== checking {corpus}", flush=True)
    return check(corpus)


def check_command(args: argparse.Namespace) -> None:
    raise SystemExit(1 if check_corpus(Path(args.corpus).expanduser().resolve()) else 0)


def new_adapter(args: argparse.Namespace) -> None:
    from genericCorpusMcpBuilder.scaffold import write_adapter
    write_adapter(args.name, Path(args.dir).expanduser().resolve())


def main() -> None:
    parser = argparse.ArgumentParser(prog="corpus-mcp", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    serve_parser = commands.add_parser("serve", help="run the MCP server")
    serve_parser.add_argument("--data", help=f"data directory (default: $CORPUS_MCP_DATA or ./{DEFAULT_DATA})")
    serve_parser.add_argument("--transport", choices=["stdio", "http"],
                              help="stdio for a local client, http for a network service (default: http)")
    serve_parser.add_argument("--host", help="HTTP bind address (default: $CORPUS_MCP_HOST or 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, help="HTTP port (default: $CORPUS_MCP_PORT or 8765)")
    serve_parser.set_defaults(handler=serve)

    build_parser = commands.add_parser("build", help="download a source, convert it and build the data directory")
    build_parser.add_argument("--data", help=f"data directory to create (default: $CORPUS_MCP_DATA or ./{DEFAULT_DATA})")
    build_parser.add_argument("--source", default="sefaria",
                              help="source adapter: sefaria, perseus, tei, table, or the path of your own adapter "
                                   "(default: sefaria)")
    build_parser.add_argument("--option", action="append", default=[], metavar="KEY=VALUE",
                              help="adapter option, repeatable (perseus: works=..., repositories=..., translations=...)")
    build_parser.add_argument("--corpus", help="build from an existing corpus directory (docs/corpus-format.md) "
                                               "instead of downloading and converting a source")
    build_parser.add_argument("--raw", help="the source's files: where downloads are kept, or your own files for the "
                                            "tei and table sources (default: <data>/raw)")
    build_parser.add_argument("--skip-fetch", action="store_true", help="reuse an already downloaded source")
    build_parser.add_argument("--no-vectors", action="store_true",
                              help="skip semantic search (saves many hours; keyword search still works)")
    build_parser.add_argument("--delete-raw", action="store_true", help="delete the downloaded and converted source files when done")
    build_parser.set_defaults(handler=build)

    check_parser = commands.add_parser("check", help="check a corpus directory for problems before building it")
    check_parser.add_argument("corpus", help="the corpus directory (docs/corpus-format.md)")
    check_parser.set_defaults(handler=check_command)

    adapter_parser = commands.add_parser("new-adapter", help="write a working adapter template to start from")
    adapter_parser.add_argument("name", help="the adapter's name, e.g. my_texts")
    adapter_parser.add_argument("--dir", default=".", help="where to create it (default: here)")
    adapter_parser.set_defaults(handler=new_adapter)

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
