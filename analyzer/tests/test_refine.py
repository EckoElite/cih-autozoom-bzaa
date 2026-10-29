"""Refinement on synthetic video: exact boundary frames, cut or ramp, fitted ramp edges, refuted segments."""

import pytest

from cih_analyzer.coarse import coarse_scan
from cih_analyzer.frames import measure_edges, read_luma, scan_edges
from cih_analyzer.probe import probe_video
from cih_analyzer.ramps import RampParams
from cih_analyzer.refine import RefineParams, refine, refine_boundary
from cih_analyzer.segmentation import BoundaryWindow, segment_samples

from .conftest import NEEDS_FFMPEG
from .synth import Seg, make_video, rect_for_ratio

W, H, FPS = 640, 360, 25
SCOPE = rect_for_ratio(2.39, W, H)  # (0, 46, 640, 268)
WIDE = rect_for_ratio(16 / 9, W, H)
IMAX = rect_for_ratio(1.90, W, H)
SUBTITLE = ((200, 330, 240, 12, 235),)  # bright text rows below the picture, in the matte


def opening(frames, curve=lambda p: p, a=SCOPE, b=WIDE):
    """Rectangle for frame i of a ramp from `a` to `b` over `frames` frames (first frame = a, last = b)."""

    def rect(i):
        p = curve(i / (frames - 1))
        h = 2 * round((a[3] + (b[3] - a[3]) * p) / 2)
        return 0, (H - h) // 2, W, h

    return rect


def smoothstep(p):
    return 3 * p**2 - 2 * p**3


def truth(rect_fn, frames, a=SCOPE, b=WIDE):
    """First frame that differs from `a`, and first frame equal to `b` for good, in a ramp's own frame numbers."""
    rects = [rect_fn(i) for i in range(frames)]
    start = next(i for i, r in enumerate(rects) if r != a)
    end = next(i for i in range(frames) if all(r == b for r in rects[i:]))
    return start, end, rects


def near(edges, rect, tol):
    x, y, w, h = rect
    return (
        max(abs(edges.left - x), abs(edges.top - y), abs(edges.right - x - w), abs(edges.bottom - y - h))
        <= tol
    )


def analyse(path, **kw):
    info = probe_video(path)
    coarse = segment_samples(coarse_scan(path, info, **kw.pop("scan", {})), info)
    return info, coarse, refine(path, info, coarse, **kw)


def test_a_picture_that_touches_one_edge_is_centred_on_its_smaller_margin():
    import numpy as np

    # bright upper two thirds, dark lower third (a sky over a dark ground): no matte, so the whole frame
    luma = np.full((360, 640), 16, dtype=np.uint8)
    luma[:240, :] = 120
    assert measure_edges(luma, 19.0).rect == (0, 0, 640, 360)
    # a centred picture is untouched
    luma = np.full((360, 640), 16, dtype=np.uint8)
    luma[46:314, :] = 120
    assert measure_edges(luma, 19.0).rect == (0, 46, 640, 268)


@NEEDS_FFMPEG
class TestFrames:
    def test_edges_are_found_in_every_frame_and_times_are_the_players(self, tmp_path):
        path = make_video(tmp_path / "cut.mkv", [Seg(4, SCOPE), Seg(4, WIDE)], extra_vf="setpts=PTS+5/TB")
        info = probe_video(path)
        assert info.start_time == pytest.approx(5, abs=0.1)
        frames = scan_edges(path, info, 3.0, 2.0, threshold=19.0)
        assert len(frames) == 50
        assert frames[0].t == pytest.approx(3.0, abs=0.02)  # seconds on the player's clock, not the stream's
        before, after = frames[24], frames[25]
        assert before.edges.rect == SCOPE and after.edges.rect == WIDE
        assert after.t == pytest.approx(4.0, abs=0.02)

    def test_a_subtitle_in_the_matte_does_not_move_the_edges(self, tmp_path):
        path = make_video(tmp_path / "subs.mp4", [Seg(3, SCOPE, boxes=SUBTITLE)])
        luma = read_luma(path, probe_video(path), 1.0)
        assert measure_edges(luma, 19.0).rect == SCOPE

    def test_a_dim_picture_a_few_levels_above_black_is_still_found(self, tmp_path):
        path = make_video(
            tmp_path / "dim.mp4", [Seg(3, SCOPE, level=19)]
        )  # texture 16 and 22 against matte 16
        luma = read_luma(path, probe_video(path), 1.0)
        edges = measure_edges(luma, 19.0)
        assert (edges.top, edges.bottom) == (46, 314)
        assert near(edges, SCOPE, 4)  # the test texture's first 4-pixel block is exactly matte black
        assert measure_edges(luma, 24.0) is None  # a fixed limit of 24 sees nothing at all

    def test_ten_bit_hdr_frames_are_read_at_native_depth(self, tmp_path):
        path = make_video(tmp_path / "pq.mp4", [Seg(3, SCOPE)], pq=True)
        info = probe_video(path)
        frames = scan_edges(path, info, 0.5, 1.0, threshold=19.0)
        assert frames and all(
            near(f.edges, SCOPE, 2) for f in frames
        )  # HEVC rings a pixel or two at the matte


