"""Content fingerprint used to notice that a file was replaced: sha1(first 4 MiB + last 4 MiB + size)."""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 4 * 1024 * 1024


def quick_hash(path: str | Path) -> str:
    """SHA-1 over the first and last 4 MiB and the byte size. Opens the file read-only; reads at most 8 MiB.

    Files under 4 MiB are hashed whole (head and tail are the same bytes); the size is always included, so two
    files that differ only in length never collide.
    """
    p = Path(path)
    size = p.stat().st_size
    digest = hashlib.sha1(usedforsecurity=False)
    with p.open("rb") as handle:
        digest.update(handle.read(CHUNK))
        handle.seek(max(0, size - CHUNK))
        digest.update(handle.read(CHUNK))
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()
