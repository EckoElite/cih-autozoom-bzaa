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

PRIORITY_DEMAND = 0  # a player asked for it
PRIORITY_RECENT = 1  # recently added to Plex
PRIORITY_BACKLOG = 2

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
)

_LIST_COLUMNS = (
    "path, size, mtime, status, priority, skip_reason, error, attempts, analyzer_version, analyzed_at, queued_at, "
    "started_at, duration_s, quick_hash, part_id, rating_key, title, added_at, flags, segments"
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
        priority: int = PRIORITY_BACKLOG,
        now: float | None = None,
    ) -> str:
        """Record a file the scanner saw. Returns `new`, `changed` (its timeline is dropped and it is queued again),
        `reanalyze` (the analyzer's major version moved on), `returned` (it had been missing) or `unchanged`."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._one(
                "SELECT status, size, mtime, analyzer_version, timeline FROM files WHERE path = ?", (path,)
            )
            if row is None:
                self._run(
                    "INSERT INTO files(path, size, mtime, status, priority, queued_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                    (path, size, mtime, priority, now),
                )
                return "new"
            if row["size"] != size or abs(row["mtime"] - mtime) > 1e-3:
                self._run(
                    "UPDATE files SET size=?, mtime=?, status='queued', priority=?, queued_at=?, error=NULL, "
                    "attempts=0, timeline=NULL, ramp_raw=NULL, quick_hash=NULL, flags=NULL, segments=NULL, "
                    "skip_reason=NULL WHERE path=? AND status != 'running'",
                    (size, mtime, priority, now, path),
                )
                return "changed"
            if row["status"] == "missing":
                status = "done" if row["timeline"] else "queued"
                self._run("UPDATE files SET status=?, queued_at=? WHERE path=?", (status, now, path))
                return "returned"
            if row["status"] == "done" and major_of(row["analyzer_version"]) != major_of(version):
                self._run(
                    "UPDATE files SET status='queued', priority=?, queued_at=?, attempts=0 WHERE path=?",
                    (priority, now, path),
                )
                return "reanalyze"
            return "unchanged"

    def restart(self, path: str, size: int, mtime: float, priority: int, now: float | None = None) -> None:
        """A file changed under a running job: drop what we know and queue it again, running or not."""
        self._run(
            "UPDATE files SET size=?, mtime=?, status='queued', priority=?, queued_at=?, error=NULL, attempts=0, "
            "timeline=NULL, ramp_raw=NULL, quick_hash=NULL, flags=NULL, segments=NULL, skip_reason=NULL, "
            "started_at=NULL WHERE path=?",
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

    def claim_next(self, now: float | None = None) -> dict[str, Any] | None:
        """Take the most urgent queued file and mark it running: on-demand first, then recently added, then the
        backlog newest first."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._one(
                "SELECT path FROM files WHERE status='queued' ORDER BY priority ASC, "
                "CASE WHEN priority = 0 THEN queued_at ELSE -mtime END ASC LIMIT 1"
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
    ) -> None:
        now = time.time() if now is None else now
        self._run(
            "UPDATE files SET status='done', timeline=?, ramp_raw=?, quick_hash=?, analyzer_version=?, flags=?, "
            "segments=?, duration_s=?, analyzed_at=?, error=NULL, skip_reason=NULL WHERE path=?",
            (
                timeline_json,
                ramp_raw_json,
                quick_hash,
                version,
                ",".join(flags),
                segments,
                duration_s,
                now,
                path,
            ),
        )

    def fail(self, path: str, error: str) -> None:
        self._run("UPDATE files SET status='error', error=? WHERE path=?", (error[:2000], path))

    def skip(self, path: str, reason: str) -> None:
        self._run("UPDATE files SET status='skipped', skip_reason=?, error=NULL WHERE path=?", (reason, path))

    def requeue(self, path: str, priority: int = PRIORITY_DEMAND, now: float | None = None) -> bool:
        """Queue a known file again (on demand, or after an error). False if it is unknown or already running; a
        file that is already queued only has its priority raised."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._one("SELECT status, priority FROM files WHERE path=?", (path,))
            if row is None or row["status"] == "running":
                return False
            if row["status"] == "queued":
                self._run("UPDATE files SET priority=MIN(priority, ?) WHERE path=?", (priority, path))
                return True
            self._run(
                "UPDATE files SET status='queued', priority=?, queued_at=?, error=NULL, attempts=0, skip_reason=NULL "
                "WHERE path=?",
                (priority, now, path),
            )
            return True

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
        """Queued files Plex added since `added_since` jump ahead of the backlog."""
        return self._run(
            "UPDATE files SET priority=? WHERE status='queued' AND priority > ? AND added_at >= ?",
            (PRIORITY_RECENT, PRIORITY_RECENT, added_since),
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

    def queue(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._all(
            f"SELECT {_LIST_COLUMNS} FROM files WHERE status IN ('running', 'queued') ORDER BY (status='running') DESC, "
            "priority ASC, CASE WHEN priority = 0 THEN queued_at ELSE -mtime END ASC LIMIT ?",
            (limit,),
        )

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
