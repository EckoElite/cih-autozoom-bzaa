"""The survey: a cheap seek-based first look that finishes files showing one picture throughout."""

import pytest

from cih_analyzer.analyze import analyze_file
from cih_analyzer.coarse import Sample
from cih_analyzer.config import ConfigError, Settings
from cih_analyzer.survey import judge, seek_targets, worth_surveying

from .conftest import NEEDS_FFMPEG
from .helpers import make_info, run, sample
from .synth import Seg, make_video, rect_for_ratio

W, H = 640, 360
SCOPE = rect_for_ratio(2.39, W, H)
WIDE = rect_for_ratio(16 / 9, W, H)


def test_the_survey_looks_at_the_middle_of_each_slot_and_skips_short_files():
    info = make_info(duration=100.0)
    targets = seek_targets(info, 8.0)
    assert len(targets) == 12 and targets[0] == pytest.approx(100 / 12 / 2) and targets[-1] < 100
    assert worth_surveying(info, 8.0) and not worth_surveying(make_info(duration=60.0), 8.0)
    assert not worth_surveying(info, 0)


def test_one_picture_throughout_is_constant_and_gives_a_whole_file_segment():
    verdict = judge(run(0, 120, 2.39, step=8.0), make_info(duration=120.0))
    assert verdict.constant and verdict.reason == "constant"
    seg = verdict.segment
    assert (seg.start, seg.end, seg.preset, seg.aspect) == (0.0, 120.0, True, 2.39)
    assert seg.needs_verification  # the refinement pass probes it at full resolution first


def test_a_single_different_sample_means_the_detailed_pass_runs():
    samples = run(0, 120, 2.39, step=8.0)
    samples[7] = sample(samples[7].t, 16 / 9, yavg=90.0)
    verdict = judge(samples, make_info(duration=120.0))
    assert not verdict.constant and verdict.reason == "varies"


def test_a_bigger_picture_that_grew_on_one_side_only_is_a_subtitle_not_a_change():
    samples = run(0, 120, 2.39, step=8.0)
    usual = samples[7]
    # the crop reached 100 px further into the matte on one side only: its larger margin is still the matte's
    samples[7] = Sample(usual.t, usual.x, usual.y - 100, usual.w, usual.h + 200, 90.0, 100, 0, 100)
    assert judge(samples, make_info(duration=120.0)).constant
    both = Sample(usual.t, usual.x, usual.y - 100, usual.w, usual.h + 200, 90.0)  # opened on both sides alike
    samples[7] = both
    assert not judge(samples, make_info(duration=120.0)).constant


def test_a_dim_bigger_sample_is_not_believed():
    samples = run(0, 120, 2.39, step=8.0)
    samples[7] = sample(samples[7].t, 16 / 9, yavg=30.0)
    assert judge(samples, make_info(duration=120.0)).constant


def test_a_smaller_picture_counts_only_when_two_seeks_in_a_row_are_bright_and_show_it():
    # dark scenes and odd crops are everywhere in a constant film; a smaller picture only costs bars, so a lone
    # bright sample, or dim ones in a row, are not enough
    info = make_info(duration=160.0)

    def film(smaller_at, yavg):
        out = run(0, 160, 16 / 9, step=8.0)
        for i in smaller_at:
            out[i] = sample(out[i].t, 2.39, yavg=yavg)
        return out

    assert judge(film([5], 90.0), info).constant
    assert judge(film([5, 6, 7], 30.0), info).constant
    assert judge(film([5, 7], 90.0), info).constant  # not in a row
    assert not judge(film([5, 6], 90.0), info).constant


def test_credits_and_dark_samples_do_not_make_a_film_variable():
    samples = run(0, 100, 2.39, step=8.0)
    samples.append(Sample(104.0, 1200, 900, 1400, 300, 60.0))  # a few lines of credits: under half the frame
    samples.append(Sample(112.0, 0, 0, 3840, 2160, 5.0))  # a fade to black
    assert judge(samples, make_info(duration=120.0)).constant


def test_a_film_too_dark_to_read_is_unclear_not_constant():
    dark = [Sample(float(t), 0, 0, 3840, 2160, 6.0) for t in range(0, 120, 8)]
    verdict = judge(dark, make_info(duration=120.0))
    assert not verdict.constant and verdict.reason == "unclear"
    assert (
        judge(run(0, 40, 2.39, step=8.0), make_info(duration=120.0)).reason == "unclear"
    )  # five readable samples


def test_the_survey_interval_is_a_setting_with_a_floor_and_an_off_switch():
    assert Settings.from_env({}).survey_interval_s == 8.0
    assert Settings.from_env({"SURVEY_INTERVAL": "12"}).survey_interval_s == 12.0
    assert Settings.from_env({"SURVEY_INTERVAL": "0"}).survey_interval_s == 0
    with pytest.raises(ConfigError):
        Settings.from_env({"SURVEY_INTERVAL": "1"})


@NEEDS_FFMPEG
class TestSurveyOnVideo:
    def test_a_constant_film_is_finished_by_the_survey_and_matches_the_detailed_pass(self, tmp_path):
        path = make_video(tmp_path / "scope.mp4", [Seg(100, SCOPE)], keyint=25)
        surveyed = analyze_file(path, survey_interval_s=8.0).timeline
        detailed = analyze_file(path).timeline
        assert "surveyed" in surveyed.flags and "surveyed" not in detailed.flags
        assert [(s.active, s.preset, s.start) for s in surveyed.segments] == [
            (s.active, s.preset, s.start) for s in detailed.segments
        ]
        assert surveyed.segments[0].end == pytest.approx(detailed.segments[0].end, abs=0.1)

    def test_a_film_that_changes_gets_the_detailed_pass_and_its_exact_frame(self, tmp_path):
        path = make_video(tmp_path / "var.mp4", [Seg(48, SCOPE), Seg(48, WIDE)], keyint=25)
        timeline = analyze_file(path, survey_interval_s=8.0).timeline
        assert "surveyed" not in timeline.flags
        assert [(s.preset, s.boundary.frame) for s in timeline.segments] == [(2.39, 0), (16 / 9, 1200)]

    def test_progress_starts_with_the_survey_and_never_goes_backwards(self, tmp_path):
        path = make_video(tmp_path / "var.mp4", [Seg(48, SCOPE), Seg(48, WIDE)], keyint=25)
        seen = []
        analyze_file(path, survey_interval_s=8.0, progress=lambda phase, share: seen.append((phase, share)))
        assert seen[0][0] == "surveying"
        assert [p for p, _ in seen if p == "surveying"] == ["surveying"] * seen.index(
            next(x for x in seen if x[0] != "surveying")
        )
        assert [s for _, s in seen] == sorted(s for _, s in seen) and seen[-1][1] <= 1.0
        assert {"reading", "checking"} <= {p for p, _ in seen}
