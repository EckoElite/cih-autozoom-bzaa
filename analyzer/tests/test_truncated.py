"""A Matroska file shorter than its own header says is reported as broken instead of being analyzed for days."""

import pytest

from cih_analyzer.probe import truncated_matroska
from cih_analyzer.skip import inspect_file

EBML = (
    bytes.fromhex("1a45dfa3") + bytes([0x80 | 4]) + b"\x42\x82\x88matroska"[:4]
)  # header content is not parsed


def _mkv(declared: int, actual: int, *, unknown: bool = False) -> bytes:
    """A stand-in Matroska file: EBML header, then a Segment whose 8-byte size field says `declared` bytes."""
    size = b"\x01\xff\xff\xff\xff\xff\xff\xff" if unknown else bytes([0x01]) + declared.to_bytes(7, "big")
    head = EBML + bytes.fromhex("18538067") + size
    return head + bytes(max(0, actual - len(head)))


def _write(tmp_path, data: bytes):
    p = tmp_path / "Film (2003) Remux-2160p.mkv"
    p.write_bytes(data)
    return p


def test_complete_file_is_fine(tmp_path):
    head = len(EBML) + 4 + 8
    assert truncated_matroska(_write(tmp_path, _mkv(declared=5_000_000, actual=5_000_000 + head))) is None


def test_cut_short_file_is_reported_with_the_missing_size(tmp_path):
    head = len(EBML) + 4 + 8
    message = truncated_matroska(_write(tmp_path, _mkv(declared=40_000_000, actual=20_000_000 + head)))
    assert message is not None and "cut short" in message and "0.02 GB missing" in message


def test_a_few_bytes_short_is_not_flagged(tmp_path):
    head = len(EBML) + 4 + 8
    assert (
        truncated_matroska(_write(tmp_path, _mkv(declared=5_000_000 + 100, actual=5_000_000 + head))) is None
    )


def test_unknown_segment_size_is_not_compared(tmp_path):
    assert truncated_matroska(_write(tmp_path, _mkv(declared=0, actual=100_000, unknown=True))) is None


@pytest.mark.parametrize("data", [b"", b"not a video", b"\x1a\x45\xdf\xa3", b"\x00" * 5000])
def test_other_files_are_left_alone(tmp_path, data):
    assert truncated_matroska(_write(tmp_path, data)) is None


def test_missing_file_is_left_to_the_probe(tmp_path):
    assert truncated_matroska(tmp_path / "gone.mkv") is None


def test_inspect_reports_it_as_broken_without_probing(tmp_path):
    head = len(EBML) + 4 + 8
    result = inspect_file(str(_write(tmp_path, _mkv(declared=40_000_000, actual=20_000_000 + head))))
    assert result.info is None and result.skip.startswith("broken: file is cut short")
