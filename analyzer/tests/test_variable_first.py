"""Titles on the known-variable list are queued ahead of recently added titles and the backlog."""

import os

from cih_analyzer.scanner import scan_media
from cih_analyzer.store import PRIORITY_BACKLOG, PRIORITY_DEMAND, PRIORITY_VARIABLE, Store

V = "0.3.13"


def film(root, name, mtime):
    folder = root / name
    folder.mkdir(parents=True)
    path = folder / f"{name}.mkv"
    path.write_bytes(b"x")
    os.utime(path, (mtime, mtime))
    return str(path).replace("\\", "/")


def test_a_listed_title_is_queued_ahead_of_newer_unlisted_ones(tmp_path):
    root = tmp_path / "movies"
    dunkirk = film(root, "Dunkirk (2017)", 100)  # on the bundled list
    plain = film(root, "Some Quiet Drama (2024)", 900)  # newer, not listed
    store = Store(tmp_path / "t.sqlite")
    summary = scan_media([str(root)], store, V)
    assert summary.new == 2
    assert store.get(dunkirk)["priority"] == PRIORITY_VARIABLE
    assert store.get(plain)["priority"] == PRIORITY_BACKLOG
    assert store.claim_next()["path"] == dunkirk  # the backlog alone would take the newer file first
    assert store.claim_next()["path"] == plain


def test_a_title_added_to_the_extra_list_is_moved_up_at_the_next_scan_and_a_hand_request_still_wins(tmp_path):
    root = tmp_path / "movies"
    quiet = film(root, "Some Quiet Drama (2024)", 900)
    other = film(root, "Another Film (2023)", 800)
    store = Store(tmp_path / "t.sqlite")
    scan_media([str(root)], store, V)
    assert store.get(quiet)["priority"] == PRIORITY_BACKLOG
    extra = tmp_path / "known.txt"
    extra.write_text("# mine\nSome Quiet Drama (2024)\n", encoding="utf-8")
    summary = scan_media([str(root)], store, V, known_variable_file=str(extra))
    assert summary.raised == 1 and summary.new == 0
    assert store.get(quiet)["priority"] == PRIORITY_VARIABLE
    assert store.get(other)["priority"] == PRIORITY_BACKLOG
    store.requeue(other, PRIORITY_DEMAND)
    assert [store.claim_next()["path"] for _ in range(2)] == [other, quiet]
    assert (
        scan_media([str(root)], store, V, known_variable_file=str(extra)).raised == 0
    )  # nothing left to raise
