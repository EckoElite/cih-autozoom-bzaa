"""SQLite store (spec D8): one file's row holds both what we know about it and its place in the queue, so the queue
survives a restart and a half-finished job is simply put back.

* WAL mode, one connection guarded by a lock (the API, the scheduler's workers and the scanner share it).
* `PRAGMA user_version` is the schema version; `MIGRATIONS` is applied in order, each in its own transaction, so an
  upgraded container finds its old appdata and carries on.
* A changed file (size or modification time) loses its timeline at once: a stale timeline would zoom the wrong
  picture, and a missing one falls back to the safe contain-by-display-ratio rule.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# One queue in four tiers (a lower number is more urgent), and express above it.
PRIORITY_PLAYER = (
    -1
)  # express: a player is waiting for it now; express workers take only these and skip the queue
PRIORITY_HIGHEST = 1  # P1: someone pressed Analyze again on the status page
PRIORITY_HIGH = (
    2  # P2: a re-queue made through the API or a script, and automatic re-analysis (file or version changed)
)
PRIORITY_MEDIUM = 3  # P3: a title on the known-variable-ratio list
PRIORITY_LOW = 4  # P4: everything else; recently added to Plex titles go first (the `recent` flag)
TIERS = (PRIORITY_HIGHEST, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)
TIER_NAMES = {1: "Highest", 2: "High", 3: "Medium", 4: "Low"}

# The order of the queue: tier, then what was placed by hand (`rank`), then the tier's own rule: first come first served
# for P1 and P2; for P3 and P4 recently added first, then the newest file first.
QUEUE_ORDER = (
    "priority ASC, (rank IS NULL) ASC, rank ASC, "
    "CASE WHEN priority <= 2 THEN 0 ELSE 1 - recent END ASC, "
    "CASE WHEN priority <= 2 THEN queued_at ELSE -mtime END ASC, path ASC"
)

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE files (
        path TEXT PRIMARY KEY,
        size INTEGER NOT NULL DEFAULT 0,
        mtime REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 2,
        skip_reason TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        analyzer_version TEXT,
        analyzed_at REAL,
        queued_at REAL,
        started_at REAL,
        duration_s REAL,
        quick_hash TEXT,
        part_id INTEGER,
        rating_key INTEGER,
        title TEXT,
        added_at REAL,
        timeline TEXT,
        ramp_raw TEXT,
        flags TEXT,
        segments INTEGER
    );
    CREATE INDEX files_queue ON files(status, priority, queued_at);
    CREATE INDEX files_part ON files(part_id);
    CREATE INDEX files_rating ON files(rating_key);
    CREATE INDEX files_hash ON files(quick_hash);
    CREATE TABLE overrides (
        path TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        preset REAL,
        updated REAL NOT NULL
    );
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
    """,
    # known-variable titles got their own priority between on demand (0) and recent: recent and backlog move down one
    "UPDATE files SET priority = priority + 1 WHERE priority >= 1",
    # how a hand re-queue asked for the analysis to run (JSON: {"detailed": true} or {"survey_interval": 2}), for one run
    "ALTER TABLE files ADD COLUMN options TEXT",
    # four tiers (1 highest ... 4 low) instead of the old lanes: hand re-queue 0 -> 2, known variable 1 -> 3,
    # recently added 2 -> 4 with the `recent` flag, backlog 3 -> 4. `rank` is the place a title was given by hand within its
    # tier (NULL: not placed, the tier's own rule decides); `ran` is how the last analysis was run, for the title page.
    """
    ALTER TABLE files ADD COLUMN rank REAL;
    ALTER TABLE files ADD COLUMN recent INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE files ADD COLUMN ran TEXT;
    UPDATE files SET recent = CASE WHEN priority = 2 THEN 1 ELSE 0 END,
        priority = CASE priority WHEN 0 THEN 2 WHEN 1 THEN 3 ELSE 4 END WHERE priority >= 0;
    DROP INDEX files_queue;
    CREATE INDEX files_queue ON files(status, priority, rank, queued_at)
    """,
)

