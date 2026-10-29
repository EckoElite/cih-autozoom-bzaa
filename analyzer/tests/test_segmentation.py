"""Gating, snapping, hysteresis and ramp-candidate detection on hand-built samples (no video involved)."""

import pytest

from cih_analyzer.coarse import Sample
from cih_analyzer.segmentation import SegmentationParams, active_aspect, segment_samples, snap_aspect

from .helpers import make_info, run, sample

R_1_78 = 16 / 9


def segs(result):
    return [(round(s.start, 1), round(s.end, 1), s.aspect) for s in result.segments]


def test_snap_to_standard_ratios_within_one_and_a_half_percent():
    assert snap_aspect(2.388) == (2.39, True)
    assert snap_aspect(1.778) == (R_1_78, True)
    assert snap_aspect(1.333) == (4 / 3, True)
    assert snap_aspect(1.905) == (1.90, True)
    assert snap_aspect(2.28) == (2.28, False)  # 3% from 2.35 and 3.6% from 2.20: stays raw
    assert snap_aspect(1.66) == (1.66, False)


def test_aspect_applies_the_pixel_aspect_ratio():
    info = make_info(720, 480, sar="32:27")
    full = Sample(0.0, 0, 0, 720, 480, 80.0)
    assert active_aspect(full, info) == pytest.approx(16 / 9)  # not the 1.5 of the raw frame
    assert snap_aspect(active_aspect(full, info)) == (R_1_78, True)


def test_one_ratio_gives_one_segment_spanning_the_file():
    result = segment_samples(run(0, 30, 2.39), make_info(duration=30.0))
    assert segs(result) == [(0.0, 30.0, 2.39)]
    assert result.boundaries == () and "var" not in result.flags
    assert result.segments[0].confidence == 1.0


def test_variable_ratio_film_gives_one_segment_per_ratio_with_boundary_windows():
    samples = run(0, 10, 2.39) + run(10, 20, 1.90) + run(20, 30, 2.39) + run(30, 40, R_1_78)
    result = segment_samples(samples, make_info(duration=40.0))
    assert [s.aspect for s in result.segments] == [2.39, 1.90, 2.39, R_1_78]
    assert [(b.lo, b.hi) for b in result.boundaries] == [(9.0, 10.0), (19.0, 20.0), (29.0, 30.0)]
    assert result.segments[0].start == 0.0 and result.segments[-1].end == 40.0
    assert "var" in result.flags and not any(b.ramp_candidate for b in result.boundaries)


def test_dark_samples_are_dropped_not_turned_into_segments():
    # a fade to black and a night scene: the crop on near-black frames is meaningless
    dark = [Sample(t, 100, 700, 800, 300, 9.0) for t in range(10, 14)]
    result = segment_samples(run(0, 10, 2.39) + dark + run(14, 30, 2.39), make_info(duration=30.0))
    assert segs(result) == [(0.0, 30.0, 2.39)]
    assert result.stats["dark"] == 4


def test_which_segments_need_verification():
    # a long clean segment does not; a short one, a non-standard ratio, and a noisy one do
    samples = run(0, 60, 2.39) + run(60, 66, 1.90) + run(66, 126, 2.39)
    flags = [s.needs_verification for s in segment_samples(samples, make_info(duration=126.0)).segments]
    assert flags == [False, True, False]
    assert segment_samples(run(0, 60, 2.28), make_info(duration=60.0)).segments[0].needs_verification
    flicker = [sample(30.0, 2.16), sample(31.0, 2.16)]
    noisy = segment_samples(
        run(0, 30, 2.39) + flicker + run(32, 90, 2.39), make_info(duration=90.0)
    ).segments[0]
    assert noisy.confidence < 1.0 and not noisy.needs_verification  # 2 of 90 samples: still above 0.9


def test_a_burned_in_subtitle_in_the_matte_is_flicker():
    # the crop grows to include the subtitle for two keyframes (2 s < the 3 s minimum)
    flicker = [sample(t, 2.16) for t in (10.0, 11.0)]
    result = segment_samples(run(0, 10, 2.39) + flicker + run(12, 30, 2.39), make_info(duration=30.0))
    assert segs(result) == [(0.0, 30.0, 2.39)]
    assert result.segments[0].confidence < 1.0  # the two merged samples count against confidence


