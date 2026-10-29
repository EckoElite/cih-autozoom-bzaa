"""Finding work: walk the media roots, and read Plex's library to attach its ids to the files found.

Unraid user shares are a FUSE filesystem, so nothing here relies on change notifications: the scanner walks on a
timer and compares size and modification time with what the store holds. It never opens a media file (only
`stat`), never writes to the media, and never follows symlinks out of a root.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .pathmap import PathMap
from .plex import PlexAuthError, PlexClient, PlexError
from .skip import DISC_IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, SkipConfig, path_skip_reason
from .store import PRIORITY_BACKLOG, Store

log = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    seen: int = 0
    new: int = 0
    changed: int = 0
    reanalyze: int = 0
    returned: int = 0
    skipped: int = 0
    missing: int = 0
    errors: list[str] = field(default_factory=list)


def scan_media(
    roots: list[str],
    store: Store,
    version: str,
    *,
    skip_config: SkipConfig | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    now: float | None = None,
) -> ScanSummary:
    """Walk every root once and update the store. Files that vanished are marked `missing`, but only for a root
    that was walked completely (an unreachable share must not make a whole library look deleted)."""
    config = skip_config or SkipConfig()
    summary = ScanSummary()
    for root in roots:
        problems: list[object] = []  # strings, and the OSErrors os.walk reports
        if not os.path.isdir(root):
            summary.errors.append(f"{root}: not a directory or not mounted")
            continue
        found: set[str] = set()
        complete = True
        for directory, dirs, files in os.walk(root, followlinks=False, onerror=problems.append):
            if should_stop():
                complete = False
                break
            dirs.sort()
            for name in sorted(files):
                ext = os.path.splitext(name)[1].lower()
                if ext not in VIDEO_EXTENSIONS and ext not in DISC_IMAGE_EXTENSIONS:
                    continue
                path = directory.replace("\\", "/").rstrip("/") + "/" + name
                try:
                    stat = os.stat(path)
                except OSError as exc:
                    problems.append(f"{path}: {exc.strerror}")
                    continue
                found.add(path)
                summary.seen += 1
                reason = path_skip_reason(path, config)
                if reason:
                    store.mark_skipped(path, reason, stat.st_size, stat.st_mtime)
                    summary.skipped += 1
                    continue
                outcome = store.seen(
                    path, stat.st_size, stat.st_mtime, version=version, priority=PRIORITY_BACKLOG, now=now
                )
                if outcome != "unchanged":
                    setattr(summary, outcome, getattr(summary, outcome) + 1)
        if problems:
            complete = False
            summary.errors.extend(str(p) for p in problems[:20])
        if complete:
            summary.missing += store.mark_missing([root.replace("\\", "/")], found)
    return summary


@dataclass
class PlexSummary:
    sections: int = 0
    parts: int = 0
    matched: int = 0
    unknown: int = (
        0  # Plex lists the file but the scanner has not seen it (not mounted, or outside the roots)
    )
    boosted: int = 0
    skipped_sections: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sync_plex(
    client: PlexClient,
    store: Store,
    pathmap: PathMap,
    *,
    skip_sections: tuple[str, ...] = (),
    recent_days: float = 14.0,
    now: float | None = None,
) -> PlexSummary:
    """Attach Plex's part id, rating key and title to the files the scanner found, and move recently added
    titles to the front of the queue. Raises `PlexError` if Plex cannot be read."""
    now = time.time() if now is None else now
    summary = PlexSummary()
    skip = {s.casefold() for s in skip_sections}
    for section in client.sections():
        if section.title.casefold() in skip:
            summary.skipped_sections.append(section.title)
            continue
        if section.type not in ("movie", "show"):
            continue
        summary.sections += 1
        try:
            for part in client.parts(section):
                summary.parts += 1
                local = pathmap.to_local(part.path)
                ok = store.set_plex(
                    local,
                    part_id=part.part_id,
                    rating_key=part.rating_key,
                    title=part.title,
                    added_at=part.added_at,
                )
                if ok:
                    summary.matched += 1
                else:
                    summary.unknown += 1
        except PlexAuthError:
            raise  # the token is wrong for every section: stop and say so
        except PlexError as exc:
            summary.errors.append(f"{section.title}: {exc}")  # one library failing must not hide the others
    summary.boosted = store.boost_recent(now - recent_days * 86400)
    return summary
