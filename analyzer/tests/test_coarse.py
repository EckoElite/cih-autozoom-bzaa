"""The coarse pass: parsing, the filter chain, and end to end on synthetic videos with known ratio changes."""

import pytest

from cih_analyzer.coarse import (
    DEFAULT_LIMIT,
    bit_depth,
    build_filter,
    coarse_scan,
    limit_for,
    parse_metadata,
    scaled_size,
)
from cih_analyzer.probe import probe_video
from cih_analyzer.segmentation import segment_samples

from .conftest import NEEDS_FFMPEG
from .helpers import make_info
from .synth import Seg, make_video, rect_for_ratio

CANNED = """frame:0    pts:0       pts_time:0
lavfi.signalstats.YAVG=276.8
lavfi.signalstats.YBITDEPTH=9
lavfi.cropdetect.x=0
lavfi.cropdetect.y=60
lavfi.cropdetect.w=960
lavfi.cropdetect.h=360
frame:1    pts:96096   pts_time:4.004
lavfi.signalstats.YAVG=64
lavfi.signalstats.YBITDEPTH=7
lavfi.cropdetect.x=0
lavfi.cropdetect.y=0
lavfi.cropdetect.w=960
lavfi.cropdetect.h=540
frame:2    pts:1       pts_time:N/A
lavfi.signalstats.YAVG=100
"""


def test_parse_scales_back_to_full_resolution_and_normalises_luma_to_8_bit():
    info = make_info(3840, 2160, start_time=0.5)
    samples = parse_metadata(CANNED, info)
    assert len(samples) == 2  # the third frame has no time or crop and is skipped
    first, second = samples
    assert (first.x, first.y, first.w, first.h) == (0, 240, 3840, 1440)  # 960 wide -> x4
    assert first.t == pytest.approx(-0.5)  # start_time subtracted: the player's clock
    assert first.yavg == pytest.approx(276.8 / 4)  # 10-bit to 8-bit scale
    assert second.t == pytest.approx(3.504) and second.yavg == pytest.approx(16.0)  # 10-bit black is 64


@pytest.mark.parametrize(
    ("pix_fmt", "depth"),
    [("yuv420p", 8), ("nv12", 8), ("yuv420p10le", 10), ("yuv422p10be", 10), ("p010le", 10), ("yuv420p12le", 12),
     ("yuv444p16le", 16), ("gray", 8), (None, 8)],
)  # fmt: skip
def test_bit_depth_comes_from_the_pixel_format_not_from_signalstats(pix_fmt, depth):
    assert bit_depth(pix_fmt) == depth


def test_8_bit_luma_is_not_rescaled():
    # signalstats claims YBITDEPTH=7 for ordinary 8-bit video; the luma must pass through unchanged
    info = make_info(pix_fmt="yuv420p")
    assert parse_metadata(CANNED, info)[0].yavg == pytest.approx(276.8)


def test_scaled_size_never_upscales_and_keeps_even_height():
    assert scaled_size(make_info(3840, 2160)) == (960, 540)
    assert scaled_size(make_info(3840, 1920)) == (960, 480)
    assert scaled_size(make_info(640, 360)) == (640, 360)
    assert scaled_size(make_info(720, 480, sar="32:27")) == (720, 480)


def test_filter_chain_reads_only_the_sampled_keyframes():
    chain = build_filter(make_info(), 0.094, 960, 1.0)
    assert (
        chain.index("select=")
        < chain.index("scale=")
        < chain.index("signalstats")
        < chain.index("cropdetect")
    )
    assert "reset=1" in chain and "limit=0.094" in chain and chain.endswith("metadata=mode=print:file=-")


def test_default_limit_is_above_limited_range_black():
    assert limit_for(make_info()) == DEFAULT_LIMIT == 0.094
    assert 64 / 1023 < DEFAULT_LIMIT and 16 / 255 < DEFAULT_LIMIT  # black is about 0.063 in both depths


