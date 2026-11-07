"""The dense pass: every change followed, however short, and the scheduler rules that decide who gets it.

Synthetic video at 25 fps with keyframes every 2 s, so a shot of half a second has no keyframe of its own and the
first pass cannot see it; the dense pass reads every reference frame and finds it on its exact frames.
"""

import json

import pytest

from cih_analyzer import scheduler as scheduler_module
from cih_analyzer.analyze import analyze_file, build_timeline
from cih_analyzer.coarse import coarse_scan
from cih_analyzer.config import Settings
from cih_analyzer.dense import TRIGGER_CHANGES, dense_pass, result_from_timeline
from cih_analyzer.geometry import picture_zoom, same_zoom
from cih_analyzer.pathmap import PathMap
from cih_analyzer.probe import probe_video
from cih_analyzer.refine import refine
from cih_analyzer.scheduler import RunResult, Scheduler
from cih_analyzer.segmentation import SegmentationParams, segment_samples
from cih_analyzer.store import PRIORITY_HIGH, PRIORITY_MEDIUM, Store

from .conftest import NEEDS_FFMPEG
from .helpers import make_info
from .synth import Seg, make_video, rect_for_ratio
from .test_refine import opening, smoothstep

W, H, FPS = 640, 360, 25
SCOPE = rect_for_ratio(2.39, W, H)
WIDE = rect_for_ratio(16 / 9, W, H)
IMAX = rect_for_ratio(1.90, W, H)


def first_pass(path):
    info = probe_video(path)
    coarse = segment_samples(coarse_scan(path, info), info, SegmentationParams())
    return info, refine(path, info, coarse)


def video(tmp_path, name, plan, **kw):
    """`plan`: (frames, rect, level) in order; 25 fps, keyframes every 2 s."""
    segs = [Seg(frames / FPS, rect, level=level) for frames, rect, level in plan]
    return make_video(tmp_path / name, segs, keyint=50, **kw)


