"""Skip rules that need no file access."""

import pytest

from cih_analyzer.skip import SkipConfig, path_skip_reason


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/media/movies/Film (2020)/Film (2020).iso", "iso"),
        ("/media/movies/Film (2020)/Film (2020).IMG", "iso"),
        ("/media/movies/Film (2020)/poster.jpg", "not-video"),
        ("/media/movies/Film (2020)/Film (2020).srt", "not-video"),
        ("/media/movies/Film (2020) 3D HSBS.mkv", "3d-filename"),
        ("/media/movies/Film (2020) [FSBS].mkv", "3d-filename"),
        ("/media/movies/Film.2020.3D.H-OU.mkv", "3d-filename"),
        ("/media/movies/Film (2020)/Extras/Making of.mkv", "extra"),
        ("/media/movies/Film (2020)/Behind The Scenes/Cast.mkv", "extra"),
        ("/media/movies/Film (2020)/Film (2020)-trailer.mp4", "extra"),
        ("/media/movies/Film (2020)/Interview-featurette.mkv", "extra"),
    ],
)
def test_skipped_by_path(path, reason):
    assert path_skip_reason(path) == reason


@pytest.mark.parametrize(
    "path",
    [
        "/media/movies/Film (2020)/Film (2020) Remux-2160p.mkv",
        "/media/tv/Show/Season 1/Show - S01E02 - Title.mp4",
        "/media/movies/Hourglass (2019)/Hourglass (2019).mkv",  # "hou" inside a word is not the 3D tag
        "/media/movies/The 3 Days (2019)/The 3 Days (2019).mkv",  # a bare 3 is not "3d"
        "/media/movies/Other People (2016)/Other People (2016).mkv",  # "other" as a word, not a folder
        "/media/movies/Short Term 12 (2013)/Short Term 12 (2013).mkv",
    ],
)
def test_feature_files_are_kept(path):
    assert path_skip_reason(path) is None


def test_extras_can_be_included():
    config = SkipConfig(skip_extras=False)
    assert path_skip_reason("/media/movies/Film (2020)/Extras/Making of.mkv", config) is None
    assert path_skip_reason("/media/movies/Film (2020)/Film (2020)-trailer.mp4", config) is None
    assert path_skip_reason("/media/movies/Film (2020) 3D.mkv", config) == "3d-filename"
