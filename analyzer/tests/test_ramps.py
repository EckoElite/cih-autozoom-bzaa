"""Cut or ramp, exact frames and ramp fits, on hand-built per-frame edge measurements (no video involved)."""

import random

import pytest

from cih_analyzer.frames import Edges, ScanFrame
from cih_analyzer.ramps import RampParams, analyze_transition, isotonic_increasing, reference_edges

FPS = 25.0
OLD = Edges(top=46, bottom=314, left=0, right=640)  # 2.39 in a 640x360 frame
NEW = Edges(top=0, bottom=360, left=0, right=640)  # 16:9


def frames_for(tops, bottoms=None, *, diffs=None, fps=FPS):
    """ScanFrames from per-frame top and bottom edges (None = unreadable); the sides stay at the full width."""
    bottoms = bottoms if bottoms is not None else [None if t is None else 360 - t for t in tops]
    out = []
    for i, (top, bottom) in enumerate(zip(tops, bottoms, strict=True)):
        edges = None if top is None else Edges(top=top, bottom=bottom, left=0, right=640)
        out.append(ScanFrame(i / fps, edges, None if diffs is None else diffs[i]))
    return out


def linear(n, a=46, b=0):
    return [round(a + (b - a) * (k + 1) / (n + 1)) for k in range(n)]


def test_isotonic_regression_pools_violators():
    assert isotonic_increasing([1, 2, 3]) == [1, 2, 3]
    assert isotonic_increasing([3, 1]) == [2, 2]
    assert isotonic_increasing([1, 3, 2, 4]) == [1, 2.5, 2.5, 4]
    assert isotonic_increasing([0, 0, 0.5, 0, 1, 1]) == [0, 0, 0.25, 0.25, 1, 1]


def test_a_hard_cut_lands_on_the_exact_frame():
    frames = frames_for([46] * 50 + [0] * 50)
    result = analyze_transition(frames, OLD, NEW, FPS)
    assert (result.kind, result.first_new, result.flags) == ("cut", 50, ())


def test_a_linear_ramp_has_exact_start_and_end_frames():
    frames = frames_for([46] * 40 + linear(30) + [0] * 40)
    result = analyze_transition(frames, OLD, NEW, FPS)
    assert result.kind == "ramp"
    assert (result.ramp_start, result.first_new) == (40, 70)
    assert len(result.ramp) == 30 and result.ramp[0].f == 40 and result.ramp[-1].f == 69
    assert result.confidence == 1.0 and result.flags == ()


def test_fitted_ramp_is_strictly_monotone_and_within_two_pixels_of_every_measurement():
    rng = random.Random(7)
    truth = linear(40)
    noisy = [t + rng.choice((-1, 0, 0, 1)) for t in truth]
    result = analyze_transition(frames_for([46] * 40 + noisy + [0] * 40), OLD, NEW, FPS)
    assert result.kind == "ramp"
    tops = [f.fitted.top for f in result.ramp]
    bottoms = [f.fitted.bottom for f in result.ramp]
    assert all(a > b for a, b in zip(tops, tops[1:], strict=False))  # top edge only ever rises
    assert all(a < b for a, b in zip(bottoms, bottoms[1:], strict=False))
    assert all(abs(f.fitted.top - f.raw.top) <= 2.0 for f in result.ramp)
    assert all(abs(f.fitted.top - t) <= 2.0 for f, t in zip(result.ramp, truth, strict=True))
    assert 0 < tops[-1] < 46 and tops[0] < 46  # strictly between the anchors


