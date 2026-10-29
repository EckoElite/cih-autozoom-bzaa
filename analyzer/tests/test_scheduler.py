"""The scheduler: running jobs, throttling, failures, restarts, and the real worker process on a synthetic film."""

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from cih_analyzer.config import Settings
from cih_analyzer.pathmap import PathMap
from cih_analyzer.plex import PlexError
from cih_analyzer.scheduler import RunResult, Scheduler, subprocess_runner
from cih_analyzer.store import PRIORITY_DEMAND, Store

from .conftest import NEEDS_FFMPEG
from .synth import Seg, make_video, rect_for_ratio

HASH = "a" * 40


def timeline(path="/x", flags=("var",)):
    return {"file": {"quick_hash": HASH, "path": path}, "flags": list(flags), "segments": [{}, {}]}


class FakePlexClient:
    def __init__(self):
        self.busy = False
        self.fail = False
        self.calls = 0

    def transcoding(self):
        self.calls += 1
        if self.fail:
            raise PlexError("down")
        return self.busy


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    for name in ("a.mkv", "b.mkv"):
        (root / name).write_bytes(b"x" * 10)
    store = Store(tmp_path / "s.sqlite")
    base = str(root).replace("\\", "/")
    settings = Settings(config_dir=tmp_path, media_roots=(base,), scan_interval_s=10, plex_interval_s=10)
    calls = []

    def runner(path, timeout, stop):
        calls.append(path)
        return RunResult(timeline=timeline(path), ramp_raw=[{"start_frame": 1, "frames": []}])

    sched = Scheduler(settings, store, PathMap([[base, "/movies"]]), runner=runner)
    sched.scan_now()
    yield sched, store, root, calls
    sched.stop(1)
    store.close()


def test_a_job_runs_and_its_timeline_and_raw_ramps_are_stored(env):
    sched, store, root, calls = env
    assert sched.run_one() is True
    row = store.get(calls[0])
    assert (
        row["status"] == "done"
        and row["quick_hash"] == HASH
        and row["segments"] == 2
        and row["flags"] == "var"
    )
    assert (
        json.loads(row["timeline"])["file"]["quick_hash"] == HASH
        and json.loads(row["ramp_raw"])[0]["start_frame"] == 1
    )
    assert row["duration_s"] is not None and row["analyzer_version"]


def test_the_queue_drains_and_then_there_is_nothing_to_do(env):
    sched, store, root, calls = env
    assert sched.run_one() and sched.run_one() and sched.run_one() is False
    assert store.counts() == {"done": 2}


def test_a_failing_runner_records_the_error_and_the_next_job_still_runs(env):
    sched, store, root, calls = env

    def broken(path, timeout, stop):
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("ffmpeg exploded X-Plex-Token=abc")
        return RunResult(timeline=timeline())

    sched.runner = broken
    assert sched.run_one() and sched.run_one()
    counts = store.counts()
    assert counts == {"error": 1, "done": 1}
    message = store.errors()[0]["error"]
    assert "RuntimeError" in message and "abc" not in message  # tokens are scrubbed even from error text


def test_a_skip_from_the_analysis_is_recorded(env):
    sched, store, root, calls = env
    sched.runner = lambda path, timeout, stop: RunResult(skip="3d-mvc")
    sched.run_one()
    assert store.counts() == {"skipped": 1, "queued": 1}


def test_a_file_that_changes_while_it_is_analysed_is_queued_again_not_given_a_stale_timeline(env):
    sched, store, root, calls = env

    def racing(path, timeout, stop):
        Path(path).write_bytes(b"replaced with something longer")
        return RunResult(timeline=timeline(path))

    sched.runner = racing
    sched.run_one()
    row = store.get(str(root / "b.mkv").replace("\\", "/")) or store.get(
        str(root / "a.mkv").replace("\\", "/")
    )
    changed = [r for r in store._all("SELECT * FROM files WHERE size = 30")]
    assert len(changed) == 1 and changed[0]["status"] == "queued" and changed[0]["timeline"] is None
    assert row is not None


def test_a_file_that_vanishes_during_analysis_is_an_error_not_a_crash(env):
    sched, store, root, calls = env

    def vanishing(path, timeout, stop):
        Path(path).unlink()
        return RunResult(timeline=timeline(path))

    sched.runner = vanishing
    sched.run_one()
    assert store.counts().get("error") == 1 and "disappeared" in store.errors()[0]["error"]


def test_a_file_that_cannot_be_read_when_claimed_is_an_error(env):
    sched, store, root, calls = env
    for f in root.iterdir():
        f.unlink()
    sched.run_one()
    assert store.counts().get("error") == 1 and "cannot read" in store.errors()[0]["error"]


