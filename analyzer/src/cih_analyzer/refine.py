"""Refinement (spec Phase 4 step 6): from coarse segments to exact boundary frames, ramps, and checked segments.

Three jobs, all at full resolution and all decoding only short windows of the file:

1. **Check the segments.** A few short probes spread through each segment re-measure its picture. A segment the
   coarse pass could not vouch for (`needs_verification`) whose probes do not show its ratio is refuted and merged
   into the neighbour its probes match; every surviving segment gets a full-resolution picture rectangle.
2. **Refine each boundary.** Decode every frame around the change (a coarse stride pass first when the window is
   long), widening the window until the picture has held still for `stable_s` on both sides, then let
   `ramps.analyze_transition` decide cut or ramp and find the exact frame.
3. **Report what could not be settled** as flags (unstable context, unreadable gap, low confidence) rather than
   guessing silently.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from .frames import Edges, ScanFrame, matte_levels, matte_threshold, scan_edges, threshold_from
from .probe import VideoInfo
from .ramps import FitFrame, RampParams, analyze_transition, reference_edges
from .segmentation import (
    BoundaryWindow,
    CoarseResult,
    CoarseSegment,
    rect_tolerance,
    rects_close,
    snap_aspect,
)

Rect = tuple[int, int, int, int]
EPSILON = 1e-6


@dataclass(frozen=True)
class RefineParams:
    pad_s: float = 1.0  # decoded either side of a boundary window
    stride_over_s: float = 10.0  # windows longer than this are narrowed with a stride pass first
    stride_step_s: float = 0.25
    stable_s: float = 1.0  # the picture must hold still this long at both ends of the decoded window
    widen_s: float = 2.0
    max_widen_s: float = 24.0  # most a window grows on each side
    probe_s: float = 0.4  # decoded per segment probe
    probes: int = 3
    probes_unsure: int = 8
    confirm_share: float = 0.6
    ramp: RampParams = field(default_factory=RampParams)


@dataclass(frozen=True)
class RefinedBoundary:
    """`t` and `frame` are the first frame at the new ratio; `t_start` is when the change begins (equal to `t` for a
    cut). `ramp` holds the fitted per-frame edges for a ramp. `old` and `new` are the measured picture edges
    either side, at full resolution."""

    kind: Literal["cut", "ramp"]
    t: float
    frame: int
    t_start: float
    scene_cut: bool | None
    old: Edges | None
    new: Edges | None
    ramp: tuple[FitFrame, ...] = ()
    confidence: float = 1.0
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefinedSegment:
    start: float
    end: float
    rect: Rect
    aspect: float  # display ratio of `rect`, pixel aspect ratio applied
    preset: float | None  # the standard ratio it snaps to, if any
    confidence: float
    verified: bool | None  # True: probes confirmed it; False: probes could not read it; None: not checked
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefineResult:
    segments: tuple[RefinedSegment, ...]
    boundaries: tuple[RefinedBoundary, ...]
    dropped: tuple[
        tuple[float, float, float], ...
    ] = ()  # (start, end, aspect) of segments the probes refuted
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SegmentCheck:
    """What the probes found. `refuted`: they agree with each other and with a neighbour, not with the segment.
    `corrected`: they agree with each other and with nothing known (the coarse rectangle was off, e.g. a subtitle
    inside the matte); `rect` is then the probes' consensus. `unreadable`: too few readable or no consensus."""

    verdict: Literal["confirmed", "corrected", "refuted", "unreadable"]
    rect: Rect
    agreeing: int
    readable: int
    into_left: bool = True


def _tolerance(info: VideoInfo) -> float:
    """How far a full-resolution edge may sit from a coarse rectangle's edge: the coarse pass is good to a pixel
    of its 960-wide frame."""
    return max(4.0, 2 * info.width / 960 + 2)


def _distance(edges: Edges, rect: Rect) -> float:
    x, y, w, h = rect
    return max(
        abs(edges.left - x), abs(edges.top - y), abs(edges.right - (x + w)), abs(edges.bottom - (y + h))
    )


