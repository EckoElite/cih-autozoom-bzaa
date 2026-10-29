"""Settings from the environment and path mapping."""

from datetime import time

import pytest

from cih_analyzer.config import ConfigError, Settings, in_quiet_hours, parse_quiet_hours
from cih_analyzer.pathmap import PathMap, normalize

RULES = [
    [
        "/media/movies",
        "/movies",
        "/mnt/user/media/Movies/4K Movies",
        "\\\\NAS\\share\\media\\Movies\\4K Movies",
    ],
    ["/media/tv", "/tv", "/mnt/user/media/TV Shows"],
]


def test_normalize_unifies_separators_and_smb_urls_and_keeps_case():
    assert normalize("\\\\NAS\\Share\\Movies\\A.mkv") == "//NAS/Share/Movies/A.mkv"
    assert normalize("smb://NAS/Share/Movies/") == "//NAS/Share/Movies"
    assert normalize("/mnt//user///media/") == "/mnt/user/media"
    assert normalize("/Movies/A.MKV") != normalize("/movies/a.mkv")
    assert normalize("/") == "/"


@pytest.mark.parametrize(
    ("given", "local"),
    [
        ("/movies/Film (2020)/Film.mkv", "/media/movies/Film (2020)/Film.mkv"),
        ("/mnt/user/media/Movies/4K Movies/Film (2020)/Film.mkv", "/media/movies/Film (2020)/Film.mkv"),
        (
            "\\\\NAS\\share\\media\\Movies\\4K Movies\\Film (2020)\\Film.mkv",
            "/media/movies/Film (2020)/Film.mkv",
        ),
        (
            "smb://NAS/share/media/Movies/4K Movies/Film (2020)/Film.mkv",
            "/media/movies/Film (2020)/Film.mkv",
        ),
        ("/media/movies/Film (2020)/Film.mkv", "/media/movies/Film (2020)/Film.mkv"),
        ("/tv/Show/S01/E01.mkv", "/media/tv/Show/S01/E01.mkv"),
        ("/movies", "/media/movies"),
        ("/elsewhere/x.mkv", "/elsewhere/x.mkv"),  # no rule covers it: unchanged
    ],
)
def test_every_name_of_a_file_maps_to_the_container_path(given, local):
    assert PathMap(RULES).to_local(given) == local


def test_a_root_only_matches_at_a_path_boundary_and_case_matters():
    pm = PathMap(RULES)
    assert pm.to_local("/movies2/x.mkv") == "/movies2/x.mkv"
    assert pm.to_local("/Movies/x.mkv") == "/Movies/x.mkv"  # Unraid is case-sensitive
    assert not pm.covers("/movies2/x.mkv") and pm.covers("/movies/x.mkv")


def test_the_longest_alias_wins():
    pm = PathMap([["/media/a", "/data"], ["/media/b", "/data/b"]])
    assert pm.to_local("/data/b/x.mkv") == "/media/b/x.mkv"
    assert pm.to_local("/data/c/x.mkv") == "/media/a/c/x.mkv"


def test_aliases_list_every_name():
    names = PathMap(RULES).aliases("/media/movies/Film (2020)/Film.mkv")
    assert names[0] == "/media/movies/Film (2020)/Film.mkv"
    assert "/movies/Film (2020)/Film.mkv" in names
    assert "//NAS/share/media/Movies/4K Movies/Film (2020)/Film.mkv" in names
    assert PathMap(RULES).aliases("/elsewhere/x") == ["/elsewhere/x"]


def test_unicode_names_survive():
    pm = PathMap(RULES)
    assert pm.to_local("/movies/Café – 日本語 (2020)/x.mkv") == "/media/movies/Café – 日本語 (2020)/x.mkv"


def test_settings_defaults_and_parsing():
    s = Settings.from_env(
        {
            "MEDIA_ROOTS": "/media/movies;/media/tv",
            "PATH_MAP": "/media/movies|/movies|/mnt/user/media/Movies;/media/tv|/tv",
            "PLEX_URL": "http://plex:32400/",
            "PLEX_TOKEN": "secret-token",
            "API_KEY": "k",
            "CONCURRENCY": "2",
            "QUIET_HOURS": "18:00-23:30",
            "PAUSE_ON_TRANSCODE": "0",
            "PLEX_SKIP_SECTIONS": "YouTube;Music",
        }
    )
    assert s.media_roots == ("/media/movies", "/media/tv") and s.concurrency == 2
    assert s.path_map[0] == ("/media/movies", "/movies", "/mnt/user/media/Movies")
    assert s.plex_url == "http://plex:32400" and s.plex_enabled and s.pause_on_transcode is False
    assert s.quiet_hours == (time(18, 0), time(23, 30)) and s.plex_skip_sections == ("YouTube", "Music")
    assert s.port == 8765 and s.scan_interval_s == 3600 and str(s.db_path).endswith("cih-analyzer.sqlite")


def test_secrets_never_appear_in_repr():
    s = Settings.from_env({"PLEX_URL": "http://p", "PLEX_TOKEN": "tok-123", "API_KEY": "key-456"})
    assert "tok-123" not in repr(s) and "key-456" not in repr(s)


@pytest.mark.parametrize(
    "env",
    [
        {"QUIET_HOURS": "evening"},
        {"QUIET_HOURS": "25:00-26:00"},
        {"CONCURRENCY": "0"},
        {"CONCURRENCY": "many"},
        {"PORT": "70000"},
        {"PATH_MAP": "/only/one"},
        {"SCAN_INTERVAL": "1"},
    ],
)
def test_bad_settings_are_rejected_with_a_message(env):
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_quiet_hours_including_a_window_across_midnight():
    window = parse_quiet_hours("22:00-06:00")
    assert in_quiet_hours(window, time(23, 0)) and in_quiet_hours(window, time(5, 59))
    assert not in_quiet_hours(window, time(6, 0)) and not in_quiet_hours(window, time(12, 0))
    day = parse_quiet_hours("18:00-23:00")
    assert in_quiet_hours(day, time(18, 0)) and not in_quiet_hours(day, time(23, 0))
    assert not in_quiet_hours(None, time(12, 0)) and parse_quiet_hours("") is None