@NEEDS_FFMPEG
class TestCuts:
    def test_every_cut_lands_on_its_exact_frame(self, tmp_path):
        plan = [(SCOPE, 110), (IMAX, 150), (SCOPE, 110), (WIDE, 150)]
        path = make_video(tmp_path / "var.mp4", [Seg(8, r, level=lv) for r, lv in plan])
        _, coarse, result = analyse(path)
        assert [s.preset for s in result.segments] == [2.39, 1.90, 2.39, 16 / 9]
        assert [(b.kind, b.frame) for b in result.boundaries] == [("cut", 200), ("cut", 400), ("cut", 600)]
        assert all(b.scene_cut is True for b in result.boundaries)  # the picture changed with the matte
        assert [round(s.start, 2) for s in result.segments] == [0.0, 8.0, 16.0, 24.0]
        assert [s.rect for s in result.segments] == [SCOPE, IMAX, SCOPE, WIDE]
        assert result.flags == ("var",)

    def test_a_matte_change_without_a_scene_change_is_flagged(self, tmp_path):
        path = make_video(
            tmp_path / "same.mp4", [Seg(8, SCOPE), Seg(8, WIDE)]
        )  # same picture level either side
        _, _, result = analyse(path)
        assert result.boundaries[0].frame == 200 and result.boundaries[0].scene_cut is False

    def test_cut_frame_is_right_when_the_stream_starts_at_five_seconds(self, tmp_path):
        path = make_video(
            tmp_path / "offset.mkv", [Seg(8, SCOPE), Seg(8, WIDE, level=150)], extra_vf="setpts=PTS+5/TB"
        )
        _, _, result = analyse(path)
        assert result.boundaries[0].frame == 200 and result.boundaries[0].t == pytest.approx(8.0, abs=0.02)

    def test_a_subtitle_burned_into_the_old_matte_does_not_move_the_cut(self, tmp_path):
        path = make_video(tmp_path / "subcut.mp4", [Seg(14, SCOPE, boxes=SUBTITLE), Seg(8, WIDE, level=150)])
        _, _, result = analyse(path)
        assert [(b.kind, b.frame) for b in result.boundaries] == [("cut", 350)]
        # a subtitle that never goes away stretches the crop; the larger rectangle is kept (bars, never spill)
        x, y, w, h = result.segments[0].rect
        assert (x, w) == (SCOPE[0], SCOPE[2]) and y <= SCOPE[1] and y + h >= SCOPE[1] + SCOPE[3]

    def test_a_short_real_segment_is_confirmed_and_both_cuts_are_exact(self, tmp_path):
        path = make_video(tmp_path / "blip.mp4", [Seg(10, SCOPE), Seg(4, IMAX, level=150), Seg(10, SCOPE)])
        _, coarse, result = analyse(path)
        assert coarse.segments[1].needs_verification  # too short for keyframes to vouch for
        assert [s.preset for s in result.segments] == [2.39, 1.90, 2.39]
        assert result.segments[1].verified is True and result.dropped == ()
        assert [b.frame for b in result.boundaries] == [250, 350]

    def test_a_long_false_segment_from_a_dim_edge_is_refuted_and_merged(self, tmp_path):
        # 14 s where the picture's top and bottom 30 rows are very dim: cropdetect treats them as matte and sees a
        # thinner picture, long enough to survive the coarse pass; full-resolution probes see the real edges
        bands = ((0, 46, 640, 30, 20), (0, 284, 640, 30, 20))
        path = make_video(
            tmp_path / "dimband.mp4", [Seg(10, SCOPE), Seg(14, SCOPE, boxes=bands), Seg(10, SCOPE)]
        )
        _, coarse, result = analyse(path)
        assert len(coarse.segments) == 3 and coarse.segments[1].needs_verification
        assert [s.preset for s in result.segments] == [2.39]
        assert result.boundaries == () and "segments_refuted" in result.flags
        assert len(result.dropped) == 1 and result.segments[0].rect == SCOPE


