"""HDR classification from canned ffprobe data (the Dolby Vision shapes follow real ffprobe output)."""

from cih_analyzer.probe import classify_hdr

DOVI_P8 = {
    "side_data_type": "DOVI configuration record",
    "dv_version_major": 1,
    "dv_version_minor": 0,
    "dv_profile": 8,
    "dv_level": 6,
    "rpu_present_flag": 1,
    "el_present_flag": 0,
    "bl_present_flag": 1,
    "dv_bl_signal_compatibility_id": 1,
}
HDR10PLUS = "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"


def test_sdr_has_no_extras():
    assert classify_hdr({"color_transfer": "bt709"}) == ("SDR", ())
    assert classify_hdr({}) == ("SDR", ())


def test_hdr10_and_hlg():
    assert classify_hdr({"color_transfer": "smpte2084"}) == ("HDR10", ())
    assert classify_hdr({"color_transfer": "arib-std-b67"}) == ("HLG", ())


def test_hdr10_plus_needs_the_frame_side_data():
    stream = {"color_transfer": "smpte2084"}
    assert classify_hdr(stream, ["Mastering display metadata", HDR10PLUS]) == ("HDR10+", ("HDR10",))
    assert classify_hdr(stream, ["Mastering display metadata"]) == ("HDR10", ())


def test_dolby_vision_profile_8_with_hdr10_plus_like_the_carrie_file():
    stream = {"color_transfer": "smpte2084", "side_data_list": [DOVI_P8]}
    assert classify_hdr(stream, [HDR10PLUS]) == ("DV-P8", ("HDR10+", "HDR10"))


def test_dolby_vision_profile_8_over_plain_hdr10():
    stream = {"color_transfer": "smpte2084", "side_data_list": [DOVI_P8]}
    assert classify_hdr(stream, []) == ("DV-P8", ("HDR10",))


def test_dolby_vision_profile_8_with_hdr10_compatible_layer_and_no_transfer_tag():
    assert classify_hdr({"side_data_list": [DOVI_P8]}) == ("DV-P8", ("HDR10",))


def test_dolby_vision_rpu_in_frames_without_a_configuration_record_is_flagged():
    # a stream-copied clip of the Carrie file: the muxer dropped the record, the frames still carry the RPU
    stream = {"color_transfer": "smpte2084"}
    frames = [HDR10PLUS, "Dolby Vision RPU Data", "Dolby Vision Metadata"]
    assert classify_hdr(stream, frames) == ("HDR10+", ("HDR10", "DV-RPU"))
    assert classify_hdr(stream, ["Dolby Vision RPU Data"]) == ("HDR10", ("DV-RPU",))


def test_dolby_vision_profile_5_has_no_hdr10_layer():
    p5 = {**DOVI_P8, "dv_profile": 5, "dv_bl_signal_compatibility_id": 0}
    assert classify_hdr({"side_data_list": [p5]}) == ("DV-P5", ())


def test_dolby_vision_profile_7_dual_layer():
    p7 = {**DOVI_P8, "dv_profile": 7, "el_present_flag": 1, "dv_bl_signal_compatibility_id": 6}
    assert classify_hdr({"color_transfer": "smpte2084", "side_data_list": [p7]}) == ("DV-P7", ("HDR10",))


def test_other_side_data_is_ignored():
    stream = {
        "color_transfer": "smpte2084",
        "side_data_list": [{"side_data_type": "Mastering display metadata"}],
    }
    assert classify_hdr(stream) == ("HDR10", ())
