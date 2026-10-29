"""`settle`: a change that could not be pinned down is never written into the timeline."""

from cih_analyzer.frames import Edges
from cih_analyzer.ramps import FitFrame
from cih_analyzer.refine import RefinedBoundary, RefinedSegment, settle

from .helpers import make_info

INFO = make_info(3840, 2160, duration=100.0)
SCOPE = (0, 276, 3840, 1608)
WIDE = (0, 0, 3840, 2160)
IMAX = (0, 216, 3840, 1728)


def seg(start, end, rect, *, verified=True, preset=None, confidence=1.0, flags=()):
    return RefinedSegment(start, end, rect, round(rect[2] / rect[3], 3), preset, confidence, verified, flags)


def cut(t, *, confidence=1.0, flags=(), t_start=None):
    return RefinedBoundary("cut", t, round(t * 24), t if t_start is None else t_start, None, None, None,
                           confidence=confidence, flags=flags)  # fmt: skip


def test_trusted_boundaries_pass_through_untouched():
    segments = [seg(0, 10, SCOPE), seg(10, 20, WIDE)]
    boundaries = [cut(10.0)]
    out_segments, out_boundaries, merges = settle(segments, boundaries, INFO)
    assert (out_segments, out_boundaries, merges) == (segments, boundaries, 0)


def test_a_low_confidence_boundary_becomes_one_segment_over_the_larger_picture():
    out_segments, out_boundaries, merges = settle(
        [seg(0, 10, SCOPE), seg(10, 20, WIDE)], [cut(10.0, confidence=0.6)], INFO
    )
    assert merges == 1 and out_boundaries == []
    (merged,) = out_segments
    assert (merged.start, merged.end, merged.rect) == (0, 20, WIDE)  # the larger rectangle: bars, never spill
    assert merged.preset == 16 / 9 and "merged_uncertain" in merged.flags


def test_an_unresolved_boundary_is_merged_even_if_it_claims_full_confidence():
    _, out_boundaries, merges = settle(
        [seg(0, 10, SCOPE), seg(10, 20, IMAX)], [cut(10.0, flags=("unresolved",))], INFO
    )
    assert merges == 1 and out_boundaries == []


def test_only_the_untrusted_boundary_of_three_is_merged():
    segments = [seg(0, 10, SCOPE), seg(10, 20, IMAX), seg(20, 30, WIDE)]
    boundaries = [cut(10.0), cut(20.0, confidence=0.5)]
    out_segments, out_boundaries, merges = settle(segments, boundaries, INFO)
    assert merges == 1 and out_boundaries == [boundaries[0]]
    assert [(s.start, s.end, s.rect) for s in out_segments] == [(0, 10, SCOPE), (10, 30, WIDE)]


def test_a_boundary_that_starts_before_the_previous_one_finished_is_merged():
    # the shape that made the timeline builder raise "segment end must be after its start"
    segments = [seg(0, 9.8, SCOPE), seg(19.5, 12.5, IMAX), seg(20.4, 26.0, WIDE)]
    boundaries = [cut(19.5, t_start=9.8), cut(20.4, t_start=12.5)]
    out_segments, out_boundaries, merges = settle(segments, boundaries, INFO)
    assert merges >= 1
    assert all(s.end > s.start for s in out_segments)
    assert all(b.t_start > a.t for a, b in zip(out_boundaries, out_boundaries[1:], strict=False))


def test_a_boundary_at_the_very_start_or_end_of_the_file_is_merged():
    _, bounds, merges = settle([seg(0, 5, SCOPE), seg(0, 20, WIDE)], [cut(0.0)], INFO)
    assert merges == 1 and bounds == []
    _, bounds, merges = settle([seg(0, 10, SCOPE), seg(20, 20, WIDE)], [cut(20.0)], INFO)
    assert merges == 1 and bounds == []


def test_a_segment_whose_picture_could_not_be_read_is_folded_into_its_neighbour():
    out_segments, out_boundaries, merges = settle(
        [seg(0, 10, SCOPE), seg(10, 14, IMAX, verified=False), seg(14, 30, SCOPE)],
        [cut(10.0), cut(14.0)],
        INFO,
    )
    assert merges >= 1 and len(out_segments) < 3
    assert all(s.verified for s in out_segments)
    assert all(
        s.rect[1] <= IMAX[1] for s in out_segments if s.start < 14 < s.end
    )  # the unreadable part is covered


def test_a_single_unverified_segment_stays_but_stays_flagged():
    only = seg(0, 30, SCOPE, verified=False, flags=("unreadable",))
    out_segments, out_boundaries, merges = settle([only], [], INFO)
    assert (out_segments, out_boundaries, merges) == ([only], [], 0)


