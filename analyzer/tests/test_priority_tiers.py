"""Four queue tiers (P1 Highest to P4 Low) under express: order, hand placement, spots, tier changes, what the status page reads."""

import json

import pytest
from fastapi.testclient import TestClient

from cih_analyzer.api import create_app
from cih_analyzer.config import Settings
from cih_analyzer.pathmap import PathMap
from cih_analyzer.scheduler import RunResult, Scheduler
from cih_analyzer.store import (
    PRIORITY_HIGH,
    PRIORITY_HIGHEST,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    PRIORITY_PLAYER,
    Store,
)

V = "0.3.15"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    yield s
    s.close()


def add(store, name, tier=PRIORITY_LOW, mtime=1000.0):
    store.seen(f"/m/{name}.mkv", 100, mtime, version=V, priority=tier, now=mtime)
    return f"/m/{name}.mkv"


def order(store):
    return [r["path"].split("/")[-1][:-4] for r in store.waiting(1000)]


def test_the_tiers_come_in_order_and_each_keeps_its_own_rule(store):
    add(store, "low_old", PRIORITY_LOW, mtime=100)
    add(store, "low_new", PRIORITY_LOW, mtime=900)
    add(store, "low_recent", PRIORITY_LOW, mtime=50)
    store._run("UPDATE files SET recent=1 WHERE path='/m/low_recent.mkv'")
    add(store, "med_old", PRIORITY_MEDIUM, mtime=10)
    add(store, "med_new", PRIORITY_MEDIUM, mtime=20)
    add(store, "high_first", PRIORITY_LOW, mtime=300)
    store.requeue("/m/high_first.mkv", PRIORITY_HIGH, now=1.0)
    add(store, "high_second", PRIORITY_LOW, mtime=200)
    store.requeue("/m/high_second.mkv", PRIORITY_HIGH, now=2.0)
    add(store, "top", PRIORITY_LOW, mtime=5)
    store.requeue("/m/top.mkv", PRIORITY_HIGHEST, now=3.0)
    add(store, "playing", PRIORITY_LOW, mtime=1)
    store.requeue("/m/playing.mkv", PRIORITY_PLAYER, now=4.0)
    assert order(store) == [
        "top", "high_first", "high_second", "med_new", "med_old", "low_recent", "low_new", "low_old",
    ]  # fmt: skip
    assert [r["path"] for r in store.express_waiting()] == ["/m/playing.mkv"]
    assert store.claim_next(max_priority=PRIORITY_PLAYER)["path"] == "/m/playing.mkv"
    assert store.claim_next(max_priority=PRIORITY_PLAYER) is None  # express workers never take the queue
    assert store.claim_next()["path"] == "/m/top.mkv"


def test_spots_count_over_the_whole_queue_and_within_each_tier(store):
    for name in "abc":
        add(store, name, PRIORITY_LOW, mtime={"a": 3, "b": 2, "c": 1}[name])
    add(store, "m", PRIORITY_MEDIUM)
    spots = store.spots()
    assert spots["/m/m.mkv"] == {"tier": 3, "tier_spot": 1, "spot": 1, "total": 4}
    assert spots["/m/c.mkv"] == {"tier": 4, "tier_spot": 3, "spot": 4, "total": 4}
    assert store.spots(["/m/b.mkv"]) == {"/m/b.mkv": {"tier": 4, "tier_spot": 2, "spot": 3, "total": 4}}
    assert store.tier_counts() == {1: 0, 2: 0, 3: 1, 4: 3}


def test_placing_a_title_keeps_the_order_of_what_was_placed_and_new_arrivals_go_behind(store):
    for i, name in enumerate("abcde"):
        add(store, name, mtime=500 - i)  # a, b, c, d, e (newest first)
    assert order(store) == list("abcde")
    assert store.place(["/m/d.mkv"], PRIORITY_LOW) == 1  # to the top of the tier
    assert order(store) == list("dabce")
    assert store.place(["/m/a.mkv"], PRIORITY_LOW, after="/m/c.mkv") == 1
    assert order(store) == list("dbcae")
    add(
        store, "z", mtime=9999
    )  # a brand new, much newer file: behind everything placed, by the tier's own rule
    assert order(store) == list("dbcaze")
    assert store.place(["/m/e.mkv"], PRIORITY_LOW, end=True) == 1
    assert order(store) == list("dbcaze")[:4] + ["z", "e"]


