"""The HTTP API and status page, through FastAPI's test client."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from cih_analyzer.api import create_app
from cih_analyzer.config import Settings
from cih_analyzer.pathmap import PathMap
from cih_analyzer.scheduler import RunResult, Scheduler
from cih_analyzer.store import PRIORITY_DEMAND, Store

HASH = "b" * 40
TIMELINE = {
    "schema": 1,
    "video": {"duration": 100.0},
    "segments": [{"start": 0, "end": 40, "aspect": 2.39, "boundary": {"kind": "cut", "frame": 0}}],
    "ramps": [
        {
            "start_frame": 960,
            "end_frame": 962,
            "confidence": 0.9,
            "frames": [
                {
                    "f": f,
                    "t": f / 24,
                    "top": 100 - 10 * i,
                    "bottom": 200 + 10 * i,
                    "left": 0,
                    "right": 640,
                    "readable": True,
                }
                for i, f in enumerate((960, 961, 962))
            ],
        },
    ],
    "flags": ["var", "ramp"],
    "file": {"quick_hash": HASH},
}
RAW = [
    {
        "start_frame": 960,
        "frames": [
            {
                "f": 960,
                "t": 40.0,
                "raw": {"top": 101, "bottom": 199, "left": 0, "right": 640},
                "readable": True,
            }
        ],
    }
]


def build(tmp_path, api_key="", media=True):
    root = tmp_path / "media"
    root.mkdir()
    film = root / "Film.mkv"
    film.write_bytes(b"x" * 10)
    base = str(root).replace("\\", "/")
    settings = Settings(config_dir=tmp_path, media_roots=(base,), api_key=api_key)
    store = Store(tmp_path / "s.sqlite")
    pathmap = PathMap([[base, "/movies"]])
    sched = Scheduler(settings, store, pathmap, runner=lambda p, t, s: RunResult(timeline=TIMELINE))
    sched.scan_now()
    local = f"{base}/Film.mkv"
    store.set_plex(local, part_id=551, rating_key=42, title="Film (2020)", added_at=None)
    store.claim_next()
    store.finish(local, timeline_json=json.dumps(TIMELINE), ramp_raw_json=json.dumps(RAW), quick_hash=HASH,
                 version="0.1.0", flags=["var", "ramp"], segments=1, duration_s=3.0)  # fmt: skip
    return TestClient(create_app(settings, store, sched, pathmap)), store, sched, root, local


@pytest.fixture
def api(tmp_path):
    client, store, sched, root, local = build(tmp_path)
    yield client, store, sched, root, local
    store.close()


def test_the_worker_count_is_set_and_reported_through_the_api(api):
    client, store, sched, *_ = api
    assert client.get("/api/v1/status").json()["settings"]["concurrency"] == 1
    assert client.post("/api/v1/concurrency", json={"workers": 5}).json() == {"workers": 5, "max_workers": 16}
    status = client.get("/api/v1/status").json()
    assert status["scheduler"]["workers"] == 5 and status["settings"]["concurrency"] == 5
    assert status["settings"]["concurrency_env"] == 1  # what the CONCURRENCY setting said
    assert store.get_meta("workers") == "5"
    for bad in (0, 17, "x"):
        assert client.post("/api/v1/concurrency", json={"workers": bad}).status_code == 422
    assert sched.workers == 5


def test_health_and_the_page_are_served(api):
    client, *_ = api
    body = client.get("/health").json()
    assert body["ok"] is True and body["schema"] == 1
    page = client.get("/")
    assert page.status_code == 200 and "CIH Analyzer" in page.text and "textContent" in page.text


def test_a_timeline_is_found_by_any_name_part_rating_key_or_hash(api):
    client, store, sched, root, local = api
    for query in (
        {"path": local}, {"path": "/movies/Film.mkv"}, {"part_id": 551}, {"rating_key": 42}, {"hash": HASH},
    ):  # fmt: skip
        r = client.get("/api/v1/timeline", params=query)
        assert r.status_code == 200, query
        assert r.json()["flags"] == ["var", "ramp"] and r.json()["override"] is None
        assert r.headers["X-Cih-Status"] == "done"


def test_a_missing_timeline_is_a_404_and_analyze_queues_it_at_demand_priority(api):
    client, store, sched, root, local = api
    other = root / "Other.mkv"
    other.write_bytes(b"y" * 5)
    sched.scan_now()
    other_local = str(other).replace("\\", "/")
    r = client.get("/api/v1/timeline", params={"path": other_local})
    assert r.status_code == 404 and r.json() == {"detail": "no timeline", "status": "queued", "queue": None}
    r = client.get("/api/v1/timeline", params={"path": other_local, "analyze": 1})
    assert r.status_code == 404 and r.json()["queue"] == "queued"
    assert store.get(other_local)["priority"] == PRIORITY_DEMAND


def test_a_new_file_on_a_media_root_is_picked_up_on_demand_but_other_paths_are_not(api):
    client, store, sched, root, local = api
    fresh = root / "Fresh.mkv"
    fresh.write_bytes(b"z" * 7)
    fresh_local = str(fresh).replace("\\", "/")
    r = client.get("/api/v1/timeline", params={"path": fresh_local, "analyze": 1})
    assert r.json()["queue"] == "queued" and store.get(fresh_local)["priority"] == PRIORITY_DEMAND
    outside = client.get("/api/v1/timeline", params={"path": "/etc/passwd", "analyze": 1}).json()
    assert outside["queue"] == "unknown" and store.get("/etc/passwd") is None
    sneaky = client.get(
        "/api/v1/timeline", params={"path": local + "/../../../etc/hosts", "analyze": 1}
    ).json()
    assert sneaky["queue"] == "unknown"


def test_a_file_that_changed_since_analysis_has_no_timeline_and_is_requeued(api):
    client, store, sched, root, local = api
    (root / "Film.mkv").write_bytes(b"a much longer replacement")
    r = client.get("/api/v1/timeline", params={"path": local, "analyze": 1})
    assert r.status_code == 404 and r.json()["status"] == "stale" and r.json()["queue"] == "queued"


def test_identifiers_are_required_and_validated(api):
    client, *_ = api
    assert client.get("/api/v1/timeline").status_code == 422
    assert client.get("/api/v1/timeline", params={"hash": "short"}).status_code == 422
    assert client.get("/api/v1/timeline", params={"part_id": 999}).status_code == 404


def test_overrides_are_persisted_and_returned_with_the_timeline(api):
    client, store, sched, root, local = api
    assert client.get("/api/v1/override", params={"path": "/movies/Film.mkv"}).json()["override"] is None
    r = client.post("/api/v1/override", json={"part_id": 551, "mode": "preset", "preset": 2.35})
    assert r.json()["override"] == {"mode": "preset", "preset": 2.35}
    assert client.get("/api/v1/timeline", params={"rating_key": 42}).json()["override"] == {
        "mode": "preset",
        "preset": 2.35,
    }
    client.post("/api/v1/override", json={"path": local, "mode": "disable"})
    assert client.get("/api/v1/override", params={"path": local}).json()["override"] == {
        "mode": "disable",
        "preset": None,
    }
    assert client.post("/api/v1/override", json={"path": local, "mode": "clear"}).json()["cleared"] is True
    assert client.get("/api/v1/timeline", params={"path": local}).json()["override"] is None


def test_override_input_is_validated(api):
    client, *_ = api
    assert client.post("/api/v1/override", json={"path": "/m/x", "mode": "zoom"}).status_code == 422
    assert client.post("/api/v1/override", json={"path": "/m/x", "mode": "preset"}).status_code == 422
    assert (
        client.post("/api/v1/override", json={"path": "/m/x", "mode": "preset", "preset": 99}).status_code
        == 422
    )
    assert client.post("/api/v1/override", json={"mode": "disable"}).status_code == 422
    assert client.post("/api/v1/override", json={"part_id": 12345, "mode": "disable"}).status_code == 404


def test_status_queue_errors_and_search(api):
    client, store, sched, root, local = api
    store.fail(local, "ffmpeg: boom")
    status = client.get("/api/v1/status").json()
    assert (
        status["counts"] == {"error": 1}
        and status["scheduler"]["workers"] == 1
        and status["plex"]["enabled"] is False
    )
    assert [r["error"] for r in client.get("/api/v1/errors").json()] == ["ffmpeg: boom"]
    assert client.get("/api/v1/queue").json() == []
    found = client.get("/api/v1/files", params={"q": "film (2020)"}).json()
    assert [r["path"] for r in found] == [local] and "timeline" not in found[
        0
    ]  # the list never carries bodies
    assert client.get("/api/v1/queue", params={"limit": 0}).status_code == 422


def test_variable_ratio_titles_are_listed(api):
    client, store, sched, root, local = api
    assert client.get("/api/v1/files", params={"var": 1}).json() == []  # one segment only
    store._run("UPDATE files SET segments = 3 WHERE path = ?", (local,))
    assert [r["path"] for r in client.get("/api/v1/files", params={"var": 1}).json()] == [local]


def test_ramp_endpoint_joins_the_fitted_curve_to_what_was_measured(api):
    client, store, sched, root, local = api
    ramp = client.get("/api/v1/ramp", params={"path": local}).json()["ramps"][0]
    assert (ramp["start_frame"], ramp["end_frame"], ramp["confidence"]) == (960, 962, 0.9)
    first, second = ramp["frames"][0], ramp["frames"][1]
    assert first["top"] == 100 and first["raw"]["top"] == 101  # fitted and measured side by side
    assert second["raw"] is None  # that frame was never measured (unreadable)
    assert client.get("/api/v1/ramp", params={"path": "/movies/none.mkv"}).status_code == 404


def test_pause_resume_scan_and_plex_controls(api):
    client, store, sched, root, local = api
    assert client.post("/api/v1/pause").json() == {"paused": "paused"}
    assert client.get("/api/v1/status").json()["scheduler"]["paused"] == "paused"
    assert client.post("/api/v1/resume").json() == {"paused": None}
    assert client.post("/api/v1/plex-sync").status_code == 409
    sched.last_scan = None
    assert client.post("/api/v1/scan").json() == {"status": "started"}
    deadline = time.time() + 5
    while sched.last_scan is None and time.time() < deadline:
        time.sleep(0.05)
    assert sched.last_scan is not None


def test_analyze_endpoint_reports_what_happened(api):
    client, store, sched, root, local = api
    assert client.post("/api/v1/analyze", json={"path": "/movies/Film.mkv"}).json() == {
        "path": local,
        "status": "queued",
    }
    assert client.post("/api/v1/analyze", json={"path": "/movies/nope.mkv"}).json()["status"] == "unknown"
    assert client.post("/api/v1/analyze", json={}).status_code == 422


def test_api_key_protects_the_api_but_not_health_or_the_page(tmp_path):
    client, store, *_ = build(tmp_path, api_key="s3cret")
    assert client.get("/health").status_code == 200 and client.get("/").status_code == 200
    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/status", headers={"X-Api-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/status", headers={"X-Api-Key": "s3cret"}).status_code == 200
    assert client.get("/api/v1/status", params={"key": "s3cret"}).status_code == 200
    assert client.post("/api/v1/pause").status_code == 401
    store.close()