def _rect_edges(rect: Rect) -> Edges:
    x, y, w, h = rect
    return Edges(top=y, bottom=y + h, left=x, right=x + w)


def _intersection(a: Rect, b: Rect, shrink: int = 8) -> Rect | None:
    x0, y0 = max(a[0], b[0]) + shrink, max(a[1], b[1]) + shrink
    x1, y1 = min(a[0] + a[2], b[0] + b[2]) - shrink, min(a[1] + a[3], b[1] + b[3]) - shrink
    return (x0, y0, x1 - x0, y1 - y0) if x1 - x0 >= 16 and y1 - y0 >= 16 else None


def _median_edges(items: list[Edges]) -> Edges:
    return Edges(
        top=statistics.median(e.top for e in items),
        bottom=statistics.median(e.bottom for e in items),
        left=statistics.median(e.left for e in items),
        right=statistics.median(e.right for e in items),
    )


def _edge_gap(a: Edges, b: Edges) -> float:
    return max(abs(a.top - b.top), abs(a.bottom - b.bottom), abs(a.left - b.left), abs(a.right - b.right))


def _file_threshold(path: str | Path, info: VideoInfo, segments: list[CoarseSegment]) -> float:
    """One edge threshold for checking the segments, from the cleanest matte any segment shows. A segment under
    suspicion must not set the bar it is judged by: if its rectangle is too small, dim picture near its edge is
    counted as matte and would raise the level, so the lowest matte level across all segments is used."""
    levels = matte_levels(path, info, [((s.start + s.end) / 2, s.rect) for s in segments])
    known = [v for v in levels if v is not None]
    return threshold_from([min(known)] if known else [])


def probe_segment(
    path: str | Path,
    info: VideoInfo,
    seg: CoarseSegment,
    left: Rect | None,
    right: Rect | None,
    threshold: float,
    params: RefineParams,
) -> SegmentCheck:
    """Re-measure `seg` at full resolution in a few short windows spread through it."""
    duration = seg.end - seg.start
    count = params.probes_unsure if seg.needs_verification else params.probes
    count = max(1, min(count, int(duration / 1.0)))
    margin = min(1.0, duration / 4)
    times = [seg.start + margin + (duration - 2 * margin) * (k + 0.5) / count for k in range(count)]
    tol = _tolerance(info)
    found: list[Edges] = []
    for t in times:
        frames = scan_edges(path, info, t, params.probe_s, threshold=threshold)
        edges = [f.edges for f in frames if f.edges is not None]
        if len(edges) >= 3:
            found.append(_median_edges(edges))
    if not found:
        return SegmentCheck("unreadable", seg.rect, 0, 0)
    cluster = max(([e for e in found if _edge_gap(e, c) <= tol] for c in found), key=len)
    if len(cluster) / len(found) < params.confirm_share:
        return SegmentCheck("unreadable", seg.rect, len(cluster), len(found))
    consensus = _median_edges(cluster)
    rect = consensus.rect
    if _distance(consensus, seg.rect) <= tol:
        return SegmentCheck("confirmed", rect, len(cluster), len(found))
    near_left = left is not None and _distance(consensus, left) <= tol
    near_right = right is not None and _distance(consensus, right) <= tol
    if near_left or near_right:
        return SegmentCheck("refuted", rect, len(cluster), len(found), into_left=near_left)
    return SegmentCheck("corrected", rect, len(cluster), len(found))


def _display_aspect(rect: Rect, info: VideoInfo) -> float:
    sar_n, sar_d = (int(p) for p in info.sar.split(":"))
    return rect[2] * sar_n / (rect[3] * sar_d)


