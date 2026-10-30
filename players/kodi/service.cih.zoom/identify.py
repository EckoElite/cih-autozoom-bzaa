"""Working out which title is playing from the file Kodi was given.

Nothing here imports Kodi. A playing file can be a Plex part URL (direct play), a Plex transcode URL, or a plain path
or share URL; each carries a different key, and the analyzer accepts a Plex part id, a Plex rating key, or a path.
Tokens in a URL are never kept, logged or sent anywhere: only the numbers and the path are taken from it.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

_PART = re.compile(r"/library/parts/(\d+)(?:/|$)")
_METADATA = re.compile(r"/library/metadata/(\d+)(?:/|$|\?|&)")


def identify(playing_file: str) -> dict:
    """`{"kind", "part_id", "rating_key", "path"}` for a playing file; values that do not apply are None.

    kind: "plex-part" (direct play), "plex-transcode" (only the rating key is known), "path" (a share path or a
    local file the analyzer can map), or "unknown".
    """
    text = unquote(playing_file or "")
    part = _PART.search(text)
    if part:
        return {"kind": "plex-part", "part_id": int(part.group(1)), "rating_key": None, "path": None}
    meta = _METADATA.search(text)
    if meta and ("transcode" in text or "plugin://" in text or "/video/" in text):
        return {"kind": "plex-transcode", "part_id": None, "rating_key": int(meta.group(1)), "path": None}
    scheme = urlsplit(text).scheme
    if scheme in ("smb", "nfs", "file", "special", "") or re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("/"):
        if text:
            return {"kind": "path", "part_id": None, "rating_key": None, "path": text}
    return {"kind": "unknown", "part_id": None, "rating_key": None, "path": None}


def identify_all(*sources: str) -> dict:
    """Identify a title from several names Kodi has for it (the URL being played, the library item's path): the first
    that carries a Plex id decides the kind, and a path found anywhere is kept as a second way to find the title.
    PM4K plays a `https://<server>:32400/library/parts/<id>/...` URL for an item whose own path is the Plex path."""
    found = [identify(s) for s in sources if s]
    best = next((i for i in found if i["kind"] in ("plex-part", "plex-transcode")), None)
    path = next((i["path"] for i in found if i["kind"] == "path"), None)
    if best is None:
        best = {"kind": "path" if path else "unknown", "part_id": None, "rating_key": None, "path": None}
    best = dict(best)
    best["path"] = best["path"] or path
    return best


def cache_key(ident: dict) -> str | None:
    """A file name for the on-device cache of this title's timeline."""
    if ident.get("part_id"):
        return f"part{ident['part_id']}"
    if ident.get("rating_key"):
        return f"rk{ident['rating_key']}"
    if ident.get("path"):
        import hashlib

        return "path" + hashlib.sha1(ident["path"].encode("utf-8")).hexdigest()[:16]  # noqa: S324 - a file name
    return None
