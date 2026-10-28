"""From coarse samples to stable segments (spec Phase 4 steps 3 to 5).

Pipeline: gate out samples that cannot be trusted (dark frames, implausibly small crops) -> turn each crop into the
display aspect of the active picture (pixel aspect ratio applied) and snap it to a standard ratio when within
1.5% -> group consecutive equal labels into runs -> set aside gradual-change candidates (ramps) -> merge short runs
(flicker: burned-in subtitles in a matte, a studio logo flash, credits, dark scenes whose black edges look like
mattes) into their neighbours.

How long a run lasts is its own sample span plus one sampling interval (two samples a second apart last two
seconds), not the distance to the next run. A non-standard ratio has to last longer (`raw_min_segment_s`) than a
standard one: dark picture content trims the crop to odd shapes far more often than a film really switches to an
odd ratio. What survives is still only a candidate where a dark scene lasted long enough; the refinement pass
(slice 3) re-checks segments at a higher sampling density.

Ramps are exempt from merging: short in-between states that fall strictly between two different stable ratios,
in order (even when they happen to equal a standard ratio), are never merged away; they are returned with the
boundary they sit on so the refinement pass can measure them frame by frame. Boundaries are returned as time
windows (last sample of one segment, first sample of the next); slice 3 narrows each window to the exact frame and
classifies it as a cut or a ramp.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field

from .coarse import Sample
from .probe import VideoInfo

PRESETS = (4 / 3, 16 / 9, 1.85, 1.90, 2.00, 2.20, 2.35, 2.39, 2.40)
SNAP_TOLERANCE = 0.015
RAW_SAME_TOLERANCE = 0.01
MIN_PICTURE_FRACTION = 0.04  # a crop smaller than this share of the frame area is noise, not a picture
MIN_PICTURE_PIXELS = 64
VERIFY_CONFIDENCE = 0.9
VERIFY_SHORT_SEGMENT_S = 15.0


@dataclass(frozen=True)
class SegmentationParams:
    min_segment_s: float = 3.0
    raw_min_segment_s: float = 12.0
    dark_yavg: float = 22.0
    min_samples: int = 2
    snap_tolerance: float = SNAP_TOLERANCE


@dataclass(frozen=True)
class CoarseSegment:
    """`needs_verification` marks a segment keyframe sampling cannot vouch for: short, a non-standard ratio, or one
    whose samples disagree. A dark scene can fake any of these; the refinement pass re-samples them densely."""

    start: float
    end: float
    aspect: float
    preset: bool
    rect: tuple[int, int, int, int]
    samples: int
    confidence: float
    needs_verification: bool = False


@dataclass(frozen=True)
class BoundaryWindow:
    """Where one segment gives way to the next: after the last sample of the first, at or before the first of
    the second. `intermediates` holds in-between samples that qualify as a gradual change (a ramp candidate)."""

    lo: float
    hi: float
    intermediates: tuple[Sample, ...] = ()

    @property
    def ramp_candidate(self) -> bool:
        return bool(self.intermediates)


@dataclass(frozen=True)
class CoarseResult:
    segments: tuple[CoarseSegment, ...]
    boundaries: tuple[BoundaryWindow, ...]
    flags: tuple[str, ...]
    stats: dict[str, int] = field(default_factory=dict)


def snap_aspect(aspect: float, tolerance: float = SNAP_TOLERANCE) -> tuple[float, bool]:
    """The nearest standard ratio if within `tolerance` (relative), else the raw value rounded to 3 places."""
    best = min(PRESETS, key=lambda p: abs(aspect - p) / p)
    if abs(aspect - best) / best <= tolerance:
        return best, True
    return round(aspect, 3), False


def active_aspect(sample: Sample, info: VideoInfo) -> float:
    """Display aspect of the cropped picture: pixel shape of the crop times the stream's pixel aspect ratio."""
    sar_n, sar_d = (int(p) for p in info.sar.split(":"))
    return (sample.w * sar_n) / (sample.h * sar_d)


def unusable_reason(sample: Sample, info: VideoInfo, params: SegmentationParams) -> str | None:
    if sample.yavg < params.dark_yavg:
        return "dark"
    if (
        sample.w < MIN_PICTURE_PIXELS
        or sample.h < MIN_PICTURE_PIXELS
        or sample.w * sample.h < MIN_PICTURE_FRACTION * info.width * info.height
    ):
        return "tiny"
    return None


@dataclass
class _Run:
    label: float
    preset: bool
    samples: list[tuple[Sample, float]]  # (sample, its own snapped label)

    @property
    def first(self) -> float:
        return self.samples[0][0].t

    @property
    def last(self) -> float:
        return self.samples[-1][0].t


