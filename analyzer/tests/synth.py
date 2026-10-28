"""Synthetic videos with a known active picture in every frame (the spec's T2 layer).

Frames are built as raw yuv420p bytes in pure Python and encoded with ffmpeg, so a test states exactly where the
picture is at every frame and at what brightness. Mattes are limited-range black (16) with a little noise, the
picture is a two-level texture around `level`, and extra bright boxes can be laid over the matte (subtitles) or
over a dark frame (credits).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MATTE = 16
Rect = tuple[int, int, int, int]  # x, y, w, h


@dataclass(frozen=True)
class Seg:
    """`seconds` of video. `rect` is the picture's rectangle, or a function of the 0-based frame index within the
    segment (for ramps). `level` is the picture's mean luma (16 makes a black frame). `boxes` are (x, y, w, h,
    luma) overlays drawn after the picture."""

    seconds: float
    rect: Rect | Callable[[int], Rect]
    level: int = 110
    boxes: tuple[tuple[int, int, int, int, int], ...] = field(default=())


def rect_for_ratio(ratio: float, width: int, height: int) -> Rect:
    """The largest even-sized rectangle of `ratio` centred in the frame (letterbox or pillarbox)."""
    if ratio >= width / height:
        h = min(height, 2 * round(width / ratio / 2))
        return 0, (height - h) // 2, width, h
    w = min(width, 2 * round(height * ratio / 2))
    return (width - w) // 2, 0, w, height


def _matte_row(width: int) -> bytes:
    return (bytes([MATTE, MATTE + 1, MATTE, MATTE - 1]) * (width // 4 + 1))[:width]


def _frame(
    width: int, height: int, rect: Rect, level: int, boxes: tuple[tuple[int, int, int, int, int], ...]
) -> bytes:
    x0, y0, rw, rh = rect
    matte = _matte_row(width)
    amp = max(0, min(30, level - MATTE))  # a black frame (level 16) has no texture at all
    texture = (bytes([level - amp] * 4) + bytes([level + amp] * 4)) * (rw // 8 + 1)
    picture = matte[:x0] + texture[:rw] + matte[x0 + rw :]
    rows = [picture if y0 <= y < y0 + rh else matte for y in range(height)]
    for bx, by, bw, bh, bl in boxes:
        for y in range(by, min(height, by + bh)):
            row = rows[y]
            rows[y] = row[:bx] + bytes([bl]) * bw + row[bx + bw :]
    chroma = bytes([128]) * (width // 2 * (height // 2))
    return b"".join(rows) + chroma + chroma


def make_video(
    out: Path,
    segs: list[Seg],
    *,
    size: tuple[int, int] = (640, 360),
    fps: int = 25,
    keyint: int = 25,
    pq: bool = False,
    extra_vf: str | None = None,
) -> Path:
    """Encode `segs` back to back. Keyframes every `keyint` frames, none at scene changes, so sampling is regular."""
    width, height = size
    vf = []
    if pq:
        vf.append("setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc")
    if extra_vf:
        vf.append(extra_vf)
    codec = (
        ["-c:v", "libx265", "-x265-params", f"log-level=error:keyint={keyint}:min-keyint={keyint}:scenecut=0",
         "-pix_fmt", "yuv420p10le"]
        if pq
        else ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", "-g", str(keyint), "-keyint_min", str(keyint),
              "-sc_threshold", "0", "-pix_fmt", "yuv420p"]
    )  # fmt: skip
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p",
           "-s", f"{width}x{height}", "-r", str(fps), "-i", "-"]  # fmt: skip
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += [*codec, str(out)]
    cache: dict[tuple, bytes] = {}
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as proc:  # noqa: S603
        assert proc.stdin is not None
        for seg in segs:
            for i in range(round(seg.seconds * fps)):
                rect = seg.rect(i) if callable(seg.rect) else seg.rect
                key = (rect, seg.level, seg.boxes)
                if key not in cache:
                    cache[key] = _frame(width, height, rect, seg.level, seg.boxes)
                proc.stdin.write(cache[key])
        proc.stdin.close()
        assert proc.wait() == 0
    return out
