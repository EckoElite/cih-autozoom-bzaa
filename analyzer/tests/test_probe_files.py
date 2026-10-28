"""The probe and the skip entry point against small generated videos."""

import pytest

from cih_analyzer.probe import NoVideoStream, ProbeError, probe_video
from cih_analyzer.skip import inspect_file

from .conftest import NEEDS_FFMPEG

pytestmark = NEEDS_FFMPEG


def test_plain_progressive_sdr(media):
    info = probe_video(media["plain"])
    assert (info.width, info.height, info.sar) == (320, 180, "1:1")
    assert info.dar == pytest.approx(320 / 180)
    assert info.fps == pytest.approx(25)
    assert not info.vfr and not info.interlaced
    assert info.hdr == "SDR" and info.hdr_extra == ()
    assert info.start_time == pytest.approx(0, abs=0.1)
    assert info.duration == pytest.approx(2, abs=0.2)


def test_cover_art_is_not_mistaken_for_the_video(media):
    info = probe_video(media["cover"])
    assert (info.width, info.height) == (320, 180)  # the cover is 64x64
    assert info.stream_index == 0


def test_display_ratio_uses_the_pixel_aspect_ratio(media):
    info = probe_video(media["nonsquare"])
    assert (info.width, info.height, info.sar) == (160, 120, "32:27")
    assert info.dar == pytest.approx(160 * 32 / (120 * 27))  # 1.5802, not the 1.333 of the raw frame


def test_non_zero_start_time_is_reported(media):
    assert probe_video(media["offset"]).start_time == pytest.approx(5, abs=0.2)


@pytest.mark.parametrize(("name", "label"), [("hdr10", "HDR10"), ("hlg", "HLG")])
def test_hdr_transfers(media, name, label):
    info = probe_video(media[name])
    assert info.hdr == label
    assert info.pix_fmt == "yuv420p10le" and info.color_primaries == "bt2020"


def test_interlaced_field_order_is_flagged(media):
    assert probe_video(media["interlaced"]).interlaced


def test_audio_only_has_no_video(media):
    with pytest.raises(NoVideoStream):
        probe_video(media["audio_only"])


def test_corrupt_file_raises_probe_error(media):
    with pytest.raises(ProbeError):
        probe_video(media["corrupt"])


def test_unicode_file_name(media):
    assert probe_video(media["unicode"]).width == 320


def test_inspect_returns_info_for_a_normal_file(media):
    result = inspect_file(str(media["plain"]))
    assert result.skip is None and result.info is not None


def test_inspect_never_raises_for_bad_files(media):
    assert inspect_file(str(media["audio_only"])).skip == "audio-only"
    broken = inspect_file(str(media["corrupt"]))
    assert broken.info is None and broken.skip.startswith("broken: ")
    missing = inspect_file(str(media["plain"].with_name("missing.mp4")))
    assert missing.skip.startswith("broken: ")


def test_inspect_skips_by_path_without_touching_the_file(tmp_path):
    assert inspect_file(str(tmp_path / "Film (2020)" / "Film.iso")).skip == "iso"
    assert inspect_file(str(tmp_path / "Film 3D HSBS.mkv")).skip == "3d-filename"
    assert inspect_file(str(tmp_path / "Film" / "Extras" / "x.mkv")).skip == "extra"