@NEEDS_FFMPEG
class TestDensePass:
    def test_a_shot_with_no_keyframe_of_its_own_is_found_on_its_exact_frames(self, tmp_path):
        # 33.2 s of scope, a 0.6 s shot of 16:9 between two keyframes (33.2 to 33.8), then scope again
        path = video(tmp_path, "flash.mp4", [(830, SCOPE, 110), (15, WIDE, 150), (655, SCOPE, 110)])
        info, base = first_pass(path)
        assert len(base.segments) == 1  # the first pass never saw it
        result = dense_pass(path, info, base)
        assert [s.rect for s in result.segments] == [SCOPE, WIDE, SCOPE]
        assert [(b.kind, b.frame) for b in result.boundaries] == [("cut", 830), ("cut", 845)]
        assert [round(s.start, 2) for s in result.segments] == [0.0, 33.2, 33.8]
        assert "dense" in result.flags and "var" in result.flags

    def test_a_shot_shorter_than_four_frames_is_not_followed(self, tmp_path):
        path = video(tmp_path, "tiny.mp4", [(830, SCOPE, 110), (3, WIDE, 150), (667, SCOPE, 110)])
        info, base = first_pass(path)
        result = dense_pass(path, info, base)
        assert [s.rect for s in result.segments] == [SCOPE]

    def test_a_short_smaller_picture_with_no_scene_change_at_its_edges_is_not_followed(self, tmp_path):
        # the same picture level either side: a matte that shrinks with nothing else changing is a misread, not a shot
        path = video(tmp_path, "samelevel.mp4", [(830, WIDE, 110), (15, SCOPE, 110), (655, WIDE, 110)])
        info, base = first_pass(path)
        result = dense_pass(path, info, base)
        assert [s.rect for s in result.segments] == [WIDE]

    def test_a_short_bigger_picture_is_followed_even_when_the_scene_cut_test_says_no(self, tmp_path):
        # in a fast montage the scene-cut test measures a change against its busy neighbourhood and misses real
        # cuts (Everything Everywhere at 7023 s: an 11-frame 1.85 shot between scope shots, scene_cut False). A
        # bigger picture with clean edges is followed: leaving it out puts light past the screen edges.
        path = video(tmp_path, "biglevel.mp4", [(830, SCOPE, 110), (15, WIDE, 110), (655, SCOPE, 110)])
        info, base = first_pass(path)
        result = dense_pass(path, info, base)
        assert [s.rect for s in result.segments] == [SCOPE, WIDE, SCOPE]
        assert [b.frame for b in result.boundaries] == [830, 845]

    def test_content_on_black_is_not_a_picture_to_follow(self, tmp_path):
        # an X-ray, a web page or credits text on black is a crop under half the frame; following it would enlarge a
        # window of content up to the full band (Everything Everywhere at 5122 s: a 1.4 s state at 2.7 times)
        window = (160, 110, 320, 140)
        path = video(tmp_path, "window.mp4", [(830, SCOPE, 110), (40, window, 200), (630, SCOPE, 110)])
        info, base = first_pass(path)
        result = dense_pass(path, info, base)
        assert [s.rect for s in result.segments] == [SCOPE]

    def test_a_brief_state_a_hair_different_from_the_picture_around_it_is_not_followed(self, tmp_path):
        # a picture 2.8% narrower than scope: within the 3% the player treats as the same zoom, even with a clean change at both
        # edges (a dim frame's crop flickering by a few rows)
        near = (9, SCOPE[1], W - 18, SCOPE[3])
        info = make_info(width=W, height=H)
        assert same_zoom(picture_zoom(near, info), picture_zoom(SCOPE, info))
        assert picture_zoom(near, info) >= picture_zoom(SCOPE, info)
        path = video(tmp_path, "hair.mp4", [(830, SCOPE, 110), (10, near, 150), (660, SCOPE, 110)])
        info, base = first_pass(path)
        result = dense_pass(path, info, base)
        assert [s.rect for s in result.segments] == [SCOPE]

    def test_a_dim_shot_that_looks_smaller_is_not_followed_but_a_dim_bigger_one_is(self, tmp_path):
        # dim frames can only make the picture look smaller, so a dim *bigger* picture is real and a dim smaller one is not
        smaller = video(tmp_path, "dimsmall.mp4", [(830, WIDE, 150), (15, SCOPE, 30), (655, WIDE, 150)])
        info, base = first_pass(smaller)
        assert [s.rect for s in dense_pass(smaller, info, base).segments] == [WIDE]
        bigger = video(tmp_path, "dimbig.mp4", [(830, SCOPE, 150), (15, WIDE, 45), (655, SCOPE, 150)])
        info, base = first_pass(bigger)
        found = dense_pass(bigger, info, base)
        assert [s.rect for s in found.segments] == [SCOPE, WIDE, SCOPE]

    def test_rapid_alternation_is_followed_change_by_change(self, tmp_path):
        plan = [(100, SCOPE, 110)]
        for n in range(6):  # six shots of a second and a half each way, none with a keyframe of its own
            plan += [(14, WIDE, 150), (22, SCOPE, 110 + (n % 2) * 20)]
        plan += [(100, SCOPE, 110)]
        path = video(tmp_path, "montage.mp4", plan)
        info, base = first_pass(path)
        result = dense_pass(path, info, base)
        starts = [b.frame for b in result.boundaries]
        expected = []
        at = 100
        for _ in range(6):
            expected += [at, at + 14]
            at += 36
        assert starts == expected
        assert [s.rect for s in result.segments[1:3]] == [WIDE, SCOPE]

    def test_a_ramp_stays_as_the_first_pass_measured_it(self, tmp_path):
        ramp = opening(60, smoothstep)
        segs = [Seg(8, SCOPE, level=110), Seg(60 / FPS, ramp, level=110), Seg(8, WIDE, level=110)]
        path = make_video(tmp_path / "ramp.mp4", segs, keyint=50)
        info, base = first_pass(path)
        assert any(b.kind == "ramp" for b in base.boundaries)
        result = dense_pass(path, info, base)
        before = [b for b in base.boundaries if b.kind == "ramp"]
        after = [b for b in result.boundaries if b.kind == "ramp"]
        assert [len(b.ramp) for b in after] == [len(b.ramp) for b in before]
        assert [b.ramp[0].f for b in after] == [b.ramp[0].f for b in before]
        assert [s.rect for s in result.segments] == [s.rect for s in base.segments]

    def test_a_stretch_the_first_pass_got_wrong_is_corrected_from_the_frames(self, tmp_path):
        path = video(tmp_path, "constant.mp4", [(1500, SCOPE, 110)])
        info, base = first_pass(path)
        # a first pass that took the whole film for 16:9, as one wide probe once did
        wrong = base.segments[0].__class__(
            base.segments[0].start, base.segments[0].end, WIDE, 1.778, 16 / 9, 1.0, True, ()
        )
        from dataclasses import replace

        result = dense_pass(path, info, replace(base, segments=(wrong,)))
        assert [s.rect for s in result.segments] == [SCOPE]

    def test_the_stored_timeline_gives_the_same_first_pass_back(self, tmp_path):
        path = video(tmp_path, "round.mp4", [(200, SCOPE, 110), (200, IMAX, 150), (100, SCOPE, 110)])
        info, base = first_pass(path)
        timeline = build_timeline(str(path), info, base, SegmentationParams())
        again = result_from_timeline(timeline, info)
        assert [(s.start, s.end, s.rect) for s in again.segments] == [
            (s.start, s.end, s.rect) for s in base.segments
        ]
        assert [(b.kind, b.t, b.frame) for b in again.boundaries] == [
            (b.kind, b.t, b.frame) for b in base.boundaries
        ]