def test_moving_between_tiers_takes_the_target_tier_and_a_running_title_is_left_alone(store):
    a = add(store, "a", PRIORITY_LOW)
    b = add(store, "b", PRIORITY_LOW, mtime=900)
    c = add(store, "c", PRIORITY_MEDIUM)
    assert store.place([a, b], PRIORITY_MEDIUM, after=c) == 2
    assert [store.get(p)["priority"] for p in (a, b, c)] == [PRIORITY_MEDIUM] * 3
    assert order(store) == ["c", "a", "b"]
    running = store.claim_next()
    assert running["path"] == c
    assert store.place([c], PRIORITY_HIGHEST) == 0 and store.get(c)["priority"] == PRIORITY_MEDIUM
    with pytest.raises(ValueError):
        store.place([a], PRIORITY_HIGH, after="/m/not-here.mkv")
    with pytest.raises(ValueError):
        store.place([a], 7)


def test_a_title_placed_by_hand_is_not_moved_by_the_variable_list_but_an_unplaced_one_is(store):
    a = add(store, "a", PRIORITY_LOW)
    b = add(store, "b", PRIORITY_LOW)
    store.place([b], PRIORITY_LOW)
    assert store.seen(a, 100, 1000.0, version=V, priority=PRIORITY_MEDIUM) == "raised"
    assert store.seen(b, 100, 1000.0, version=V, priority=PRIORITY_MEDIUM) == "unchanged"
    assert store.get(a)["priority"] == PRIORITY_MEDIUM and store.get(b)["priority"] == PRIORITY_LOW


def test_a_replaced_file_and_a_new_analyzer_version_are_queued_as_high(store):
    a = add(store, "a", PRIORITY_LOW)
    store.claim_next()
    store.finish(
        a,
        timeline_json="{}",
        ramp_raw_json=None,
        quick_hash="h" * 40,
        version="0.1.0",
        flags=[],
        segments=1,
        duration_s=1.0,
    )
    assert store.seen(a, 100, 1000.0, version=V, priority=PRIORITY_LOW) == "reanalyze"
    assert store.get(a)["priority"] == PRIORITY_HIGH
    b = add(store, "b", PRIORITY_LOW)
    assert store.seen(b, 555, 1000.0, version=V, priority=PRIORITY_LOW) == "changed"
    assert store.get(b)["priority"] == PRIORITY_HIGH
    c = add(store, "c", PRIORITY_LOW)
    store.place([c], PRIORITY_HIGHEST)
    assert store.seen(c, 556, 1000.0, version=V, priority=PRIORITY_LOW) == "changed"
    assert store.get(c)["priority"] == PRIORITY_HIGHEST  # already ahead: stays there


def test_requeueing_to_a_higher_tier_drops_the_old_place_and_finishing_clears_it(store):
    a = add(store, "a", PRIORITY_LOW)
    store.place([a], PRIORITY_LOW)
    assert store.get(a)["rank"] is not None
    store.requeue(a, PRIORITY_HIGH)
    assert store.get(a)["priority"] == PRIORITY_HIGH and store.get(a)["rank"] is None
    store.place([a], PRIORITY_HIGH)
    store.claim_next()
    store.finish(a, timeline_json="{}", ramp_raw_json=None, quick_hash="h" * 40, version=V, flags=[], segments=1,
                 duration_s=1.0, ran=json.dumps({"pass": "detailed"}))  # fmt: skip
    row = store.get(a)
    assert row["rank"] is None and json.loads(row["ran"]) == {"pass": "detailed"}


# ---- through the API ---------------------------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    base = str(root).replace("\\", "/")
    for name in ("Alpha (2001)", "Beta (2002)", "Gamma (2003)", "Dunkirk (2017)"):
        (root / f"{name}.mkv").write_bytes(b"x" * 10)
    settings = Settings(config_dir=tmp_path, media_roots=(base,), survey_interval_s=8.0)
    store = Store(tmp_path / "s.sqlite")
    sched = Scheduler(
        settings, store, PathMap([[base, base]]), runner=lambda p, t, s: RunResult(error="unused")
    )
    sched.scan_now()
    client = TestClient(create_app(settings, store, sched, PathMap([[base, base]])))
    yield client, store, sched, base
    store.close()


