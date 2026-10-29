"""Full-resolution frames for the refinement pass (spec Phase 4 step 6).

The coarse pass looks at scaled-down keyframes. Refinement needs every frame at full resolution, so ffmpeg decodes a
short window, keeps only the luma plane (`extractplanes=y` copies values untouched, so limited-range black stays at
16 or 64) and pipes it out raw. Each frame is reduced on the spot to the edges of its picture and discarded; only
the edges, a frame-to-frame difference and the frame time are kept.

Edges are found against a threshold just above the matte's own noise rather than a fixed black limit, so a dim
shot whose picture is only a few levels above black still shows where the matte ends. The picture is the largest
contiguous block of rows (and of columns) above the threshold, which leaves a burned-in subtitle sitting in the
matte, a few rows away from the picture, out of the measurement.

Edge convention: `top` and `left` are the first picture row and column; `bottom` and `right` are one past the last,
so `(left, top, right - left, bottom - top)` is the picture rectangle.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .coarse import bit_depth
from .probe import ProbeError, VideoInfo

DEFAULT_THRESHOLD = 20.0  # 8-bit luma scale, used when no matte can be sampled
MIN_THRESHOLD = 18.0
MAX_THRESHOLD = 32.0
THRESHOLD_HEADROOM = 2.0
MATTE_MARGIN_PX = 8
MIN_PICTURE_FRACTION = 0.01  # a row or column needs this share of its pixels above the threshold to count
# Matte tolerated inside one picture block, as a share of the dimension. Rows are held tight so a subtitle a few
# lines below the picture stays out; dark vertical strips inside a scene are common, so columns are looser.
ROW_GAP_FRACTION = 0.004
COLUMN_GAP_FRACTION = 0.01
FRAME_TIMEOUT_S = 900.0
_PTS_TIME = re.compile(r"pts_time:\s*(-?\d+(?:\.\d+)?)")


LOPSIDED = 0.02  # margins differing by more than this share of the frame are not a centred matte


@dataclass(frozen=True)
class Edges:
    """`lopsided` marks a measurement whose two margins differed (see `measure_edges`): the rectangle is safe to use
    for a segment, but a frame like that says nothing reliable about where a ramp has got to."""

    top: float
    bottom: float
    left: float
    right: float
    lopsided: bool = field(default=False, compare=False)

    @property
    def rect(self) -> tuple[int, int, int, int]:
        x, y = round(self.left), round(self.top)
        return x, y, round(self.right) - x, round(self.bottom) - y


@dataclass(frozen=True)
class ScanFrame:
    """One decoded frame: its time on the player's clock, its picture edges (None if no picture was found),
    and how much its luma changed from the previous scanned frame (8-bit scale, inside the diff rectangle)."""

    t: float
    edges: Edges | None
    diff: float | None = None
    mean: float | None = (
        None  # mean luma of the whole frame on the 8-bit scale (a dark frame cannot be trusted)
    )


def _largest_block(flags: np.ndarray, max_gap: int) -> tuple[int, int] | None:
    """Start and one-past-end of the longest run of True values, runs closer than `max_gap` counting as one."""
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return None
    breaks = np.flatnonzero(np.diff(idx) > max_gap + 1)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [idx.size - 1]))
    best = int(np.argmax(idx[ends] - idx[starts]))
    return int(idx[starts[best]]), int(idx[ends[best]]) + 1


def measure_edges(luma: np.ndarray, threshold: float) -> Edges | None:
    """The picture's edges in one luma plane, or None if nothing in it rises above `threshold` (native units)."""
    height, width = luma.shape
    above = luma > threshold
    rows = np.count_nonzero(above, axis=1) >= max(1.0, MIN_PICTURE_FRACTION * width)
    cols = np.count_nonzero(above, axis=0) >= max(1.0, MIN_PICTURE_FRACTION * height)
    vertical = _largest_block(rows, max(2, round(ROW_GAP_FRACTION * height)))
    horizontal = _largest_block(cols, max(2, round(COLUMN_GAP_FRACTION * width)))
    if vertical is None or horizontal is None:
        return None
    # A matte is centred. A picture that touches one edge of the frame and stops short of the other (a bright sky
    # over a dark ground, a title card) has lost its dark part, not gained a matte, so each axis is centred on its
    # smaller margin: the rectangle only ever grows, and a picture is never shown bigger than it is.
    margin_y = min(vertical[0], height - vertical[1])
    margin_x = min(horizontal[0], width - horizontal[1])
    lopsided = abs(vertical[0] - (height - vertical[1])) > max(8.0, LOPSIDED * height) or abs(
        horizontal[0] - (width - horizontal[1])
    ) > max(8.0, LOPSIDED * width)
    return Edges(
        top=margin_y, bottom=height - margin_y, left=margin_x, right=width - margin_x, lopsided=lopsided
    )