@NEEDS_FFMPEG
class TestWhenItRuns:
    def test_one_probe_in_a_short_shot_no_longer_stretches_the_whole_segment(self, tmp_path):
        # a minute of scope with 0.4 s of 16:9 at 30.04 s, where the middle one of the segment's three probes (30.0 to 30.4 s) lands
        path = video(tmp_path, "probe.mp4", [(751, SCOPE, 110), (10, WIDE, 150), (739, SCOPE, 110)])
        _, base = first_pass(path)
        assert [s.rect for s in base.segments] == [SCOPE]
        assert "mixed_probes" in base.flags

    def test_the_first_pass_asks_for_the_dense_pass_when_a_probe_saw_a_bigger_picture(self, tmp_path):
        path = video(tmp_path, "ask.mp4", [(751, SCOPE, 110), (10, WIDE, 150), (739, SCOPE, 110)])
        outcome = analyze_file(path)
        assert outcome.wants_dense is True and outcome.timeline is not None

    def test_a_plain_film_does_not_ask_for_it(self, tmp_path):
        path = video(tmp_path, "plain.mp4", [(500, SCOPE, 110)])
        assert analyze_file(path).wants_dense is False

    def test_running_dense_from_a_stored_result_does_not_read_the_file_for_the_first_pass(self, tmp_path):
        path = video(tmp_path, "stored.mp4", [(830, SCOPE, 110), (15, WIDE, 150), (655, SCOPE, 110)])
        first = analyze_file(path)
        base = {"timeline": json.loads(first.timeline.to_json()), "ramp_raw": list(first.ramp_raw)}
        dense = analyze_file(path, dense=True, base=base)
        assert "dense" in dense.timeline.flags
        assert [(round(s.start, 2), s.active.h) for s in dense.timeline.segments] == [
            (0.0, SCOPE[3]),
            (33.2, WIDE[3]),
            (33.8, SCOPE[3]),
        ]
        assert dense.wants_dense is False

    def test_dense_inline_after_a_first_pass_in_one_run(self, tmp_path):
        path = video(tmp_path, "inline.mp4", [(830, SCOPE, 110), (15, WIDE, 150), (655, SCOPE, 110)])
        outcome = analyze_file(path, dense=True)
        assert len(outcome.timeline.segments) == 3 and "dense" in outcome.timeline.flags


