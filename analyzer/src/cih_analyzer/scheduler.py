"""Running the queue without getting in Plex's way (spec R7).

* A worker takes the most urgent queued file, runs the analysis in a **separate process** at the lowest CPU and disk
  priority (`nice 19`, `ionice` idle class) and stores the result. A separate process means a crash or a hang in
  ffmpeg cannot take the service down, and a timeout can kill it cleanly.
* Analysis pauses during quiet hours, while Plex is transcoding, or when paused by hand; the queue simply waits.
* A job running when the container stops goes back in the queue at the next start; a file that changed while it was
  being analysed is queued again instead of being given a timeline for the old contents.
* Failures are recorded on the file (status `error`, a message) and never stop the workers.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import __version__
from .config import Settings, in_quiet_hours
from .pathmap import PathMap
from .plex import PlexClient, PlexError, redact
from .scanner import PlexSummary, ScanSummary, scan_media, sync_plex
from .store import PRIORITY_DEMAND, Store

log = logging.getLogger(__name__)

IDLE_POLL_S = 5.0
PAUSED_POLL_S = 15.0
TRANSCODE_CACHE_S = 30.0


@dataclass
class RunResult:
    timeline: dict | None = None
    ramp_raw: list = field(default_factory=list)
    skip: str | None = None
    version: str = __version__
    error: str | None = None


class Runner(Protocol):
    def __call__(self, path: str, timeout: float, stop: threading.Event) -> RunResult: ...


@functools.cache
def _low_priority_prefix() -> tuple[str, ...]:
    """`ionice -c3 nice -n 19` where those exist and work (Linux): the analysis only uses what Plex is not using.
    Each tool is tried once on `true`: a container runtime that forbids changing I/O priority must cost us the
    priority, not every analysis."""
    prefix: list[str] = []
    if os.name == "posix":
        for tool, args in (("ionice", ["-c", "3"]), ("nice", ["-n", "19"])):
            exe = shutil.which(tool)
            if exe is None:
                continue
            try:
                ok = (
                    subprocess.run(  # noqa: S603 - fixed command
                        [exe, *args, "true"], capture_output=True, timeout=10, check=False
                    ).returncode
                    == 0
                )
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            if ok:
                prefix += [exe, *args]
            else:
                log.warning("%s does not work here; the analysis will run without it", tool)
    return tuple(prefix)


def subprocess_runner(
    path: str, timeout: float, stop: threading.Event, sample_interval: float | None = None
) -> RunResult:
    """Analyse one file in a child process and parse the envelope it prints."""
    src = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [*_low_priority_prefix(), sys.executable, "-m", "cih_analyzer"]
    log.debug("worker command prefix: %s", _low_priority_prefix() or "(normal priority)")
    if sample_interval is not None:
        cmd += ["--sample-interval", f"{sample_interval:g}"]
    cmd += ["--analyze-envelope", path]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=out, stderr=err, env=env)  # noqa: S603 - fixed command, no shell
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if stop.is_set():
                proc.kill()
                proc.wait()
                return RunResult(error="stopped")
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                return RunResult(error=f"analysis timed out after {timeout:.0f} s")
            time.sleep(0.2)
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", errors="replace").strip()
        stderr = err.read().decode("utf-8", errors="replace").strip()
    if proc.returncode not in (0, 1):
        tail = stderr.splitlines()[-1] if stderr else f"exit {proc.returncode}"
        return RunResult(error=redact(tail))
    try:
        data = json.loads(stdout.splitlines()[-1])
    except (ValueError, IndexError):
        return RunResult(error="the analysis printed nothing usable")
    if "skip" in data:
        return RunResult(skip=str(data["skip"]))
    return RunResult(
        timeline=data["timeline"], ramp_raw=data.get("ramp_raw", []), version=data.get("version", __version__)
    )


class Scheduler:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        pathmap: PathMap,
        *,
        runner: Runner | None = None,
        plex: PlexClient | None = None,
        now_local: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.store = store
        self.pathmap = pathmap
        self.runner: Runner = runner or (
            lambda path, timeout, stop: subprocess_runner(
                path, timeout, stop, sample_interval=settings.interval_for(path)
            )
        )
        self.plex = (
            plex
            if plex is not None
            else (PlexClient(settings.plex_url, settings.plex_token) if settings.plex_enabled else None)
        )
        self.now_local = now_local
        self.clock = clock
        self.stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._manual_pause = False
        self._transcode: tuple[float, bool] = (0.0, False)
        self.last_scan: dict | None = None
        self.last_plex: dict | None = None
        self._scan_lock = threading.Lock()

    # ---- throttling ----------------------------------------------------------------------------------------------

    def pause(self) -> None:
        self._manual_pause = True

    def resume(self) -> None:
        self._manual_pause = False

    def transcode_active(self) -> bool:
        """Plex is transcoding video (cached for 30 s; an unreachable Plex never blocks analysis)."""
        if not (self.settings.pause_on_transcode and self.plex):
            return False
        checked, value = self._transcode
        if self.clock() - checked < TRANSCODE_CACHE_S:
            return value
        try:
            value = self.plex.transcoding()
        except PlexError as exc:
            log.warning("cannot ask Plex about transcodes: %s", exc)
            value = False
        self._transcode = (self.clock(), value)
        return value

    def paused_reason(self) -> str | None:
        if self._manual_pause:
            return "paused"
        if in_quiet_hours(self.settings.quiet_hours, self.now_local().time()):
            return "quiet-hours"
        if self.transcode_active():
            return "plex-transcode"
        return None

    # ---- the work ------------------------------------------------------------------------------------------------

    def run_one(self) -> bool:
        """Take and run the most urgent job. Returns False if there was nothing to do."""
        job = self.store.claim_next(self.clock())
        if job is None:
            return False
        path = job["path"]
        started = time.monotonic()
        try:
            before = os.stat(path)
        except OSError as exc:
            self.store.fail(path, f"cannot read the file: {exc.strerror}")
            return True
        try:
            result = self.runner(path, self.settings.analysis_timeout_s, self.stop_event)
        except Exception as exc:  # noqa: BLE001 - a bad file must never stop the worker
            result = RunResult(error=redact(f"{type(exc).__name__}: {exc}"))
        if result.error == "stopped":
            self.store.requeue_running()
            return True
        try:
            after = os.stat(path)
        except OSError:
            self.store.fail(path, "the file disappeared while it was being analysed")
            return True
        if (after.st_size, after.st_mtime) != (before.st_size, before.st_mtime):
            self.store.restart(path, after.st_size, after.st_mtime, PRIORITY_DEMAND, self.clock())
            log.info("%s changed while being analysed; queued again", path)
            return True
        if result.error:
            self.store.fail(path, result.error)
        elif result.skip:
            self.store.skip(path, result.skip)
        elif result.timeline is not None:
            timeline = result.timeline
            self.store.finish(
                path,
                timeline_json=json.dumps(timeline, ensure_ascii=False, separators=(",", ":")),
                ramp_raw_json=json.dumps(result.ramp_raw, separators=(",", ":")) if result.ramp_raw else None,
                quick_hash=timeline["file"]["quick_hash"],
                version=result.version,
                flags=timeline.get("flags", []),
                segments=len(timeline.get("segments", [])),
                duration_s=time.monotonic() - started,
                now=self.clock(),
            )
        else:
            self.store.fail(path, "the analysis returned nothing")
        return True

    def _worker(self) -> None:
        while not self.stop_event.is_set():
            if self.paused_reason():
                self.stop_event.wait(PAUSED_POLL_S)
                continue
            try:
                worked = self.run_one()
            except Exception:  # noqa: BLE001
                log.exception("worker error")
                worked = False
            if not worked:
                self.stop_event.wait(IDLE_POLL_S)

    # ---- discovery -----------------------------------------------------------------------------------------------

    def scan_now(self) -> ScanSummary:
        with self._scan_lock:
            summary = scan_media(
                list(self.settings.media_roots),
                self.store,
                __version__,
                should_stop=self.stop_event.is_set,
                now=self.clock(),
            )
        self.last_scan = {"at": self.clock(), **summary.__dict__}
        log.info(
            "scan: %d seen, %d new, %d changed, %d missing, %d errors",
            summary.seen, summary.new, summary.changed, summary.missing, len(summary.errors),
        )  # fmt: skip
        return summary

    def plex_now(self) -> PlexSummary | None:
        if not self.plex:
            return None
        try:
            summary = sync_plex(
                self.plex, self.store, self.pathmap,
                skip_sections=self.settings.plex_skip_sections, recent_days=self.settings.recent_days, now=self.clock(),
            )  # fmt: skip
        except PlexError as exc:
            self.last_plex = {"at": self.clock(), "error": str(exc)}
            log.warning("Plex sync failed: %s", exc)
            return None
        self.last_plex = {"at": self.clock(), **summary.__dict__}
        log.info(
            "plex: %d files, %d matched, %d not scanned", summary.parts, summary.matched, summary.unknown
        )
        return summary

    def _loop(self, interval: float, task: Callable[[], object], name: str) -> None:
        while not self.stop_event.is_set():
            try:
                task()
            except Exception:  # noqa: BLE001
                log.exception("%s failed", name)
            self.stop_event.wait(interval)

    def request(self, path: str, priority: int = PRIORITY_DEMAND) -> str:
        """Ask for a file to be analysed now: `queued`, `running`, or `unknown` (not a file the scanner has seen)."""
        local = self.pathmap.to_local(path)
        row = self.store.get(local)
        if row is None:
            return "unknown"
        if row["status"] == "running":
            return "running"
        return "queued" if self.store.requeue(local, priority, self.clock()) else "unknown"

    # ---- lifecycle -----------------------------------------------------------------------------------------------

    def start(self) -> None:
        requeued = self.store.requeue_running()
        if requeued:
            log.info("%d job(s) that were running at the last stop are queued again", requeued)
        self.stop_event.clear()
        threads = [
            threading.Thread(
                target=self._loop, args=(self.settings.scan_interval_s, self.scan_now, "scan"), name="scan"
            ),
            *(
                [
                    threading.Thread(
                        target=self._loop,
                        args=(self.settings.plex_interval_s, self.plex_now, "plex"),
                        name="plex",
                    )
                ]
                if self.plex
                else []
            ),
            *(
                threading.Thread(target=self._worker, name=f"worker-{i}")
                for i in range(self.settings.concurrency)
            ),
        ]
        for t in threads:
            t.daemon = True
            t.start()
        self._threads = threads

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        for t in self._threads:
            t.join(timeout)
        self._threads = []

    def status(self) -> dict:
        return {
            "paused": self.paused_reason(),
            "workers": self.settings.concurrency,
            "running": [r["path"] for r in self.store.queue(50) if r["status"] == "running"],
            "last_scan": self.last_scan,
            "last_plex": self.last_plex,
        }
