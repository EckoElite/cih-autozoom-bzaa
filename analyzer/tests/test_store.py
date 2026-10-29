"""The SQLite store: queue order, change detection, migrations, lookups, overrides."""

import sqlite3

import pytest

from cih_analyzer.store import (
    MIGRATIONS,
    PRIORITY_BACKLOG,
    PRIORITY_DEMAND,
    PRIORITY_RECENT,
    Store,
    major_of,
)

V = "0.1.0"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    yield s
    s.close()


def add(store, path, size=100, mtime=1000.0, **kw):
    return store.seen(path, size, mtime, version=V, **kw)


def finish(store, path, timeline="{}", version=V, quick_hash="h" * 40):
    store.finish(
        path, timeline_json=timeline, ramp_raw_json=None, quick_hash=quick_hash, version=version,
        flags=[], segments=1, duration_s=1.0,
    )  # fmt: skip


def test_database_is_in_wal_mode_at_the_current_schema(store):
    assert store._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.schema_version == len(MIGRATIONS)


def test_new_files_are_queued_and_unchanged_files_are_left_alone(store):
    assert add(store, "/m/a.mkv") == "new"
    assert add(store, "/m/a.mkv") == "unchanged"
    assert store.counts() == {"queued": 1}


def test_queue_order_demand_then_recent_then_backlog_newest_first(store):
    add(store, "/m/old.mkv", mtime=100)
    add(store, "/m/new.mkv", mtime=900)
    add(store, "/m/mid.mkv", mtime=500)
    add(store, "/m/recent.mkv", mtime=50, priority=PRIORITY_RECENT)
    add(store, "/m/asked.mkv", mtime=10)
    store.requeue("/m/asked.mkv", PRIORITY_DEMAND)
    order = [store.claim_next()["path"] for _ in range(5)]
    assert order == ["/m/asked.mkv", "/m/recent.mkv", "/m/new.mkv", "/m/mid.mkv", "/m/old.mkv"]
    assert store.claim_next() is None


def test_claiming_marks_running_and_finishing_stores_the_timeline(store):
    add(store, "/m/a.mkv")
    job = store.claim_next(now=5.0)
    assert job["status"] == "running" and job["started_at"] == 5.0 and job["attempts"] == 1
    store.finish(
        "/m/a.mkv", timeline_json='{"x":1}', ramp_raw_json=None, quick_hash="h" * 40, version=V,
        flags=["var", "ramp"], segments=3, duration_s=2.5, now=9.0,
    )  # fmt: skip
    row = store.get("/m/a.mkv")
    assert (row["status"], row["timeline"], row["flags"], row["segments"]) == (
        "done",
        '{"x":1}',
        "var,ramp",
        3,
    )


def test_a_changed_file_loses_its_timeline_and_is_queued_again(store):
    add(store, "/m/a.mkv")
    store.claim_next()
    finish(store, "/m/a.mkv")
    assert add(store, "/m/a.mkv", size=200) == "changed"  # a new edition
    row = store.get("/m/a.mkv")
    assert row["status"] == "queued" and row["timeline"] is None and row["quick_hash"] is None
    assert add(store, "/m/a.mkv", size=200, mtime=2000.0) == "changed"  # same size, newer modification time


def test_a_major_version_bump_requeues_but_keeps_the_old_timeline_until_replaced(store):
    add(store, "/m/a.mkv")
    store.claim_next()
    finish(store, "/m/a.mkv", timeline='{"old":1}', version="0.1.0")
    assert store.seen("/m/a.mkv", 100, 1000.0, version="0.1.9") == "unchanged"  # a patch release
    assert store.seen("/m/a.mkv", 100, 1000.0, version="0.2.0") == "reanalyze"
    row = store.get("/m/a.mkv")
    assert row["status"] == "queued" and row["timeline"] == '{"old":1}'


def test_major_of():
    versions = ("1.4.2", "0.3.1", "0.3.9", "2.0.0", "", None, "x")
    assert [major_of(v) for v in versions] == ["1", "0.3", "0.3", "2", "", "", ""]


def test_jobs_running_at_shutdown_go_back_in_the_queue(store):
    add(store, "/m/a.mkv")
    store.claim_next()
    assert store.requeue_running() == 1
    assert store.get("/m/a.mkv")["status"] == "queued"


def test_a_failure_is_recorded_and_the_file_can_be_requeued(store):
    add(store, "/m/a.mkv")
    store.claim_next()
    store.fail("/m/a.mkv", "ffmpeg: Invalid data")
    assert store.get("/m/a.mkv")["error"] == "ffmpeg: Invalid data" and store.counts() == {"error": 1}
    assert store.requeue("/m/a.mkv") is True and store.get("/m/a.mkv")["error"] is None
    assert store.requeue("/m/unknown.mkv") is False


def test_requeue_of_a_running_file_is_refused_and_of_a_queued_one_raises_its_priority(store):
    add(store, "/m/a.mkv")
    add(store, "/m/b.mkv")
    store.claim_next()  # one of them is now running
    running = store.queue()[0]
    assert running["status"] == "running" and store.requeue(running["path"]) is False
    waiting = next(r for r in store.queue() if r["status"] == "queued")
    assert store.requeue(waiting["path"], PRIORITY_DEMAND) is True
    assert store.get(waiting["path"])["priority"] == PRIORITY_DEMAND