@NEEDS_FFMPEG
class TestRamps:
    def check_ramp(self, result, rect_fn, frames, first_frame, *, slack=0):
        start, end, rects = truth(rect_fn, frames)
        (boundary,) = result.boundaries
        assert boundary.kind == "ramp" and "low_confidence" not in boundary.flags
        got = boundary.ramp
        assert abs(got[0].f - (first_frame + start)) <= slack
        assert abs(boundary.frame - (first_frame + end)) <= slack
        assert len(got) == boundary.frame - got[0].f
        tops = [f.fitted.top for f in got]
        assert all(a > b for a, b in zip(tops, tops[1:], strict=False))  # strictly monotone, no reversals
        for f in got:
            true = rects[f.f - first_frame]
            assert abs(f.fitted.top - true[1]) <= 2.0 and abs(f.fitted.bottom - (true[1] + true[3])) <= 2.0
        return boundary

    def test_linear_ramp_has_exact_start_and_end_frames(self, tmp_path):
        rect_fn = opening(75)
        path = make_video(tmp_path / "lin.mp4", [Seg(8, SCOPE), Seg(3, rect_fn), Seg(8, WIDE)], keyint=12)
        _, coarse, result = analyse(path, scan={"min_interval_s": 0.4})
        assert coarse.boundaries[0].ramp_candidate
        boundary = self.check_ramp(result, rect_fn, 75, 200)
        assert boundary.scene_cut is None and boundary.confidence == 1.0

    def test_eased_ramp_follows_the_curve(self, tmp_path):
        rect_fn = opening(75, smoothstep)
        path = make_video(tmp_path / "ease.mp4", [Seg(8, SCOPE), Seg(3, rect_fn), Seg(8, WIDE)], keyint=12)
        _, _, result = analyse(path, scan={"min_interval_s": 0.4})
        self.check_ramp(result, rect_fn, 75, 200, slack=2)  # the soft ends move less than a pixel a frame

    def test_ramp_in_a_near_black_shot(self, tmp_path):
        rect_fn = opening(75)
        dim = 21  # picture texture 16 and 26 against matte 16: barely above black
        segs = [
            Seg(8, SCOPE), Seg(2, SCOPE, level=dim), Seg(3, rect_fn, level=dim), Seg(2, WIDE, level=dim), Seg(8, WIDE)
        ]  # fmt: skip
        path = make_video(tmp_path / "dark.mp4", segs, keyint=12)
        _, coarse, result = analyse(path, scan={"min_interval_s": 0.4})
        assert coarse.stats["dark"] >= 5  # the coarse pass could not read this stretch
        self.check_ramp(result, rect_fn, 75, 250)

    def test_ramp_longer_than_the_decoded_window_is_followed_to_its_end(self, tmp_path):
        rect_fn = opening(350)  # 14 s
        path = make_video(tmp_path / "long.mp4", [Seg(8, SCOPE), Seg(14, rect_fn), Seg(8, WIDE)], keyint=12)
        info = probe_video(path)
        # a window that only covers part of the ramp: the decoded span has to grow until the picture holds still
        window = BoundaryWindow(lo=9.0, hi=10.0)
        boundary = refine_boundary(path, info, window, SCOPE, WIDE, floor=0.0, ceiling=30.0)
        start, end, _ = truth(rect_fn, 350)
        assert boundary.kind == "ramp" and boundary.confidence == 1.0
        assert boundary.ramp[0].f == 200 + start and boundary.frame == 200 + end
        assert len(boundary.ramp) > 300

    def test_opening_that_cuts_to_a_small_window_and_grows_back_is_one_ramp_after_the_cut(self, tmp_path):
        full = rect_for_ratio(1.85, W, H)
        inset = (160, 80, 320, 200)

        def growing(i, frames=250):  # 10 s: the window grows from `inset` to the full 1.85 picture
            return tuple(
                2 * round((a + (b - a) * i / (frames - 1)) / 2) for a, b in zip(inset, SCOPE, strict=True)
            )

        segs = [Seg(2, full, level=110), Seg(10, growing, level=150), Seg(8, SCOPE, level=110)]
        path = make_video(tmp_path / "opening.mp4", segs, keyint=12)
        _, _, result = analyse(path, scan={"min_interval_s": 0.5})
        (boundary,) = result.boundaries
        assert (
            boundary.kind == "ramp"
            and "ramp_after_cut" in boundary.flags
            and "low_confidence" not in boundary.flags
        )
        assert boundary.t_start == pytest.approx(2.0, abs=0.04) and boundary.scene_cut is True
        assert boundary.ramp[0].f == 50 and abs(boundary.frame - 300) <= 3
        assert result.segments[0].rect == full and result.segments[-1].rect == SCOPE
        assert result.segments[0].end == pytest.approx(2.0, abs=0.04)
        # the stored curve starts at the inset, not at the old picture, and never jumps
        assert (
            abs(boundary.ramp[0].fitted.left - inset[0]) <= 2
            and abs(boundary.ramp[0].fitted.top - inset[1]) <= 2
        )
        lefts = [f.fitted.left for f in boundary.ramp]
        assert all(a > b for a, b in zip(lefts, lefts[1:], strict=False))

    def test_a_window_that_grows_back_to_the_same_ratio_is_found(self, tmp_path):
        # the picture before and after is identical (1.85 full frame), so a ratio label alone sees no change (DD21)
        full = rect_for_ratio(1.85, W, H)
        inset = (160, 80, 320, 200)

        def growing(i, frames=250):
            return tuple(
                2 * round((a + (b - a) * i / (frames - 1)) / 2) for a, b in zip(inset, full, strict=True)
            )

        segs = [Seg(2, full, level=110), Seg(10, growing, level=150), Seg(8, full, level=110)]
        path = make_video(tmp_path / "same.mp4", segs, keyint=12)
        _, coarse, result = analyse(path, scan={"min_interval_s": 0.5})
        assert coarse.boundaries[0].ramp_candidate
        (boundary,) = result.boundaries
        assert boundary.kind == "ramp" and "ramp_after_cut" in boundary.flags
        assert boundary.t_start == pytest.approx(2.0, abs=0.04) and abs(boundary.frame - 300) <= 3
        assert [s.rect for s in result.segments] == [full, full]
        assert result.segments[0].end == pytest.approx(2.0, abs=0.04)
        assert result.flags.count("ramp") == 1

    def test_edge_threshold_ignores_a_rectangle_that_contains_picture(self, tmp_path):
        from cih_analyzer.frames import matte_threshold

        path = make_video(tmp_path / "scope.mp4", [Seg(4, SCOPE, level=150)])
        info = probe_video(path)
        small = (200, 150, 240, 60)  # far too small: the "matte" outside it is nearly all picture
        good = matte_threshold(path, info, [(2.0, SCOPE)])
        assert good == pytest.approx(19.0, abs=1.5)
        assert matte_threshold(path, info, [(2.0, small), (2.5, SCOPE)]) == good  # the cleanest sample wins

    def test_ramp_tracks_a_matte_that_also_moves_at_the_sides(self, tmp_path):
        # a window that grows in both directions, from a small 4:3 picture to the full frame (a change in width
        # alone would not change the zoom, so it is not worth a ramp)
        small = (160, 60, 320, 240)

        def growing(i, frames=60):
            return tuple(
                2 * round((a + (b - a) * i / (frames - 1)) / 2)
                for a, b in zip(small, (0, 0, W, H), strict=True)
            )

        path = make_video(
            tmp_path / "side.mp4", [Seg(6, small), Seg(2.4, growing), Seg(6, (0, 0, W, H))], keyint=12
        )
        _, _, result = analyse(path, scan={"min_interval_s": 0.4})
        (boundary,) = result.boundaries
        assert boundary.kind == "ramp" and len(boundary.ramp) >= 55
        lefts = [f.fitted.left for f in boundary.ramp]
        tops = [f.fitted.top for f in boundary.ramp]
        assert all(a > b for a, b in zip(lefts, lefts[1:], strict=False)) and lefts[0] > 0
        assert all(a > b for a, b in zip(tops, tops[1:], strict=False)) and tops[0] > 0

    def test_a_ramp_that_only_changes_the_width_is_not_kept(self, tmp_path):
        four_three = rect_for_ratio(4 / 3, W, H)

        def pillar_opening(i, frames=60):
            w = 2 * round((four_three[2] + (W - four_three[2]) * i / (frames - 1)) / 2)
            return (W - w) // 2, 0, w, H

        path = make_video(
            tmp_path / "side.mp4",
            [Seg(6, four_three), Seg(2.4, pillar_opening), Seg(6, (0, 0, W, H))],
            keyint=12,
        )
        _, _, result = analyse(path, scan={"min_interval_s": 0.4})
        assert (
            result.boundaries == () and len(result.segments) == 1
        )  # 4:3 and 16:9 zoom the same in a 16:9 frame