def matte_level(luma: np.ndarray, rect: tuple[int, int, int, int], native_to_8bit: float) -> float | None:
    """Luma (8-bit scale) that 99% of the matte pixels stay under, sampled outside `rect` plus a margin.
    None when the rectangle fills the frame and there is no matte to sample."""
    height, width = luma.shape
    x, y, w, h = rect
    m = MATTE_MARGIN_PX
    strips = [
        luma[: max(0, y - m), ::4],
        luma[min(height, y + h + m) :, ::4],
        luma[::2, : max(0, x - m)],
        luma[::2, min(width, x + w + m) :],
    ]
    parts = [s.ravel() for s in strips if s.size]
    if not parts:
        return None
    values = np.concatenate(parts)
    if values.size < 64:
        return None
    return float(np.percentile(values, 99)) * native_to_8bit


def threshold_from(levels: list[float | None]) -> float:
    """The edge threshold (8-bit scale) from matte levels sampled in the frames either side of a boundary."""
    known = [v for v in levels if v is not None]
    if not known:
        return DEFAULT_THRESHOLD
    return min(MAX_THRESHOLD, max(MIN_THRESHOLD, max(known) + THRESHOLD_HEADROOM))


def _luma_dtype(info: VideoInfo) -> tuple[np.dtype, int]:
    depth = bit_depth(info.pix_fmt)
    return (np.dtype("<u2"), depth) if depth > 8 else (np.dtype("u1"), 8)