def test_restart_resets_even_a_running_job(store):
    add(store, "/m/a.mkv")
    store.claim_next()
    store.restart("/m/a.mkv", 300, 3000.0, PRIORITY_DEMAND)
    row = store.get("/m/a.mkv")
    assert (row["status"], row["size"], row["priority"]) == ("queued", 300, PRIORITY_DEMAND)


def test_missing_files_are_marked_and_come_back(store):
    for name in ("a", "b"):
        add(store, f"/m/{name}.mkv")
        store.claim_next()
        finish(store, f"/m/{name}.mkv")
    assert store.mark_missing(["/m"], {"/m/a.mkv"}) == 1
    assert store.get("/m/b.mkv")["status"] == "missing" and store.get("/m/b.mkv")["timeline"] == "{}"
    assert add(store, "/m/b.mkv") == "returned" and store.get("/m/b.mkv")["status"] == "done"
    assert store.mark_missing(["/other"], set()) == 0  # only files under the scanned root


def test_plex_ids_lookup_and_recent_boost(store):
    add(store, "/m/a.mkv", mtime=10)
    add(store, "/m/b.mkv", mtime=20)
    assert store.set_plex("/m/a.mkv", part_id=551, rating_key=42, title="A", added_at=1_000_000.0) is True
    assert store.set_plex("/m/nope.mkv", part_id=1, rating_key=1, title="x", added_at=None) is False
    assert store.boost_recent(900_000.0) == 1
    assert store.get("/m/a.mkv")["priority"] == PRIORITY_RECENT
    assert store.get("/m/b.mkv")["priority"] == PRIORITY_BACKLOG
    assert store.find(part_id=551)["path"] == "/m/a.mkv"
    assert store.find(rating_key=42)["path"] == "/m/a.mkv"
    assert store.find(path="/m/b.mkv")["path"] == "/m/b.mkv" and store.find(part_id=999) is None


def test_lookup_by_hash(store):
    add(store, "/m/a.mkv")
    store.claim_next()
    finish(store, "/m/a.mkv", quick_hash="c" * 40)
    assert store.find(quick_hash="c" * 40)["path"] == "/m/a.mkv"


def test_search_matches_path_and_title_and_escapes_wildcards(store):
    add(store, "/m/Alien (1979).mkv")
    add(store, "/m/100%_real.mkv")
    store.set_plex("/m/Alien (1979).mkv", part_id=1, rating_key=1, title="Alien", added_at=None)
    assert [r["path"] for r in store.search("alien")] == ["/m/Alien (1979).mkv"]
    assert [r["path"] for r in store.search("100%_")] == ["/m/100%_real.mkv"]
    assert [r["path"] for r in store.search("%")] == [
        "/m/100%_real.mkv"
    ]  # a literal percent sign, not a wildcard


def test_skipped_files_keep_their_reason(store):
    store.mark_skipped("/m/disc.iso", "iso", 5, 6.0)
    row = store.get("/m/disc.iso")
    assert (row["status"], row["skip_reason"]) == ("skipped", "iso") and store.claim_next() is None


def test_overrides_persist_and_validate(store):
    store.set_override("/m/a.mkv", "preset", 2.35)
    assert store.get_override("/m/a.mkv")["preset"] == 2.35
    store.set_override("/m/a.mkv", "disable")
    assert (
        store.get_override("/m/a.mkv")["mode"] == "disable"
        and store.get_override("/m/a.mkv")["preset"] is None
    )
    with pytest.raises(ValueError):
        store.set_override("/m/a.mkv", "preset")
    with pytest.raises(ValueError):
        store.set_override("/m/a.mkv", "zoom", 1.0)
    assert store.clear_override("/m/a.mkv") is True and store.get_override("/m/a.mkv") is None
    assert store.clear_override("/m/a.mkv") is False


def test_meta_round_trip(store):
    assert store.get_meta("k") is None
    store.set_meta("k", "v1")
    store.set_meta("k", "v2")
    assert store.get_meta("k") == "v2"


def test_existing_data_survives_a_schema_upgrade(tmp_path):
    path = tmp_path / "up.sqlite"
    first = Store(path)  # an older container's database
    first.seen("/m/a.mkv", 1, 1.0, version=V)
    first.set_override("/m/a.mkv", "disable")
    first.close()
    upgrade = MIGRATIONS + ("ALTER TABLE files ADD COLUMN note TEXT; CREATE INDEX files_note ON files(note)",)
    second = Store(path, upgrade)
    assert second.schema_version == 2
    assert second.get("/m/a.mkv")["status"] == "queued" and second.get("/m/a.mkv")["note"] is None
    assert second.get_override("/m/a.mkv")["mode"] == "disable"
    second.close()
    assert Store(path, upgrade).schema_version == 2  # reopening applies nothing twice


def test_a_failed_migration_leaves_the_old_version_intact(tmp_path):
    path = tmp_path / "bad.sqlite"
    Store(path).close()
    bad = MIGRATIONS + ("ALTER TABLE files ADD COLUMN ok TEXT; ALTER TABLE nope ADD COLUMN x TEXT",)
    with pytest.raises(sqlite3.OperationalError):
        Store(path, bad)
    check = Store(path)
    assert check.schema_version == 1
    assert "ok" not in {r[1] for r in check._db.execute("PRAGMA table_info(files)")}  # rolled back as a whole


def test_a_database_from_a_newer_analyzer_is_refused(tmp_path):
    path = tmp_path / "new.sqlite"
    Store(path, MIGRATIONS + ("CREATE TABLE extra (x)",)).close()
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)