# ---- the scheduler's rules ----------------------------------------------------------------------------------------


def stored(path, segments, flags=()):
    return {
        "file": {"quick_hash": "c" * 40, "path": path},
        "flags": list(flags),
        "segments": [{} for _ in range(segments)],
    }


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    names = ["Many (2020).mkv", "Listed (2021).mkv", "Few (2022).mkv", "Done (2023).mkv"]
    for n in names:
        (root / n).write_bytes(b"x" * 10)
    base = str(root).replace("\\", "/")
    known = tmp_path / "known.txt"
    known.write_text("Listed (2021)\n", encoding="utf-8")
    store = Store(tmp_path / "s.sqlite")
    settings = Settings(config_dir=tmp_path, media_roots=(base,), known_variable_file=str(known))
    sched = Scheduler(settings, store, PathMap([[base, base]]))
    sched.scan_now()
    paths = {n: f"{base}/{n}" for n in names}
    yield store, sched, paths
    sched.stop(1)
    store.close()


def finish(store, path, segments, flags=()):
    store._run(
        "UPDATE files SET status='done', timeline=?, flags=?, segments=?, quick_hash=? WHERE path=?",
        (json.dumps(stored(path, segments, flags)), ",".join(flags), segments, "c" * 40, path),
    )


def test_finished_titles_with_many_changes_or_on_the_variable_list_are_queued_for_the_dense_pass(world):
    store, sched, paths = world
    for name, segs, flags in (
        ("Many (2020).mkv", TRIGGER_CHANGES + 1, ("var",)),
        ("Listed (2021).mkv", 4, ("var",)),
        ("Few (2022).mkv", TRIGGER_CHANGES, ("var",)),  # one segment short of TRIGGER_CHANGES changes
        ("Done (2023).mkv", 80, ("var", "dense")),  # already went through it
    ):
        finish(store, paths[name], segs, flags)
    assert sched.queue_dense() == 2
    many, listed = store.get(paths["Many (2020).mkv"]), store.get(paths["Listed (2021).mkv"])
    assert (many["status"], many["priority"]) == ("queued", PRIORITY_HIGH)
    assert (listed["status"], listed["priority"]) == ("queued", PRIORITY_MEDIUM)
    assert json.loads(many["options"]) == {"dense": True} == json.loads(listed["options"])
    assert store.get(paths["Few (2022).mkv"])["status"] == "done"
    assert store.get(paths["Done (2023).mkv"])["status"] == "done"
    assert sched.queue_dense() == 0  # they are queued now, not asked again


def test_a_dense_result_is_never_queued_for_the_dense_pass_again_even_when_it_failed(world):
    store, sched, paths = world
    finish(store, paths["Many (2020).mkv"], 90, ("dense_failed",))
    finish(store, paths["Listed (2021).mkv"], 90, ("dense_unreadable",))
    assert sched.queue_dense() == 0


def capture(monkeypatch, wants=False):
    seen = []

    def fake(path, timeout, stop, **kw):
        seen.append({"dense": kw.get("dense"), "base": kw.get("base")})
        return RunResult(timeline=stored(path, 3), wants_dense=wants)

    monkeypatch.setattr(scheduler_module, "subprocess_runner", fake)
    return seen