def _narrow(
    path: str | Path,
    info: VideoInfo,
    t0: float,
    t1: float,
    old: Rect,
    new: Rect,
    threshold: float,
    params: RefineParams,
) -> tuple[float, float]:
    """Shrink a long window to the stretch where the picture goes from `old` to `new`, using a stride pass."""
    frames = scan_edges(path, info, t0, t1 - t0, threshold=threshold, step=params.stride_step_s)
    tol = max(8.0, 0.012 * max(info.width, info.height))

    def state(f: ScanFrame) -> str | None:
        if f.edges is None:
            return None
        if _distance(f.edges, old) <= tol:
            return "old"
        return "new" if _distance(f.edges, new) <= tol else None

    states = [state(f) for f in frames]
    first_new = next((i for i, s in enumerate(states) if s == "new"), None)
    if first_new is None:
        return t0, t1
    last_old = max((i for i in range(first_new) if states[i] == "old"), default=None)
    if last_old is None:
        return t0, t1
    return max(t0, frames[last_old].t - params.pad_s), min(t1, frames[first_new].t + params.pad_s)


def refine_boundary(
    path: str | Path,
    info: VideoInfo,
    window: BoundaryWindow,
    old_rect: Rect,
    new_rect: Rect,
    params: RefineParams = RefineParams(),  # noqa: B008
    *,
    floor: float = 0.0,
    ceiling: float | None = None,
    threshold: float | None = None,
) -> RefinedBoundary:
    """The exact frame (or ramp) where the picture goes from `old_rect` to `new_rect` between window.lo and window.hi.

    `floor` and `ceiling` bound how far the decoded window may grow: the first sample of the old segment and the
    last sample of the new one, which the coarse pass saw at their ratios. `threshold` (8-bit scale) overrides the
    edge threshold, which is otherwise calibrated on the matte outside the two rectangles.
    """
    ceiling = info.duration if ceiling is None else ceiling
    if threshold is None:
        threshold = matte_threshold(path, info, [(window.lo, old_rect), (window.hi, new_rect)])
    t0, t1 = max(floor, window.lo - params.pad_s), min(ceiling, window.hi + params.pad_s)
    if t1 - t0 > params.stride_over_s:
        t0, t1 = _narrow(path, info, t0, t1, old_rect, new_rect, threshold, params)
    diff_rect = _intersection(old_rect, new_rect)
    frames = scan_edges(path, info, t0, t1 - t0, threshold=threshold, diff_rect=diff_rect)
    frame_s = 1 / info.fps
    flags: list[str] = []

    grown_head = grown_tail = 0.0
    while frames:
        old_ref, head_ok = reference_edges(frames, head=True, stable_s=params.stable_s)
        new_ref, tail_ok = reference_edges(frames, head=False, stable_s=params.stable_s)
        grew = False
        first, last = frames[0].t, frames[-1].t
        if not head_ok and first - floor > frame_s and grown_head < params.max_widen_s:
            start = max(floor, first - params.widen_s)
            grown_head += first - start
            extra = scan_edges(
                path, info, start, first - start + frame_s, threshold=threshold, diff_rect=diff_rect
            )
            frames = [f for f in extra if f.t < first - EPSILON] + frames
            grew = True
        if not tail_ok and ceiling - last > frame_s and grown_tail < params.max_widen_s:
            span = min(params.widen_s, ceiling - last)
            grown_tail += span
            extra = scan_edges(path, info, last, span + frame_s, threshold=threshold, diff_rect=diff_rect)
            frames = frames + [f for f in extra if f.t > last + EPSILON]
            grew = True
        if not grew:
            break
    if not frames or old_ref is None or new_ref is None:
        t = window.hi
        return RefinedBoundary(
            "cut", t, round(t * info.fps), t, None, old_ref, new_ref, confidence=0.0, flags=("unresolved",)
        )
    if not head_ok:
        flags.append("unstable_before")
    if not tail_ok:
        flags.append("unstable_after")
    tol = 2 * _tolerance(info)
    if _distance(old_ref, old_rect) > tol or _distance(new_ref, new_rect) > tol:
        flags.append("reference_mismatch")

    change = analyze_transition(frames, old_ref, new_ref, info.fps, params.ramp)
    flags.extend(change.flags)
    if change.kind == "none":
        t = window.hi
        flags.append("unresolved")
        return RefinedBoundary(
            "cut", t, round(t * info.fps), t, None, old_ref, new_ref, confidence=0.0, flags=tuple(flags)
        )
    t_new = frames[change.first_new].t
    # steady stretches at both ends that disagree with the coarse rectangles are a coarse error (a ramp can look
    # like a segment there), worth a flag but no loss of confidence; stretches that never settle are
    confidence = change.confidence * (0.7 if {"unstable_before", "unstable_after"} & set(flags) else 1.0)
    t_start = frames[change.ramp_start].t if change.ramp_start is not None else t_new
    return RefinedBoundary(
        kind="ramp" if change.kind == "ramp" else "cut",
        t=t_new,
        frame=round(t_new * info.fps),
        t_start=t_start,
        scene_cut=change.scene_cut,
        old=old_ref,
        new=new_ref,
        ramp=change.ramp,
        confidence=round(confidence, 3),
        flags=tuple(flags),
    )