class _Context:
    """Timing facts shared by the merge steps: the sampling interval and where the file starts and ends."""

    def __init__(self, params: SegmentationParams, gap: float, min_samples: int, end: float):
        self.params, self.gap, self.min_samples, self.end = params, gap, min_samples, end

    def durations(
        self, runs: list[_Run], starts_file: bool, ends_file: bool, stretch_end: float
    ) -> list[float]:
        """Each run lasts from its first sample to its last plus one sampling interval; the first run of the file
        starts at 0 and the last runs to the end of the file."""
        out = []
        for i, run in enumerate(runs):
            begin = 0.0 if (i == 0 and starts_file) else run.first
            finish = run.last + self.gap
            if i == len(runs) - 1 and ends_file:
                finish = max(finish, stretch_end)
            out.append(finish - begin)
        return out

    def is_short(self, run: _Run, duration: float) -> bool:
        floor = self.params.min_segment_s if run.preset else self.params.raw_min_segment_s
        return duration < floor or len(run.samples) < self.min_samples


def _same(a: float, b: float, a_preset: bool, b_preset: bool) -> bool:
    if a_preset != b_preset:
        return False
    if a_preset:
        return a == b
    return abs(a - b) / max(a, b) <= RAW_SAME_TOLERANCE


def _same_run(a: _Run, b: _Run) -> bool:
    return _same(a.label, b.label, a.preset, b.preset)


def _build_runs(labelled: list[tuple[Sample, float, bool]]) -> list[_Run]:
    runs: list[_Run] = []
    for sample, label, preset in labelled:
        if runs and _same(runs[-1].label, label, runs[-1].preset, preset):
            runs[-1].samples.append((sample, label))
        else:
            runs.append(_Run(label, preset, [(sample, label)]))
    return runs


def _coalesce(runs: list[_Run]) -> list[_Run]:
    out: list[_Run] = []
    for run in runs:
        if out and _same_run(out[-1], run):
            out[-1].samples.extend(run.samples)
        else:
            out.append(run)
    return out


def _monotone_between(left: float, chain: list[float], right: float) -> bool:
    """True if the chain's labels lie strictly between `left` and `right` and move from one to the other in order."""
    lo, hi = sorted((left, right))
    if left == right or not all(lo < c < hi for c in chain):
        return False
    ordered = chain if left < right else chain[::-1]
    return all(a <= b for a, b in zip(ordered, ordered[1:], strict=False))