@NEEDS_FFMPEG
class TestHardCases:
    def test_a_fade_through_black_hides_the_cut_so_the_boundary_is_not_pinned_down_and_the_timeline_stays_safe(
        self, tmp_path
    ):
        segs = [Seg(8, SCOPE), Seg(2, SCOPE, level=16), Seg(2, WIDE, level=16), Seg(8, WIDE, level=150)]
        path = make_video(tmp_path / "fade.mp4", segs)
        info = probe_video(path)
        # the window itself can say only "somewhere in the black stretch" and says so
        boundary = refine_boundary(
            path, info, BoundaryWindow(7.0, 12.0), SCOPE, WIDE, floor=0.0, ceiling=20.0
        )
        assert boundary.kind == "cut" and 200 <= boundary.frame <= 300
        assert "boundary_in_unreadable_gap" in boundary.flags and boundary.confidence < 0.7
        # and the file as a whole is therefore written as one wide segment, not as a guess
        _, _, result = analyse(path)
        assert result.boundaries == () and "uncertain_merged" in result.flags
        (segment,) = result.segments
        assert segment.rect == WIDE and segment.preset == 16 / 9 and "merged_uncertain" in segment.flags

    def test_a_dark_fade_in_does_not_become_a_false_opening_segment(self, tmp_path):
        # a 4:3 picture whose first seconds are almost black with one bright patch (the Foreigner case)
        four_three = rect_for_ratio(4 / 3, W, H)
        patch = ((four_three[0] + 60, 120, 140, 120, 150),)
        segs = [Seg(4, four_three, level=17, boxes=patch), Seg(24, four_three, level=110)]
        path = make_video(tmp_path / "fadein.mp4", segs)
        _, _, result = analyse(path)
        assert result.boundaries == () and len(result.segments) == 1
        x, y, w, h = result.segments[0].rect
        assert x <= four_three[0] + 4 and x + w >= four_three[0] + four_three[2] - 4  # the whole 4:3 picture
        assert result.segments[0].preset == 4 / 3

    def test_unresolvable_boundary_is_reported_not_guessed(self, tmp_path):
        path = make_video(tmp_path / "plain.mp4", [Seg(8, SCOPE), Seg(8, SCOPE)])
        info = probe_video(path)
        boundary = refine_boundary(path, info, BoundaryWindow(7.0, 8.0), SCOPE, WIDE, floor=0.0, ceiling=16.0)
        assert boundary.confidence == 0.0 and "unresolved" in boundary.flags

    def test_ramp_params_are_adjustable(self):
        assert RefineParams(ramp=RampParams(min_ramp_frames=6)).ramp.min_ramp_frames == 6
