"""The dense pass: follow every change of picture, however short, in titles that change ratio often.

The first pass reads one keyframe in every second or so, and the segment checks widen a whole segment to cover any
bigger picture a probe happens to land on. Both are right for a film that changes ratio a few times, and both go
wrong in one that flips between ratios every second or two (a multiverse montage): a shot shorter than the
keyframe spacing is never seen, and one probe in a 1.85 shot turns a minute of 2.39 into 1.85.

This pass reads every reference frame (about every third frame), measures the picture in each, and builds the
states of the film from those measurements, then pins every change on its exact frame with the same boundary
search the first pass uses. What it keeps from the first pass: the ramps (gradual changes, already fitted frame by
frame) and, where the dense measurements say nothing (credits on black, a dark scene), the first pass's own states.

Guards against misreads, which matter more here than anywhere because every state is short:
- a sample whose crop grew on one side only (a subtitle, a bright object in the matte) says nothing, unless the
  picture already spans that axis end to end (a dark side of a full-width picture says nothing about its height);
- a state shorter than a second must be bright (a dark frame's crop looks like a smaller picture) unless it is a
  bigger picture than both neighbours, must last `min_frames` frames once its edges are found, and, unless it is
  a bigger picture than both neighbours, must show a scene cut at both edges (the scene-cut test misses real cuts
  in a fast montage, where every frame differs from the next);
- a state that fails those is not followed: it goes to the neighbour with the bigger picture, so what is left
  over is a little extra black, never a picture too big for the screen.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from .coarse import Sample, coarse_scan
from .frames import Edges
from .geometry import EQUIVALENT_ZOOM, picture_zoom
from .probe import VideoInfo
from .ramps import FitFrame
from .refine import (
    RefinedBoundary,
    RefinedSegment,
    RefineParams,
    RefineResult,
    _display_aspect,
    _file_threshold,
    refine_boundary,
)
from .schema import Timeline
from .segmentation import (
    BoundaryWindow,
    SegmentationParams,
    rect_tolerance,
    rects_close,
    snap_aspect,
    unusable_reason,
)

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]

#: how many changes the first pass has to find before a title gets the dense pass on its own (`DENSE_TRIGGER`)
TRIGGER_CHANGES = 20
MIN_FRAMES = 4  # the shortest state followed (`DENSE_MIN_FRAMES`)
#: bumped when the dense pass changes what it produces, so titles that went through an older one run again
DENSE_VERSION = 1

FLASH_S = 1.0  # a state shorter than this gets the extra checks
FLASH_MIN_YAVG = 40.0  # 0..255: a dim frame's crop is not to be trusted for a short state
LOPSIDED = 0.02  # of the frame: a crop that grew on one side by more than this is a subtitle or bright object
HOLE_S = (
    1.5  # between two samples further apart than this the dense pass has no say about where the change is
)
RAMP_PAD_S = 0.2  # samples this close to a ramp are part of it
TRUST = 0.7  # confidence a refined boundary needs to be used as it is


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def trigger_changes() -> int:
    """How many changes make a title get the dense pass automatically; 0 turns the automatic trigger off."""
    return max(0, _env_int("DENSE_TRIGGER", TRIGGER_CHANGES))


def min_frames() -> int:
    return max(2, _env_int("DENSE_MIN_FRAMES", MIN_FRAMES))


@dataclass(frozen=True)
class DenseParams:
    min_frames: int = field(default_factory=min_frames)  # the shortest state followed
    skip_frame: str = "noref"  # `none` reads every frame (about 1.7 times the work)
    refine: RefineParams = RefineParams(  # noqa: B008
        pad_s=0.15, stable_s=0.15, widen_s=0.25, max_widen_s=0.75, probe_s=0.2, probes=2, probes_unsure=2
    )


@dataclass
class _Run:
    anchor: Rect
    rect: Rect
    first: float
    last: float
    samples: list[Sample]


def _usable(s: Sample, info: VideoInfo, seg: SegmentationParams) -> bool:
    if unusable_reason(s, info, seg) is not None:
        return False
    skew_x = s.skew if s.skew_x is None else s.skew_x
    skew_y = s.skew if s.skew_y is None else s.skew_y
    tol_x, tol_y = rect_tolerance(info)
    # a crop lopsided across an axis the picture already spans end to end (a dark side of a full-width picture)
    # cannot have grown into a matte on that axis, so the skew says nothing against the other axis
    spans_x = s.w >= info.width - tol_x
    spans_y = s.h >= info.height - tol_y
    return (skew_x <= LOPSIDED * info.width or spans_x) and (skew_y <= LOPSIDED * info.height or spans_y)


def _mode_rect(samples: list[Sample]) -> Rect:
    rects = Counter((s.x, s.y, s.w, s.h) for s in samples)
    return rects.most_common(1)[0][0]


def _runs(samples: list[Sample], info: VideoInfo) -> list[_Run]:
    """Consecutive usable samples whose crops agree (within the rectangle tolerance of the run's first)."""
    tol = rect_tolerance(info)
    runs: list[_Run] = []
    for s in samples:
        rect = (s.x, s.y, s.w, s.h)
        if runs and rects_close(runs[-1].anchor, rect, tol):
            runs[-1].samples.append(s)
            runs[-1].last = s.t
        else:
            runs.append(_Run(rect, rect, s.t, s.t, [s]))
    for r in runs:
        r.rect = _mode_rect(r.samples)
    return runs


def _coalesce(runs: list[_Run], info: VideoInfo) -> list[_Run]:
    """Join neighbouring runs that carry the same picture (what is left once a short state is dropped)."""
    tol = rect_tolerance(info)
    out: list[_Run] = []
    for r in runs:
        if out and rects_close(out[-1].rect, r.rect, tol):
            out[-1].samples.extend(r.samples)
            out[-1].last = r.last
        else:
            out.append(r)
    return out


def _snap_to_palette(runs: list[_Run], base: RefineResult, info: VideoInfo) -> None:
    """A run's rectangle comes from a 960-pixel-wide measurement, good to a few pixels; where the first pass already
    measured the same picture at full resolution, use that rectangle."""
    tol = rect_tolerance(info)
    palette = [s.rect for s in base.segments if s.verified is not False]
    for r in runs:
        near = [p for p in palette if rects_close(r.rect, p, tol)]
        if near:
            r.rect = min(near, key=lambda p: sum(abs(a - b) for a, b in zip(p, r.rect, strict=True)))


def _ramp_spans(base: RefineResult) -> list[tuple[float, float, int]]:
    return [
        (b.t_start - RAMP_PAD_S, b.t + RAMP_PAD_S, k)
        for k, b in enumerate(base.boundaries)
        if b.kind == "ramp" and b.ramp
    ]


def _zoom(rect: Rect, info: VideoInfo) -> float:
    return picture_zoom(rect, info)


def _conservative(lo: float, hi: float, before: Rect, after: Rect, info: VideoInfo) -> float:
    """Where a change goes when its frame cannot be found: the end of the doubt that keeps the larger picture on screen."""
    return lo if _zoom(after, info) < _zoom(before, info) else hi


def _cut(
    t: float,
    fps: float,
    scene_cut: bool | None,
    flags: tuple[str, ...],
    window: tuple[float, float] | None = None,
    confidence: float = 1.0,
    old: Edges | None = None,
    new: Edges | None = None,
) -> RefinedBoundary:
    return RefinedBoundary(
        "cut", t, round(t * fps), t, scene_cut, old, new, (), confidence, flags, window
    )  # fmt: skip


def dense_pass(
    path: str | Path,
    info: VideoInfo,
    base: RefineResult,
    params: DenseParams = DenseParams(),  # noqa: B008
    progress: Callable[[str, float], None] | None = None,
    samples: list[Sample] | None = None,
) -> RefineResult:
    """Rebuild `base` (the first pass's segments, boundaries and ramps) from a measurement of every reference frame."""
    if not base.segments:
        return base
    seg_params = SegmentationParams()
    if samples is None:
        samples = coarse_scan(
            path,
            info,
            min_interval_s=0.0,
            skip_frame=params.skip_frame,
            progress=(lambda share: progress("dense scan", 0.6 * share)) if progress else None,
        )
    spans = _ramp_spans(base)
    usable = [
        s
        for s in sorted(samples, key=lambda x: x.t)
        if _usable(s, info, seg_params) and not any(a <= s.t <= b for a, b, _ in spans)
    ]
    runs = _runs(usable, info)
    if not runs:
        return replace(base, flags=tuple(dict.fromkeys([*base.flags, "dense_unreadable"])))
    _snap_to_palette(runs, base, info)
    runs = _coalesce(runs, info)
    fps = info.fps
    tol = rect_tolerance(info)
    threshold = _file_threshold(path, info, list(base.segments))  # duck-typed: needs start, end, rect
    min_s = params.min_frames / fps

    def ramp_between(lo: float, hi: float) -> int | None:
        for a, b, k in spans:
            if lo - 1e-6 <= a + RAMP_PAD_S and b - RAMP_PAD_S <= hi + 1e-6:
                return k
        return None

    def baseline_cut(lo: float, hi: float) -> RefinedBoundary | None:
        for b in base.boundaries:
            if b.kind == "cut" and b.scene_cut is True and lo - 0.1 <= b.t <= hi + 0.1:
                return b
        return None

    done = [0]
    total = max(1, len(runs) - 1)

    def boundary_between(left: _Run, right: _Run) -> RefinedBoundary:
        lo, hi = left.last, right.first
        k = ramp_between(lo, hi)
        if k is not None:
            rb = base.boundaries[k]
            first, last = rb.ramp[0].fitted, rb.ramp[-1].fitted
            wide = (4 * tol[0], 4 * tol[1])
            ends_ok = rects_close(first.rect, left.rect, wide) and rects_close(last.rect, right.rect, wide)
            if ends_ok:
                return rb
            return _cut(
                _conservative(rb.t_start, rb.t, left.rect, right.rect, info),
                fps,
                None,
                ("ramp_dropped",),
                (lo, hi),
                0.6,
            )
        kept = baseline_cut(lo, hi)
        if kept is not None:
            return kept
        if hi - lo <= HOLE_S:
            found = refine_boundary(
                path, info, BoundaryWindow(lo, hi), left.rect, right.rect, params.refine,
                floor=left.first, ceiling=right.last, threshold=threshold,
            )  # fmt: skip
            done[0] += 1
            if progress:
                progress("dense check", 0.6 + 0.4 * min(1.0, done[0] / total))
            if found.confidence >= TRUST and "unresolved" not in found.flags and found.kind == "cut":
                return found
            if found.kind == "ramp" and found.ramp and found.confidence >= TRUST:
                return found
        t = _conservative(lo, hi, left.rect, right.rect, info)
        return _cut(t, fps, None, ("placed_conservatively",), (lo, hi), 0.6)

    bounds: list[RefinedBoundary] = []
    for a, b in zip(runs, runs[1:], strict=False):
        bounds.append(boundary_between(a, b))

    # drop states too short or too doubtful to follow, until nothing more goes
    while len(runs) > 1:
        victim = None
        for i, r in enumerate(runs):
            start = 0.0 if i == 0 else bounds[i - 1].t
            end = base.segments[-1].end if i == len(runs) - 1 else bounds[i].t_start
            length = end - start
            dim = bool(r.samples) and min(s.yavg for s in r.samples) < FLASH_MIN_YAVG
            neighbours = [runs[j] for j in (i - 1, i + 1) if 0 <= j < len(runs)]
            # a dim frame can only make a crop look smaller than the picture, so a dim state that is bigger than
            # both its neighbours is still real; one that is smaller or equal could be a dark scene
            bigger = bool(neighbours) and all(
                _zoom(r.rect, info) < _zoom(n.rect, info) / (1 + EQUIVALENT_ZOOM) for n in neighbours
            )
            doubtful = dim and not bigger
            cut_edges = [
                bounds[j].scene_cut for j in (i - 1, i) if 0 <= j < len(bounds) and bounds[j].kind == "cut"
            ]
            # a bigger picture with clean edges is a shot of its own whether or not the scene-cut test agrees: in a
            # fast montage the test measures the change against its busy neighbourhood and misses real cuts. The
            # test still guards a state that is not bigger (a dark scene or a matte flicker looks smaller, not bigger)
            no_cut = any(c is False for c in cut_edges) and not bigger
            if length < min_s - 1e-9 or (length < FLASH_S and (doubtful or no_cut)):
                victim = i
                break
        if victim is None:
            break
        i = victim
        left = runs[i - 1] if i > 0 else None
        right = runs[i + 1] if i + 1 < len(runs) else None
        into_left = left is not None and (
            right is None or _zoom(left.rect, info) <= _zoom(right.rect, info)
        )  # the neighbour with the bigger picture hosts it
        host = left if into_left else right
        host.samples.extend(runs[i].samples)
        if into_left:
            host.last = runs[i].last
            del bounds[i - 1]  # the host keeps going to where the dropped state ended
        else:
            host.first = runs[i].first
            del bounds[i]
        del runs[i]
        merged = _coalesce(runs, info)
        while len(merged) < len(runs):  # two neighbours became one picture: the change between them goes too
            for j in range(len(runs) - 1):
                if rects_close(runs[j].rect, runs[j + 1].rect, tol):
                    runs[j].samples.extend(runs[j + 1].samples)
                    runs[j].last = runs[j + 1].last
                    del runs[j + 1]
                    del bounds[j]
                    break
            merged = _coalesce(runs, info)

    segments: list[RefinedSegment] = []
    end_of_file = base.segments[-1].end
    for i, r in enumerate(runs):
        start = 0.0 if i == 0 else bounds[i - 1].t
        end = end_of_file if i == len(runs) - 1 else bounds[i].t_start
        if end <= start:
            continue
        label, snapped = snap_aspect(_display_aspect(r.rect, info))
        flags = ("dense",) if len(r.samples) >= 3 else ("dense", "few_samples")
        segments.append(
            RefinedSegment(
                start,
                end,
                r.rect,
                round(_display_aspect(r.rect, info), 3),
                label if snapped else None,
                1.0,
                True,
                flags,
            )
        )
    if len(segments) != len(runs):  # a zero-length state: not worth a change
        return replace(base, flags=tuple(dict.fromkeys([*base.flags, "dense_failed"])))
    flags = [
        f
        for f in base.flags
        if f
        not in ("segments_refuted", "uncertain_merged", "position_uncertain", "mixed_probes", "var", "ramp")
    ]
    flags.append("dense")
    if len({(s.preset, round(s.aspect, 2) if s.preset is None else 0) for s in segments}) > 1:
        flags.append("var")
    if any(b.kind == "ramp" for b in bounds):
        flags.append("ramp")
    if any("placed_conservatively" in b.flags for b in bounds):
        flags.append("position_uncertain")
    return RefineResult(tuple(segments), tuple(bounds), base.dropped, tuple(dict.fromkeys(flags)))


def result_from_timeline(timeline: Timeline, info: VideoInfo) -> RefineResult:
    """The first pass's result as a `RefineResult` again, from a stored timeline, so the dense pass can run on it
    without reading the file a second time."""
    segs: list[RefinedSegment] = []
    bounds: list[RefinedBoundary] = []
    for i, s in enumerate(timeline.segments):
        a = s.active
        rect = (a.x, a.y, a.w, a.h)
        segs.append(RefinedSegment(s.start, s.end, rect, s.aspect, s.preset, s.confidence, True, ()))
        if i == 0:
            continue
        b = s.boundary
        if b.kind == "ramp":
            ramp = next(
                (
                    r
                    for r in timeline.ramps
                    if r.end_frame == b.frame - 1 or abs(r.end - s.start) < 1.5 / info.fps
                ),
                None,
            )
            if ramp is not None:
                fit = tuple(
                    FitFrame(f.f, f.t, Edges(f.top, f.bottom, f.left, f.right), None, f.readable)
                    for f in ramp.frames
                )
                bounds.append(
                    RefinedBoundary(
                        "ramp",
                        s.start,
                        b.frame,
                        ramp.start,
                        b.scene_cut,
                        None,
                        None,
                        fit,
                        ramp.confidence,
                        (),
                        None,
                    )
                )
                continue
        bounds.append(
            RefinedBoundary("cut", s.start, b.frame, s.start, b.scene_cut, None, None, (), 1.0, (), None)
        )
    return RefineResult(tuple(segs), tuple(bounds), (), tuple(timeline.flags))


def is_dense(flags: list[str] | tuple[str, ...]) -> bool:
    return "dense" in flags


def wants_dense(change_count: int) -> bool:
    trigger = trigger_changes()
    return trigger > 0 and change_count >= trigger