def _same_label(a: CoarseSegment, b: CoarseSegment, tol: tuple[float, float]) -> bool:
    """Same ratio and the same picture size (a window that grows to full frame keeps its ratio, not its size)."""
    if a.preset != b.preset or not rects_close(a.rect, b.rect, tol):
        return False
    return a.aspect == b.aspect if a.preset else abs(a.aspect - b.aspect) / max(a.aspect, b.aspect) <= 0.01


def _join(
    segments: list[CoarseSegment], boundaries: list[BoundaryWindow], i: int, keep: Literal["left", "right"]
) -> None:
    """Merge segments i and i+1 in place, keeping one's label and rectangle, and drop the boundary between them."""
    a, b = segments[i], segments[i + 1]
    base = a if keep == "left" else b
    segments[i : i + 2] = [replace(base, start=a.start, end=b.end, samples=a.samples + b.samples)]
    del boundaries[i]


def _merge_refuted(
    segments: list[CoarseSegment],
    boundaries: list[BoundaryWindow],
    i: int,
    into_left: bool,
    tol: tuple[float, float],
) -> None:
    """Fold a refuted segment into the neighbour its probes matched (both, when the neighbours share a ratio)."""
    has_left, has_right = i > 0, i + 1 < len(segments)
    across_ramp = (
        has_left and has_right and (boundaries[i - 1].ramp_candidate or boundaries[i].ramp_candidate)
    )
    if has_left and has_right and not across_ramp and _same_label(segments[i - 1], segments[i + 1], tol):
        _join(segments, boundaries, i - 1, "left")
        _join(segments, boundaries, i - 1, "left")
    elif has_left and (into_left or not has_right):
        _join(segments, boundaries, i - 1, "left")
    else:
        _join(segments, boundaries, i, "right")


def _merge_equal(
    segments: list[CoarseSegment], boundaries: list[BoundaryWindow], tol: tuple[float, float]
) -> bool:
    """Join neighbours that carry the same label (a correction can make two segments equal), except across a ramp
    candidate: a window that returns to the picture it started from is two segments with a ramp between."""
    merged = False
    i = 0
    while i + 1 < len(segments):
        if not boundaries[i].ramp_candidate and _same_label(segments[i], segments[i + 1], tol):
            _join(segments, boundaries, i, "left")
            merged = True
        else:
            i += 1
    return merged


