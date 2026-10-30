"""Provisional timelines: what a survey that has only just started can already tell a waiting player."""

import json

import pytest

from cih_analyzer.__main__ import main
from cih_analyzer.analyze import analyze_file
from cih_analyzer.coarse import Sample
from cih_analyzer.provisional import provisional_timeline
from cih_analyzer.survey import survey_scan

from .conftest import NEEDS_FFMPEG
from .helpers import make_info, sample
from .synth import Seg, make_video, rect_for_ratio

STEP = 10.0  # a 100 s file surveyed every 10 s: ten slots
SCOPE_H = 1606  # sample(…, 2.39) at 3840 wide
FULL = (0, 0, 3840, 2160)


def slot_samples(slot, aspect, **kw):
    """What one seek of the survey returns: two keyframes a second apart."""
    return [sample(slot * STEP + 5.0 + i, aspect, **kw) for i in range(2)]


def where(first=0, forward=10, wrapped=0, slots=10):
    return {"step": STEP, "slots": slots, "first": first, "forward": forward, "wrapped": wrapped}


def rects(timeline):
    return [
        (round(s["start"]), round(s["end"]), s["active"]["h"], s["measured"]) for s in timeline["segments"]
    ]


INFO = make_info(duration=100.0)


def test_a_steady_start_gives_the_measured_picture_for_the_stretch_and_the_whole_frame_elsewhere():
    seen = {k: slot_samples(k, 2.39) for k in (4, 5, 6)}  # a player at 40 s: three places surveyed so far
    found = provisional_timeline(INFO, seen, where(first=4, forward=3))
    assert found["provisional"] and found["coverage"] == [[40.0, 70.0]]
    assert rects(found) == [(0, 40, 2160, False), (40, 70, SCOPE_H + 16, True), (70, 100, 2160, False)]
    # the rectangle errs large (8 px each side at 4K), and the boundaries are frame numbers on the player's clock
    seg = found["segments"][1]["active"]
    assert (seg["x"], seg["y"], seg["w"], seg["h"]) == (0, 277 - 8, 3840, SCOPE_H + 16)
    assert [s["boundary"]["frame"] for s in found["segments"]] == [
        0,
        round(40 * INFO.fps),
        round(70 * INFO.fps),
    ]
    assert (
        found["video"]["width"] == 3840
        and found["video"]["duration"] == 100.0
        and found["flags"] == ["provisional"]
    )


def test_nothing_is_claimed_from_too_little_or_from_dark_samples():
    assert provisional_timeline(INFO, {}, where(forward=0)) is None
    one = {5: [sample(55.0, 2.39)]}
    assert (
        provisional_timeline(INFO, one, where(first=5, forward=1)) is None
    )  # one bright sample is not enough
    dark = {k: slot_samples(k, 2.39, yavg=30.0) for k in (5, 6, 7)}  # a dim cold open
    assert provisional_timeline(INFO, dark, where(first=5, forward=3)) is None
    tiny = {
        5: [Sample(55.0, 1000, 900, 1400, 300, 80.0), Sample(56.0, 1000, 900, 1400, 300, 80.0)]
    }  # credits
    assert provisional_timeline(INFO, tiny, where(first=5, forward=1)) is None


def test_the_stretch_ends_before_a_bigger_picture_and_a_dark_slot_does_not_end_it():
    seen = {4: slot_samples(4, 2.39), 5: slot_samples(5, 2.39, yavg=10.0), 6: slot_samples(6, 2.39)}
    seen[7] = slot_samples(
        7, 16 / 9
    )  # a full-frame shot: played at the zoom found so far it would be cut off
    found = provisional_timeline(INFO, seen, where(first=4, forward=4))
    assert found["coverage"] == [[40.0, 70.0]]  # through slot 6; the dark slot 5 is neither for nor against