def test_pausing_by_hand_in_quiet_hours_and_during_a_plex_transcode(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    plex = FakePlexClient()
    clock = [1000.0]
    now = [datetime(2026, 10, 28, 12, 0)]
    settings = Settings.from_env({"QUIET_HOURS": "18:00-23:00", "PLEX_URL": "http://p", "PLEX_TOKEN": "t"})
    sched = Scheduler(settings, store, PathMap(), plex=plex, now_local=lambda: now[0], clock=lambda: clock[0])
    assert sched.paused_reason() is None
    sched.pause()
    assert sched.paused_reason() == "paused"
    sched.resume()
    now[0] = datetime(2026, 10, 28, 19, 30)
    assert sched.paused_reason() == "quiet-hours"
    now[0] = datetime(2026, 10, 28, 12, 0)
    plex.busy = True
    clock[0] += 31  # the answer of the first check (not busy) is cached for 30 s
    assert sched.paused_reason() == "plex-transcode"
    calls = plex.calls
    assert sched.paused_reason() == "plex-transcode" and plex.calls == calls  # cached for 30 s
    clock[0] += 31
    plex.busy = False
    assert sched.paused_reason() is None
    plex.busy, plex.fail = True, True
    clock[0] += 31
    assert sched.paused_reason() is None  # an unreachable Plex must not stall analysis
    store.close()


def test_transcode_pause_can_be_switched_off(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    plex = FakePlexClient()
    plex.busy = True
    settings = Settings(config_dir=tmp_path, pause_on_transcode=False)
    assert Scheduler(settings, store, PathMap(), plex=plex).paused_reason() is None and plex.calls == 0
    store.close()


def test_request_maps_any_name_of_the_file_and_reports_unknown_and_running(env):
    sched, store, root, calls = env
    base = str(root).replace("\\", "/")
    assert sched.run_one()  # one file is now done
    assert sched.request("/movies/a.mkv", PRIORITY_DEMAND) == "queued"
    assert store.get(f"{base}/a.mkv")["priority"] == PRIORITY_DEMAND
    assert sched.request("/movies/zzz.mkv") == "unknown"
    store.claim_next()
    running = store.queue()[0]["path"]
    assert sched.request(running) == "running"


def test_threads_scan_run_and_stop_and_a_running_job_survives_a_restart(env):
    sched, store, root, calls = env
    store.claim_next()  # as if the container had stopped mid-job
    assert store.counts() == {"running": 1, "queued": 1}
    sched.start()
    deadline = time.time() + 15
    while time.time() < deadline and store.counts() != {"done": 2}:
        time.sleep(0.1)
    sched.stop(5)
    assert store.counts() == {"done": 2} and sched._threads == []
    assert sched.last_scan is not None and sched.last_scan["seen"] == 2
    assert sched.status()["paused"] is None


def wait_for(condition, seconds=10.0):
    deadline = time.time() + seconds
    while time.time() < deadline and not condition():
        time.sleep(0.02)
    return condition()


def test_the_worker_count_changes_while_running_and_never_interrupts_a_job(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    for i in range(6):
        (root / f"f{i}.mkv").write_bytes(b"x" * 10)
    base = str(root).replace("\\", "/")
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(config_dir=tmp_path, media_roots=(base,), scan_interval_s=60, concurrency=1)
    gate, lock = threading.Event(), threading.Lock()
    started: list[str] = []

    def runner(path, timeout, stop):
        with lock:
            started.append(threading.current_thread().name)
        if len(started) <= 3:
            gate.wait(10)  # hold the first three jobs open
        return RunResult(timeline=timeline(path))

    sched = Scheduler(settings, store, PathMap(), runner=runner)
    sched.scan_now()
    sched.start()
    try:
        assert wait_for(lambda: len(started) == 1) and sched.workers == 1
        sched.set_workers(3)  # raising it starts workers at once
        assert wait_for(lambda: len(started) == 3)
        assert sched.status()["workers"] == 3 and sched.status()["busy"] == 3
        sched.set_workers(1)  # lowering it interrupts nothing
        assert sched.status()["busy"] == 3 and store.counts().get("done", 0) == 0
        gate.set()
        assert wait_for(lambda: store.counts() == {"done": 6})
        # the three jobs that were running finished; every job after them ran on the one worker that was kept
        assert started[3:] == ["worker-0"] * 3
        assert wait_for(lambda: len(sched._workers) == 1)
    finally:
        gate.set()
        sched.stop(5)
    # the count is saved: a new scheduler on the same store starts from it, not from CONCURRENCY
    assert Scheduler(settings, store, PathMap()).workers == 1
    sched.set_workers(4)
    assert Scheduler(settings, store, PathMap()).workers == 4
    store.close()


def test_the_worker_count_is_validated_and_does_not_start_workers_before_start(env):
    sched, store, root, calls = env
    for bad in (0, -1, 17):
        with pytest.raises(ValueError, match="1 to 16"):
            sched.set_workers(bad)
    assert sched.set_workers(2) == 2 and sched._workers == {} and calls == []


def test_a_saved_count_that_is_unreadable_falls_back_to_the_setting(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    store.set_meta("workers", "many")
    settings = Settings(config_dir=tmp_path, media_roots=(), concurrency=3)
    assert Scheduler(settings, store, PathMap()).workers == 3
    store.close()


def test_workers_stay_idle_while_paused(env):
    sched, store, root, calls = env
    sched.pause()
    sched.start()
    time.sleep(0.5)
    sched.stop(5)
    assert calls == [] and store.counts() == {"queued": 2}


def test_the_default_runner_passes_the_folder_sampling_interval_to_the_worker(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "cih_analyzer.scheduler.subprocess_runner",
        lambda path, timeout, stop, sample_interval=None: (
            seen.append((path, sample_interval)) or RunResult(skip="iso")
        ),
    )
    store = Store(tmp_path / "s.sqlite")
    root = tmp_path / "tv"
    root.mkdir()
    (root / "e.mkv").write_bytes(b"x")
    base = str(tmp_path).replace("\\", "/")
    settings = Settings(config_dir=tmp_path, media_roots=(base,), sample_intervals=((base + "/tv", 8.0),))
    sched = Scheduler(settings, store, PathMap())
    sched.scan_now()
    sched.run_one()
    assert seen == [(base + "/tv/e.mkv", 8.0)]
    store.close()


def test_a_stop_during_analysis_puts_the_job_back(env):
    sched, store, root, calls = env
    sched.runner = lambda path, timeout, stop: RunResult(error="stopped")
    sched.run_one()
    assert store.counts() == {"queued": 2}


# ---- the real worker process -------------------------------------------------------------------------------------


@NEEDS_FFMPEG
class TestWorkerProcess:
    def film(self, tmp_path):
        w, h = 640, 360
        return make_video(
            tmp_path / "Film (2020).mkv",
            [Seg(8, rect_for_ratio(2.39, w, h), level=110), Seg(8, rect_for_ratio(16 / 9, w, h), level=150)],
        )

    def test_the_subprocess_returns_the_timeline_and_raw_ramps(self, tmp_path):
        result = subprocess_runner(str(self.film(tmp_path)), 300, threading.Event())
        assert result.error is None and result.skip is None
        assert [s["boundary"]["frame"] for s in result.timeline["segments"]] == [0, 200]
        assert result.version and result.timeline["file"]["quick_hash"]

    def test_skips_are_reported_not_raised(self, tmp_path):
        iso = tmp_path / "disc.iso"
        iso.write_bytes(b"x")
        assert subprocess_runner(str(iso), 60, threading.Event()).skip == "iso"

    def test_a_broken_file_is_reported_as_skipped_broken(self, tmp_path):
        bad = tmp_path / "bad.mkv"
        bad.write_bytes(bytes(range(256)) * 64)
        assert subprocess_runner(str(bad), 60, threading.Event()).skip.startswith("broken")

    def test_a_timeout_kills_the_worker_and_says_so(self, tmp_path):
        result = subprocess_runner(str(self.film(tmp_path)), 0.01, threading.Event())
        assert result.error and "timed out" in result.error

    def test_a_stop_request_kills_the_worker(self, tmp_path):
        stop = threading.Event()
        stop.set()
        assert subprocess_runner(str(self.film(tmp_path)), 300, stop).error == "stopped"

    def test_end_to_end_through_the_scheduler(self, tmp_path):
        root = tmp_path / "lib"
        root.mkdir()
        self.film(root.parent)
        (root.parent / "Film (2020).mkv").replace(root / "Film (2020).mkv")
        store = Store(tmp_path / "s.sqlite")
        base = str(root).replace("\\", "/")
        sched = Scheduler(Settings(config_dir=tmp_path, media_roots=(base,)), store, PathMap())
        sched.scan_now()
        assert sched.run_one()
        row = store.get(f"{base}/Film (2020).mkv")
        assert row["status"] == "done" and row["segments"] == 2 and "var" in row["flags"]
        assert json.loads(row["timeline"])["segments"][1]["boundary"]["frame"] == 200
        store.close()