def test_up_next_lists_tiers_with_spots_the_variable_tag_and_express_slots(world):
    client, store, sched, base = world
    body = client.get("/api/v1/up-next").json()
    assert body["tiers"] == {"1": 0, "2": 0, "3": 1, "4": 3} and body["total"] == 4
    assert body["express_slots"] == 3 and body["tier_names"]["3"] == "Medium"
    first = body["items"][0]
    assert first["path"].endswith("Dunkirk (2017).mkv") and first["variable"] is True
    assert (first["tier"], first["tier_spot"], first["spot"], first["total"]) == (3, 1, 1, 4)
    assert all(r["variable"] is False for r in body["items"][1:])
    page = client.get("/api/v1/up-next?limit=2&offset=1").json()
    assert [r["spot"] for r in page["items"]] == [2, 3] and page["total"] == 4


def test_ui_requests_are_p1_script_requests_p2_and_players_express(world):
    client, store, sched, base = world
    ui = client.post("/api/v1/analyze", json={"path": f"{base}/Alpha (2001).mkv", "source": "ui"}).json()
    script = client.post(
        "/api/v1/analyze", json={"path": f"{base}/Beta (2002).mkv", "source": "manual"}
    ).json()
    player = client.post("/api/v1/analyze", json={"path": f"{base}/Gamma (2003).mkv"}).json()
    assert (ui["status"], script["status"], player["status"]) == ("queued",) * 3
    assert store.get(f"{base}/Alpha (2001).mkv")["priority"] == PRIORITY_HIGHEST
    assert store.get(f"{base}/Beta (2002).mkv")["priority"] == PRIORITY_HIGH
    assert store.get(f"{base}/Gamma (2003).mkv")["priority"] == PRIORITY_PLAYER
    body = client.get("/api/v1/up-next").json()
    assert [r["path"].split("/")[-1] for r in body["express"]] == ["Gamma (2003).mkv"]
    assert [(r["tier"], r["spot"]) for r in body["items"]] == [(1, 1), (2, 2), (3, 3)]


def test_place_moves_below_a_title_into_its_tier_or_to_a_tier_and_rejects_bad_requests(world):
    client, store, sched, base = world
    alpha, beta, gamma, dunkirk = (
        f"{base}/{n}.mkv" for n in ("Alpha (2001)", "Beta (2002)", "Gamma (2003)", "Dunkirk (2017)")
    )
    assert client.post("/api/v1/place", json={"paths": [gamma], "after": dunkirk}).json() == {
        "moved": 1,
        "tier": 3,
    }
    assert store.get(gamma)["priority"] == PRIORITY_MEDIUM
    assert client.post("/api/v1/place", json={"paths": [beta], "tier": 1}).json() == {"moved": 1, "tier": 1}
    assert client.post("/api/v1/place", json={"paths": [alpha], "tier": 2, "end": True}).json()["tier"] == 2
    names = [r["path"].split("/")[-1][:5] for r in client.get("/api/v1/up-next").json()["items"]]
    assert names == ["Beta ", "Alpha", "Dunki", "Gamma"]
    assert (
        client.post("/api/v1/place", json={"paths": [alpha], "after": f"{base}/nope.mkv"}).status_code == 409
    )
    assert client.post("/api/v1/place", json={"paths": [alpha], "tier": 9}).status_code == 422
    assert client.post("/api/v1/place", json={"paths": []}).status_code == 422


def test_title_search_shows_the_spot_of_a_waiting_title_and_the_date_version_and_pass_of_a_finished_one(
    world, monkeypatch
):
    client, store, sched, base = world
    found = client.get("/api/v1/files?q=Dunkirk").json()
    assert (found[0]["tier"], found[0]["tier_spot"], found[0]["spot"], found[0]["variable"]) == (
        3,
        1,
        1,
        True,
    )
    sched.runner = lambda p, t, s: RunResult(
        timeline={"file": {"quick_hash": "c" * 40}, "flags": [], "segments": [{}]}, version="0.3.15"
    )
    client.post(
        "/api/v1/analyze", json={"path": f"{base}/Alpha (2001).mkv", "source": "ui", "mode": "detailed"}
    )
    assert sched.run_one() is True
    done = client.get("/api/v1/files?q=Alpha").json()[0]
    assert done["status"] == "done" and done["analyzer_version"] == "0.3.15" and done["analyzed_at"]
    assert done["ran"] == {"pass": "detailed"} and done["duration_s"] is not None
    assert done["tier"] is None and done["spot"] is None
    assert client.get("/api/v1/queue").json()[0]["spot"] == 1
