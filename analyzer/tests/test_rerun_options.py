"""Re-running one title by hand: a detailed pass or a survey at its own interval, and removing the saved result."""

import json

import pytest
from fastapi.testclient import TestClient

from cih_analyzer import scheduler as scheduler_module
from cih_analyzer.api import create_app
from cih_analyzer.config import Settings
from cih_analyzer.pathmap import PathMap
from cih_analyzer.scheduler import RunResult, Scheduler
from cih_analyzer.store import Store

HASH = "b" * 40


def timeline(path):
    return {"file": {"quick_hash": HASH, "path": path}, "flags": ["var"], "segments": [{}, {}, {}]}


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    film = root / "Film (2020).mkv"
    film.write_bytes(b"x" * 10)
    base = str(root).replace("\\", "/")
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(config_dir=tmp_path, media_roots=(base,), survey_interval_s=8.0)
    sched = Scheduler(settings, store, PathMap([[base, base]]))  # the real runner, replaced per test below
    sched.scan_now()
    client = TestClient(create_app(settings, store, sched, PathMap([[base, base]])))
    yield client, store, sched, f"{base}/Film (2020).mkv"
    sched.stop(1)
    store.close()


def capture_worker(monkeypatch):
    """Replace the worker process with a function that records how it was asked to survey."""
    seen = []

    def fake(path, timeout, stop, **kw):
        seen.append({"survey_interval": kw.get("survey_interval")})
        return RunResult(timeline=timeline(path))

    monkeypatch.setattr(scheduler_module, "subprocess_runner", fake)
    return seen


def test_a_plain_run_surveys_at_the_setting_a_detailed_run_does_not_survey_and_one_can_choose_its_own_interval(
    world, monkeypatch
):
    client, store, sched, film = world
    seen = capture_worker(monkeypatch)
    assert sched.run_one() is True  # first queued by the scan: no options
    for body, expected in (
        ({"mode": "detailed"}, None),
        ({"mode": "auto", "survey_interval": 2}, 2),
        ({}, 8.0),  # the plain button again: back to the setting
    ):
        r = client.post("/api/v1/analyze", json={"path": film, "source": "manual", **body})
        assert r.status_code == 200 and r.json()["status"] == "queued"
        assert sched.run_one() is True
        assert seen[-1]["survey_interval"] == expected, body
    assert seen[0]["survey_interval"] == 8.0
    assert store.get(film)["options"] is None  # a run clears what it was asked


def test_the_options_wait_in_the_queue_with_the_title_and_a_player_request_cannot_set_them(
    world, monkeypatch
):
    client, store, sched, film = world
    seen = capture_worker(monkeypatch)
    sched.run_one()
    client.post("/api/v1/analyze", json={"path": film, "source": "manual", "mode": "detailed"})
    assert json.loads(store.get(film)["options"]) == {"detailed": True}
    player = client.post("/api/v1/analyze", json={"path": film, "mode": "detailed"}).json()
    assert "options" not in player  # a player's request ignores them
    assert json.loads(store.get(film)["options"]) == {"detailed": True}  # still queued as the hand asked
    sched.run_one()
    assert seen[-1]["survey_interval"] is None


def test_the_survey_interval_is_checked(world):
    client, _, _, film = world
    for bad in (0, 0.5, 500, "x"):
        r = client.post("/api/v1/analyze", json={"path": film, "source": "manual", "survey_interval": bad})
        assert r.status_code == 422
    assert (
        client.post("/api/v1/analyze", json={"path": film, "source": "manual", "mode": "slow"}).status_code
        == 422
    )


def test_removing_the_saved_result_leaves_the_title_idle_and_keeps_the_override(world, monkeypatch):
    client, store, sched, film = world
    capture_worker(monkeypatch)
    sched.run_one()
    assert (
        store.get(film)["status"] == "done"
        and client.get("/api/v1/timeline", params={"path": film}).status_code == 200
    )
    client.post("/api/v1/override", json={"path": film, "mode": "disable"})
    r = client.post("/api/v1/forget", json={"path": film})
    assert r.status_code == 200 and r.json() == {"path": film, "status": "idle"}
    row = store.get(film)
    assert (
        row["status"] == "idle"
        and row["timeline"] is None
        and row["flags"] is None
        and row["segments"] is None
    )
    assert row["quick_hash"] is None and row["analyzed_at"] is None and row["analyzer_version"] is None
    assert client.get("/api/v1/timeline", params={"path": film}).status_code == 404
    assert store.get_override(film)["mode"] == "disable"
    assert sched.run_one() is False  # idle is not queued: nothing runs until a player asks
    asked = client.get("/api/v1/timeline", params={"path": film, "analyze": 1})
    assert asked.status_code == 404 and asked.json()["queue"] == "queued"  # a player brings it back
    assert store.get(film)["status"] == "queued"


def test_a_running_skipped_or_unknown_title_is_left_alone(world):
    client, store, sched, film = world
    store.claim_next()  # running
    assert client.post("/api/v1/forget", json={"path": film}).status_code == 409
    store.requeue_one_running(film)
    store.skip(film, "3d")
    assert client.post("/api/v1/forget", json={"path": film}).status_code == 409
    assert store.get(film)["status"] == "skipped"
    assert client.post("/api/v1/forget", json={"path": film + ".nope"}).status_code == 404
    assert client.post("/api/v1/forget", json={}).status_code == 422


def test_a_hand_re_queue_can_ask_for_the_dense_pass(world, monkeypatch):
    client, store, sched, film = world
    capture_worker(monkeypatch)
    sched.run_one()
    r = client.post("/api/v1/analyze", json={"path": film, "source": "manual", "mode": "dense"})
    assert r.status_code == 200 and r.json()["options"] == {"dense": True}
    assert json.loads(store.get(film)["options"]) == {"dense": True}
