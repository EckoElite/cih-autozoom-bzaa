"""A text file of media paths (one per line) that another script keeps up to date, read to queue those episodes.

The file is only ever opened for reading, in one short read, and nothing here writes to it or locks it, so the script that
makes it is not affected. Its paths are the ones that script knows (for example `/mnt/cache_media/media/TV Shows/Show/
Season 1/Show.S01E01.Title.mkv`), which are not the container's: each is matched to a file the analyzer has by the
PATH_MAP rules, else by its folder and file names (the same way Plex's paths are matched). A listed title that has no
timeline is queued in the Medium tier, with the known-variable films; one already queued lower is moved up.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .pathmap import PathMap
from .scanner import NameIndex
from .store import PRIORITY_MEDIUM, Store

log = logging.getLogger(__name__)

MAX_BYTES = 8_000_000  # a list this long is something else; read no more than this
EXAMPLES = 10  # unmatched lines kept for the status page


@dataclass
class PriorityListSummary:
    file: str
    lines: int = 0  # paths in the file
    matched: int = 0  # of those, files the analyzer has
    queued: int = 0  # not queued before, now queued
    raised: int = 0  # were waiting in a lower tier, now moved up
    waiting: int = 0  # already running, or queued as high or higher
    done: int = 0  # already analysed
    left_alone: int = 0  # skipped, missing or failed: not queued again
    unmatched: int = 0  # no file the analyzer has
    unmatched_examples: list[str] = field(default_factory=list)
    error: str | None = None


def read_lines(file: str) -> list[str]:
    """The paths in the file, in order, without blanks and `#` comments. Opened read only; one read."""
    with open(file, encoding="utf-8", errors="replace") as handle:  # mode "r": the file is never written
        text = handle.read(MAX_BYTES)
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def apply_priority_list(
    file: str, store: Store, pathmap: PathMap, now: float | None = None
) -> PriorityListSummary:
    """Read `file` and queue what it lists (see the module text). Never raises for a missing or unreadable file: the
    summary says why in `error`."""
    summary = PriorityListSummary(file=file)
    try:
        lines = read_lines(file)
    except OSError as exc:
        summary.error = f"cannot read the priority list: {exc.strerror or exc}"
        return summary
    summary.lines = len(lines)
    names: NameIndex | None = None
    seen: set[str] = set()
    for line in lines:
        local = pathmap.to_local(line)
        row = store.get(local)
        if row is None:
            if names is None:
                names = NameIndex(store.all_paths())
            local = names.find(line) or ""
            row = store.get(local) if local else None
        if row is None:
            summary.unmatched += 1
            if len(summary.unmatched_examples) < EXAMPLES:
                summary.unmatched_examples.append(line)
            continue
        if local in seen:
            continue  # the same file listed twice
        seen.add(local)
        summary.matched += 1
        status = row["status"]
        if status == "done" and row["timeline"]:
            summary.done += 1
        elif status in ("skipped", "missing", "error"):
            summary.left_alone += 1
        elif (
            status == "running"
            or (status == "queued" and row["priority"] <= PRIORITY_MEDIUM)
            or (status == "queued" and row["rank"] is not None)  # placed by hand: where the user put it stays
        ):
            summary.waiting += 1
        elif store.requeue(local, PRIORITY_MEDIUM, now):
            if status == "queued":
                summary.raised += 1
            else:
                summary.queued += 1
        else:
            summary.waiting += 1
    return summary


def seconds_until_next(times: tuple[time, ...], now: float) -> float:
    """Seconds from `now` (epoch seconds, read as local time) to the next of `times`; a day when `times` is empty."""
    moment = datetime.fromtimestamp(now)
    waits = []
    for t in times:
        due = moment.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if due <= moment:
            due += timedelta(days=1)
        waits.append((due - moment).total_seconds())
    return min(waits) if waits else 86400.0