def test_one_smaller_slot_is_tolerated_two_in_a_row_end_the_stretch():
    one = {k: slot_samples(k, 2.39) for k in (3, 4, 6, 7)}
    one[5] = [Sample(55.0 + i, 420, 330, 3000, 1500, 80.0) for i in range(2)]
    found = provisional_timeline(INFO, one, where(first=3, forward=5))
    assert found["coverage"] == [[30.0, 80.0]]
    two = dict(one)
    two[6] = [Sample(65.0 + i, 420, 330, 3000, 1500, 80.0) for i in range(2)]
    found = provisional_timeline(INFO, two, where(first=3, forward=5))
    # the first stretch ends before the first of the two; those two slots are a smaller picture of their own, and the
    # stretch after them has only one slot behind it, which is not enough to claim
    assert found["coverage"] == [[30.0, 50.0], [50.0, 70.0]]
    assert [r[2] for r in rects(found)] == [2160, SCOPE_H + 16, 1500 + 16, 2160]


def test_a_survey_that_has_wrapped_past_the_end_covers_both_ends():
    seen = {k: slot_samples(k, 2.39) for k in (8, 9, 0, 1)}
    found = provisional_timeline(INFO, seen, where(first=8, forward=2, wrapped=2))
    assert found["coverage"] == [[0.0, 20.0], [80.0, 100.0]]
    assert rects(found) == [(0, 20, SCOPE_H + 16, True), (20, 80, 2160, False), (80, 100, SCOPE_H + 16, True)]


def test_a_lopsided_bigger_sample_is_a_subtitle_not_a_new_picture():
    seen = {k: slot_samples(k, 2.39) for k in (4, 5)}
    base = sample(65.0, 2.39)
    seen[6] = [Sample(base.t, 0, base.y - 100, 3840, base.h + 200, 90.0, 100, 0, 100)] + slot_samples(
        6, 2.39
    )[1:]
    found = provisional_timeline(INFO, seen, where(first=4, forward=3))
    assert found["coverage"] == [[40.0, 70.0]]


@NEEDS_FFMPEG
class TestOnVideo:
    SCOPE = rect_for_ratio(2.39, 640, 360)

    def test_the_survey_starts_at_the_playhead_and_reports_early(self, tmp_path):
        path = make_video(tmp_path / "scope.mp4", [Seg(100, self.SCOPE)], keyint=25)
        from cih_analyzer.probe import probe_video

        info = probe_video(path)
        reports = []
        survey_scan(
            path,
            info,
            interval=5.0,
            start_at=60.0,
            on_batch=lambda by, w: reports.append((dict(w), sorted(by))),
        )
        first, details = reports[0]
        assert first["first"] == 12 and first["forward"] == 3 and details == [12, 13, 14]
        assert reports[-1][0]["forward"] + reports[-1][0]["wrapped"] == 20  # every place visited in the end

    def test_a_worker_reports_a_provisional_timeline_and_still_finishes_the_file(self, tmp_path):
        path = make_video(tmp_path / "scope.mp4", [Seg(100, self.SCOPE)], keyint=25)
        got = []
        outcome = analyze_file(path, survey_interval_s=5.0, start_at=40.0, on_partial=got.append)
        assert got and got[0]["provisional"] and got[0]["coverage"][0][0] == pytest.approx(40.0, abs=1.0)
        assert (
            outcome.timeline is not None and "surveyed" in outcome.timeline.flags
        )  # the final answer follows

    def test_a_listed_title_is_surveyed_for_the_player_but_still_gets_the_full_pass(self, tmp_path):
        folder = tmp_path / "Dunkirk (2017)"
        folder.mkdir()
        path = make_video(folder / "Dunkirk (2017).mp4", [Seg(100, self.SCOPE)], keyint=25)
        got = []
        outcome = analyze_file(path, survey_interval_s=5.0, start_at=0.0, on_partial=got.append)
        assert got  # the player had something to use
        assert "known_variable" in outcome.timeline.flags and "surveyed" not in outcome.timeline.flags

    def test_the_command_line_writes_the_partial_file(self, tmp_path):
        path = make_video(tmp_path / "scope.mp4", [Seg(100, self.SCOPE)], keyint=25)
        partial = tmp_path / "partial.json"
        args = ["--survey-interval", "5", "--start-at", "30", "--partial-file", str(partial)]
        assert main([*args, "--analyze", str(path)]) == 0
        data = json.loads(partial.read_text(encoding="utf-8"))
        assert data["provisional"] and data["segments"]