def test_an_eased_ramp_follows_the_curve_and_never_jumps_to_the_end():
    n = 72
    eased = [round(46 - 46 * (3 * (k / n) ** 2 - 2 * (k / n) ** 3)) for k in range(1, n + 1)]
    result = analyze_transition(frames_for([46] * 30 + eased + [0] * 30), OLD, NEW, FPS)
    assert result.kind == "ramp" and result.ramp_start is not None
    fitted = [f.fitted.top for f in result.ramp]
    assert fitted[0] > 40 and fitted[-1] < 6  # starts near the old edge, ends near the new one
    assert max(abs(f.fitted.top - f.raw.top) for f in result.ramp) <= 2.0
    midpoint = fitted[len(fitted) // 2]
    assert 15 < midpoint < 31  # halfway through the time it is about halfway through the move


def test_unreadable_frames_inside_a_ramp_are_filled_from_the_curve():
    truth = linear(30)
    holes = list(truth)
    for k in (10, 11, 12, 13):
        holes[k] = None
    frames = frames_for(
        [46] * 30 + holes + [0] * 30,
        [314] * 30 + [None if t is None else 360 - t for t in holes] + [360] * 30,
    )
    result = analyze_transition(frames, OLD, NEW, FPS)
    assert result.kind == "ramp"
    gaps = [f for f in result.ramp if not f.readable]
    assert len(gaps) == 4 and all(
        abs(f.fitted.top - t) <= 2.0 for f, t in zip(result.ramp, truth, strict=True)
    )
    assert result.confidence == pytest.approx(1 - 2 * 4 / 30, abs=0.01)


def test_mostly_unreadable_ramp_is_flagged_low_confidence():
    truth = linear(30)
    holes = [None if k % 2 else t for k, t in enumerate(truth)]
    frames = frames_for(
        [46] * 30 + holes + [0] * 30,
        [314] * 30 + [None if t is None else 360 - t for t in holes] + [360] * 30,
    )
    result = analyze_transition(frames, OLD, NEW, FPS)
    assert (
        result.kind == "ramp"
        and "low_confidence" in result.flags
        and "many_unreadable_frames" in result.flags
    )


def test_a_cut_hidden_in_an_unreadable_stretch_is_placed_at_the_biggest_change():
    tops = [46] * 20 + [None] * 10 + [0] * 20
    diffs = [0.1] * 50
    diffs[27] = 40.0  # the scene change inside the dark stretch
    result = analyze_transition(frames_for(tops, diffs=diffs), OLD, NEW, FPS)
    assert result.kind == "cut" and result.first_new == 27
    assert "boundary_in_unreadable_gap" in result.flags and result.confidence < 1.0


def test_one_blended_frame_is_a_cut_not_a_ramp():
    result = analyze_transition(frames_for([46] * 30 + [23] + [0] * 30), OLD, NEW, FPS)
    assert result.kind == "cut" and result.first_new == 30 and "blend_frames" in result.flags


def test_scene_cut_flag_comes_from_the_frame_difference_at_the_boundary():
    quiet = [0.2] * 60
    loud = list(quiet)
    loud[30] = 35.0
    tops = [46] * 30 + [0] * 30
    assert analyze_transition(frames_for(tops, diffs=loud), OLD, NEW, FPS).scene_cut is True
    assert analyze_transition(frames_for(tops, diffs=quiet), OLD, NEW, FPS).scene_cut is False
    assert analyze_transition(frames_for(tops), OLD, NEW, FPS).scene_cut is None


def test_a_window_that_ends_mid_ramp_is_incomplete_not_a_cut():
    result = analyze_transition(frames_for([46] * 20 + linear(30)[:15]), OLD, NEW, FPS)
    assert result.kind == "none" and "incomplete" in result.flags


def test_a_single_misread_frame_does_not_start_a_ramp():
    tops = [46] * 50 + [0] * 50
    tops[20] = 30  # a flash that reads as a partly opened matte
    result = analyze_transition(frames_for(tops), OLD, NEW, FPS)
    assert result.kind == "cut" and result.first_new == 50


def test_sides_that_move_are_tracked_too():
    # a pillarboxed 4:3 picture opening to the full 16:9 frame: the left and right edges move, top and bottom do not
    old = Edges(top=0, bottom=360, left=80, right=560)
    new = Edges(top=0, bottom=360, left=0, right=640)
    n = 30
    frames = []
    for i in range(30 + n + 30):
        k = min(max(i - 30 + 1, 0), n + 1) / (n + 1)
        left = round(80 * (1 - k))
        frames.append(ScanFrame(i / FPS, Edges(0, 360, left, 640 - left)))
    result = analyze_transition(frames, old, new, FPS)
    assert result.kind == "ramp" and result.ramp_start == 30 and result.first_new == 60
    assert result.ramp[10].fitted.top == 0 and result.ramp[10].fitted.bottom == 360
    assert 0 < result.ramp[10].fitted.left < 80


FULL = Edges(top=46, bottom=314, left=0, right=640)
INSET = Edges(top=100, bottom=260, left=120, right=520)


def lerp(a, b, k, n):
    return Edges(
        *(
            round(getattr(a, f) + (getattr(b, f) - getattr(a, f)) * k / n)
            for f in ("top", "bottom", "left", "right")
        )
    )


def opening_frames(*, before=30, ramp=60, after=30, flashes=(), diffs=None):
    """The same picture, a cut to a small inset that grows back to full size, then the same picture again."""
    edges = [FULL] * before + [lerp(INSET, FULL, k, ramp) for k in range(ramp)] + [FULL] * after
    for i in flashes:
        edges[i] = Edges(top=10, bottom=350, left=5, right=635)  # a flash frame read at the wrong place
    return [ScanFrame(i / FPS, e, None if diffs is None else diffs[i]) for i, e in enumerate(edges)]


def test_a_cut_to_a_small_inset_that_grows_back_is_a_ramp_after_the_cut():
    diffs = [0.1] * 120
    diffs[30] = 45.0
    result = analyze_transition(opening_frames(diffs=diffs), FULL, FULL, FPS)
    assert result.kind == "ramp" and "ramp_after_cut" in result.flags
    assert (result.ramp_start, result.first_new, len(result.ramp)) == (30, 90, 60)
    assert result.scene_cut is True
    assert max(abs(result.ramp[0].fitted.left - 120), abs(result.ramp[0].fitted.top - 100)) <= 2
    lefts = [f.fitted.left for f in result.ramp]
    assert all(a > b for a, b in zip(lefts, lefts[1:], strict=False))


def test_a_ramp_whose_last_step_is_a_few_pixels_still_arrives():
    # the real Scott Pilgrim ramp ends 7 px short and then lands: a fixed "small step" test rejected it
    frames = opening_frames()
    last = frames[89].edges  # the last ramp frame, close to FULL
    near = Edges(top=last.top, bottom=last.bottom, left=7, right=633)
    frames[87:90] = [ScanFrame(f.t, near) for f in frames[87:90]]
    result = analyze_transition(frames, FULL, FULL, FPS)
    assert result.kind == "ramp" and "ramp_after_cut" in result.flags


def test_flash_frames_inside_a_ramp_are_set_aside_and_do_not_bend_the_fit():
    result = analyze_transition(opening_frames(flashes=(50, 51)), FULL, FULL, FPS)
    assert result.kind == "ramp"
    flashed = [f for f in result.ramp if not f.readable]
    assert [f.f for f in flashed] == [50, 51]
    for f, k in zip(result.ramp, range(60), strict=True):
        assert abs(f.fitted.left - lerp(INSET, FULL, k, 60).left) <= 2.0


def test_a_subtitle_that_touches_the_picture_and_vanishes_is_not_a_ramp():
    touching = Edges(top=46, bottom=330, left=0, right=640)  # the picture block grew to take in a subtitle
    frames = [ScanFrame(i / FPS, touching if 30 <= i < 60 else FULL) for i in range(90)]
    assert analyze_transition(frames, FULL, FULL, FPS).kind == "none"


def test_edges_that_barely_differ_between_the_states_are_not_tracked():
    near = Edges(top=2, bottom=358, left=0, right=640)
    assert analyze_transition(frames_for([2] * 30), near, NEW, FPS, RampParams(min_move_px=6)).kind == "none"


def test_reference_edges_report_whether_the_picture_holds_still():
    steady = frames_for([46] * 40)
    edges, ok = reference_edges(steady, head=True, stable_s=1.0)
    assert ok and edges == OLD
    moving = frames_for(linear(40))
    assert reference_edges(moving, head=True, stable_s=1.0)[1] is False
    dark = frames_for([None] * 40)
    assert reference_edges(dark, head=False, stable_s=1.0) == (None, False)
