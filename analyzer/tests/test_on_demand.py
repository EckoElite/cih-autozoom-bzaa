"""Hybrid analysis: libraries kept `idle` until a player asks, and the express worker that serves those asks."""

import threading
import time

from cih_analyzer.config import Settings
from cih_analyzer.pathmap import PathMap
from cih_analyzer.scanner import scan_media
from cih_analyzer.scheduler import RunResult, Scheduler
from cih_analyzer.store import PRIORITY_DEMAND, PRIORITY_PLAYER, Store

HASH = "b" * 40


def timeline(path="/x"):
    return {"file": {"quick_hash": HASH, "path": path}, "flags": [], "segments": [{}]}


def libraries(tmp_path):
    movies, tv = tmp_path / "media" / "movies", tmp_path / "media" / "tv"
    for root, names in ((movies, ("m1.mkv", "m2.mkv")), (tv, ("s01e01.mkv", "s01e02.mkv"))):
        root.mkdir(parents=True)
        for name in names:
            (root / name).write_bytes(b"x" * 10)
    return str(movies).replace("\\", "/"), str(tv).replace("\\", "/")


def test_files_in_an_on_demand_root_are_catalogued_idle_and_never_queued(tmp_path):
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    summary = scan_media([movies, tv], store, "0.3.8", on_demand_roots=(tv,))
    assert summary.new == 4
    assert store.counts() == {"queued": 2, "idle": 2}
    assert store.claim_next()["path"].startswith(movies)
    assert store.claim_next()["path"].startswith(movies)
    assert store.claim_next() is None  # the idle episodes are not work
    store.close()


def test_an_on_demand_folder_inside_a_larger_root_is_still_on_demand(tmp_path):
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    parent = str(tmp_path / "media").replace(
        "\\", "/"
    )  # the real set-up: MEDIA_ROOTS=/media holds both libraries
    scan_media([parent], store, "0.3.8", on_demand_roots=(tv,))
    assert store.counts() == {"queued": 2, "idle": 2}
    store.close()


def test_an_idle_file_is_queued_at_priority_when_asked_for_and_not_by_a_rescan(tmp_path):
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    scan_media([tv], store, "0.3.8", on_demand_roots=(tv,))
    episode = f"{tv}/s01e01.mkv"
    assert store.requeue(episode, PRIORITY_DEMAND)
    assert store.get(episode)["status"] == "queued" and store.get(episode)["priority"] == PRIORITY_DEMAND
    scan_media([tv], store, "0.3.8", on_demand_roots=(tv,))
    assert store.counts() == {"queued": 1, "idle": 1}
    store.close()


def test_files_queued_before_the_folder_was_marked_on_demand_go_back_to_waiting_for_a_player(tmp_path):
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    scan_media([tv], store, "0.3.8")  # the first scan, before ON_DEMAND_ROOTS was set: everything is queued
    assert store.counts() == {"queued": 2}
    asked = f"{tv}/s01e02.mkv"
    store.requeue(asked, PRIORITY_DEMAND)  # a player asked for this one meanwhile
    summary = scan_media([tv], store, "0.3.8", on_demand_roots=(tv,))
    assert summary.idled == 1 and summary.new == 0
    assert store.get(f"{tv}/s01e01.mkv")["status"] == "idle"
    assert store.get(asked)["status"] == "queued" and store.get(asked)["priority"] == PRIORITY_DEMAND
    store.close()


def test_an_analysed_episode_that_changes_goes_back_to_idle_and_a_new_version_does_not_queue_it(tmp_path):
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    scan_media([tv], store, "0.3.8", on_demand_roots=(tv,))
    episode = f"{tv}/s01e01.mkv"
    store.requeue(episode)
    job = store.claim_next()
    store.finish(job["path"], timeline_json="{}", ramp_raw_json=None, quick_hash=HASH, version="0.3.8", flags=[],
                 segments=1, duration_s=1.0)  # fmt: skip
    summary = scan_media([tv], store, "0.4.0", on_demand_roots=(tv,))  # a new major version
    assert summary.reanalyze == 0 and store.get(episode)["status"] == "done"
    (tmp_path / "media" / "tv" / "s01e01.mkv").write_bytes(b"y" * 20)
    scan_media([tv], store, "0.4.0", on_demand_roots=(tv,))
    assert store.get(episode)["status"] == "idle" and store.get(episode)["timeline"] is None
    store.close()


def test_claim_next_can_be_limited_to_what_a_player_asked_for(tmp_path):
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    scan_media([movies], store, "0.3.8")
    assert store.claim_next(max_priority=PRIORITY_DEMAND) is None  # only backlog work is queued
    store.requeue(f"{movies}/m1.mkv", PRIORITY_DEMAND)  # a re-queue made by hand is not a player waiting
    assert store.claim_next(max_priority=PRIORITY_PLAYER) is None
    store.requeue(f"{movies}/m2.mkv", PRIORITY_PLAYER)
    assert store.claim_next(max_priority=PRIORITY_PLAYER)["path"].endswith("m2.mkv")
    assert store.get(f"{movies}/m1.mkv")["priority"] == PRIORITY_DEMAND  # still queued, still by hand
    store.close()