def test_a_studio_logo_at_16_9_before_a_scope_film_is_two_segments():
    result = segment_samples(run(0, 4, R_1_78) + run(4, 40, 2.39), make_info(duration=40.0))
    assert segs(result) == [(0.0, 3.0, R_1_78), (4.0, 40.0, 2.39)]


def test_pillarboxed_4_3_and_windowboxed_content():
    pillar = segment_samples(run(0, 20, 4 / 3), make_info(duration=20.0))
    assert pillar.segments[0].aspect == 4 / 3 and pillar.segments[0].rect[0] > 0  # side bars
    # bars on all four sides: 2.00 picture inset in a 16:9 frame
    inset = [Sample(float(t), 480, 270, 2880, 1440, 80.0) for t in range(20)]
    result = segment_samples(inset, make_info(duration=20.0))
    assert result.segments[0].aspect == 2.00 and result.segments[0].rect == (480, 270, 2880, 1440)


def test_off_ratio_stable_segment_keeps_its_raw_value():
    result = segment_samples(run(0, 20, 2.28), make_info(duration=20.0))
    assert result.segments[0].aspect == 2.28 and not result.segments[0].preset


def test_implausible_tiny_crops_are_ignored():
    tiny = [Sample(float(t), 1000, 1000, 40, 30, 80.0) for t in range(10, 13)]
    result = segment_samples(run(0, 10, 2.39) + tiny + run(13, 30, 2.39), make_info(duration=30.0))
    assert segs(result) == [(0.0, 30.0, 2.39)] and result.stats["tiny"] == 3


def test_a_ramp_is_kept_as_intermediate_samples_and_never_merged_away():
    # the matte opens from 2.39 to 1.78 over about 3 s: in-between states are 2.20, 2.00 and 1.85 (all standard ratios)
    ramp = [sample(10.5, 2.20), sample(11.0, 2.00), sample(11.5, 1.85)]
    result = segment_samples(run(0, 10, 2.39) + ramp + run(12, 30, R_1_78), make_info(duration=30.0))
    assert [s.aspect for s in result.segments] == [2.39, R_1_78]
    boundary = result.boundaries[0]
    assert boundary.ramp_candidate and len(boundary.intermediates) == 3
    assert (boundary.lo, boundary.hi) == (9.0, 12.0)
    assert "ramp_candidate" in result.flags


def test_a_ramp_caught_by_a_single_keyframe_is_still_a_candidate():
    result = segment_samples(
        run(0, 10, 2.39) + [sample(10.5, 2.00)] + run(11, 30, R_1_78), make_info(duration=30.0)
    )
    assert result.boundaries[0].ramp_candidate and len(result.boundaries[0].intermediates) == 1


def test_a_hard_cut_has_no_intermediates():
    result = segment_samples(run(0, 10, 2.39) + run(10, 30, R_1_78), make_info(duration=30.0))
    assert not result.boundaries[0].ramp_candidate


def test_out_of_order_in_between_states_are_not_a_ramp_and_are_merged():
    # 2.39 -> 2.00 -> 2.20 -> 1.78 goes the wrong way in the middle, so it is flicker, not a ramp
    wobble = [sample(10.5, 2.00), sample(11.0, 2.20)]
    result = segment_samples(run(0, 10, 2.39) + wobble + run(11.5, 30, R_1_78), make_info(duration=30.0))
    assert [s.aspect for s in result.segments] == [2.39, R_1_78]
    assert not result.boundaries[0].ramp_candidate


def test_a_ramp_in_the_opposite_direction():
    ramp = [sample(10.5, 1.85), sample(11.0, 2.00), sample(11.5, 2.20)]
    result = segment_samples(run(0, 10, R_1_78) + ramp + run(12, 30, 2.39), make_info(duration=30.0))
    assert [s.aspect for s in result.segments] == [R_1_78, 2.39]
    assert len(result.boundaries[0].intermediates) == 3


def box(t, x, y, w, h, yavg=80.0):
    return Sample(t, x, y, w, h, yavg)