def _extract_ramps(runs: list[_Run], ctx: _Context) -> tuple[list[_Run], dict[int, list[Sample]]]:
    """Remove chains of short runs that sit strictly between two different stable runs, in order.

    A ramp's in-between states may coincide with standard ratios (2.20, 2.00, 1.85 between 2.39 and 1.78), so every
    short run is a candidate; the strictly-between-and-in-order test decides. Returns the remaining runs and, keyed
    by the index (in the remaining list) of the run *before* each removed chain, the chain's samples.
    """
    durations = ctx.durations(runs, True, True, ctx.end)
    short = [ctx.is_short(r, d) for r, d in zip(runs, durations, strict=True)]
    keep = [True] * len(runs)
    found: dict[int, list[Sample]] = {}
    i = 0
    while i < len(runs):
        if not short[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(runs) and short[j + 1]:
            j += 1
        if i > 0 and j + 1 < len(runs):
            left, right = runs[i - 1], runs[j + 1]
            chain = [r.label for r in runs[i : j + 1]]
            if not _same_run(left, right) and _monotone_between(left.label, chain, right.label):
                for k in range(i, j + 1):
                    keep[k] = False
                found[i - 1] = [s for r in runs[i : j + 1] for s, _ in r.samples]
        i = j + 1
    new_index = {old: new for new, old in enumerate(k for k in range(len(runs)) if keep[k])}
    return [r for r, k in zip(runs, keep, strict=True) if k], {
        new_index[prev]: s for prev, s in found.items()
    }


def _merge_short(
    runs: list[_Run], ctx: _Context, starts_file: bool, ends_file: bool, stretch_end: float
) -> list[_Run]:
    while len(runs) > 1:
        durations = ctx.durations(runs, starts_file, ends_file, stretch_end)
        shorts = [i for i, r in enumerate(runs) if ctx.is_short(r, durations[i])]
        if not shorts:
            break
        i = min(shorts, key=lambda k: (durations[k], k))
        left = runs[i - 1] if i > 0 else None
        right = runs[i + 1] if i + 1 < len(runs) else None
        if left and right and _same_run(left, right):
            # A B A with B too short to count: B was flicker, the picture never changed
            left.samples.extend(runs[i].samples)
            left.samples.extend(right.samples)
            del runs[i : i + 2]
        else:
            into_left = left is not None and (right is None or durations[i - 1] >= durations[i + 1])
            host = runs[i - 1 if into_left else i + 1]
            host.samples.extend(runs[i].samples)
            host.samples.sort(key=lambda s: s[0].t)
            del runs[i]
        runs = _coalesce(runs)
    return runs


def _merge_with_ramps(
    runs: list[_Run], chains: dict[int, list[Sample]], ctx: _Context
) -> list[tuple[_Run, list[Sample]]]:
    """Merge short runs without ever joining two runs across a ramp chain: split into stretches at each chain,
    merge within each, and carry the chain's samples to the boundary after the stretch."""
    stretches: list[list[_Run]] = [[]]
    carried: list[list[Sample]] = []
    for idx, run in enumerate(runs):
        stretches[-1].append(run)
        if idx in chains:
            carried.append(chains[idx])
            stretches.append([])
    out: list[tuple[_Run, list[Sample]]] = []
    for n, stretch in enumerate(stretches):
        if not stretch:
            continue
        last_stretch = n == len(stretches) - 1
        stretch_end = ctx.end if last_stretch else stretches[n + 1][0].first
        merged = _merge_short(_coalesce(stretch), ctx, n == 0, last_stretch, stretch_end)
        for k, run in enumerate(merged):
            out.append((run, carried[n] if k == len(merged) - 1 and n < len(carried) else []))
    return out


def _rect_of(samples: list[tuple[Sample, float]], label: float) -> tuple[int, int, int, int]:
    own = [s for s, own_label in samples if own_label == label] or [s for s, _ in samples]
    rects = Counter((s.x, s.y, s.w, s.h) for s in own)
    best = rects.most_common(1)[0]
    if best[1] > 1 or len(rects) == 1:
        return best[0]
    x, y, w, h = (round(statistics.median(v)) for v in zip(*rects, strict=True))
    return x, y, w, h


def segment_samples(
    samples: list[Sample],
    info: VideoInfo,
    params: SegmentationParams = SegmentationParams(),  # noqa: B008
) -> CoarseResult:
    """Gate, snap, group and merge. `info.duration` closes the last segment."""
    stats: Counter[str] = Counter(total=len(samples))
    ordered = sorted(samples, key=lambda s: s.t)
    labelled: list[tuple[Sample, float, bool]] = []
    for s in ordered:
        reason = unusable_reason(s, info, params)
        if reason:
            stats[reason] += 1
            continue
        label, preset = snap_aspect(active_aspect(s, info), params.snap_tolerance)
        stats["snapped" if preset else "raw"] += 1
        labelled.append((s, label, preset))
    if not labelled:
        return CoarseResult((), (), ("no_usable_samples",), dict(stats))
    stats["kept"] = len(labelled)
    end = max(info.duration, labelled[-1][0].t)
    # the sampling interval is taken from all samples, so gated-out dark stretches do not look like sparse sampling
    all_gaps = [b.t - a.t for a, b in zip(ordered, ordered[1:], strict=False)]
    gap = statistics.median(all_gaps) if all_gaps else 1.0
    min_samples = params.min_samples if gap <= params.min_segment_s / 2 else 1
    ctx = _Context(params, gap, min_samples, end)

    runs, chains = _extract_ramps(_build_runs(labelled), ctx)
    runs_with_chains = _merge_with_ramps(runs, chains, ctx)

    segments: list[CoarseSegment] = []
    boundaries: list[BoundaryWindow] = []
    for idx, (run, intermediates) in enumerate(runs_with_chains):
        last = idx == len(runs_with_chains) - 1
        agree = sum(1 for _, own in run.samples if _same(own, run.label, run.preset, run.preset))
        start, stop = (0.0 if idx == 0 else run.first), (end if last else run.last)
        confidence = round(agree / len(run.samples), 3)
        segments.append(
            CoarseSegment(
                start=start,
                end=stop,
                aspect=run.label,
                preset=run.preset,
                rect=_rect_of(run.samples, run.label),
                samples=len(run.samples),
                confidence=confidence,
                needs_verification=(
                    not run.preset or confidence < VERIFY_CONFIDENCE or stop - start < VERIFY_SHORT_SEGMENT_S
                ),
            )
        )
        if not last:
            boundaries.append(
                BoundaryWindow(run.last, runs_with_chains[idx + 1][0].first, tuple(intermediates))
            )
    flags = []
    if len({(s.aspect, s.preset) for s in segments}) > 1:
        flags.append("var")
    if any(b.ramp_candidate for b in boundaries):
        flags.append("ramp_candidate")
    return CoarseResult(tuple(segments), tuple(boundaries), tuple(flags), dict(stats))