def test_a_chain_of_untrusted_boundaries_collapses_to_the_widest_rectangle():
    segments = [seg(0, 10, SCOPE), seg(10, 20, IMAX), seg(20, 30, WIDE), seg(30, 40, SCOPE)]
    boundaries = [cut(10.0, confidence=0.3), cut(20.0, confidence=0.3), cut(30.0, confidence=0.3)]
    out_segments, out_boundaries, merges = settle(segments, boundaries, INFO)
    assert (
        merges == 3
        and out_boundaries == []
        and [(s.start, s.end, s.rect) for s in out_segments] == [(0, 40, WIDE)]
    )


def ramp(t_start, t, *, flags=("ramp_after_cut",), confidence=1.0):
    return RefinedBoundary("ramp", t, round(t * 24), t_start, None, None, None, confidence=confidence, flags=flags)  # fmt: skip


def test_a_short_dip_to_a_smaller_picture_and_back_is_a_fade_not_a_ramp():
    segments = [seg(0, 48.8, WIDE), seg(49.7, 100, WIDE)]
    out_segments, out_boundaries, merges = settle(segments, [ramp(48.8, 49.7)], INFO)
    assert merges == 1 and out_boundaries == [] and [(s.start, s.end) for s in out_segments] == [(0, 100)]


def fit(rect, k=0):
    x, y, w, h = rect
    return FitFrame(100 + k, 4.0 + k / 24, Edges(top=y, bottom=y + h, left=x, right=x + w), None, True)


def opening_ramp(frames, *, t_start=10.0, t=14.0, flags=()):
    return RefinedBoundary("ramp", t, round(t * 24), t_start, None, None, None, tuple(frames), 0.9, flags)


def test_a_ramp_that_moves_the_picture_a_real_amount_between_its_two_segments_is_kept():
    mid = (0, 138, 3840, 1884)
    frames = [fit((0, 276 - 20 * k, 3840, 1608 + 40 * k), k) for k in range(5)] + [fit(WIDE, 5)]
    segments = [seg(0, 10, SCOPE), seg(14, 30, WIDE)]
    boundaries = [opening_ramp(frames)]
    assert mid[1] > 0 and settle(segments, boundaries, INFO)[1:] == (boundaries, 0)


def test_a_ramp_that_ends_somewhere_the_next_segment_is_not_is_merged():
    # the shape left behind when a neighbouring segment was widened: the ramp lands on a narrow picture
    narrow = (600, 0, 2640, 2160)
    frames = [fit((0, 0, 3840, 2160), 0), fit((300, 0, 3240, 2160), 1), fit(narrow, 2)]
    out_segments, out_boundaries, merges = settle(
        [seg(0, 10, WIDE), seg(14, 40, WIDE)], [opening_ramp(frames)], INFO
    )
    assert merges == 1 and out_boundaries == []


def test_a_ramp_that_barely_moves_the_picture_is_not_worth_following():
    frames = [fit((0, 276 + k, 3840, 1608 - 2 * k), k) for k in range(6)]  # a few pixels in all
    segments = [seg(0, 10, SCOPE), seg(14, 30, (0, 286, 3840, 1588))]
    out_segments, out_boundaries, merges = settle(segments, [opening_ramp(frames)], INFO)
    assert merges == 1 and out_boundaries == []


def test_a_long_excursion_ramp_is_kept():
    segments = [seg(0, 1.6, WIDE), seg(17.8, 100, WIDE)]
    boundaries = [ramp(1.6, 17.8)]
    assert settle(segments, boundaries, INFO)[1:] == (boundaries, 0)


def test_a_cut_between_nearly_identical_pictures_is_not_worth_a_zoom_change():
    a, b = (0, 70, 3840, 2020), (0, 86, 3840, 1988)  # about 1.6% apart in zoom
    out_segments, out_boundaries, merges = settle([seg(0, 10, a), seg(10, 20, b)], [cut(10.0)], INFO)
    assert merges == 1 and out_boundaries == [] and out_segments[0].rect == a  # the larger of the two
    _, kept, merges = settle([seg(0, 10, SCOPE), seg(10, 20, WIDE)], [cut(10.0)], INFO)
    assert merges == 0 and len(kept) == 1  # 2.39 against 16:9 is a real change


def test_a_segment_under_a_second_is_folded_into_its_neighbour():
    segments = [seg(0, 20, WIDE), seg(20, 20.4, IMAX)]
    out_segments, out_boundaries, merges = settle(segments, [cut(20.0)], INFO)
    assert merges == 1 and out_boundaries == [] and [(s.start, s.end) for s in out_segments] == [(0, 20.4)]
