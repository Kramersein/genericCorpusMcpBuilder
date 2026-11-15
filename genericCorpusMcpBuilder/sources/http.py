"""Resumable downloads shared by the source adapters."""
import json
import sys
import time
import urllib.request
from pathlib import Path

USER_AGENT = "genericCorpusMcpBuilder-corpus-builder"


def is_complete(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    if path.suffix != ".json":
        return True
    try:
        json.loads(path.read_bytes())
        return True
    except ValueError:
        return False


def download(url: str, destination: Path, attempts: int = 4, headers: dict | None = None) -> bool:
    """Save url at destination unless a complete copy is there already. Returns True if it was downloaded now (so
    callers can pause between real requests), False if it was already there or could not be downloaded."""
    destination = Path(destination)
    if is_complete(destination):
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(request, timeout=120) as response, open(partial, "wb") as out:
                expected = int(response.headers.get("Content-Length") or -1)
                while chunk := response.read(1 << 20):
                    out.write(chunk)
            if expected >= 0 and partial.stat().st_size != expected:
                raise IOError(f"short read {partial.stat().st_size}/{expected}")
            partial.rename(destination)
            if is_complete(destination):
                return True
            destination.unlink()
            raise IOError("downloaded JSON does not parse")
        except Exception as error:
            if getattr(error, "code", None) == 404:
                print(f"missing: {url}", file=sys.stderr)
                return False
            time.sleep(2**attempt)
    print(f"failed: {url}", file=sys.stderr)
    return False
