"""The priority list: a text file of media paths from another script, read (only) to queue those episodes."""

import os
import stat
import time as clock
from datetime import datetime, time

import pytest
from fastapi.testclient import TestClient

from cih_analyzer.api import create_app
from cih_analyzer.config import ConfigError, Settings
from cih_analyzer.pathmap import PathMap
from cih_analyzer.prioritylist import apply_priority_list, read_lines, seconds_until_next
from cih_analyzer.scheduler import RunResult, Scheduler
from cih_analyzer.store import PRIORITY_LOW, PRIORITY_MEDIUM, Store

NOW = 1_800_000_000.0
LISTED = "/mnt/cache_media/media/TV Shows/Show/Season 1/Show.S01E01.Pilot.mkv"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    yield s
    s.close()


def add(store, name, *, season=1, status="idle", priority=PRIORITY_LOW, show="Show"):
    path = f"/media/tv/{show}/Season {season}/{name}"
    store.seen(path, 10, 1.0, version="0.3.21", on_demand=True, now=NOW)
    if status == "done":
        store.finish(
            path, timeline_json="{}", ramp_raw_json=None, quick_hash="a" * 40, version="0.3.21",
            flags=[], segments=1, duration_s=1.0,
        )  # fmt: skip
    elif status != "idle":
        store._run("UPDATE files SET status=?, priority=? WHERE path=?", (status, priority, path))
    return path


def listing(tmp_path, *lines, name="exclude.txt"):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_lines_are_read_without_blanks_and_comments(tmp_path):
    f = listing(tmp_path, "# header", LISTED, "", "  /mnt/x/y.mkv  ")
    assert read_lines(f) == [LISTED, "/mnt/x/y.mkv"]


def test_the_file_is_only_read_never_changed(tmp_path):
    f = listing(tmp_path, LISTED)
    os.chmod(f, stat.S_IREAD)  # a read-only file reads fine, so nothing here needs write access
    before = (os.stat(f).st_mtime_ns, os.stat(f).st_size, open(f, "rb").read())
    s = Store(tmp_path / "x.sqlite")
    try:
        apply_priority_list(f, s, PathMap(), NOW)
    finally:
        s.close()
        os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
    assert (os.stat(f).st_mtime_ns, os.stat(f).st_size, open(f, "rb").read()) == before


def test_a_listed_episode_is_matched_by_folder_and_file_name_and_queued_medium(store, tmp_path):
    path = add(store, "Show.S01E01.Pilot.mkv")
    other = add(store, "Show.S01E02.Next.mkv")
    summary = apply_priority_list(listing(tmp_path, LISTED), store, PathMap(), NOW)
    assert (summary.lines, summary.matched, summary.queued, summary.unmatched) == (1, 1, 1, 0)
    assert store.get(path)["status"] == "queued" and store.get(path)["priority"] == PRIORITY_MEDIUM
    assert store.get(other)["status"] == "idle"


def test_a_path_map_rule_matches_too(store, tmp_path):
    path = add(store, "Show.S01E01.Pilot.mkv")
    pathmap = PathMap([["/media/tv", "/mnt/cache_media/media/TV Shows"]])
    assert apply_priority_list(listing(tmp_path, LISTED), store, pathmap, NOW).queued == 1
    assert store.get(path)["priority"] == PRIORITY_MEDIUM


def test_what_is_already_waiting_done_or_unusable_is_not_queued_again(store, tmp_path):
    lines = []
    for number, (status, priority) in enumerate(
        [
            ("done", 4),
            ("running", 4),
            ("queued", 2),
            ("queued", 4),
            ("skipped", 4),
            ("error", 4),
            ("idle", 4),
        ],
        1,
    ):
        name = f"Show.S01E{number:02d}.T.mkv"
        add(store, name, status=status, priority=priority)
        lines.append(f"/mnt/cache_media/media/TV Shows/Show/Season 1/{name}")
    lines.append(lines[0])  # listed twice
    s = apply_priority_list(listing(tmp_path, *lines), store, PathMap(), NOW)
    assert (s.done, s.waiting, s.raised, s.left_alone, s.queued) == (1, 2, 1, 2, 1)
    assert s.matched == 7 and s.lines == 8
    assert store.get("/media/tv/Show/Season 1/Show.S01E04.T.mkv")["priority"] == PRIORITY_MEDIUM  # moved up
    assert store.get("/media/tv/Show/Season 1/Show.S01E03.T.mkv")["priority"] == 2  # never moved down


def test_titles_with_apostrophes_and_commas_match(store, tmp_path):
    add(store, "Hacks.(2021).S01E03.A.Gig’s.A.Gig.mkv", show="Hacks (2021)")
    add(store, "XO,.Kitty.S03E04.A.List.mkv", season=3, show="XO, Kitty")
    s = apply_priority_list(
        listing(
            tmp_path,
            "/mnt/cache_media/media/TV Shows/Hacks (2021)/Season 1/Hacks.(2021).S01E03.A.Gig’s.A.Gig.mkv",
            "/mnt/cache_media/media/TV Shows/XO, Kitty/Season 3/XO,.Kitty.S03E04.A.List.mkv",
        ),
        store,
        PathMap(),
        NOW,
    )
    assert s.matched == 2 and s.unmatched == 0


def test_a_path_the_analyzer_does_not_have_is_counted_and_shown(store, tmp_path):
    add(store, "Show.S01E01.Pilot.mkv")
    s = apply_priority_list(
        listing(tmp_path, LISTED, "/mnt/cache_media/media/TV Shows/Gone/Season 1/Gone.S01E01.mkv"),
        store,
        PathMap(),
        NOW,
    )
    assert (s.matched, s.unmatched) == (1, 1)
    assert s.unmatched_examples == ["/mnt/cache_media/media/TV Shows/Gone/Season 1/Gone.S01E01.mkv"]


