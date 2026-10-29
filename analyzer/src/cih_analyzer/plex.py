"""A small read-only Plex client (spec: Plex mapping): library sections, their files with Plex's part and rating
ids, recently added items, and whether a transcode is running.

The token goes in the `X-Plex-Token` header, never in a URL, and no error message or log line contains it. Every
request has a timeout. Nothing here writes to Plex.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

PAGE_SIZE = 200
_TOKEN_IN_TEXT = re.compile(r"(X-Plex-Token[=:]\s*)[^&\s'\"]+", re.IGNORECASE)


class PlexError(RuntimeError):
    """Plex could not be reached or answered something unusable."""


class PlexAuthError(PlexError):
    """Plex refused the token (it was rotated or is wrong)."""


def redact(text: str) -> str:
    """Remove any Plex token from text before it is logged or shown."""
    return _TOKEN_IN_TEXT.sub(r"\1<redacted>", text)


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs Plex tokens from every record (installed on the root handlers by the server)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)  # type: ignore[assignment]
        return True


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    type: str


@dataclass(frozen=True)
class PlexPart:
    path: str  # the file's path as Plex sees it
    size: int
    part_id: int
    rating_key: int
    title: str
    added_at: float | None


class PlexClient:
    def __init__(self, url: str, token: str, *, timeout: float = 15.0):
        self.base = url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(  # noqa: S310 - the URL is the user's own Plex server
            url, headers={"Accept": "application/json", "X-Plex-Token": self._token}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PlexAuthError(
                    f"Plex refused the token ({exc.code}); it may have been rotated"
                ) from None
            raise PlexError(f"Plex answered {exc.code} for {path}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PlexError(f"cannot reach Plex: {redact(str(getattr(exc, 'reason', exc)))}") from None
        except ValueError:
            raise PlexError(f"Plex sent something that is not JSON for {path}") from None

    def sections(self) -> list[Section]:
        data = self._get("/library/sections")
        return [
            Section(str(d["key"]), str(d.get("title", "")), str(d.get("type", "")))
            for d in data.get("MediaContainer", {}).get("Directory", [])
        ]

    @staticmethod
    def _parts(item: dict[str, Any]) -> Iterator[PlexPart]:
        title = str(item.get("title", ""))
        if item.get("type") == "episode":
            show = item.get("grandparentTitle")
            season, episode = item.get("parentIndex"), item.get("index")
            if show and season is not None and episode is not None:
                title = f"{show} S{int(season):02d}E{int(episode):02d} {title}".strip()
        try:
            rating_key = int(item["ratingKey"])
        except (KeyError, TypeError, ValueError):
            return
        added = item.get("addedAt")
        for media in item.get("Media", []) or []:
            for part in media.get("Part", []) or []:
                if "file" not in part or "id" not in part:
                    continue
                yield PlexPart(
                    path=str(part["file"]),
                    size=int(part.get("size") or 0),
                    part_id=int(part["id"]),
                    rating_key=rating_key,
                    title=title,
                    added_at=float(added) if added is not None else None,
                )

    def parts(self, section: Section) -> Iterator[PlexPart]:
        """Every file of a movie or TV library, paged. Other library types yield nothing."""
        if section.type not in ("movie", "show"):
            return
        start = 0
        while True:
            params: dict[str, Any] = {"X-Plex-Container-Start": start, "X-Plex-Container-Size": PAGE_SIZE}
            if section.type == "show":
                params["type"] = 4  # episodes
            container = self._get(f"/library/sections/{section.key}/all", params).get("MediaContainer", {})
            items = container.get("Metadata", []) or []
            for item in items:
                yield from self._parts(item)
            start += len(items)
            total = container.get("totalSize", container.get("size", 0))
            if not items or start >= int(total or 0):
                return

    def recently_added(self, limit: int = 200) -> list[PlexPart]:
        container = self._get(
            "/library/recentlyAdded", {"X-Plex-Container-Start": 0, "X-Plex-Container-Size": limit}
        )
        return [
            p
            for item in container.get("MediaContainer", {}).get("Metadata", []) or []
            for p in self._parts(item)
        ]

    def transcoding(self) -> bool:
        """True if any session is transcoding video (direct play and plain remuxing are cheap and do not count)."""
        sessions = self._get("/status/sessions").get("MediaContainer", {}).get("Metadata", []) or []
        return any((s.get("TranscodeSession") or {}).get("videoDecision") == "transcode" for s in sessions)