def _stream_luma(
    path: str | Path,
    info: VideoInfo,
    start: float,
    duration: float,
    step: float | None,
    on_frame: Callable[[np.ndarray], bool | None],
    timeout: float,
) -> list[float]:
    """Decode `duration` seconds from `start` (player clock) and hand each luma plane to `on_frame`, which may
    return True to stop early. Returns the frame times (player clock) in decode order."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise ProbeError("ffmpeg not found on PATH")
    chain = []
    if step:
        chain.append(f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{step})'")
    chain += ["showinfo", "extractplanes=y"]
    cmd = [
        exe, "-nostdin", "-hide_banner", "-loglevel", "info",
        "-ss", f"{max(0.0, start):.6f}", "-t", f"{duration:.6f}", "-copyts",
        "-i", str(Path(path).resolve()),
        "-map", f"0:{info.stream_index}", "-an", "-sn", "-dn",
        "-vf", ",".join(chain), "-fps_mode", "passthrough", "-f", "rawvideo", "-",
    ]  # fmt: skip
    dtype, _ = _luma_dtype(info)
    frame_bytes = info.width * info.height * dtype.itemsize
    killed = threading.Event()
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log)  # noqa: S603 - fixed executable, no shell
        if proc.stdout is None:
            raise ProbeError("ffmpeg gave no output stream")

        def stop() -> None:
            killed.set()
            proc.kill()

        timer = threading.Timer(timeout, stop)
        timer.start()
        try:
            while True:
                data = proc.stdout.read(frame_bytes)
                if len(data) < frame_bytes:
                    break
                if on_frame(np.frombuffer(data, dtype=dtype).reshape(info.height, info.width)):
                    break
        finally:
            timer.cancel()
            proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        log.seek(0)
        text = log.read().decode("utf-8", errors="replace")
    stopped_early = killed.is_set()
    if stopped_early:
        raise ProbeError(f"ffmpeg timed out after {timeout:.0f} s")
    times = [float(m.group(1)) - info.start_time for m in _PTS_TIME.finditer(text)]
    if proc.returncode not in (0, None) and not times:
        tail = [ln for ln in text.splitlines() if ln.strip()]
        raise ProbeError(tail[-1] if tail else f"ffmpeg exit {proc.returncode}")
    return times


def read_luma(
    path: str | Path, info: VideoInfo, t: float, timeout: float = FRAME_TIMEOUT_S
) -> np.ndarray | None:
    """The first frame at or after `t` (player clock), or None if there is none."""
    got: list[np.ndarray] = []

    def keep(luma: np.ndarray) -> bool:
        got.append(luma.copy())
        return True

    _stream_luma(path, info, t, 2.0, None, keep, timeout)
    return got[0] if got else None


def matte_levels(
    path: str | Path,
    info: VideoInfo,
    samples: list[tuple[float, tuple[int, int, int, int]]],
) -> list[float | None]:
    """For each `(time, picture rectangle)`, read one frame and report the matte level outside the rectangle."""
    _, depth = _luma_dtype(info)
    to_8bit = 1.0 / (1 << (depth - 8))
    levels = []
    for t, rect in samples:
        luma = read_luma(path, info, t)
        levels.append(None if luma is None else matte_level(luma, rect, to_8bit))
    return levels


def matte_threshold(
    path: str | Path,
    info: VideoInfo,
    samples: list[tuple[float, tuple[int, int, int, int]]],
) -> float:
    """Edge threshold (8-bit scale) for a stretch of the file, from the cleanest matte sampled. A rectangle that is
    too small (a ramp's coarse rectangle, say) puts real picture in its "matte" and raises that sample's level, so
    the lowest level wins."""
    known = [v for v in matte_levels(path, info, samples) if v is not None]
    return threshold_from([min(known)] if known else [])


def scan_edges(
    path: str | Path,
    info: VideoInfo,
    start: float,
    duration: float,
    *,
    threshold: float,
    diff_rect: tuple[int, int, int, int] | None = None,
    step: float | None = None,
    timeout: float = FRAME_TIMEOUT_S,
) -> list[ScanFrame]:
    """Measure every frame (or one per `step` seconds) in [start, start + duration) at full resolution.

    `threshold` is on the 8-bit luma scale. `diff_rect` is the region used for the frame-to-frame difference (it
    should lie inside the picture in both states, so a changing matte does not register as a scene change).
    """
    _, depth = _luma_dtype(info)
    native = threshold * (1 << (depth - 8))
    shrink = float(1 << (depth - 8))
    measured: list[tuple[Edges | None, float | None, float]] = []
    previous: list[np.ndarray] = []

    def measure(luma: np.ndarray) -> None:
        diff = None
        if diff_rect is not None and previous:
            x, y, w, h = diff_rect
            a = luma[y : y + h : 2, x : x + w : 4].astype(np.int32)
            b = previous[0][y : y + h : 2, x : x + w : 4].astype(np.int32)
            if a.size:
                diff = float(np.abs(a - b).mean()) / shrink
        mean = float(luma[::8, ::8].mean()) / shrink
        measured.append((measure_edges(luma, native), diff, mean))
        previous[:] = [luma]

    times = _stream_luma(path, info, start, duration, step, measure, timeout)
    frames: list[ScanFrame] = []
    for i, (edges, diff, mean) in enumerate(measured):
        t = times[i] if i < len(times) else (frames[-1].t + 1 / info.fps if frames else start)
        if frames and t <= frames[-1].t + 1e-6:
            continue  # a decoder can flush its last frames with one repeated timestamp; they add nothing
        frames.append(ScanFrame(t, edges, diff, mean))
    return frames
