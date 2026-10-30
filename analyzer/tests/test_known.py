"""The list of titles known to change ratio: matching by name and year, and what listing a title does."""

import pytest

from cih_analyzer.analyze import analyze_file
from cih_analyzer.known import BUNDLED, is_known_variable, known_titles, load, normalize, parse_title

from .conftest import NEEDS_FFMPEG
from .synth import Seg, make_video, rect_for_ratio

SCOPE = rect_for_ratio(2.39, 640, 360)


def test_names_are_matched_by_title_and_year_not_punctuation_or_edition():
    assert parse_title("Dunkirk (2017) 4k HDR.mkv") == ("dunkirk", 2017)
    assert parse_title("The Hunger Games Catching Fire (2013)") == parse_title(
        "Hunger Games: Catching Fire (2013) Remux-2160p.mkv"
    )
    assert normalize("Mission: Impossible - Ghost Protocol") == normalize(
        "Mission Impossible – Ghost Protocol"
    )
    assert normalize("Tom & Jerry") == normalize("Tom and Jerry")
    assert parse_title("Some Home Video.mkv") is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/media/movies/Dunkirk (2017)/Dunkirk (2017) 4k HDR.mkv", True),
        ("/media/movies/Oppenheimer (2023)/movie.mkv", True),  # the folder names it
        ("/media/movies/Dunkirk (1958)/Dunkirk (1958) Remux-1080p.mkv", False),  # same title, another year
        ("/media/movies/The Grand Budapest Hotel (2014)/x.mkv", True),
        ("/media/movies/Rings (2017)/Rings (2017) Remux-2160p.mkv", False),
    ],
)
def test_the_bundled_list_matches_a_file_by_its_name_or_folder(path, expected):
    assert is_known_variable(path) is expected


def test_the_bundled_list_is_readable_and_has_no_line_that_fails_to_parse():
    lines = [x.strip() for x in BUNDLED.read_text(encoding="utf-8").splitlines()]
    entries = [x for x in lines if x and not x.startswith("#")]
    assert len(entries) > 40 and all(parse_title(x) for x in entries), [
        x for x in entries if not parse_title(x)
    ]
    assert len(load()) > 40


def test_a_file_of_your_own_adds_titles(tmp_path):
    extra = tmp_path / "mine.txt"
    extra.write_text("# mine\nMy Home Movie (2020)\nnot a title\n", encoding="utf-8")
    known_titles.cache_clear()
    assert not is_known_variable("/media/movies/My Home Movie (2020)/a.mkv")
    assert is_known_variable("/media/movies/My Home Movie (2020)/a.mkv", str(extra))
    assert not is_known_variable("/media/movies/x/a.mkv", str(tmp_path / "missing.txt"))


@NEEDS_FFMPEG
def test_a_listed_title_skips_the_survey_and_says_so(tmp_path):
    folder = tmp_path / "Dunkirk (2017)"
    folder.mkdir()
    listed = make_video(folder / "Dunkirk (2017).mp4", [Seg(100, SCOPE)], keyint=25)
    plain = make_video(tmp_path / "Other (2020).mp4", [Seg(100, SCOPE)], keyint=25)
    timeline = analyze_file(listed, survey_interval_s=8.0).timeline
    assert "known_variable" in timeline.flags and "surveyed" not in timeline.flags
    assert "surveyed" in analyze_file(plain, survey_interval_s=8.0).timeline.flags
    assert "known_variable" not in analyze_file(plain, survey_interval_s=8.0).timeline.flags