def wait_for(condition, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def test_the_express_worker_serves_a_request_while_every_background_worker_is_busy_or_paused(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("cih_analyzer.scheduler.PAUSED_POLL_S", 0.1)  # a resumed worker notices within 15 s
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(config_dir=tmp_path, media_roots=(movies, tv), on_demand_roots=(tv,), scan_interval_s=3600,
                        plex_interval_s=3600, express_workers=1, start_paused=True)  # fmt: skip
    release, started = threading.Event(), []

    def runner(path, timeout, stop):
        started.append(path)
        if path.startswith(movies):  # a long film: holds its worker until released
            release.wait(10)
        return RunResult(timeline=timeline(path))

    sched = Scheduler(settings, store, PathMap(), runner=runner)
    sched.scan_now()
    sched.start()
    try:
        assert sched.paused_reason() == "paused"
        assert sched.request(f"{tv}/s01e02.mkv", PRIORITY_PLAYER) == "queued"
        assert wait_for(
            lambda: store.get(f"{tv}/s01e02.mkv")["status"] == "done"
        )  # served although paused by hand
        assert not any(p.startswith(movies) for p in started)  # the background queue stayed paused
        assert sched.status()["express"] == 1
        # a hand pause is lifted, the background workers take the films and hold them; a new request still goes through
        sched.resume()
        assert wait_for(lambda: any(p.startswith(movies) for p in started))
        assert sched.request(f"{tv}/s01e01.mkv", PRIORITY_PLAYER) == "queued"
        assert wait_for(lambda: store.get(f"{tv}/s01e01.mkv")["status"] == "done")
    finally:
        release.set()
        sched.stop(2)
        store.close()


def test_the_express_worker_leaves_background_work_alone_and_obeys_quiet_hours(tmp_path):
    from datetime import datetime

    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    from cih_analyzer.config import parse_quiet_hours

    settings = Settings(config_dir=tmp_path, media_roots=(movies,), express_workers=1, start_paused=True,
                        quiet_hours=parse_quiet_hours("00:00-23:59"))  # fmt: skip
    ran = []
    sched = Scheduler(settings, store, PathMap(), runner=lambda p, t, s: ran.append(p) or RunResult(timeline=timeline(p)),
                      now_local=lambda: datetime(2026, 1, 1, 12, 0))  # fmt: skip
    sched.scan_now()
    sched.start()
    try:
        sched.request(f"{movies}/m1.mkv", PRIORITY_PLAYER)
        time.sleep(0.6)
        assert ran == []  # quiet hours: even a request waits
    finally:
        sched.stop(2)
        store.close()


def test_a_requeue_made_by_hand_waits_for_resume_and_does_not_take_the_express_lane(tmp_path, monkeypatch):
    # what happened with Oppenheimer: re-queued from the status page while paused, it must not start by itself, and
    # it must not occupy the express worker that a player's request needs
    monkeypatch.setattr("cih_analyzer.scheduler.PAUSED_POLL_S", 0.1)
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(config_dir=tmp_path, media_roots=(movies, tv), on_demand_roots=(tv,), scan_interval_s=3600,
                        plex_interval_s=3600, express_workers=1, start_paused=True)  # fmt: skip
    ran = []
    sched = Scheduler(
        settings, store, PathMap(), runner=lambda p, t, s: ran.append(p) or RunResult(timeline=timeline(p))
    )
    sched.scan_now()
    sched.start()
    try:
        sched.request(f"{movies}/m1.mkv", PRIORITY_DEMAND)  # by hand
        time.sleep(0.6)
        assert ran == [] and store.get(f"{movies}/m1.mkv")["status"] == "queued"
        sched.request(f"{tv}/s01e01.mkv", PRIORITY_PLAYER)  # a player is waiting
        assert wait_for(lambda: store.get(f"{tv}/s01e01.mkv")["status"] == "done")
        assert ran == [f"{tv}/s01e01.mkv"]
        sched.resume()
        assert wait_for(lambda: store.get(f"{movies}/m1.mkv")["status"] == "done")  # it runs once resumed
    finally:
        sched.stop(2)
        store.close()


def test_a_request_with_the_playhead_position_is_remembered_and_moves_a_survey_that_began_far_away(tmp_path):
    movies, tv = libraries(tmp_path)
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(config_dir=tmp_path, media_roots=(movies,), express_workers=0)
    sched = Scheduler(settings, store, PathMap(), runner=lambda p, t, s: RunResult(timeline=timeline(p)))
    sched.scan_now()
    film = f"{movies}/m1.mkv"
    assert sched.request(film, PRIORITY_PLAYER, at=600.0) == "queued"
    assert sched._start_at[film] == 600.0  # the survey will begin there
    job = store.claim_next(max_priority=PRIORITY_PLAYER)
    assert job["path"] == film
    stop = threading.Event()
    sched._job_stops[film] = stop  # what run_one registers while the worker process runs
    # the player resumed far from where the survey began, and nothing known covers it: the job moves
    assert sched.request(film, PRIORITY_PLAYER, at=2400.0) == "queued"
    assert stop.is_set() and sched._start_at[film] == 2400.0
    # when what the survey knows already covers the place, or it is near, the job is left alone
    stop.clear()
    sched._set_provisional(film, {"coverage": [[2300.0, 2500.0]]})
    assert sched.request(film, PRIORITY_PLAYER, at=2450.0) == "running" and not stop.is_set()
    assert sched.request(film, PRIORITY_PLAYER, at=2420.0) == "running" and not stop.is_set()
    # a request made by hand never moves anything
    assert sched.request(film, PRIORITY_DEMAND, at=10.0) == "running" and not stop.is_set()
    store.close()