_LIST_COLUMNS = (
    "path, size, mtime, status, priority, skip_reason, error, attempts, analyzer_version, analyzed_at, queued_at, "
    "started_at, duration_s, quick_hash, part_id, rating_key, title, added_at, flags, segments, options, rank, recent, ran"
)


def major_of(version: str | None) -> str:
    """The part of a version that decides whether old results are still current: the major number, or for 0.x the
    minor too (0.1 and 0.2 are different analyzers)."""
    parts = (version or "").split(".")
    if len(parts) < 2 or not parts[0].isdigit():
        return ""
    return parts[0] if parts[0] != "0" else f"0.{parts[1]}"


class Store:
    def __init__(self, path: str | Path, migrations: Sequence[str] = MIGRATIONS):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._migrate(migrations)

    def _migrate(self, migrations: Sequence[str]) -> None:
        with self._lock:
            current = self._db.execute("PRAGMA user_version").fetchone()[0]
            if current > len(migrations):
                raise RuntimeError(
                    f"the database is at schema version {current}, newer than this analyzer's {len(migrations)}"
                )
            for version in range(current, len(migrations)):
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _statements(migrations[version]):
                        self._db.execute(statement)
                    self._db.execute(f"PRAGMA user_version = {version + 1}")
                    self._db.execute("COMMIT")
                except Exception:
                    self._db.execute("ROLLBACK")
                    raise

    @property
    def schema_version(self) -> int:
        with self._lock:
            return self._db.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _one(self, sql: str, args: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def _run(self, sql: str, args: Sequence[Any] = ()) -> int:
        with self._lock:
            return self._db.execute(sql, args).rowcount

    # ---- discovery -------------------------------------------------------------------------------------------

    def seen(
        self,
        path: str,
        size: int,
        mtime: float,
        *,
        version: str,
        priority: int = PRIORITY_LOW,
        now: float | None = None,
        on_demand: bool = False,
    ) -> str:
        """Record a file the scanner saw. Returns `new`, `changed` (its timeline is dropped and it is queued again, as High),
        `reanalyze` (the analyzer's major version moved on; queued as High), `returned` (it had been missing), `idled` (on demand, but it was
        queued in the background before that was set), `raised` (still waiting, now at a higher priority) or `unchanged`.

        With `on_demand` (a library analysed only when a player asks for a title) a file is kept `idle` instead of
        queued, and a new analyzer version does not queue it: it is analysed when asked for."""
        now = time.time() if now is None else now
        waiting = "idle" if on_demand else "queued"
        with self._lock:
            row = self._one(
                "SELECT status, priority, rank, size, mtime, analyzer_version, timeline FROM files WHERE path = ?",
                (path,),
            )
            if row is None:
                self._run(
                    f"INSERT INTO files(path, size, mtime, status, priority, queued_at) VALUES (?, ?, ?, '{waiting}', ?, ?)",  # noqa: S608
                    (path, size, mtime, priority, now),
                )
                return "new"
            if row["size"] != size or abs(row["mtime"] - mtime) > 1e-3:
                # a replaced file is re-analysed ahead of the plain queue (High); an on-demand file just waits
                again = priority if on_demand else min(PRIORITY_HIGH, priority)
                if row["status"] == "queued":
                    again = min(again, row["priority"])
                self._run(
                    f"UPDATE files SET size=?, mtime=?, status='{waiting}', priority=?, queued_at=?, error=NULL, "  # noqa: S608
                    "attempts=0, timeline=NULL, ramp_raw=NULL, quick_hash=NULL, flags=NULL, segments=NULL, "
                    "skip_reason=NULL, ran=NULL, recent=0 WHERE path=? AND status != 'running'",
                    (size, mtime, again, now, path),
                )
                if row["status"] != "queued":
                    self._run("UPDATE files SET rank=NULL WHERE path=? AND status != 'running'", (path,))
                return "changed"
            if row["status"] == "missing":
                status = "done" if row["timeline"] else waiting
                self._run("UPDATE files SET status=?, queued_at=? WHERE path=?", (status, now, path))
                return "returned"
            if on_demand:
                if row["status"] == "queued" and not row["timeline"] and row["priority"] >= PRIORITY_LOW:
                    # queued in the background before this folder was marked on demand (the setting was added after
                    # the first scan): put it back to waiting for a player
                    self._run("UPDATE files SET status='idle' WHERE path=?", (path,))
                    return "idled"
                return "unchanged"
            if row["status"] == "done" and major_of(row["analyzer_version"]) != major_of(version):
                self._run(
                    "UPDATE files SET status='queued', priority=?, queued_at=?, attempts=0, rank=NULL, recent=0 "
                    "WHERE path=?",
                    (min(PRIORITY_HIGH, priority), now, path),
                )
                return "reanalyze"
            if row["status"] == "queued" and row["priority"] > priority and row["rank"] is None:
                # a title that is waiting at a lower priority than the scanner now gives it (it was added to the
                # known-variable list after it was first queued): move it up, never down, and never one that was
                # placed by hand
                self._run("UPDATE files SET priority=? WHERE path=?", (priority, path))
                return "raised"
            return "unchanged"

    def restart(self, path: str, size: int, mtime: float, priority: int, now: float | None = None) -> None:
        """A file changed under a running job: drop what we know and queue it again, running or not."""
        self._run(
            "UPDATE files SET size=?, mtime=?, status='queued', priority=?, queued_at=?, error=NULL, attempts=0, "
            "timeline=NULL, ramp_raw=NULL, quick_hash=NULL, flags=NULL, segments=NULL, skip_reason=NULL, "
            "started_at=NULL, options=NULL, ran=NULL, rank=NULL, recent=0 WHERE path=?",
            (size, mtime, priority, time.time() if now is None else now, path),
        )

    def mark_skipped(self, path: str, reason: str, size: int = 0, mtime: float = 0.0) -> None:
        with self._lock:
            self._run(
                "INSERT INTO files(path, size, mtime, status, skip_reason) VALUES (?, ?, ?, 'skipped', ?) "
                "ON CONFLICT(path) DO UPDATE SET status='skipped', skip_reason=excluded.skip_reason, "
                "size=excluded.size, mtime=excluded.mtime, error=NULL",
                (path, size, mtime, reason),
            )

    def mark_missing(self, roots: Sequence[str], seen: set[str]) -> int:
        """Files under `roots` that a complete scan did not see are `missing`; their timelines are kept in case the
        file comes back."""
        marked = 0
        with self._lock:
            for root in roots:
                prefix = root.rstrip("/") + "/"
                rows = self._all(
                    "SELECT path FROM files WHERE substr(path, 1, ?) = ? AND status NOT IN ('missing', 'running')",
                    (len(prefix), prefix),
                )
                for r in rows:
                    if r["path"] not in seen:
                        self._run("UPDATE files SET status='missing' WHERE path=?", (r["path"],))
                        marked += 1
        return marked

    # ---- jobs --------------------------------------------------------------------------------------------------

    def claim_next(self, now: float | None = None, max_priority: int | None = None) -> dict[str, Any] | None:
        """Take the most urgent queued file and mark it running: express (a player is waiting) first, then the tiers in
        order, each in its own order (see `QUEUE_ORDER`). With `max_priority`, only files at least that urgent
        (`PRIORITY_PLAYER`: only what a player is waiting for)."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._one(
                f"SELECT path FROM files WHERE status='queued' AND priority <= ? ORDER BY {QUEUE_ORDER} LIMIT 1",  # noqa: S608
                (99 if max_priority is None else max_priority,),
            )
            if row is None:
                return None
            self._run(
                "UPDATE files SET status='running', started_at=?, attempts=attempts+1, error=NULL WHERE path=?",
                (now, row["path"]),
            )
            return self._one(f"SELECT {_LIST_COLUMNS} FROM files WHERE path=?", (row["path"],))

    def finish(
        self,
        path: str,
        *,
        timeline_json: str,
        ramp_raw_json: str | None,
        quick_hash: str,
        version: str,
        flags: Sequence[str],
        segments: int,
        duration_s: float,
        now: float | None = None,
        ran: str | None = None,
    ) -> None:
        now = time.time() if now is None else now
        self._run(
            "UPDATE files SET status='done', timeline=?, ramp_raw=?, quick_hash=?, analyzer_version=?, flags=?, "
            "segments=?, duration_s=?, analyzed_at=?, error=NULL, skip_reason=NULL, options=NULL, ran=?, rank=NULL, "
            "recent=0 WHERE path=?",
            (
                timeline_json,
                ramp_raw_json,
                quick_hash,
                version,
                ",".join(flags),
                segments,
                duration_s,
                now,
                ran,
                path,
            ),
        )

    def fail(self, path: str, error: str) -> None:
        self._run("UPDATE files SET status='error', error=?, rank=NULL WHERE path=?", (error[:2000], path))

    def skip(self, path: str, reason: str) -> None:
        self._run(
            "UPDATE files SET status='skipped', skip_reason=?, error=NULL, options=NULL, rank=NULL WHERE path=?",
            (reason, path),
        )

    def requeue(
        self,
        path: str,
        priority: int = PRIORITY_HIGH,
        now: float | None = None,
        options: str | None = None,
    ) -> bool:
        """Queue a known file again (on demand, or after an error). False if it is unknown or already running; a
        file that is already queued only has its priority raised (and its `options` replaced if new ones are given).
        `options` is JSON saying how this one run should go (see `Scheduler.run_one`); the run clears it."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._one("SELECT status, priority FROM files WHERE path=?", (path,))
            if row is None or row["status"] == "running":
                return False
            if row["status"] == "queued":
                if priority < row["priority"]:  # raised: it leaves any place it was given in the old tier
                    self._run(
                        "UPDATE files SET priority=?, rank=NULL, queued_at=? WHERE path=?",
                        (priority, now, path),
                    )
                if options is not None:
                    self._run("UPDATE files SET options=? WHERE path=?", (options, path))
                return True
            self._run(
                "UPDATE files SET status='queued', priority=?, queued_at=?, error=NULL, attempts=0, skip_reason=NULL, "
                "options=?, rank=NULL, recent=0 WHERE path=?",
                (priority, now, options, path),
            )
            return True

    def needing_dense(self) -> list[dict]:
        """Finished titles whose result did not come from the dense pass (its flags never say `dense`): the
        scheduler picks from them the ones that should run it."""
        return self._all(
            "SELECT path, segments, flags FROM files WHERE status='done' AND timeline IS NOT NULL "
            "AND COALESCE(flags, '') NOT LIKE '%dense%'"
        )

    FORGETTABLE = ("done", "error", "queued", "idle")

    def forget(self, path: str) -> str:
        """Drop what is known about a file's picture (timeline, flags, hash, errors) and leave it `idle`, as a title
        that has never been analysed: it waits until a player asks for it or someone presses Analyze. Returns the
        new status, or `unknown`, `running` (not touched) or `not-allowed` (a skipped or missing file). A manual
        override is kept: it is the user's choice, not a result."""
        with self._lock:
            row = self._one("SELECT status FROM files WHERE path=?", (path,))
            if row is None:
                return "unknown"
            if row["status"] == "running":
                return "running"
            if row["status"] not in self.FORGETTABLE:
                return "not-allowed"
            self._run(
                "UPDATE files SET status='idle', timeline=NULL, ramp_raw=NULL, quick_hash=NULL, flags=NULL, "
                "segments=NULL, analyzer_version=NULL, analyzed_at=NULL, duration_s=NULL, started_at=NULL, "
                "error=NULL, attempts=0, skip_reason=NULL, options=NULL, ran=NULL, rank=NULL, recent=0 WHERE path=?",
                (path,),
            )
            return "idle"

    def requeue_one_running(self, path: str) -> bool:
        """A running job that was stopped (not failed) goes back in the queue with its priority kept."""
        return bool(
            self._run(
                "UPDATE files SET status='queued', started_at=NULL WHERE path=? AND status='running'", (path,)
            )
        )

    def requeue_running(self) -> int:
        """At start-up: a job that was running when the container stopped goes back in the queue."""
        return self._run("UPDATE files SET status='queued', started_at=NULL WHERE status='running'")

    # ---- Plex ids ----------------------------------------------------------------------------------------------

    def set_plex(
        self,
        path: str,
        *,
        part_id: int | None,
        rating_key: int | None,
        title: str | None,
        added_at: float | None,
    ) -> bool:
        return (
            self._run(
                "UPDATE files SET part_id=?, rating_key=?, title=?, added_at=? WHERE path=?",
                (part_id, rating_key, title, added_at, path),
            )
            > 0
        )

    def all_paths(self) -> list[str]:
        return [r["path"] for r in self._all("SELECT path FROM files")]

    def boost_recent(self, added_since: float) -> int:
        """Low-tier queued files Plex added since `added_since` are flagged recent: they go ahead of the rest of the tier."""
        return self._run(
            "UPDATE files SET recent=1 WHERE status='queued' AND priority >= ? AND recent=0 AND added_at >= ?",
            (PRIORITY_LOW, added_since),
        )

    # ---- reads -------------------------------------------------------------------------------------------------

    def get(self, path: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM files WHERE path=?", (path,))

    def find(
        self,
        *,
        path: str | None = None,
        part_id: int | None = None,
        rating_key: int | None = None,
        quick_hash: str | None = None,
    ) -> dict[str, Any] | None:
        for column, value in (
            ("path", path),
            ("part_id", part_id),
            ("rating_key", rating_key),
            ("quick_hash", quick_hash),
        ):
            if value is not None:
                row = self._one(
                    f"SELECT * FROM files WHERE {column}=? ORDER BY (timeline IS NULL), analyzed_at DESC",
                    (value,),
                )
                if row:
                    return row
        return None

    def counts(self) -> dict[str, int]:
        rows = self._all("SELECT status, COUNT(*) AS n FROM files GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def queue(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self._all(
            f"SELECT {_LIST_COLUMNS} FROM files WHERE status IN ('running', 'queued') "  # noqa: S608
            f"ORDER BY (status='running') DESC, {QUEUE_ORDER} LIMIT ? OFFSET ?",
            (limit, offset),
        )

    def waiting(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """The tiered queue (P1-P4) in the order it will be taken, `limit` rows from `offset`. Express titles (a player
        is waiting) are not part of it."""
        return self._all(
            f"SELECT {_LIST_COLUMNS} FROM files WHERE status='queued' AND priority >= 1 "  # noqa: S608
            f"ORDER BY {QUEUE_ORDER} LIMIT ? OFFSET ?",
            (limit, offset),
        )

    def express_waiting(self) -> list[dict[str, Any]]:
        """Titles a player is waiting for that no express worker has taken yet, longest wait first."""
        return self._all(
            f"SELECT {_LIST_COLUMNS} FROM files WHERE status='queued' AND priority <= 0 ORDER BY queued_at ASC"  # noqa: S608
        )

    def tier_counts(self) -> dict[int, int]:
        rows = self._all(
            "SELECT priority, COUNT(*) AS n FROM files WHERE status='queued' AND priority >= 1 GROUP BY priority"
        )
        return {tier: next((r["n"] for r in rows if r["priority"] == tier), 0) for tier in TIERS}

    def spots(self, paths: Sequence[str] | None = None) -> dict[str, dict[str, int]]:
        """Where queued titles stand: `{path: {"tier": 1-4, "tier_spot": n, "spot": n, "total": n}}`, `spot` counting
        from 1 over the whole tiered queue and `tier_spot` within the title's tier. All of them, or only `paths`."""
        with self._lock:
            ordered = self._all(
                f"SELECT path, priority FROM files WHERE status='queued' AND priority >= 1 ORDER BY {QUEUE_ORDER}"  # noqa: S608
            )
        total = len(ordered)
        wanted = None if paths is None else set(paths)
        in_tier: dict[int, int] = {}
        out: dict[str, dict[int, int]] = {}
        for spot, row in enumerate(ordered, start=1):
            tier = row["priority"]
            in_tier[tier] = in_tier.get(tier, 0) + 1
            if wanted is None or row["path"] in wanted:
                out[row["path"]] = {"tier": tier, "tier_spot": in_tier[tier], "spot": spot, "total": total}
        return out

    def place(
        self,
        paths: Sequence[str],
        tier: int,
        *,
        after: str | None = None,
        end: bool = False,
    ) -> int:
        """Move queued titles to `tier`, keeping their given order, to just below `after` (a title in that tier), to the
        top of the tier (no `after`), or to its end (`end`). Returns how many moved; a title that is not waiting in the
        queue (running, done) is left alone.

        A title's place is its `rank`. Placing ranks the titles from the top of the tier down to the last one placed
        by hand, in their current order, so that what the user ordered stays as it is; the rest of the tier, never
        touched, keeps following the tier's own rule behind them (and so does anything queued later)."""
        if tier not in TIERS:
            raise ValueError(f"the tier must be one of {TIERS}")
        with self._lock:
            moving: list[str] = []
            for path in paths:
                row = self._one("SELECT status, priority FROM files WHERE path=?", (path,))
                if row and row["status"] == "queued" and row["priority"] >= 1 and path not in moving:
                    moving.append(path)
            if not moving:
                return 0
            ordered = self._all(
                f"SELECT path, rank FROM files WHERE status='queued' AND priority=? ORDER BY {QUEUE_ORDER}",  # noqa: S608
                (tier,),
            )
            rest = [r for r in ordered if r["path"] not in moving]
            if end:
                index = len(rest)
            elif after is None:
                index = 0
            else:
                names = [r["path"] for r in rest]
                if after not in names:
                    raise ValueError("the title to place it after is not in that tier")
                index = names.index(after) + 1
            placed = sum(1 for r in rest if r["rank"] is not None)
            new = [r["path"] for r in rest[:index]] + moving + [r["path"] for r in rest[index:]]
            ranked = max(index + len(moving), placed + len(moving))
            for number, path in enumerate(new[:ranked], start=1):
                self._run("UPDATE files SET rank=? WHERE path=?", (float(number), path))
            self._run(
                f"UPDATE files SET priority=? WHERE path IN ({','.join('?' * len(moving))})",  # noqa: S608
                (tier, *moving),
            )
            return len(moving)

    def errors(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._all(
            f"SELECT {_LIST_COLUMNS} FROM files WHERE status='error' ORDER BY analyzed_at DESC LIMIT ?",
            (limit,),
        )

    def search(self, text: str, limit: int = 50) -> list[dict[str, Any]]:
        like = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return self._all(
            f"SELECT {_LIST_COLUMNS} FROM files WHERE path LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
            "ORDER BY analyzed_at DESC, path LIMIT ?",
            (like, like, limit),
        )

    def var_titles(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._all(
            f"SELECT {_LIST_COLUMNS} FROM files WHERE status='done' AND segments > 1 ORDER BY path LIMIT ?",
            (limit,),
        )

    # ---- overrides (R11) and meta --------------------------------------------------------------------------------

    def get_override(self, path: str) -> dict[str, Any] | None:
        return self._one("SELECT path, mode, preset, updated FROM overrides WHERE path=?", (path,))

    def set_override(
        self, path: str, mode: str, preset: float | None = None, now: float | None = None
    ) -> None:
        if mode not in ("preset", "disable"):
            raise ValueError("override mode must be 'preset' or 'disable'")
        if mode == "preset" and preset is None:
            raise ValueError("a preset override needs a ratio")
        self._run(
            "INSERT INTO overrides(path, mode, preset, updated) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET mode=excluded.mode, preset=excluded.preset, updated=excluded.updated",
            (path, mode, preset if mode == "preset" else None, time.time() if now is None else now),
        )

    def clear_override(self, path: str) -> bool:
        return self._run("DELETE FROM overrides WHERE path=?", (path,)) > 0

    def get_meta(self, key: str) -> str | None:
        row = self._one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._run(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def _statements(script: str) -> list[str]:
    """Split a migration script into statements (none of ours contains a semicolon inside a string)."""
    return [s.strip() for s in script.split(";") if s.strip()]