def test_a_missing_file_is_reported_not_raised(store, tmp_path):
    s = apply_priority_list(str(tmp_path / "nope.txt"), store, PathMap(), NOW)
    assert s.error and "cannot read the priority list" in s.error and s.lines == 0


def test_two_files_that_end_alike_are_not_guessed(store, tmp_path):
    add(store, "Show.S01E01.Pilot.mkv", show="Show")
    store.seen(
        "/media/tv2/Show/Season 1/Show.S01E01.Pilot.mkv", 10, 1.0, version="0.3.21", on_demand=True, now=NOW
    )
    s = apply_priority_list(
        listing(tmp_path, "/mnt/x/Show/Season 1/Show.S01E01.Pilot.mkv"), store, PathMap(), NOW
    )
    assert s.matched == 0 and s.unmatched == 1  # the folder above them is not in the listed path


# ---- the time of day ---------------------------------------------------------------------------------------------


def test_settings_read_the_file_and_the_times_from_the_environment():
    s = Settings.from_env({"PRIORITY_LIST_FILE": " /priority/x.txt ", "PRIORITY_LIST_TIME": "16:30, 04:00"})
    assert s.priority_list_file == "/priority/x.txt" and s.priority_list_times == (time(4, 0), time(16, 30))
    default = Settings.from_env({})
    assert default.priority_list_file == "" and default.priority_list_times == (time(4, 0),)
    with pytest.raises(ConfigError):
        Settings.from_env({"PRIORITY_LIST_TIME": "4pm"})


def test_the_wait_runs_to_the_next_listed_time_today_or_tomorrow():
    now = datetime(2026, 11, 16, 10, 0, 0).timestamp()
    assert seconds_until_next((time(16, 30),), now) == 6.5 * 3600
    assert seconds_until_next((time(4, 0),), now) == 18 * 3600
    assert seconds_until_next((time(4, 0), time(16, 30)), now) == 6.5 * 3600
    assert seconds_until_next((), now) == 86400.0


# ---- the scheduler and the API -----------------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    list_file = listing(tmp_path, LISTED)
    settings = Settings(config_dir=tmp_path, media_roots=(str(tmp_path),), priority_list_file=list_file)
    store = Store(tmp_path / "s.sqlite")
    pathmap = PathMap()
    sched = Scheduler(settings, store, pathmap, runner=lambda p, t, s: RunResult(timeline={}))
    yield TestClient(create_app(settings, store, sched, pathmap)), store, sched, settings
    store.close()


def test_the_scheduler_keeps_the_result_of_the_last_read_for_the_status_page(app):
    client, store, sched, _ = app
    add(store, "Show.S01E01.Pilot.mkv")
    assert sched.priority_list_now()["queued"] == 1
    status = client.get("/api/v1/status").json()
    assert status["scheduler"]["last_priority_list"]["queued"] == 1
    assert status["settings"]["priority_list"]["times"] == ["04:00"]
    first = sched.last_priority_list["at"]
    assert client.post("/api/v1/priority-list/read").status_code == 200
    deadline = clock.time() + 5
    while sched.last_priority_list["at"] == first and clock.time() < deadline:  # it runs in the background
        clock.sleep(0.02)
    assert sched.last_priority_list["at"] > first


def test_without_a_priority_list_there_is_nothing_to_read(tmp_path):
    settings = Settings(config_dir=tmp_path, media_roots=(str(tmp_path),))
    store = Store(tmp_path / "s.sqlite")
    sched = Scheduler(settings, store, PathMap(), runner=lambda p, t, s: RunResult(timeline={}))
    client = TestClient(create_app(settings, store, sched, PathMap()))
    assert sched.priority_list_now() is None
    assert client.post("/api/v1/priority-list/read").status_code == 409
    assert client.get("/api/v1/status").json()["settings"]["priority_list"] is None
    store.close()


def test_an_episode_that_stays_on_the_list_for_days_is_queued_once_and_never_again(store, tmp_path):
    path = add(store, "Show.S01E01.Pilot.mkv")
    f = listing(tmp_path, LISTED)
    assert apply_priority_list(f, store, PathMap(), NOW).queued == 1  # day 1: queued
    second = apply_priority_list(f, store, PathMap(), NOW + 86400)  # day 2: still waiting
    assert (second.queued, second.raised, second.waiting) == (0, 0, 1)
    store.claim_next()
    store.finish(
        path, timeline_json="{}", ramp_raw_json=None, quick_hash="a" * 40, version="0.3.21",
        flags=[], segments=1, duration_s=1.0, now=NOW + 90000,
    )  # fmt: skip
    third = apply_priority_list(f, store, PathMap(), NOW + 2 * 86400)  # day 3: processed
    assert (third.queued, third.raised, third.waiting, third.done) == (0, 0, 0, 1)
    assert store.get(path)["status"] == "done" and store.get(path)["analyzed_at"] == NOW + 90000
    store._run("UPDATE files SET status='error', error='x' WHERE path=?", (path,))
    assert (
        apply_priority_list(f, store, PathMap(), NOW + 3 * 86400).queued == 0
    )  # a failed one is not retried daily


def test_an_episode_the_user_placed_lower_by_hand_is_not_moved_back_up(store, tmp_path):
    path = add(store, "Show.S01E01.Pilot.mkv")
    f = listing(tmp_path, LISTED)
    apply_priority_list(f, store, PathMap(), NOW)
    assert store.place([path], PRIORITY_LOW, end=True) == 1  # dragged down on the status page
    again = apply_priority_list(f, store, PathMap(), NOW + 86400)
    assert (again.raised, again.waiting) == (0, 1)
    assert store.get(path)["priority"] == PRIORITY_LOW
