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

import statistics
from dataclasses import dataclass, field
from typing import Literal

from .frames import Edges, ScanFrame

EDGE_NAMES = ("top", "bottom", "left", "right")
EPS = 1e-9
STABLE_PX = 2.0
STABLE_SHARE = 0.8
DRIFT_PX = 1.0  # most the picture edge may drift between the two halves of a window's quiet stretch
SCENE_CUT_MIN_DIFF = 6.0
SCENE_CUT_RATIO = 4.0
SMOOTHING_WINDOWS = (7, 5, 3, 1)


@dataclass(frozen=True)
class RampParams:
    snap_px: float = (
        1.0  # edges closer than this to a state's edge are at that state (integer edges: exact matches)
    )
    min_move_px: float = 6.0  # an edge that differs by less between the two states is not tracked
    min_ramp_frames: int = 3
    fit_tolerance_px: float = 2.0
    median_window: int = 5
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
    half = window // 2
    out: list[float | None] = []
    for i, v in enumerate(values):
        if v is None:
            out.append(None)
            continue
        near = [x for x in values[max(0, i - half) : i + half + 1] if x is not None]
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


def _isotonic_series(values: list[float | None]) -> list[float | None]:
    idx = [i for i, v in enumerate(values) if v is not None]
    fitted = isotonic_increasing([values[i] for i in idx])  # type: ignore[misc]
    out: list[float | None] = [None] * len(values)
    for i, v in zip(idx, fitted, strict=True):
        out[i] = v
    return out


def _fit_edge(raw: list[float | None], a: float, b: float, tol: float) -> tuple[list[float], bool]:
    """Fitted values for one moving edge over the ramp frames, and whether they are within `tol` of every
    readable measurement. `a` and `b` are the edge before and after; they anchor both ends."""
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
        padded = [0.0] * pad + dense + [span] * pad
        out = [sum(padded[i : i + window]) / window for i in range(n)]
        eps = min(1e-3, span / (n + 1) / 10)
        for i in range(n):  # strictly increasing, kept inside the anchors
            out[i] = max(out[i], (out[i - 1] if i else 0.0) + eps)
        for i in range(n - 1, -1, -1):
            out[i] = min(out[i], (out[i + 1] if i < n - 1 else span) - eps)
        return out

    chosen, ok = dense, False
    for window in SMOOTHING_WINDOWS:
        candidate = smooth(window)
        if all(abs(c - u[i]) <= tol for i, c in enumerate(candidate) if u[i] is not None):  # type: ignore[operator]
            chosen, ok = candidate, True
            break
    else:
        chosen = smooth(1)
    return [a + sign * c for c in chosen], ok


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


def analyze_transition(
    frames: list[ScanFrame],
    old: Edges,
    new: Edges,
    fps: float,
    params: RampParams = RampParams(),  # noqa: B008
) -> Transition:
    """Where and how the picture changes from `old` to `new` edges within `frames`."""
    names = [n for n in EDGE_NAMES if abs(getattr(new, n) - getattr(old, n)) >= params.min_move_px]
    if not names or not frames:
        return Transition("none", 0, flags=("no_change",), confidence=0.0)
    q: dict[str, list[float | None]] = {}
    for n in names:
        p = _progress(frames, n, getattr(old, n), getattr(new, n), params.snap_px)
        q[n] = _isotonic_series(_running_median(p, params.median_window))
    qbar: list[float | None] = []
    for i in range(len(frames)):
        vals = [q[n][i] for n in names if q[n][i] is not None]
        qbar.append(sum(vals) / len(vals) if vals else None)  # type: ignore[arg-type]
    last_old, first_new = _last_old_first_new(qbar)
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
    for n in EDGE_NAMES:
        a, b = getattr(old, n), getattr(new, n)
        if n in names:
            raw = [None if f.edges is None else getattr(f.edges, n) for f in span]
            fitted[n], good = _fit_edge(raw, a, b, params.fit_tolerance_px)
            ok = ok and good
        else:
            fitted[n] = [(a + b) / 2] * len(span)
    first_frame = round(span[0].t * fps)
    unreadable = sum(1 for f in span if f.edges is None)
    out = tuple(
        FitFrame(
            f=first_frame + k,
            t=f.t,
            fitted=Edges(**{n: fitted[n][k] for n in EDGE_NAMES}),
            raw=f.edges,
            readable=f.edges is not None,
        )
        for k, f in enumerate(span)
    )
    flags = []
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
