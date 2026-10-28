import hashlib
import os

from cih_analyzer.fingerprint import CHUNK, quick_hash


def _write(path, size, fill=b"\x07"):
    path.write_bytes(fill * size)
    return path


def test_small_file_hash_matches_the_definition(tmp_path):
    p = _write(tmp_path / "small.bin", 1000)
    expected = hashlib.sha1(b"\x07" * 1000 + b"\x07" * 1000 + b"1000").hexdigest()  # noqa: S324
    assert quick_hash(p) == expected


def test_same_content_same_hash_and_size_matters(tmp_path):
    a = _write(tmp_path / "a.bin", 5000)
    b = _write(tmp_path / "b.bin", 5000)
    c = _write(tmp_path / "c.bin", 5001)
    assert quick_hash(a) == quick_hash(b)
    assert quick_hash(a) != quick_hash(c)


def test_only_the_first_and_last_chunks_are_read(tmp_path):
    p = _write(tmp_path / "big.bin", 3 * CHUNK)
    before = quick_hash(p)
    with p.open("r+b") as handle:  # change one byte in the middle
        handle.seek(CHUNK + CHUNK // 2)
        handle.write(b"\x00")
    assert quick_hash(p) == before
    with p.open("r+b") as handle:  # change the first byte
        handle.write(b"\x00")
    assert quick_hash(p) != before


def test_the_file_is_not_modified(tmp_path):
    p = _write(tmp_path / "x.bin", 4096)
    stamp = os.stat(p)
    quick_hash(p)
    assert os.stat(p).st_mtime_ns == stamp.st_mtime_ns
