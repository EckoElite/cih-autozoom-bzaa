"""Cut or ramp, and the fitted curve of a ramp (spec Phase 4 step 6), on measured edges. Pure functions: nothing here
touches a video, so the maths is tested on hand-built measurements.

Input is one boundary's frames (from `frames.scan_edges`) plus the picture edges either side of it. For each edge that
moves between the two states a frame's position is a progress number: 0 at the old edge, 1 at the new one, snapped
to exactly 0 or 1 when within a pixel of either end. Each edge's progress is cleaned with a running median (drops
isolated misreads), then made non-decreasing with isotonic regression (pool-adjacent-violators), which is the closest
monotone curve to the measurements. From that:

* the last frame where every edge is still at the old position and the first where every edge is at the new one
  bound the change;
* with at least `min_ramp_frames` readable frames between them it is a **ramp**, otherwise a **cut**;
* a cut's first new frame is where progress passes one half, or, when the change falls in a stretch of unreadable
  frames (a fade through black), the frame with the largest picture change in that stretch;
* a ramp's per-frame edges are the isotonic fit of the raw measurements, lightly smoothed, kept strictly
  monotonic and within `fit_tolerance_px` of every readable measurement (smoothing is reduced until it is).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np

from .frames import Edges, ScanFrame

EDGE_NAMES = ("top", "bottom", "left", "right")
EPS = 1e-9
STABLE_PX = 2.0
STABLE_SHARE = 0.8
ARRIVAL_SHARE = 0.1  # a ramp is at its destination when it is within this share of its furthest excursion
DRIFT_PX = 1.0  # most the picture edge may drift between the two halves of a window's quiet stretch
SCENE_CUT_MIN_DIFF = 6.0
SCENE_CUT_RATIO = 4.0
SMOOTHING_WINDOWS = (7, 5, 3, 1)
JUMP_FROM = 0.1  # a cut: progress is below this before it ...
JUMP_TO = 0.9  # ... and above this on the very next frame ...
JUMP_HOLD = 0.85  # ... and stays above this for the frames that follow
JUMP_FRAMES = 8  # how many readable frames either side have to agree
JUMP_MIN_FRAMES = 3  # and at least this many (a window that ends at the cut says nothing)

# A matte that opens or closes in post is animated by a formula: constant speed, or an ease in and out. When the
# measured edge follows one of those curves closely, the curve is a better description of the ramp than the
# measurements, which carry a pixel or two of noise (more in a dim shot, where the edge is a faint step); playing the
# measurements back would make the picture edge move in uneven steps.
PATTERN_MODELS = ("linear", "smooth", "ease_in", "ease_out")  # in order of preference
PATTERN_RMS_PX = 1.5  # most the curve may be from the measurements, as a root mean square
PATTERN_MAX_PX = 3.5  # and from any one of them (a few exceptions are allowed and counted as misreads)
PATTERN_MAX_EXCEPTIONS = 0.1
PATTERN_MIN_FRAMES = 5  # fewer frames cannot show a pattern
PATTERN_REF_FRAMES = 10  # frames either side of a ramp that give its two end positions (fine measurements)
PATTERN_PREFER_SIMPLER = 1.15  # a simpler model wins unless a later one fits this much better


@dataclass(frozen=True)
class RampParams:
    snap_px: float = (
        1.0  # edges closer than this to a state's edge are at that state (integer edges: exact matches)
    )
    min_move_px: float = 6.0  # an edge that differs by less between the two states is not tracked
    min_ramp_frames: int = 3
    jump_px: float = 6.0  # an edge that moves this far between consecutive frames jumped, it did not ease
    fit_tolerance_px: float = 2.0
    median_window: int = 5
    outlier_window: int = 9
    outlier_px: float = 8.0
    max_unreadable_share: float = 0.25


@dataclass(frozen=True)
class FitFrame:
    """One ramp frame: `f` and `t` are the frame number and time on the player's clock, `fitted` the stored edges,
    `raw` what was measured (None where unreadable)."""

    f: int
    t: float
    fitted: Edges
    raw: Edges | None
    readable: bool


def _shape(model: str, s: np.ndarray) -> np.ndarray:
    if model == "linear":
        return s
    if model == "smooth":
        return s * s * (3.0 - 2.0 * s)
    if model == "ease_in":
        return s * s
    return 1.0 - (1.0 - s) ** 2  # ease_out


@dataclass(frozen=True)
class PatternFit:
    """`values[k]` is the fitted progress (0 at the old edge, 1 at the new) of ramp frame k."""

    model: str
    start: float  # frame index (fractional, from the first ramp frame) where the edge starts to move
    length: float  # frames it takes
    values: tuple[float, ...]
    rms_px: float
    exceptions: tuple[int, ...]


def _grid_fit(
    model: str, k: np.ndarray, p: np.ndarray, t0s: np.ndarray, lengths: np.ndarray
) -> tuple[float, float, float]:
    """Best (start, length, mean squared error) of `model` over the grid; k and p are the readable frames."""
    s = (k[None, None, :] - t0s[:, None, None]) / lengths[None, :, None]
    fitted = _shape(model, np.clip(s, 0.0, 1.0))
    err = ((fitted - p[None, None, :]) ** 2).mean(axis=2)
    i, j = np.unravel_index(int(np.argmin(err)), err.shape)
    return float(t0s[i]), float(lengths[j]), float(err[i, j])


def fit_pattern(progress: list[float | None], span_px: float) -> PatternFit | None:
    """Fit a constant-speed or eased curve to a ramp's per-frame progress (0 to 1, None where unreadable).

    Each model has two unknowns, when the edge starts and how many frames it takes, found by a coarse search and then
    a fine one. The simplest model that fits within PATTERN_RMS_PX is returned, or None when none does (a ramp that
    stalls, reverses or changes speed is not a pattern, and keeps its measured curve)."""
    n = len(progress)
    idx = [i for i, v in enumerate(progress) if v is not None]
    if n < PATTERN_MIN_FRAMES or len(idx) < PATTERN_MIN_FRAMES or span_px <= 0:
        return None
    k = np.array(idx, dtype=float)
    p = np.array([progress[i] for i in idx], dtype=float)
    coarse_t0 = np.arange(-3.0, n + 1.0, 0.5)
    coarse_len = np.arange(1.0, n + 5.0, 0.5)
    best: PatternFit | None = None
    for model in PATTERN_MODELS:
        t0, length, _ = _grid_fit(model, k, p, coarse_t0, coarse_len)
        t0, length, err = _grid_fit(
            model,
            k,
            p,
            np.arange(t0 - 0.5, t0 + 0.51, 0.05),
            np.arange(max(0.5, length - 0.5), length + 0.51, 0.05),
        )
        rms = (err**0.5) * span_px
        if rms > PATTERN_RMS_PX:
            continue
        if best is not None and best.rms_px <= rms * PATTERN_PREFER_SIMPLER:
            continue
        values = _shape(model, np.clip((np.arange(n) - t0) / length, 0.0, 1.0))
        residual = np.abs(values[idx] - p) * span_px
        exceptions = tuple(idx[i] for i in np.flatnonzero(residual > PATTERN_MAX_PX))
        if len(exceptions) > PATTERN_MAX_EXCEPTIONS * len(idx):
            continue
        best = PatternFit(model, t0, length, tuple(float(v) for v in values), rms, exceptions)
    return best


def _strictly_between(values: tuple[float, ...], span_px: float) -> list[float]:
    """Progress values nudged so they rise strictly and stay inside (0, 1): a flat stretch at an end of the fitted
    curve (the edge has not started, or has arrived) would read as a stall.

    The nudge adds up over a flat stretch, one step per frame, and the stretch can be long (the scanned window runs on
    past the ramp, or starts well before it): at a thousandth of the distance per frame, 35 flat frames after a
    231 px ramp held its last frames 8 px short of the new edge, and 170 flat frames before one lifted the start of
    the next by 16 px (Everything Everywhere at 1956 s and 4385 s). The steps are so small that the whole nudge, however
    long the flat stretch, stays under a quarter of a pixel."""
    n = len(values)
    eps = min(1e-3, 1.0 / (n + 1) / 10, 0.25 / (max(span_px, 1.0) * (n + 1)))
    out = list(values)
    for i in range(n):
        out[i] = max(out[i], (out[i - 1] if i else 0.0) + eps)
    for i in range(n - 1, -1, -1):
        out[i] = min(out[i], (out[i + 1] if i < n - 1 else 1.0) - eps)
    return out


def _fine_progress(
    frames: list[ScanFrame], name: str, start: int, end: int, a: float, b: float, params: RampParams
) -> list[float | None] | None:
    """Progress of each ramp frame from the sub-pixel measurements, with the two end positions taken from the same
    kind of measurement on the settled frames either side. None when those are not there."""
    reach = max(3.0, 2 * params.snap_px)

    def settled(chunk: list[ScanFrame], at: float) -> list[float]:
        return [
            getattr(f.fine, name)
            for f in chunk
            if f.fine is not None and f.edges is not None and abs(getattr(f.edges, name) - at) <= reach
        ]

    head = settled(frames[max(0, start - PATTERN_REF_FRAMES) : start], a)
    tail = settled(frames[end : end + PATTERN_REF_FRAMES], b)
    if len(head) < 2 or len(tail) < 2:
        return None
    a_fine, b_fine = statistics.median(head), statistics.median(tail)
    if (b_fine - a_fine) * (b - a) <= 0 or abs(b_fine - a_fine) < 0.5 * abs(b - a):
        return None
    out: list[float | None] = []
    for f in frames[start:end]:
        if f.fine is None or f.edges is None:
            out.append(None)
            continue
        q = (getattr(f.fine, name) - a_fine) / (b_fine - a_fine)
        out.append(None if q < -0.15 or q > 1.15 else min(1.0, max(0.0, q)))
    return out


@dataclass(frozen=True)
class Transition:
    """`first_new` indexes the input frame list: the first frame at the new ratio. A ramp's own frames are those
    before it, from `ramp_start`."""

    kind: Literal["cut", "ramp", "none"]
    first_new: int
    ramp_start: int | None = None
    ramp: tuple[FitFrame, ...] = ()
    scene_cut: bool | None = None
    confidence: float = 1.0
    flags: tuple[str, ...] = field(default=())


def reference_edges(frames: list[ScanFrame], *, head: bool, stable_s: float) -> tuple[Edges | None, bool]:
    """The picture edges at one end of a window: per-edge median of the readable frames in the first (or last)
    `stable_s` seconds, and whether they hold still (at least 80% within STABLE_PX of the median, on every edge)."""
    if not frames:
        return None, False
    span = frames[0].t + stable_s if head else frames[-1].t - stable_s
    inside = [f for f in frames if f.edges is not None and (f.t <= span if head else f.t >= span)]
    chosen = [f.edges for f in inside]
    if len(chosen) < 3:
        return None, False
    med = {n: statistics.median(getattr(e, n) for e in chosen) for n in EDGE_NAMES}
    steady = all(
        sum(abs(getattr(e, n) - med[n]) <= STABLE_PX for e in chosen) >= STABLE_SHARE * len(chosen)
        for n in EDGE_NAMES
    )
    # a slow ramp can sit inside the tolerance for a whole second, so also require that no edge is drifting
    times = [f.t for f in inside]
    if steady and times[-1] > times[0]:
        for n in EDGE_NAMES:
            slope = statistics.linear_regression(times, [getattr(f.edges, n) for f in inside]).slope
            steady = steady and abs(slope) * (times[-1] - times[0]) <= DRIFT_PX
    return Edges(**med), steady


def isotonic_increasing(values: list[float], weights: list[float] | None = None) -> list[float]:
    """Pool-adjacent-violators: the non-decreasing sequence nearest (least squares) to `values`."""
    w = weights or [1.0] * len(values)
    blocks: list[list[float]] = []  # weighted sum, weight, count
    for v, wt in zip(values, w, strict=True):
        blocks.append([v * wt, wt, 1])
        while len(blocks) > 1 and blocks[-2][0] * blocks[-1][1] > blocks[-1][0] * blocks[-2][1]:
            top = blocks.pop()
            blocks[-1][0] += top[0]
            blocks[-1][1] += top[1]
            blocks[-1][2] += top[2]
    out: list[float] = []
    for total, weight, count in blocks:
        out.extend([total / weight] * int(count))
    return out


def _running_median(values: list[float | None], window: int) -> list[float | None]:
    """Median of each value and its neighbours. The window is symmetric and shrinks at the ends of the series: a
    lopsided window on a rising series pulls its median toward the inside, which would turn the first and last
    frames of a steep ramp into "outliers"."""
    half = window // 2
    out: list[float | None] = []
    for i, v in enumerate(values):
        if v is None:
            out.append(None)
            continue
        reach = min(half, i, len(values) - 1 - i)
        near = [x for x in values[i - reach : i + reach + 1] if x is not None]
        out.append(statistics.median(near))
    return out


def _progress(frames: list[ScanFrame], name: str, a: float, b: float, snap: float) -> list[float | None]:
    out: list[float | None] = []
    margin = snap + 1.0
    for f in frames:
        if f.edges is None:
            out.append(None)
            continue
        v = getattr(f.edges, name)
        if abs(v - a) < snap:
            out.append(0.0)
        elif abs(v - b) < snap:
            out.append(1.0)
        else:
            p = (v - a) / (b - a)
            # far outside the range between the two states: a misread (a subtitle block, a dark scene), not progress
            out.append(
                None if (v - a) * (b - a) < -margin or (v - b) * (b - a) > margin else min(1.0, max(0.0, p))
            )
    return out


def _jump_cut(bar: list[float | None]) -> int | None:
    """Index of the first frame after a change that goes from the old picture to the new one in a single frame and
    holds there, or None. A ramp never covers most of its distance in one frame; a cut does, and in a dim shot the
    settled edge then reads a pixel or two either side of the new position, which pooled into a ramp as long as that
    flicker lasted when only the monotone fit was asked (Everything Everywhere at 2576 s, 2634 s and 5583 s)."""
    seen = [i for i, v in enumerate(bar) if v is not None]
    for k in range(1, len(seen)):
        i, j = seen[k - 1], seen[k]
        if j != i + 1 or bar[i] > JUMP_FROM or bar[j] < JUMP_TO:  # type: ignore[operator]
            continue  # (a change across unreadable frames is placed by the caller from the biggest picture change)
        before = [bar[x] for x in seen[max(0, k - JUMP_FRAMES) : k]]
        after = [bar[x] for x in seen[k : k + JUMP_FRAMES]]
        if len(before) < JUMP_MIN_FRAMES or len(after) < JUMP_MIN_FRAMES:
            continue
        if all(v <= JUMP_FROM for v in before) and all(v >= JUMP_HOLD for v in after):  # type: ignore[operator]
            return j
    return None


def _isotonic_series(values: list[float | None]) -> list[float | None]:
    idx = [i for i, v in enumerate(values) if v is not None]
    fitted = isotonic_increasing([values[i] for i in idx])  # type: ignore[misc]
    out: list[float | None] = [None] * len(values)
    for i, v in zip(idx, fitted, strict=True):
        out[i] = v
    return out


def _reject_outliers(raw: list[float | None], window: int, tol: float) -> list[float | None]:
    """Blank out measurements that sit more than `tol` pixels from the running median of their neighbours. In a
    monotone sequence the median is the middle value, so a real ramp is never touched, while a single flash or
    dark frame (an edge read at the wrong place) would drag a least-squares monotone fit far off."""
    half = window // 2
    out: list[float | None] = []
    for i, v in enumerate(raw):
        if v is None:
            out.append(None)
            continue
        # symmetric, shrinking at the ends (see _running_median): with the window cut off on one side the median of
        # a steep ramp sits several frames' worth of pixels from the frame itself, and the ends read as outliers
        reach = min(half, i, len(raw) - 1 - i)
        near = [x for x in raw[i - reach : i + reach + 1] if x is not None]
        out.append(v if abs(v - statistics.median(near)) <= tol else None)
    return out


def _fit_edge(
    raw: list[float | None], a: float, b: float, tol: float, max_misfit: float = 0.03
) -> tuple[list[float], bool, list[int]]:
    """Fitted values for one moving edge over the ramp frames, whether they are within `tol` of the readable
    measurements, and which measurements are the exceptions. A few exceptions (at most `max_misfit` of the readable
    frames) are tolerated and reported as misreads; more than that makes the fit not ok. `a` and `b` are the edge
    before and after; they anchor both ends."""
    n = len(raw)
    sign = 1.0 if b > a else -1.0
    span = abs(b - a)
    u = [None if v is None else min(span, max(0.0, (v - a) * sign)) for v in raw]
    idx = [i for i, v in enumerate(u) if v is not None]
    heavy = 1e3
    positions = [-1, *idx, n]
    series = [0.0, *[u[i] for i in idx], span]  # type: ignore[misc]
    pav = isotonic_increasing(series, [heavy, *[1.0] * len(idx), heavy])
    known = dict(zip(positions, pav, strict=True))
    dense: list[float] = []
    for i in range(n):
        if i in known:
            dense.append(known[i])
            continue
        left = max(p for p in positions if p < i)
        right = min(p for p in positions if p > i)
        dense.append(known[left] + (known[right] - known[left]) * (i - left) / (right - left))

    def smooth(window: int) -> list[float]:
        pad = window // 2
        # Padded with the series' own first and last values. Padding with 0 and `span` (the two states) assumed the
        # ramp starts at the old edge and ends at the new one, but a ramp that a cut ends part-way (Everything
        # Everywhere at 4385 s: the matte stops at 87% and the next shot is at the new ratio) was dragged toward `span`
        # over its last frames, up to 11 px, and the frames then counted as misreads and were filled.
        padded = [dense[0]] * pad + dense + [dense[-1]] * pad
        out = [sum(padded[i : i + window]) / window for i in range(n)]
        eps = min(1e-3, span / (n + 1) / 10)
        for i in range(n):  # strictly increasing, kept inside the anchors
            out[i] = max(out[i], (out[i - 1] if i else 0.0) + eps)
        for i in range(n - 1, -1, -1):
            out[i] = min(out[i], (out[i + 1] if i < n - 1 else span) - eps)
        return out

    def misfits(candidate: list[float]) -> list[int]:
        return [i for i, c in enumerate(candidate) if u[i] is not None and abs(c - u[i]) > tol]  # type: ignore[operator]

    allowed = int(max_misfit * len(idx))
    chosen, bad, ok = smooth(1), [], False
    for window in SMOOTHING_WINDOWS:
        candidate = smooth(window)
        bad = misfits(candidate)
        if len(bad) <= allowed:
            chosen, ok = candidate, True
            break
    return [a + sign * c for c in chosen], ok, (bad if ok else [])


def _last_old_first_new(qbar: list[float | None]) -> tuple[int | None, int | None]:
    last_old = None
    first_new = None
    for i, q in enumerate(qbar):
        if q is not None and q <= EPS:
            last_old = i
    start = -1 if last_old is None else last_old
    for i in range(start + 1, len(qbar)):
        q = qbar[i]
        if q is not None and q >= 1 - EPS:
            first_new = i
            break
    return last_old, first_new


def _scene_cut(frames: list[ScanFrame], at: int) -> bool | None:
    here = frames[at].diff
    if here is None:
        return None
    others = [f.diff for i, f in enumerate(frames) if i != at and f.diff is not None]
    baseline = statistics.median(others) if others else 0.0
    return here >= max(SCENE_CUT_MIN_DIFF, SCENE_CUT_RATIO * baseline)


def _excursion_start(frames: list[ScanFrame], old: Edges, new: Edges, params: RampParams) -> int | None:
    """Index of the first frame after a jump to a picture that is neither `old` nor `new` nor between them, when
    that picture then eases gradually into `new` (a cut to a small inset that grows back to full size, as in a film
    opening). A subtitle that touches the picture also leaves the box, but it vanishes with a jump, not gradually."""
    box = {}
    for n in EDGE_NAMES:
        a, b = getattr(old, n), getattr(new, n)
        margin = max(3.0, 0.05 * abs(a - b))
        box[n] = (min(a, b) - margin, max(a, b) + margin)

    def outside(e: Edges) -> bool:
        return any(not box[n][0] <= getattr(e, n) <= box[n][1] for n in EDGE_NAMES)

    def gap(a: Edges, b: Edges) -> float:
        return max(abs(getattr(a, n) - getattr(b, n)) for n in EDGE_NAMES)

    readable = [(i, f.edges) for i, f in enumerate(frames) if f.edges is not None]
    for pos, (i, e) in enumerate(readable):
        if not outside(e):
            continue
        if pos == 0 or gap(readable[pos - 1][1], e) < params.jump_px:
            return None  # the run does not start with a jump (or starts the window): not this shape
        end = pos
        while end + 1 < len(readable) and outside(readable[end + 1][1]):
            end += 1
        if end - pos + 1 < params.min_ramp_frames or end + 1 >= len(readable):
            return None
        # The run must arrive at `new`, not just stop. A ramp's last step is small, or it ends close to where it is
        # going compared with how far it travelled; a subtitle that vanishes ends as far away as it ever was.
        last_out, first_in = readable[end][1], readable[end + 1][1]
        reach = max(gap(readable[k][1], new) for k in range(pos, end + 1))
        arrived = gap(last_out, first_in) < params.jump_px or gap(last_out, new) <= ARRIVAL_SHARE * reach
        return i if arrived else None
    return None


def analyze_transition(
    frames: list[ScanFrame],
    old: Edges,
    new: Edges,
    fps: float,
    params: RampParams = RampParams(),  # noqa: B008
) -> Transition:
    """Where and how the picture changes from `old` to `new` edges within `frames`.

    A window that opens on `old`, jumps to some other picture and then eases into `new` is read as a ramp that
    starts at the jump (flag `ramp_after_cut`), whose first frame is the first frame after the jump.
    """
    jump = _excursion_start(frames, old, new, params)
    if jump is not None and frames[jump].edges is not None:
        inner = _analyze(frames[jump:], frames[jump].edges, new, fps, params, from_cut=True)
        if inner.kind == "ramp":
            return replace(
                inner,
                first_new=inner.first_new + jump,
                ramp_start=jump,
                scene_cut=_scene_cut(frames, jump),
                flags=(*inner.flags, "ramp_after_cut"),
            )
    return _analyze(frames, old, new, fps, params)


def _analyze(
    frames: list[ScanFrame],
    old: Edges,
    new: Edges,
    fps: float,
    params: RampParams,
    *,
    from_cut: bool = False,
) -> Transition:
    names = [n for n in EDGE_NAMES if abs(getattr(new, n) - getattr(old, n)) >= params.min_move_px]
    if not names or not frames:
        return Transition("none", 0, flags=("no_change",), confidence=0.0)
    q: dict[str, list[float | None]] = {}
    raw: list[list[float | None]] = []
    for n in names:
        p = _progress(frames, n, getattr(old, n), getattr(new, n), params.snap_px)
        raw.append(p)
        q[n] = _isotonic_series(_running_median(p, params.median_window))
    if not from_cut:
        bar = []
        for i in range(len(frames)):
            vals = [r[i] for r in raw if r[i] is not None]
            bar.append(sum(vals) / len(vals) if vals else None)  # type: ignore[arg-type]
        jump = _jump_cut(bar)
        if jump is not None:
            return Transition("cut", jump, scene_cut=_scene_cut(frames, jump), confidence=1.0)
    qbar: list[float | None] = []
    for i in range(len(frames)):
        vals = [q[n][i] for n in names if q[n][i] is not None]
        qbar.append(sum(vals) / len(vals) if vals else None)  # type: ignore[arg-type]
    last_old, first_new = _last_old_first_new(qbar)
    if from_cut and first_new is not None:
        last_old = -1  # the first frame already belongs to the ramp: there is no old state before it
    flags: list[str] = []
    if last_old is None or first_new is None:
        return Transition(
            "none", 0, flags=("incomplete" if first_new is None else "no_old_state",), confidence=0.0
        )

    between = range(last_old + 1, first_new)
    readable_between = [i for i in between if qbar[i] is not None]
    if len(readable_between) >= params.min_ramp_frames:
        return _ramp(frames, names, old, new, last_old + 1, first_new, fps, params)

    # a cut (possibly with a blended frame or two)
    cut_at = first_new
    if readable_between:
        flags.append("blend_frames")
        cut_at = next((i for i in readable_between if (qbar[i] or 0.0) >= 0.5), first_new)
    elif len(between) > 0:
        flags.append("boundary_in_unreadable_gap")
        diffs = [
            (frames[i].diff, i) for i in range(last_old + 1, first_new + 1) if frames[i].diff is not None
        ]
        if diffs:
            cut_at = max(diffs)[1]
    confidence = 1.0 if not flags else 0.6
    return Transition(
        "cut", cut_at, scene_cut=_scene_cut(frames, cut_at), confidence=confidence, flags=tuple(flags)
    )


def _ramp(
    frames: list[ScanFrame],
    names: list[str],
    old: Edges,
    new: Edges,
    start: int,
    end: int,
    fps: float,
    params: RampParams,
) -> Transition:
    span = frames[start:end]
    fitted: dict[str, list[float]] = {}
    ok = True
    patterns: list[str] = []
    fits: list[PatternFit] = []
    misread = [
        f.edges is None for f in span
    ]  # unreadable, or an outlier among its neighbours (a flash frame)
    for n in EDGE_NAMES:
        a, b = getattr(old, n), getattr(new, n)
        if n in names:
            fine = _fine_progress(frames, n, start, end, a, b, params)
            pattern = fit_pattern(fine, abs(b - a)) if fine is not None else None
            if pattern is not None:
                fitted[n] = [a + (b - a) * v for v in _strictly_between(pattern.values, abs(b - a))]
                patterns.append(pattern.model)
                fits.append(pattern)
                for i in pattern.exceptions:
                    misread[i] = True
                continue
            raw = [None if f.edges is None else getattr(f.edges, n) for f in span]
            kept = _reject_outliers(raw, params.outlier_window, params.outlier_px)
            misread = [m or (r is not None and k is None) for m, r, k in zip(misread, raw, kept, strict=True)]
            fitted[n], good, exceptions = _fit_edge(kept, a, b, params.fit_tolerance_px)
            for i in exceptions:
                misread[i] = True
            ok = ok and good
        else:
            fitted[n] = [(a + b) / 2] * len(span)
    if fits and len(fits) == len(names):
        # every moving edge follows a curve: the ramp is where the curves move, not the whole stretch of window that
        # was fitted (the frames before it and after it sit at the old and new edges and only add table entries)
        lo = max(0, min(math.floor(f.start) for f in fits))
        hi = min(len(span), max(math.ceil(f.start + f.length) for f in fits))
        if hi - lo >= params.min_ramp_frames:
            fitted = {n: v[lo:hi] for n, v in fitted.items()}
            misread = misread[lo:hi]
            end = start + hi
            start += lo
            span = frames[start:end]
    first_frame = round(span[0].t * fps)
    unreadable = sum(misread)
    out = tuple(
        FitFrame(
            f=first_frame + k,
            t=f.t,
            fitted=Edges(**{n: fitted[n][k] for n in EDGE_NAMES}),
            raw=f.edges,
            readable=not misread[k],
        )
        for k, f in enumerate(span)
    )
    flags = []
    if patterns:
        flags.append("pattern:" + "/".join(dict.fromkeys(patterns)))
    share = unreadable / len(span)
    if share > params.max_unreadable_share:
        flags.append("many_unreadable_frames")
    if not ok:
        flags.append("fit_exceeds_tolerance")
    confidence = max(0.0, 1.0 - 2 * share) * (1.0 if ok else 0.5)
    if confidence < 0.7:
        flags.append("low_confidence")
    return Transition(
        "ramp", end, ramp_start=start, ramp=out, confidence=round(confidence, 3), flags=tuple(flags)
    )
