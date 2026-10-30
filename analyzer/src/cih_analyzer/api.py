"""The HTTP API and status page (spec Phase 4 "API"). LAN only; if `API_KEY` is set every `/api/v1` route needs it
(`X-Api-Key` header, or `key=` in the query for simple clients). `/health` and the page itself are open; the page
asks for the key and sends it as a header.

A timeline answer is the stored timeline plus an `override` member (null, or the manual choice for this title). A
file that changed since it was analysed gets no timeline (404 with `status: stale`), because a timeline for the old
contents would zoom the wrong picture; the player then falls back to contain-by-display-ratio.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .pathmap import PathMap, normalize
from .scheduler import MAX_WORKERS, Scheduler
from .store import PRIORITY_DEMAND, PRIORITY_PLAYER, Store

PAGE = Path(__file__).with_name("web") / "index.html"
LIST_LIMIT = 200


class Target(BaseModel):
    """Any one way to name a title."""

    path: str | None = None
    part_id: int | None = None
    rating_key: int | None = None
    hash: str | None = Field(default=None, min_length=40, max_length=40)


class AnalyzeBody(Target):
    """`source: "player"` says a player is waiting for the title (it goes to an express worker); anything else is a
    request made by hand and waits its turn like the rest of the queue."""

    source: str | None = None


class OverrideBody(Target):
    mode: str = Field(pattern="^(preset|disable|clear)$")
    preset: float | None = Field(default=None, gt=0.5, lt=5)


class WorkersBody(BaseModel):
    workers: int = Field(ge=1, le=MAX_WORKERS)


def _brief(row: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "path", "title", "status", "priority", "skip_reason", "error", "attempts", "analyzer_version", "analyzed_at",
        "queued_at", "started_at", "duration_s", "part_id", "rating_key", "flags", "segments", "size",
    )  # fmt: skip
    return {k: row.get(k) for k in keep}


def create_app(settings: Settings, store: Store, scheduler: Scheduler, pathmap: PathMap) -> FastAPI:
    started = time.time()
    app = FastAPI(title="CIH Auto-Zoom analyzer", version=__version__, docs_url=None, redoc_url=None)

    def authorize(request: Request) -> None:
        if not settings.api_key:
            return
        given = request.headers.get("X-Api-Key") or request.query_params.get("key") or ""
        if not hmac.compare_digest(given.encode(), settings.api_key.encode()):
            raise HTTPException(401, "a valid API key is required")

    secured = [Depends(authorize)]

    def under_media_root(local: str) -> bool:
        roots = [normalize(r) for r in settings.media_roots]
        return ".." not in local.split("/") and any(
            local == r or local.startswith(r.rstrip("/") + "/") for r in roots
        )

    def resolve(target: Target) -> tuple[dict[str, Any] | None, str | None]:
        """The store row and the container path a request names (either may be missing)."""
        if not any((target.path, target.part_id, target.rating_key, target.hash)):
            raise HTTPException(422, "give one of path, part_id, rating_key or hash")
        local = pathmap.to_local(target.path) if target.path else None
        row = store.find(
            path=local, part_id=target.part_id, rating_key=target.rating_key, quick_hash=target.hash
        )
        return row, (row["path"] if row else local)

    def stale(row: dict[str, Any]) -> bool:
        try:
            stat = os.stat(row["path"])
        except OSError:
            return False  # not mounted here: serve what we have
        return stat.st_size != row["size"] or abs(stat.st_mtime - row["mtime"]) > 1e-3

    def queue_for(row: dict[str, Any] | None, local: str | None, player: bool = False) -> str:
        priority = PRIORITY_PLAYER if player else PRIORITY_DEMAND
        if local is None:
            return "unknown"
        if row is None:
            if not (under_media_root(local) and os.path.isfile(local)):
                return "unknown"
            stat = os.stat(local)
            store.seen(local, stat.st_size, stat.st_mtime, version=__version__, priority=priority)
        return scheduler.request(local, priority)

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            counts = store.counts()
        except Exception:  # noqa: BLE001 - report, do not crash
            return JSONResponse({"ok": False, "version": __version__}, status_code=503)
        return JSONResponse(
            {
                "ok": True,
                "version": __version__,
                "schema": store.schema_version,
                "queued": counts.get("queued", 0),
            }
        )

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/v1/timeline", dependencies=secured)
    def timeline(
        path: str | None = None,
        part_id: int | None = None,
        rating_key: int | None = None,
        hash: str | None = Query(default=None, min_length=40, max_length=40),  # noqa: A002 - the documented name
        analyze: int = 0,
    ) -> JSONResponse:
        row, local = resolve(Target(path=path, part_id=part_id, rating_key=rating_key, hash=hash))
        status = row["status"] if row else "unknown"
        if row and row["timeline"] and not stale(row):
            body = json.loads(row["timeline"])
            override = store.get_override(row["path"])
            body["override"] = {"mode": override["mode"], "preset": override["preset"]} if override else None
            return JSONResponse(body, headers={"X-Cih-Status": status})
        if row and row["timeline"]:
            status = "stale"
        queued = queue_for(row, local, player=True) if analyze else None  # `analyze=1` is what a player sends
        return JSONResponse(
            {"detail": "no timeline", "status": status, "queue": queued},
            status_code=404,
            headers={"X-Cih-Status": status},
        )

    @app.post("/api/v1/analyze", dependencies=secured)
    def analyze(target: AnalyzeBody) -> dict[str, Any]:
        row, local = resolve(target)
        return {"path": local, "status": queue_for(row, local, player=target.source == "player")}

    @app.get("/api/v1/override", dependencies=secured)
    def get_override(
        path: str | None = None, part_id: int | None = None, rating_key: int | None = None
    ) -> dict[str, Any]:
        row, local = resolve(Target(path=path, part_id=part_id, rating_key=rating_key))
        found = store.get_override(local) if local else None
        return {
            "path": local,
            "override": {"mode": found["mode"], "preset": found["preset"]} if found else None,
        }

    @app.post("/api/v1/override", dependencies=secured)
    def set_override(body: OverrideBody) -> dict[str, Any]:
        _, local = resolve(body)
        if local is None:
            raise HTTPException(404, "no such title")
        if body.mode == "clear":
            return {"path": local, "cleared": store.clear_override(local)}
        if body.mode == "preset" and body.preset is None:
            raise HTTPException(422, "a preset override needs a ratio")
        store.set_override(local, body.mode, body.preset)
        return {
            "path": local,
            "override": {"mode": body.mode, "preset": body.preset if body.mode == "preset" else None},
        }

    @app.get("/api/v1/status", dependencies=secured)
    def status() -> dict[str, Any]:
        return {
            "version": __version__,
            "schema": store.schema_version,
            "uptime_s": round(time.time() - started),
            "counts": store.counts(),
            "scheduler": scheduler.status(),
            "plex": {"enabled": settings.plex_enabled, "skipped_sections": list(settings.plex_skip_sections)},
            "media_roots": list(settings.media_roots),
            "settings": {
                "concurrency": scheduler.workers,
                "concurrency_env": settings.concurrency,
                "concurrency_saved": store.get_meta("workers") or None,
                "start_paused": settings.start_paused,
                "max_workers": MAX_WORKERS,
                "quiet_hours": [t.strftime("%H:%M") for t in settings.quiet_hours]
                if settings.quiet_hours
                else None,
                "pause_on_transcode": settings.pause_on_transcode,
                "scan_interval_s": settings.scan_interval_s,
                "sample_interval_s": settings.sample_interval_s,
                "survey_interval_s": settings.survey_interval_s,
                "sample_intervals": [list(i) for i in settings.sample_intervals],
            },
        }

    @app.get("/api/v1/queue", dependencies=secured)
    def queue(limit: int = Query(100, ge=1, le=LIST_LIMIT)) -> list[dict[str, Any]]:
        rows = [_brief(r) for r in store.queue(limit)]
        for row in rows:
            if row["status"] == "running":
                row["progress"] = scheduler.progress_of(row["path"])
        return rows

    @app.get("/api/v1/errors", dependencies=secured)
    def errors(limit: int = Query(100, ge=1, le=LIST_LIMIT)) -> list[dict[str, Any]]:
        return [_brief(r) for r in store.errors(limit)]

    @app.get("/api/v1/files", dependencies=secured)
    def files(
        q: str = Query("", max_length=200), var: int = 0, limit: int = Query(50, ge=1, le=LIST_LIMIT)
    ) -> list[dict[str, Any]]:
        rows = store.var_titles(limit) if var else store.search(q, limit)
        return [_brief(r) for r in rows]

    @app.get("/api/v1/ramp", dependencies=secured)
    def ramp(
        path: str | None = None, part_id: int | None = None, rating_key: int | None = None
    ) -> dict[str, Any]:
        """Per ramp, per frame: the stored (fitted) edges and what was measured, for the status page plot (R14)."""
        row, local = resolve(Target(path=path, part_id=part_id, rating_key=rating_key))
        if not row or not row["timeline"]:
            raise HTTPException(404, "no timeline")
        fitted = json.loads(row["timeline"]).get("ramps", [])
        raw = {
            r["start_frame"]: {f["f"]: f for f in r["frames"]} for r in json.loads(row["ramp_raw"] or "[]")
        }
        out = []
        for r in fitted:
            measured = raw.get(r["start_frame"], {})
            out.append(
                {
                    "start_frame": r["start_frame"],
                    "end_frame": r["end_frame"],
                    "confidence": r["confidence"],
                    "frames": [{**f, "raw": (measured.get(f["f"]) or {}).get("raw")} for f in r["frames"]],
                }
            )
        return {"path": row["path"], "ramps": out}

    def background(task: Any) -> dict[str, str]:
        threading.Thread(target=task, daemon=True, name="api-task").start()
        return {"status": "started"}

    @app.post("/api/v1/scan", dependencies=secured)
    def scan() -> dict[str, str]:
        return background(scheduler.scan_now)

    @app.post("/api/v1/plex-sync", dependencies=secured)
    def plex_sync() -> dict[str, str]:
        if not settings.plex_enabled:
            raise HTTPException(409, "Plex is not configured")
        return background(scheduler.plex_now)

    @app.post("/api/v1/concurrency", dependencies=secured)
    def concurrency(body: WorkersBody) -> dict[str, Any]:
        """How many files are analysed at once. Raising it starts more now; lowering it stops nothing that is running,
        the surplus workers just take no new file. Saved across restarts, and wins over the CONCURRENCY setting."""
        return {"workers": scheduler.set_workers(body.workers), "max_workers": MAX_WORKERS}

    @app.delete("/api/v1/concurrency", dependencies=secured)
    def reset_concurrency() -> dict[str, Any]:
        """Forget the saved count: the CONCURRENCY setting applies again, now and at every start."""
        return {"workers": scheduler.reset_workers(), "max_workers": MAX_WORKERS}

    @app.post("/api/v1/pause", dependencies=secured)
    def pause() -> dict[str, Any]:
        """Start no new file. The files being analysed carry on and finish; `running` says how many."""
        scheduler.pause()
        return {"paused": scheduler.paused_reason(), "running": store.counts().get("running", 0)}

    @app.post("/api/v1/stop-now", dependencies=secured)
    def stop_now() -> dict[str, Any]:
        """Pause and kill every file being analysed. Each goes back in the queue and starts again from the beginning
        after Resume; `stopped` says how many were killed."""
        stopped = scheduler.stop_now()
        return {"paused": scheduler.paused_reason(), "stopped": stopped}

    @app.post("/api/v1/resume", dependencies=secured)
    def resume() -> dict[str, Any]:
        scheduler.resume()
        return {"paused": scheduler.paused_reason()}

    return app