def test_the_same_ratio_at_two_sizes_is_two_segments():
    # a 16:9 picture filling the frame, then the same 16:9 inset with bars on all four sides
    full = [box(float(t), 0, 0, 3840, 2160) for t in range(10)]
    inset = [box(float(t), 480, 270, 2880, 1620) for t in range(10, 20)]
    result = segment_samples(full + inset, make_info(duration=20.0))
    assert [(s.rect, s.preset) for s in result.segments] == [
        ((0, 0, 3840, 2160), True),
        ((480, 270, 2880, 1620), True),
    ]
    assert result.segments[0].aspect == result.segments[1].aspect == R_1_78


def test_measurement_jitter_within_a_percent_is_still_one_segment():
    jittered = [box(float(t), 0, 270 + (t % 3) * 4, 3840, 1620 - (t % 3) * 8) for t in range(20)]
    assert len(segment_samples(jittered, make_info(duration=20.0)).segments) == 1


def test_a_window_that_grows_at_a_constant_ratio_is_a_ramp_between_two_sizes():
    small = [box(float(t), 480, 270, 2880, 1620) for t in range(8)]
    growing = [
        box(8.0 + k, 480 - 60 * (k + 1), 270 - 33 * (k + 1), 2880 + 120 * (k + 1), 1620 + 66 * (k + 1))
        for k in range(7)
    ]
    full = [box(float(t), 0, 0, 3840, 2160) for t in range(15, 25)]
    result = segment_samples(small + growing + full, make_info(duration=25.0))
    assert [s.rect for s in result.segments] == [(480, 270, 2880, 1620), (0, 0, 3840, 2160)]
    assert result.boundaries[0].ramp_candidate and len(result.boundaries[0].intermediates) >= 3


def test_a_jump_to_a_small_window_that_eases_back_to_the_same_picture_is_a_ramp_candidate():
    before = [
        box(float(t), 0, 0, 3840, 2160) for t in range(2)
    ]  # a short opening, too short to be a stable run
    eased = [box(2.0 + k, 600 - 75 * k, 340 - 42 * k, 2640 + 150 * k, 1480 + 84 * k) for k in range(8)]
    after = [box(float(t), 0, 0, 3840, 2160) for t in range(10, 22)]
    result = segment_samples(before + eased + after, make_info(duration=22.0))
    assert len(result.segments) == 2 and result.segments[0].rect == result.segments[1].rect
    assert result.boundaries[0].ramp_candidate and "ramp_candidate" in result.flags


def test_unrelated_short_states_are_not_a_ramp():
    # random crops of a dark scene between two identical stable runs: not one-way, so flicker, not a ramp
    junk = [box(10.0, 300, 100, 3000, 1700), box(11.0, 100, 400, 3500, 1200), box(12.0, 500, 50, 2800, 1900)]
    result = segment_samples(run(0, 10, R_1_78) + junk + run(13, 23, R_1_78), make_info(duration=23.0))
    assert len(result.segments) == 1 and result.boundaries == ()


def test_sparse_keyframes_do_not_make_every_segment_flicker():
    # one keyframe every 8 s: one sample per run is all there is, and must still be enough
    samples = [
        sample(0.0, 2.39),
        sample(8.0, 2.39),
        sample(16.0, 1.90),
        sample(24.0, 1.90),
        sample(32.0, 2.39),
    ]
    result = segment_samples(samples, make_info(duration=40.0))
    assert [s.aspect for s in result.segments] == [2.39, 1.90, 2.39]


def test_no_usable_samples():
    result = segment_samples([Sample(t, 0, 0, 3840, 2160, 3.0) for t in range(5)], make_info())
    assert result.segments == () and result.flags == ("no_usable_samples",)
    assert segment_samples([], make_info()).flags == ("no_usable_samples",)


def test_minimum_segment_length_is_a_parameter():
    samples = run(0, 10, 2.39) + run(10, 12, 1.90) + run(12, 30, 2.39)
    assert len(segment_samples(samples, make_info(duration=30.0)).segments) == 1
    short = SegmentationParams(min_segment_s=1.5)
    assert [s.aspect for s in segment_samples(samples, make_info(duration=30.0), short).segments] == [
        2.39,
        1.90,
        2.39,
    ]