def refine(
    path: str | Path,
    info: VideoInfo,
    coarse: CoarseResult,
    params: RefineParams = RefineParams(),  # noqa: B008
) -> RefineResult:
    """Check the coarse segments, then pin down every boundary."""
    segments, boundaries = list(coarse.segments), list(coarse.boundaries)
    if not segments:
        return RefineResult((), (), (), coarse.flags)
    dropped: list[tuple[float, float, float]] = []
    corrected: list[float] = []  # start times of segments whose rectangle the probes changed
    threshold = _file_threshold(path, info, segments)
    size_tol = rect_tolerance(info)
    cache: dict[tuple, SegmentCheck] = {}

    def check(i: int) -> SegmentCheck:
        seg = segments[i]
        left = segments[i - 1].rect if i > 0 else None
        right = segments[i + 1].rect if i + 1 < len(segments) else None
        key = (seg.start, seg.end, seg.rect, seg.needs_verification, left, right)
        if key not in cache:
            cache[key] = probe_segment(path, info, seg, left, right, threshold, params)
        return cache[key]

    # 1. segments the coarse pass could not vouch for: refuted ones fold into a neighbour, wrong rectangles are fixed
    for _ in range(2 * len(segments) + 4):
        acted = False
        for i, seg in enumerate(segments):
            if not seg.needs_verification:
                continue
            result = check(i)
            if result.verdict == "refuted" and len(segments) > 1:
                dropped.append((seg.start, seg.end, seg.aspect))
                _merge_refuted(segments, boundaries, i, result.into_left, size_tol)
                acted = True
            elif result.verdict == "corrected":
                label, snapped = snap_aspect(_display_aspect(result.rect, info))
                segments[i] = replace(
                    seg, rect=result.rect, aspect=label, preset=snapped, needs_verification=False
                )
                corrected.append(seg.start)
                acted = True
            if acted:
                break
        acted = _merge_equal(segments, boundaries, size_tol) or acted
        if not acted:
            break

    # 2. every surviving segment gets a full-resolution rectangle
    final = [check(i) for i in range(len(segments))]
    rects = [
        c.rect if c.verdict in ("confirmed", "corrected") else s.rect
        for s, c in zip(segments, final, strict=True)
    ]

    # 3. the boundaries, each bounded by its neighbours' windows
    refined: list[RefinedBoundary] = []
    for k, window in enumerate(boundaries):
        floor = boundaries[k - 1].hi if k > 0 else 0.0
        ceiling = boundaries[k + 1].lo if k + 1 < len(boundaries) else segments[-1].end
        refined.append(
            refine_boundary(
                path,
                info,
                window,
                rects[k],
                rects[k + 1],
                params,
                floor=floor,
                ceiling=ceiling,
                threshold=threshold,
            )  # fmt: skip
        )

    # 4. segments on the exact boundary times
    out: list[RefinedSegment] = []
    for i, (seg, check_result, rect) in enumerate(zip(segments, final, rects, strict=True)):
        start = 0.0 if i == 0 else refined[i - 1].t
        end = seg.end if i == len(segments) - 1 else refined[i].t_start
        flags = []
        verified: bool | None = None
        if check_result.verdict in ("confirmed", "corrected"):
            verified = True
        elif check_result.verdict == "unreadable":
            verified = False
            flags.append("unreadable")
        if check_result.verdict == "unreadable":
            # the probes saw no steady picture; a trusted boundary reference next to it is a better rectangle
            nxt = refined[i] if i < len(refined) else None
            prv = refined[i - 1] if i > 0 else None
            ref = next(
                (
                    edges
                    for b, edges in ((nxt, nxt and nxt.old), (prv, prv and prv.new))
                    if b is not None and edges is not None and b.confidence >= 0.7
                ),
                None,
            )
            if ref is not None:
                # a steady stretch of at least a second beside a confident boundary stands in for the probes
                rect = ref.rect
                verified = True
                flags = [f for f in flags if f != "unreadable"]
                flags.append("rect_from_boundary")
        if any(seg.start - EPSILON <= c <= seg.end + EPSILON for c in corrected):
            flags.append("rect_corrected")
        if seg.needs_verification and verified is not True:
            flags.append("needs_review")
        label, snapped = snap_aspect(_display_aspect(rect, info))
        aspect = round(_display_aspect(rect, info), 3)
        out.append(
            RefinedSegment(
                start, end, rect, aspect, label if snapped else None, seg.confidence, verified, tuple(flags)
            )
        )
    flags = list(coarse.flags)
    if dropped:
        flags.append("segments_refuted")
    if any(b.kind == "ramp" for b in refined):
        flags.append("ramp")
    if any("low_confidence" in b.flags or b.confidence < 0.7 for b in refined):
        flags.append("low_confidence")
    return RefineResult(tuple(out), tuple(refined), tuple(dropped), tuple(dict.fromkeys(flags)))