def test_a_first_pass_that_asks_for_it_queues_the_title_again_at_high_and_the_dense_run_starts_from_the_result(
    world, monkeypatch
):
    store, sched, paths = world
    path = paths["Few (2022).mkv"]
    seen = capture(monkeypatch, wants=True)
    for p in paths.values():
        if p != path:
            store._run("UPDATE files SET status='idle' WHERE path=?", (p,))
    assert sched.run_one() is True
    assert seen[-1]["dense"] is False  # an ordinary title: the first pass alone
    row = store.get(path)
    assert (row["status"], row["priority"]) == ("queued", PRIORITY_HIGH)
    assert json.loads(row["options"]) == {"dense": True}
    assert row["timeline"]  # the players keep what the first pass found meanwhile
    seen2 = capture(monkeypatch, wants=True)  # a dense run that also "wants" it must not loop
    assert sched.run_one() is True
    assert seen2[-1]["dense"] is True and seen2[-1]["base"] is not None
    row = store.get(path)
    assert row["status"] == "done" and json.loads(row["ran"]) == {"pass": "dense"}


def test_a_title_on_the_variable_list_goes_straight_through_the_dense_pass(world, monkeypatch):
    store, sched, paths = world
    seen = capture(monkeypatch)
    for p in paths.values():
        if "Listed" not in p:
            store._run("UPDATE files SET status='idle' WHERE path=?", (p,))
    assert sched.run_one() is True
    assert (
        seen[-1]["dense"] is True and seen[-1]["base"] is None
    )  # nothing stored yet: first pass and dense in one run
    assert json.loads(store.get(paths["Listed (2021).mkv"])["ran"]) == {"pass": "dense"}


def test_the_trigger_and_the_shortest_change_are_settings(world, monkeypatch):
    from cih_analyzer.dense import DenseParams, trigger_changes, wants_dense

    assert trigger_changes() == TRIGGER_CHANGES and DenseParams().min_frames == 4
    assert wants_dense(TRIGGER_CHANGES) and not wants_dense(TRIGGER_CHANGES - 1)
    monkeypatch.setenv("DENSE_TRIGGER", "5")
    monkeypatch.setenv("DENSE_MIN_FRAMES", "6")
    assert trigger_changes() == 5 and DenseParams().min_frames == 6 and wants_dense(5)
    monkeypatch.setenv("DENSE_TRIGGER", "0")  # off: only the variable list gets it
    assert not wants_dense(500)
    store, sched, paths = world
    finish(store, paths["Many (2020).mkv"], 300, ("var",))
    finish(store, paths["Listed (2021).mkv"], 4, ("var",))
    assert sched.queue_dense() == 1
    assert store.get(paths["Many (2020).mkv"])["status"] == "done"
    assert store.get(paths["Listed (2021).mkv"])["priority"] == PRIORITY_MEDIUM
    monkeypatch.setenv("DENSE_TRIGGER", "junk")  # not a number: the default
    assert trigger_changes() == TRIGGER_CHANGES


def test_a_lopsided_crop_on_an_axis_the_picture_spans_is_still_usable():
    # Everything Everywhere at 5826 s: a 1.85 shot with a dark side read as skew_x 1512 on a full-width crop, and every
    # sample of the shot was thrown away. A crop that grew into a matte on one side is still thrown away.
    from cih_analyzer.coarse import Sample
    from cih_analyzer.dense import _usable

    info = make_info()
    seg = SegmentationParams()
    dark_side = Sample(0.0, 0, 40, 3840, 2080, 32.0, skew=1512, skew_x=1512, skew_y=8)
    assert _usable(dark_side, info, seg)
    subtitle = Sample(0.0, 0, 272, 3840, 1700, 60.0, skew=84, skew_x=0, skew_y=84)
    assert not _usable(subtitle, info, seg)  # grew down into the bottom matte: the height is in doubt
    object_in_bar = Sample(0.0, 480, 0, 2880, 2160, 60.0, skew=300, skew_x=300, skew_y=0)
    assert not _usable(object_in_bar, info, seg)  # a pillarboxed picture with something in a side bar
