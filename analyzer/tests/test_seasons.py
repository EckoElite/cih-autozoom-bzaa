"""One analysed episode per TV season stands for the rest: which episode is queued, where it waits, and the
`season_hint` an unanalysed episode's timeline answer carries."""

import json

import pytest
from fastapi.testclient import TestClient

from cih_analyzer.api import create_app
from cih_analyzer.config import Settings
from cih_analyzer.pathmap import PathMap
from cih_analyzer.plex import PlexClient
from cih_analyzer.scheduler import RunResult, Scheduler
from cih_analyzer.season import ratio_shares, season_hint
from cih_analyzer.store import PRIORITY_LOW, Store

NOW = 1_800_000_000.0


def timeline(*pieces):
    """A timeline from (seconds, preset, aspect) pieces laid end to end."""
    segments, t = [], 0.0
    for seconds, preset, aspect in pieces:
        segments.append({"start": t, "end": t + seconds, "aspect": aspect, "preset": preset})
        t += seconds
    return {"segments": segments}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    yield s
    s.close()


def episode(store, name, season_key, season, number, *, status="idle", sample=0, recent=0, finished=None):
    """A TV file the scanner has seen (on demand: idle) with the season Plex gave it."""
    path = f"/media/tv/Show/{name}.mkv"
    store.seen(path, 10, 1.0, version="0.3.20", on_demand=True, now=NOW)
    store.set_plex(
        path,
        part_id=season_key * 100 + number,
        rating_key=season_key * 100 + number,
        title=name,
        added_at=None,
        season_key=season_key,
        season=season,
        episode=number,
    )
    if status == "done":
        store.finish(
            path, timeline_json=json.dumps(finished or timeline((100, 1.78, 1.777))), ramp_raw_json=None,
            quick_hash="a" * 40, version="0.3.20", flags=[], segments=1, duration_s=5.0,
        )  # fmt: skip
    elif status != "idle":
        store._run("UPDATE files SET status=?, season_sample=? WHERE path=?", (status, sample, path))
    if recent:
        store._run("UPDATE files SET status='queued', recent=1 WHERE path=?", (path,))
    return path


def status_of(store, path):
    return store.get(path)["status"]


def test_the_lowest_numbered_regular_episode_of_each_season_is_queued(store):
    e3 = episode(store, "S01E03", 10, 1, 3)
    e1 = episode(store, "S01E01", 10, 1, 1)
    e2 = episode(store, "S01E02", 10, 1, 2)
    other = episode(store, "S02E05", 11, 2, 5)
    done = store.plan_season_samples(now=NOW)
    assert done == {"seasons": 2, "queued": 2}
    assert [status_of(store, p) for p in (e1, e2, e3, other)] == ["queued", "idle", "idle", "queued"]
    row = store.get(e1)
    assert row["priority"] == PRIORITY_LOW and row["season_sample"] == 1


def test_specials_and_episode_zero_are_not_samples(store):
    special = episode(store, "S00E01", 20, 0, 1)
    zero = episode(store, "S01E00", 21, 1, 0)
    first = episode(store, "S01E01", 21, 1, 1)
    assert store.plan_season_samples(now=NOW) == {"seasons": 1, "queued": 1}
    assert status_of(store, special) == "idle" and status_of(store, zero) == "idle"
    assert status_of(store, first) == "queued"


def test_planning_twice_queues_nothing_more(store):
    episode(store, "S01E01", 10, 1, 1)
    episode(store, "S01E02", 10, 1, 2)
    assert store.plan_season_samples(now=NOW)["queued"] == 1
    assert store.plan_season_samples(now=NOW)["queued"] == 0


def test_a_season_with_a_finished_episode_is_left_alone(store):
    episode(store, "S01E04", 10, 1, 4, status="done")
    e1 = episode(store, "S01E01", 10, 1, 1)
    assert store.plan_season_samples(now=NOW)["queued"] == 0
    assert status_of(store, e1) == "idle"


def test_an_episode_that_errors_or_is_skipped_hands_over_to_the_next(store):
    e1 = episode(store, "S01E01", 10, 1, 1)
    e2 = episode(store, "S01E02", 10, 1, 2)
    e3 = episode(store, "S01E03", 10, 1, 3)
    store.plan_season_samples(now=NOW)
    store.claim_next()
    store.fail(e1, "broken")
    assert store.plan_season_samples(now=NOW)["queued"] == 1 and status_of(store, e2) == "queued"
    store.claim_next()
    store.skip(e2, "broken: file is incomplete")
    assert store.plan_season_samples(now=NOW)["queued"] == 1 and status_of(store, e3) == "queued"
    assert store.get(e1)["season_sample"] == 0


