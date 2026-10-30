"""End to end on synthetic video: file in, schema-valid timeline out."""

import json

import pytest

from cih_analyzer.__main__ import main
from cih_analyzer.analyze import analyze_file
from cih_analyzer.schema import load_timeline

from .conftest import NEEDS_FFMPEG
from .synth import Seg, make_video, rect_for_ratio

W, H = 640, 360
SCOPE = rect_for_ratio(2.39, W, H)
WIDE = rect_for_ratio(16 / 9, W, H)
IMAX = rect_for_ratio(1.90, W, H)


def opening(frames, a=SCOPE, b=WIDE):
    def rect(i):
        h = 2 * round((a[3] + (b[3] - a[3]) * i / (frames - 1)) / 2)
        return 0, (H - h) // 2, W, h

    return rect


@NEEDS_FFMPEG
class TestAnalyzeFile:
    def test_variable_ratio_film_becomes_a_valid_timeline(self, tmp_path):
        plan = [(SCOPE, 110), (IMAX, 150), (SCOPE, 110), (WIDE, 150)]
        path = make_video(tmp_path / "var.mp4", [Seg(8, r, level=lv) for r, lv in plan])
        outcome = analyze_file(path, display_path="/media/movies/Var (2024)/var.mp4")
        timeline = outcome.timeline
        assert outcome.skip is None and timeline is not None
        assert (
            timeline.file.path == "/media/movies/Var (2024)/var.mp4"
            and timeline.file.size == path.stat().st_size
        )
        assert (timeline.video.width, timeline.video.height, timeline.video.fps) == (W, H, 25.0)
        assert [s.preset for s in timeline.segments] == [2.39, 1.90, 2.39, 16 / 9]
        assert [(s.boundary.kind, s.boundary.frame) for s in timeline.segments] == [
            ("cut", 0), ("cut", 200), ("cut", 400), ("cut", 600),
        ]  # fmt: skip
        assert [(s.start, s.end) for s in timeline.segments[1:3]] == [(8.0, 16.0), (16.0, 24.0)]
        assert timeline.segments[1].active.model_dump() == {"x": 0, "y": IMAX[1], "w": W, "h": IMAX[3]}
        assert timeline.segments[0].start == 0.0 and timeline.segments[-1].end == pytest.approx(32.0, abs=0.1)
        assert timeline.ramps == [] and "var" in timeline.flags

    def test_the_json_round_trips_through_the_schema(self, tmp_path):
        path = make_video(tmp_path / "scope.mp4", [Seg(6, SCOPE)])
        timeline = analyze_file(path).timeline
        assert load_timeline(timeline.to_json()) == timeline
        data = json.loads(timeline.to_json())
        assert data["schema"] == 1 and data["segments"][0]["boundary"] == {
            "kind": "cut",
            "frame": 0,
            "scene_cut": None,
        }

    def test_a_ramp_is_stored_frame_by_frame_between_its_segments(self, tmp_path):
        path = make_video(
            tmp_path / "ramp.mp4", [Seg(8, SCOPE), Seg(3, opening(75)), Seg(8, WIDE)], keyint=12
        )
        timeline = analyze_file(path).timeline
        assert [s.preset for s in timeline.segments] == [2.39, 16 / 9]
        (ramp,) = timeline.ramps
        old, new = timeline.segments
        assert new.boundary.kind == "ramp" and new.boundary.frame == ramp.end_frame + 1
        assert old.end == pytest.approx(ramp.start) and new.start == pytest.approx(
            ramp.end + 1 / 25, abs=1e-6
        )
        assert len(ramp.frames) == ramp.end_frame - ramp.start_frame + 1 > 50
        assert all(f.readable for f in ramp.frames) and ramp.confidence == 1.0

    def test_a_sparse_sampling_interval_still_finds_the_exact_frame(self, tmp_path):
        plan = [(SCOPE, 110), (WIDE, 150)]
        path = make_video(tmp_path / "sparse.mp4", [Seg(20, r, level=lv) for r, lv in plan], keyint=25)
        timeline = analyze_file(path, sample_interval_s=6).timeline
        assert [(s.preset, s.boundary.frame) for s in timeline.segments] == [(2.39, 0), (16 / 9, 500)]

    def test_command_line_accepts_a_sampling_interval(self, tmp_path, capsys):
        path = make_video(tmp_path / "scope.mp4", [Seg(12, SCOPE)])
        assert main(["--sample-interval", "4", "--analyze", str(path)]) == 0
        assert load_timeline(capsys.readouterr().out).segments[0].preset == 2.39
        assert main(["--sample-interval", "fast", "--analyze", str(path)]) == 2

    def test_progress_runs_forward_from_reading_the_file_to_checking_the_changes(self, tmp_path):
        plan = [(SCOPE, 110), (WIDE, 150), (SCOPE, 110)]
        path = make_video(tmp_path / "var.mp4", [Seg(8, r, level=lv) for r, lv in plan])
        seen: list[tuple[str, float]] = []
        analyze_file(path, progress=lambda phase, fraction: seen.append((phase, fraction)))
        phases = [p for p, _ in seen]
        assert phases[0] == "reading" and phases[-1] == "checking" and "checking" in phases
        assert phases == sorted(phases, key=["reading", "checking"].index)  # never back to reading
        fractions = [f for _, f in seen]
        assert fractions == sorted(fractions) and 0 <= fractions[0] and fractions[-1] < 1.0
        assert max(f for p, f in seen if p == "reading") == 0.5  # the first pass fills the first half

    def test_command_line_writes_progress_to_a_file_and_rejects_a_missing_name(self, tmp_path, capsys):
        path = make_video(tmp_path / "scope.mp4", [Seg(6, SCOPE)])
        progress = tmp_path / "progress.json"
        assert main(["--progress-file", str(progress), "--analyze", str(path)]) == 0
        last = json.loads(progress.read_text(encoding="utf-8"))
        assert last["phase"] == "checking" and 0.5 < last["fraction"] < 1.0
        assert main(["--analyze", str(path), "--progress-file"]) == 2

    def test_skipped_files_come_back_with_a_reason(self, tmp_path):
        iso = tmp_path / "disc.iso"
        iso.write_bytes(b"x")
        outcome = analyze_file(iso)
        assert outcome.timeline is None and outcome.skip == "iso"
        broken = tmp_path / "broken.mkv"
        broken.write_bytes(bytes(range(256)) * 64)
        assert analyze_file(broken).skip.startswith("broken")

    def test_a_file_with_nothing_readable_gives_an_empty_timeline_with_a_flag(self, tmp_path):
        path = make_video(tmp_path / "black.mp4", [Seg(6, SCOPE, level=16)])
        timeline = analyze_file(path).timeline
        assert timeline.segments == [] and "no_usable_samples" in timeline.flags

    def test_command_line_prints_the_timeline_or_the_skip_reason(self, tmp_path, capsys):
        path = make_video(tmp_path / "scope.mp4", [Seg(6, SCOPE)])
        assert main(["--analyze", str(path)]) == 0
        assert load_timeline(capsys.readouterr().out).segments[0].preset == 2.39
        assert main(["--analyze", str(tmp_path / "x.iso")]) == 1
        assert json.loads(capsys.readouterr().out)["skip"] == "iso"
        assert main(["--analyze"]) == 2