@NEEDS_FFMPEG
class TestOnVideo:
    W, H = 640, 360

    def scan(self, path, **kw):
        info = probe_video(path)
        return info, coarse_scan(path, info, **kw)

    def rect(self, ratio):
        return rect_for_ratio(ratio, self.W, self.H)

    def test_ratio_changes_are_found_in_the_right_places(self, tmp_path):
        plan = [(2.39, 8), (1.90, 8), (2.39, 8), (16 / 9, 8)]
        path = make_video(tmp_path / "var.mp4", [Seg(s, self.rect(r)) for r, s in plan])
        info, samples = self.scan(path)
        assert len(samples) == 32  # one keyframe a second
        result = segment_samples(samples, info)
        assert [s.aspect for s in result.segments] == [2.39, 1.90, 2.39, 16 / 9]
        assert [(b.lo, b.hi) for b in result.boundaries] == [(7.0, 8.0), (15.0, 16.0), (23.0, 24.0)]
        assert result.segments[0].rect == self.rect(2.39)

    def test_fade_to_black_in_the_middle_makes_no_false_segment(self, tmp_path):
        scope = self.rect(2.39)
        segs = [Seg(8, scope), Seg(3, scope, level=16), Seg(8, scope)]  # 3 s of black inside the matte
        info, samples = self.scan(make_video(tmp_path / "fade.mp4", segs))
        result = segment_samples(samples, info)
        assert [s.aspect for s in result.segments] == [2.39] and result.stats["dark"] >= 2

    def test_burned_in_subtitle_in_the_bottom_matte_makes_no_false_segment(self, tmp_path):
        scope = self.rect(2.39)
        subtitle = ((200, 330, 240, 12, 235),)  # bright text rows below the picture, inside the matte
        segs = [Seg(8, scope), Seg(2, scope, boxes=subtitle), Seg(8, scope)]
        info, samples = self.scan(make_video(tmp_path / "subs.mp4", segs))
        assert any(s.h > scope[3] for s in samples)  # the crop really did grow on the subtitle frames
        assert [s.aspect for s in segment_samples(samples, info).segments] == [2.39]

    def test_credits_on_black_make_no_false_segment(self, tmp_path):
        scope = self.rect(2.39)
        credits = ((150, 120, 340, 10, 235), (200, 200, 240, 10, 235))
        segs = [Seg(8, scope), Seg(6, scope, level=16, boxes=credits), Seg(8, scope)]
        info, samples = self.scan(make_video(tmp_path / "credits.mp4", segs))
        assert [s.aspect for s in segment_samples(samples, info).segments] == [2.39]

    def test_studio_logo_before_a_scope_film(self, tmp_path):
        segs = [Seg(4, self.rect(16 / 9)), Seg(10, self.rect(2.39))]
        info, samples = self.scan(make_video(tmp_path / "logo.mp4", segs))
        assert [s.aspect for s in segment_samples(samples, info).segments] == [16 / 9, 2.39]

    def test_pillarboxed_4_3_inside_16_9(self, tmp_path):
        info, samples = self.scan(make_video(tmp_path / "pillar.mp4", [Seg(10, self.rect(4 / 3))]))
        result = segment_samples(samples, info)
        assert result.segments[0].aspect == 4 / 3 and result.segments[0].rect[0] > 0

    def test_non_square_pixels_use_the_display_ratio(self, tmp_path):
        path = make_video(
            tmp_path / "ntsc.mp4", [Seg(10, (0, 0, 720, 480))], size=(720, 480), extra_vf="setsar=32/27"
        )
        info, samples = self.scan(path)
        assert info.sar == "32:27"
        assert segment_samples(samples, info).segments[0].aspect == 16 / 9  # not 1.5

    def test_a_ramp_is_found_as_a_candidate(self, tmp_path):
        top, bottom = self.rect(2.39), self.rect(16 / 9)

        def opening(i, frames=75):  # 3 s linear: picture height from 268 to 360
            p = i / (frames - 1)
            h = 2 * round((top[3] + (bottom[3] - top[3]) * p) / 2)
            return 0, (self.H - h) // 2, self.W, h

        segs = [Seg(8, top), Seg(3, opening), Seg(8, bottom)]
        path = make_video(tmp_path / "ramp.mp4", segs, keyint=12)
        info, samples = self.scan(path, min_interval_s=0.4)
        result = segment_samples(samples, info)
        assert [s.aspect for s in result.segments] == [2.39, 16 / 9]
        boundary = result.boundaries[0]
        assert boundary.ramp_candidate and len(boundary.intermediates) >= 3
        assert boundary.lo <= 8.0 and boundary.hi >= 11.0  # the window covers the whole opening

    def test_threshold_below_black_detects_nothing_and_the_default_detects_the_matte(self, tmp_path):
        path = make_video(tmp_path / "scope.mp4", [Seg(4, self.rect(2.39))])
        info = probe_video(path)
        below = coarse_scan(path, info, limit=0.02)
        assert {(s.w, s.h) for s in below} == {(self.W, self.H)}  # nothing counts as black: the whole frame
        assert {s.h for s in coarse_scan(path, info)} == {self.rect(2.39)[3]}

    def test_hdr10_pq_ten_bit_matte_is_found_with_the_same_default(self, tmp_path):
        path = make_video(tmp_path / "pq.mp4", [Seg(4, self.rect(2.39))], pq=True)
        info = probe_video(path)
        assert info.hdr == "HDR10" and info.pix_fmt == "yuv420p10le"
        samples = coarse_scan(path, info)
        assert {s.h for s in samples} == {self.rect(2.39)[3]}
        assert all(40 < s.yavg < 130 for s in samples)  # normalised to the 8-bit scale

    def test_non_zero_start_time_is_normalised_to_the_player_clock(self, tmp_path):
        path = make_video(tmp_path / "offset.mkv", [Seg(6, self.rect(2.39))], extra_vf="setpts=PTS+5/TB")
        info, samples = self.scan(path)
        assert info.start_time == pytest.approx(5, abs=0.1)
        assert samples[0].t == pytest.approx(0, abs=0.1)
