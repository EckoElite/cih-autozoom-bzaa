import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from cih_analyzer.schema import load_timeline


def _frames(first, count, t0=10.0):
    return [
        {
            "f": first + i,
            "t": t0 + i / 24,
            "top": 276.0 - i,
            "bottom": 1884.0 + i,
            "left": 0.0,
            "right": 3840.0,
            "readable": True,
        }
        for i in range(count)
    ]


def _segment(start, end, frame, kind="cut"):
    return {
        "start": start,
        "end": end,
        "active": {"x": 0, "y": 276, "w": 3840, "h": 1608},
        "aspect": 2.388,
        "preset": 2.39,
        "confidence": 0.98,
        "boundary": {"kind": kind, "frame": frame, "scene_cut": True},
    }


@pytest.fixture
def good():
    return {
        "schema": 1,
        "analyzer_version": "0.1.0",
        "params": {"min_segment_s": 3, "dark_yavg": 0, "cropdetect_limit": 0.05},
        "file": {
            "path": "/media/movies/Film (2020).mkv",
            "size": 1234,
            "mtime": 1.7e9,
            "quick_hash": "a" * 40,
        },
        "plex": {"part_id": 1001, "rating_key": 2002},
        "video": {
            "width": 3840,
            "height": 2160,
            "sar": "1:1",
            "dar": 1.7778,
            "fps": 23.976,
            "start_time": 0.0,
            "duration": 100.0,
            "hdr": "HDR10",
        },
        "segments": [_segment(0.0, 10.0, 0), _segment(12.0, 100.0, 288)],
        "ramps": [
            {
                "start": 10.0,
                "end": 12.0,
                "start_frame": 240,
                "end_frame": 287,
                "confidence": 0.9,
                "frames": _frames(240, 48),
            }
        ],
        "flags": ["var"],
    }


def test_a_good_timeline_validates_and_round_trips_with_the_spec_key_names(good):
    timeline = load_timeline(good)
    text = timeline.to_json()
    assert json.loads(text)["schema"] == 1 and "schema_version" not in json.loads(text)
    assert load_timeline(text) == timeline


def test_unknown_keys_are_rejected(good):
    good["surprise"] = 1
    with pytest.raises(ValidationError):
        load_timeline(good)


def test_only_schema_version_1_is_accepted(good):
    good["schema"] = 2
    with pytest.raises(ValidationError):
        load_timeline(good)


def test_overlapping_segments_are_rejected(good):
    good["segments"][1]["start"] = 9.0
    with pytest.raises(ValidationError, match="overlap"):
        load_timeline(good)


def test_a_segment_must_end_after_it_starts(good):
    good["segments"][0]["end"] = 0.0
    with pytest.raises(ValidationError):
        load_timeline(good)


def test_a_ramp_stores_every_frame(good):
    good["ramps"][0]["frames"].pop()
    with pytest.raises(ValidationError, match="every frame"):
        load_timeline(good)


def test_ramp_frames_must_be_consecutive_and_increasing_in_time(good):
    bad = deepcopy(good)
    bad["ramps"][0]["frames"][5]["f"] = 999
    with pytest.raises(ValidationError, match="consecutive"):
        load_timeline(bad)
    bad = deepcopy(good)
    bad["ramps"][0]["frames"][5]["t"] = bad["ramps"][0]["frames"][4]["t"]
    with pytest.raises(ValidationError, match="increase"):
        load_timeline(bad)


@pytest.mark.parametrize("hdr", ["SDR", "HDR10", "HDR10+", "HLG", "DV-P5", "DV-P7", "DV-P8", "DV-P10"])
def test_hdr_labels(good, hdr):
    good["video"]["hdr"] = hdr
    assert load_timeline(good).video.hdr == hdr


def test_an_unknown_hdr_label_and_a_bad_hash_are_rejected(good):
    bad = deepcopy(good)
    bad["video"]["hdr"] = "Dolby"
    with pytest.raises(ValidationError):
        load_timeline(bad)
    bad = deepcopy(good)
    bad["file"]["quick_hash"] = "xyz"
    with pytest.raises(ValidationError):
        load_timeline(bad)


def test_a_file_with_no_ramps_is_fine(good):
    good["ramps"] = []
    assert load_timeline(good).ramps == []