def test_an_episode_already_in_the_backlog_is_marked_and_moved_up(store):
    e1 = episode(store, "S01E01", 10, 1, 1, status="queued")  # background analysis: queued, not a sample
    e2 = episode(store, "S01E02", 10, 1, 2, status="queued")
    store.plan_season_samples(now=NOW)
    assert store.get(e1)["season_sample"] == 1 and store.get(e2)["season_sample"] == 0
    assert store.claim_next()["path"] == e1


def test_samples_come_after_recently_added_and_before_the_backlog(store):
    backlog = episode(store, "S09E01", 90, 9, 1, status="queued")
    recent = episode(store, "S08E01", 80, 8, 1, recent=1)
    sample = episode(store, "S01E01", 10, 1, 1)
    store.plan_season_samples(now=NOW)
    order = [store.claim_next()["path"] for _ in range(3)]
    assert order == [recent, sample, backlog]


def test_a_rescan_does_not_send_a_queued_sample_back_to_waiting(store):
    e1 = episode(store, "S01E01", 10, 1, 1)
    store.plan_season_samples(now=NOW)
    again = store.seen(e1, 10, 1.0, version="0.3.20", on_demand=True, now=NOW + 1)
    assert again == "unchanged" and status_of(store, e1) == "queued"


def test_progress_counts_seasons_by_state(store):
    episode(store, "S01E01", 10, 1, 1, status="done")  # sampled
    episode(store, "S02E01", 11, 2, 1)  # will be queued
    episode(store, "S03E01", 12, 3, 1)
    gone = episode(store, "S04E01", 13, 4, 1)
    store.skip(gone, "3D")
    episode(store, "S00E01", 14, 0, 1)  # specials are not counted
    store.plan_season_samples(now=NOW)
    assert store.season_progress() == {
        "seasons": 4, "sampled": 1, "pending": 2, "unreachable": 1, "not_queued": 0,
    }  # fmt: skip


def test_the_sample_standing_for_a_season_is_its_lowest_numbered_finished_episode(store):
    episode(store, "S01E05", 10, 1, 5, status="done")
    e2 = episode(store, "S01E02", 10, 1, 2, status="done")
    target = episode(store, "S01E09", 10, 1, 9)
    row = store.season_sample_for(10, target)
    assert row["path"] == e2 and row["episode"] == 2
    assert store.season_sample_for(10, e2)["episode"] == 5  # not itself


# ---- the hint ----------------------------------------------------------------------------------------------------


def test_ratios_are_counted_by_share_of_running_time_longest_first():
    ratios = ratio_shares(timeline((60, 2.39, 2.388), (30, 1.78, 1.777), (10, 2.39, 2.392)))
    assert [(r["preset"], r["share"]) for r in ratios] == [(2.39, 0.7), (1.78, 0.3)]
    assert ratios[0]["aspect"] == pytest.approx(2.389, abs=0.001)


def test_a_picture_with_no_preset_is_told_apart_by_its_aspect():
    ratios = ratio_shares(timeline((50, None, 2.1), (50, None, 2.104), (10, None, 1.5)))
    assert [(r["preset"], r["aspect"], r["share"]) for r in ratios] == [
        (None, 2.102, 0.909),
        (None, 1.5, 0.091),
    ]


def test_one_ratio_is_not_variable_and_a_second_big_enough_one_makes_it_so():
    one = season_hint({"timeline": json.dumps(timeline((990, 1.78, 1.777), (10, 2.39, 2.39))), "rating_key": 7,
                       "season": 2, "episode": 1})  # fmt: skip
    assert one["preset"] == 1.78 and one["variable"] is False
    assert one["from"] == {"rating_key": 7, "episode": 1, "season": 2}
    two = season_hint({"timeline": json.dumps(timeline((90, 1.78, 1.777), (10, 2.39, 2.39))), "rating_key": 7,
                       "season": 2, "episode": 1})  # fmt: skip
    assert two["variable"] is True and [r["preset"] for r in two["ratios"]] == [1.78, 2.39]


def test_a_timeline_with_nothing_in_it_gives_no_hint():
    assert season_hint({"timeline": json.dumps({"segments": []})}) is None
    assert season_hint({"timeline": "not json"}) is None


# ---- Plex and the API --------------------------------------------------------------------------------------------


def test_plex_gives_an_episode_its_season_and_numbers():
    item = {
        "ratingKey": "500", "title": "Pilot", "type": "episode", "grandparentTitle": "Show", "parentIndex": 2,
        "index": 4, "parentRatingKey": "77", "addedAt": 1,
        "Media": [{"Part": [{"id": 9, "file": "/tv/Show/S02E04.mkv", "size": 10}]}],
    }  # fmt: skip
    (part,) = list(PlexClient._parts(item))
    assert (part.season_key, part.season, part.episode) == (77, 2, 4)
    movie = {
        "ratingKey": "1",
        "title": "F",
        "type": "movie",
        "Media": [{"Part": [{"id": 2, "file": "/m/f.mkv"}]}],
    }
    (film,) = list(PlexClient._parts(movie))
    assert (film.season_key, film.season, film.episode) == (None, None, None)


