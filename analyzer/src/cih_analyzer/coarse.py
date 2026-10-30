"""Coarse pass (spec Phase 4 step 2): sample keyframes and measure the active picture on each.

ffmpeg decodes keyframes only, scales them to a small width, and runs `signalstats` and `cropdetect` (reset on every
frame, so each sample is independent). Results are read from the `metadata` filter's printout, which carries the
frame time, the crop rectangle and the average luma. Crop rectangles are scaled back to full-resolution pixels; at
the default 960 px width they are good to about +-4 px at 4K, and the refinement pass (slice 3) re-measures
boundaries at full resolution.

Threshold: `cropdetect`'s `limit` is a fraction of the full luma scale. Limited-range black is 16/255 or 64/1023,
about 0.063; anything below that detects nothing (the whole frame counts as picture). The default of 0.094
(ffmpeg's own 24/255) leaves a little headroom above matte noise, calibrated on letterboxed SDR and PQ fixtures
(tests/test_coarse.py). Dark picture content below the limit is why dark samples are gated later.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .probe import ProbeError, VideoInfo

SCALE_WIDTH = 960
MIN_SAMPLE_INTERVAL_S = 1.0
DEFAULT_LIMIT = 0.094
SCAN_TIMEOUT_S = 4 * 3600.0


@dataclass(frozen=True)
class Sample:
    """One keyframe measurement. `t` is on the player's clock; the rectangle is in full-resolution pixels;
    `yavg` is the frame's average luma on a 0..255 scale whatever the bit depth (limited-range black is 16)."""

    t: float
    x: int
    y: int
    w: int
    h: int
    yavg: float


def bit_depth(pix_fmt: str | None) -> int:
    """Bits per sample from an ffmpeg pixel format name: yuv420p10le is 10, p010le is 10, nv12 and yuv420p are 8.

    `signalstats`' own YBITDEPTH is the estimated bits *needed* by the values (7 for ordinary 8-bit video), so it
    cannot be used to scale luma.
    """
    match = re.search(r"(\d+)(?:le|be)$", pix_fmt or "") or re.match(r"p0?(\d\d)", pix_fmt or "")
    return int(match.group(1)) if match and int(match.group(1)) in (9, 10, 12, 14, 16) else 8


def limit_for(info: VideoInfo) -> float:
    """The `cropdetect` limit for this stream. One value today; kept per stream so a transfer that needs its own
    (Dolby Vision profile 5, whose luma is not display-referred) can be tuned without touching callers."""
    return DEFAULT_LIMIT


def scaled_size(info: VideoInfo, scale_width: int = SCALE_WIDTH) -> tuple[int, int]:
    """Size the frames are scaled to: never upscaled, height even, aspect kept."""
    width = min(scale_width, info.width)
    height = max(2, 2 * round(info.height * width / info.width / 2))
    return width, height


def build_filter(info: VideoInfo, limit: float, scale_width: int, min_interval_s: float) -> str:
    width, height = scaled_size(info, scale_width)
    return ",".join(
        [
            f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{min_interval_s})'",
            f"scale={width}:{height}:flags=area",
            "signalstats",
            f"cropdetect=limit={limit}:round=2:reset=1:skip=0",
            "metadata=mode=print:file=-",
        ]
    )


def parse_metadata(text: str, info: VideoInfo, scale_width: int = SCALE_WIDTH) -> list[Sample]:
    """Turn the `metadata=mode=print` printout into samples. Pure, so it can be tested on canned text."""
    width, height = scaled_size(info, scale_width)
    fx, fy = info.width / width, info.height / height
    scale = 2 ** (bit_depth(info.pix_fmt) - 8)
    samples: list[Sample] = []
    current: dict[str, float] = {}

    def flush() -> None:
        needed = ("t", "x", "y", "w", "h", "yavg")
        if all(k in current for k in needed):
            x, y = round(current["x"] * fx), round(current["y"] * fy)
            w, h = round(current["w"] * fx), round(current["h"] * fy)
            # a matte is centred: centre the crop on its smaller margin, so a dark part of the picture that touches
            # one edge cannot be mistaken for a matte (the rectangle only ever grows; see frames.measure_edges)
            margin_x = max(0, min(x, info.width - (x + w)))
            margin_y = max(0, min(y, info.height - (y + h)))
            samples.append(
                Sample(
                    t=current["t"] - info.start_time,
                    x=margin_x,
                    y=margin_y,
                    w=info.width - 2 * margin_x,
                    h=info.height - 2 * margin_y,
                    yavg=current["yavg"] / scale,
                )
            )
        current.clear()

    for line in text.splitlines():
        if line.startswith("frame:"):
            flush()
            for part in line.split():
                if part.startswith("pts_time:"):
                    try:
                        current["t"] = float(part.split(":", 1)[1])
                    except ValueError:
                        pass
            continue
        key, _, value = line.partition("=")
        try:
            number = float(value)
        except ValueError:
            continue
        name = {
            "lavfi.signalstats.YAVG": "yavg",
            "lavfi.cropdetect.x": "x",
            "lavfi.cropdetect.y": "y",
            "lavfi.cropdetect.w": "w",
            "lavfi.cropdetect.h": "h",
        }.get(key)
        if name:
            current[name] = number
    flush()
    return samples


def coarse_scan(
    path: str | Path,
    info: VideoInfo,
    *,
    limit: float | None = None,
    scale_width: int = SCALE_WIDTH,
    min_interval_s: float = MIN_SAMPLE_INTERVAL_S,
    threads: int | None = None,
    timeout: float = SCAN_TIMEOUT_S,
    progress: Callable[[float], None] | None = None,
) -> list[Sample]:
    """Decode the keyframes of `path` and measure each. Raises ProbeError if ffmpeg fails or times out.

    `progress`, if given, is called about once a second with the share of the file's duration ffmpeg has reached."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise ProbeError("ffmpeg not found on PATH")
    with tempfile.TemporaryDirectory(prefix="cih-coarse-") as scratch:
        progress_file = Path(scratch, "progress.txt")
        cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey"]
        if progress:
            cmd += ["-nostats", "-progress", progress_file.as_posix()]
        if threads:
            cmd += ["-threads", str(threads)]
        cmd += [
            "-i", str(path),
            "-map", f"0:{info.stream_index}",
            "-an", "-sn", "-dn",
            "-vf", build_filter(info, limit if limit is not None else limit_for(info), scale_width, min_interval_s),
            "-f", "null", "-",
        ]  # fmt: skip
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(cmd, stdout=out, stderr=err)  # noqa: S603 - fixed executable, no shell
            deadline = time.monotonic() + timeout
            try:
                while True:
                    try:
                        proc.wait(timeout=1.0)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() > deadline:
                            raise ProbeError(f"ffmpeg timed out after {timeout:.0f} s") from None
                        if progress and info.duration > 0:
                            reached = _reached(progress_file)
                            if reached is not None:
                                progress(min(1.0, max(0.0, reached / info.duration)))
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
            stderr = err.read().decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        tail = stderr.splitlines()
        raise ProbeError(tail[-1] if tail else f"ffmpeg exit {proc.returncode}")
    if progress:
        progress(1.0)
    return parse_metadata(stdout, info, scale_width)


def _reached(progress_file: Path) -> float | None:
    """Seconds of the file ffmpeg says it has processed, from the end of its `-progress` file."""
    try:
        with open(progress_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 2048))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    found = re.findall(r"^out_time_us=(-?\d+)$", tail, flags=re.MULTILINE)
    return int(found[-1]) / 1e6 if found and int(found[-1]) >= 0 else None