@pytest.fixture
def api(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    base = str(root).replace("\\", "/")
    settings = Settings(config_dir=tmp_path, media_roots=(base,))
    store = Store(tmp_path / "s.sqlite")
    pathmap = PathMap([[base, "/tv"]])
    sched = Scheduler(settings, store, pathmap, runner=lambda p, t, s: RunResult(timeline={}))
    yield TestClient(create_app(settings, store, sched, pathmap)), store, base
    store.close()


def add(store, base, number, *, season_key=300, season=1, status="idle", tl=None):
    path = f"{base}/Show S01E{number:02d}.mkv"
    store.seen(path, 10, 1.0, version="0.3.20", on_demand=True, now=NOW)
    store.set_plex(path, part_id=900 + number, rating_key=800 + number, title=path, added_at=None,
                   season_key=season_key, season=season, episode=number)  # fmt: skip
    if status == "done":
        store.finish(path, timeline_json=json.dumps(tl or timeline((100, 1.78, 1.777))), ramp_raw_json=None,
                     quick_hash="c" * 40, version="0.3.20", flags=[], segments=1, duration_s=5.0)  # fmt: skip
    return path


def test_an_unanalysed_episode_gets_its_seasons_ratios_in_the_404(api):
    client, store, base = api
    add(store, base, 1, status="done", tl=timeline((80, 2.0, 2.0), (20, 1.78, 1.777)))
    add(store, base, 2)
    for query in ("rating_key=802", "part_id=902"):
        r = client.get(f"/api/v1/timeline?{query}")
        assert r.status_code == 404
        hint = r.json()["season_hint"]
        assert hint["preset"] == 2.0 and hint["variable"] is True
        assert hint["from"] == {"rating_key": 801, "episode": 1, "season": 1}
        assert [(x["preset"], x["share"]) for x in hint["ratios"]] == [(2.0, 0.8), (1.78, 0.2)]


def test_no_hint_without_a_finished_sample_for_a_special_or_for_a_title_of_its_own_timeline(api):
    client, store, base = api
    add(store, base, 2)
    assert "season_hint" not in client.get("/api/v1/timeline?rating_key=802").json()  # nothing analysed yet
    add(store, base, 5, season_key=301, season=0, status="done")
    add(store, base, 6, season_key=301, season=0)
    assert "season_hint" not in client.get("/api/v1/timeline?rating_key=806").json()  # season 0
    add(store, base, 1, status="done")
    own = client.get("/api/v1/timeline?rating_key=801")
    assert own.status_code == 200 and "season_hint" not in own.json()  # its own timeline wins


def test_the_status_reports_how_many_seasons_have_a_sample(api):
    client, store, base = api
    add(store, base, 1, status="done")
    add(store, base, 2)
    add(store, base, 3, season_key=301, season=2)
    store.plan_season_samples(now=NOW)
    seasons = client.get("/api/v1/status").json()["seasons"]
    assert seasons == {"seasons": 2, "sampled": 1, "pending": 1, "unreachable": 0, "not_queued": 0}


def test_a_plex_sync_stores_the_seasons_and_queues_one_sample_each(store):
    from cih_analyzer.plex import PlexPart, Section
    from cih_analyzer.scanner import sync_plex

    for name in ("S01E01", "S01E02", "S02E01", "S00E01"):
        store.seen(f"/media/tv/Show/{name}.mkv", 10, 1.0, version="0.3.20", on_demand=True, now=NOW)

    class Plex:
        def sections(self):
            return [Section("1", "TV", "show")]

        def parts(self, section):
            for i, (name, key, season, number) in enumerate(
                [("S01E01", 10, 1, 1), ("S01E02", 10, 1, 2), ("S02E01", 11, 2, 1), ("S00E01", 12, 0, 1)]
            ):
                yield PlexPart(
                    f"/media/tv/Show/{name}.mkv", 10, 100 + i, 200 + i, name, None, key, season, number
                )

    summary = sync_plex(Plex(), store, PathMap(), now=NOW)
    assert (summary.seasons, summary.season_samples_queued) == (2, 2)
    queued = {r["path"] for r in store.queue()}
    assert queued == {"/media/tv/Show/S01E01.mkv", "/media/tv/Show/S02E01.mkv"}
    assert sync_plex(Plex(), store, PathMap(), now=NOW + 900).season_samples_queued == 0
