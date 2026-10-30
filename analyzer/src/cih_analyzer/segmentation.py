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
from .geometry import EQUIVALENT_ZOOM, picture_zoom
from .probe import VideoInfo

PRESETS = (4 / 3, 16 / 9, 1.85, 1.90, 2.00, 2.20, 2.35, 2.39, 2.40)
SNAP_TOLERANCE = 0.015
RAW_SAME_TOLERANCE = 0.01
MIN_PICTURE_FRACTION = 0.04  # a crop smaller than this share of the frame area is noise, not a picture
MIN_PICTURE_PIXELS = 64
RECT_TOLERANCE = 0.01  # fraction of the frame
MIN_RECT_TOLERANCE_PX = 8
MIN_EXCURSION_STATES = 3
VERIFY_CONFIDENCE = 0.9
VERIFY_SHORT_SEGMENT_S = 15.0
FULL_FRAME_SHARE = 0.9  # a picture this much of the frame in both directions (1.85 in 16:9 is 96%) fills it
INSERT_MIN_YAVG = 40.0  # a sample must be at least this bright (0..255) to vouch for a full-frame insert
INSERT_MAX_SKEW = 0.02  # of the frame height: a real bigger picture opens the matte on both sides alike
INSERT_LARGER_BY = 0.9  # the pictures either side must cover at most this share of the insert's area
MIN_INSERT_SAMPLES = 2  # a bigger shot that is not full frame has to be seen at least this often to count
MIN_INSERT_AREA = 0.5  # of the frame: a smaller picture is content on black, not a shot


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


Rect = tuple[int, int, int, int]


def rect_tolerance(info: VideoInfo) -> tuple[float, float]:
    """How far apart (horizontally, vertically) two picture rectangles' edges may be and still be the same state:
    1% of the frame, at least 8 px. The coarse pass is good to about a pixel of its 960-wide frame."""
    return max(MIN_RECT_TOLERANCE_PX, RECT_TOLERANCE * info.width), max(
        MIN_RECT_TOLERANCE_PX, RECT_TOLERANCE * info.height
    )


def _edges(rect: Rect) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    return x, y, x + w, y + h


def rects_close(a: Rect, b: Rect, tol: tuple[float, float]) -> bool:
    ax0, ay0, ax1, ay1 = _edges(a)
    bx0, by0, bx1, by1 = _edges(b)
    return (
        abs(ax0 - bx0) <= tol[0]
        and abs(ax1 - bx1) <= tol[0]
        and abs(ay0 - by0) <= tol[1]
        and abs(ay1 - by1) <= tol[1]
    )


@dataclass
class _Run:
    """Consecutive samples in the same state: the same label and a picture rectangle within tolerance of the
    first sample's, so one ratio at two different sizes (a window that grows to full frame) is two states."""

    label: float
    preset: bool
    samples: list[tuple[Sample, float]]  # (sample, its own snapped label)
    rect: Rect
    tol: tuple[float, float]

    @property
    def first(self) -> float:
        return self.samples[0][0].t

    @property
    def last(self) -> float:
        return self.samples[-1][0].t


class _Context:
    """Timing facts shared by the merge steps: the sampling interval and where the file starts and ends."""

    def __init__(
        self,
        params: SegmentationParams,
        gap: float,
        min_samples: int,
        end: float,
        frame: tuple[int, int] = (0, 0),
        info: VideoInfo | None = None,
    ):
        self.params, self.gap, self.min_samples, self.end = params, gap, min_samples, end
        self.frame = frame  # the container's width and height
        self.info = info

    def is_bigger_shot(self, runs: list[_Run], i: int) -> bool:
        """A short run of at least two bright samples whose picture is clearly bigger than the pictures either side
        (it would hang over the screen if played at their zoom): a real shot at another ratio, not flicker, whatever
        its ratio. Dropping it as flicker would leave that shot too big for the screen, so it is kept for the
        refinement pass to check at full resolution. Growth on one side only (a subtitle, a bright object in the
        matte) is not a bigger picture; that is judged on the axis along which the picture grew."""
        run = runs[i]
        width, height = self.frame
        if self.info is None or not (width and height) or len(run.samples) < MIN_INSERT_SAMPLES:
            return False
        x, y, w, h = run.rect
        if w * h < MIN_INSERT_AREA * width * height:
            return False
        if min(s.yavg for s, _ in run.samples) < INSERT_MIN_YAVG:
            return False
        beside = [runs[j] for j in (i - 1, i + 1) if 0 <= j < len(runs)]
        if not beside:
            return False
        zoom = picture_zoom(run.rect, self.info)
        if not all(zoom < picture_zoom(r.rect, self.info) / (1 + EQUIVALENT_ZOOM) for r in beside):
            return False
        # the axis along which it grew has to reach the edge of the frame (a 2.16 crop among 2.39 ones is not enough:
        # that is what a subtitle does), or it is not a bigger shot but a slightly different crop
        grown_y = (
            h > max(r.rect[3] for r in beside) + INSERT_MAX_SKEW * height and h >= FULL_FRAME_SHARE * height
        )
        grown_x = (
            w > max(r.rect[2] for r in beside) + INSERT_MAX_SKEW * width and w >= FULL_FRAME_SHARE * width
        )
        if not (grown_y or grown_x):
            return False
        # A crop is the picture's smaller margin on both sides; its larger margin is the smaller plus `skew`. A real
        # bigger picture has opened the matte on both sides, so even the larger margin is smaller than the matte beside
        # it. A subtitle opens one side only: its other margin is still the matte's. (Dark picture at one edge looks
        # like the first and is the second only where it is not as wide as the matte.)
        room_y = min(r.rect[1] for r in beside) - INSERT_MAX_SKEW * height
        room_x = min(r.rect[0] for r in beside) - INSERT_MAX_SKEW * width
        for s, _ in run.samples:
            skew_x = s.skew if s.skew_x is None else s.skew_x
            skew_y = s.skew if s.skew_y is None else s.skew_y
            if (grown_y and s.y + skew_y > room_y) or (grown_x and s.x + skew_x > room_x):
                return False
        return True

    def is_full_frame_insert(self, runs: list[_Run], i: int) -> bool:
        """A short run whose picture fills the whole frame on bright samples, between smaller pictures: a real shot
        of a bigger picture (a 16:9 IMAX shot among scope ones), not a flash or a misread. Dropping it as flicker
        would leave that shot cropped, so it is kept for the refinement pass to check at full resolution."""
        run = runs[i]
        width, height = self.frame
        if not (run.preset and width and height):
            return False
        x, y, w, h = run.rect
        if w < FULL_FRAME_SHARE * width or h < FULL_FRAME_SHARE * height:
            return False
        if min(s.yavg for s, _ in run.samples) < INSERT_MIN_YAVG:
            return False
        if max(s.skew for s, _ in run.samples) > INSERT_MAX_SKEW * height:
            return False  # a crop that grew on one side only is a subtitle or a bright object in the matte
        beside = [runs[j] for j in (i - 1, i + 1) if 0 <= j < len(runs)]
        return bool(beside) and all(r.rect[2] * r.rect[3] <= INSERT_LARGER_BY * w * h for r in beside)

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
    return _same(a.label, b.label, a.preset, b.preset) and rects_close(a.rect, b.rect, a.tol)


def _build_runs(labelled: list[tuple[Sample, float, bool]], tol: tuple[float, float]) -> list[_Run]:
    runs: list[_Run] = []
    for sample, label, preset in labelled:
        rect = (sample.x, sample.y, sample.w, sample.h)
        if (
            runs
            and _same(runs[-1].label, label, runs[-1].preset, preset)
            and rects_close(runs[-1].rect, rect, tol)
        ):
            runs[-1].samples.append((sample, label))
        else:
            runs.append(_Run(label, preset, [(sample, label)], rect, tol))
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


def _rect_ramp(left: _Run, chain: list[_Run], right: _Run) -> bool:
    """True if the chain's picture rectangles follow a gradual change, judged on the rectangles themselves.

    Either (1) every edge moves one way from `left` through the chain to `right`, and the two ends really differ;
    or (2) the chain has at least three states, every edge of which moves one way on to `right` (the picture jumped
    to something else, perhaps a small window, then eased into `right`, which may even equal `left`).
    """
    tol = left.tol

    def one_way(seq: list[tuple[int, int, int, int]]) -> bool:
        for e in range(4):
            t = tol[e % 2]  # edges are x0, y0, x1, y1
            vals = [s[e] for s in seq]
            rising = all(b >= a - t for a, b in zip(vals, vals[1:], strict=False))
            falling = all(b <= a + t for a, b in zip(vals, vals[1:], strict=False))
            if not (rising or falling):
                return False
        return True

    def differ(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
        return any(abs(a[e] - b[e]) > 2 * tol[e % 2] for e in range(4))

    start, end = _edges(left.rect), _edges(right.rect)
    mid = [_edges(r.rect) for r in chain]
    if differ(start, end) and one_way([start, *mid, end]):
        return True
    return len(chain) >= MIN_EXCURSION_STATES and differ(mid[0], end) and one_way([*mid, end])


def _extract_ramps(runs: list[_Run], ctx: _Context) -> tuple[list[_Run], dict[int, list[Sample]]]:
    """Remove chains of short runs that are a gradual change between two stable runs.

    A ramp's in-between states may coincide with standard ratios (2.20, 2.00, 1.85 between 2.39 and 1.78), so every
    short run is a candidate. Two tests decide: the labels lie strictly between the two ratios, in order; or the
    picture rectangles follow a one-way change (`_rect_ramp`), which also covers a ramp that returns to the ratio it
    started from, and one at a constant ratio whose size changes. The first run of the file may serve as the left end
    even when it is short (an opening that cuts to a small window). Returns the remaining runs and, keyed by the
    index (in the remaining list) of the run *before* each removed chain, the chain's samples.
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
        first = i + 1 if i == 0 and j > i else i  # a short run that opens the file anchors a chain after it
        if first > 0 and j + 1 < len(runs) and first <= j:
            left, right = runs[first - 1], runs[j + 1]
            states = runs[first : j + 1]
            chain = [r.label for r in states]
            if (
                not _same_run(left, right) and _monotone_between(left.label, chain, right.label)
            ) or _rect_ramp(left, states, right):
                for k in range(first, j + 1):
                    keep[k] = False
                found[first - 1] = [s for r in states for s, _ in r.samples]
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
        shorts = [
            i
            for i, r in enumerate(runs)
            if ctx.is_short(r, durations[i])
            and not (ctx.is_full_frame_insert(runs, i) or ctx.is_bigger_shot(runs, i))
        ]
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
            if left is not None and right is not None and ctx.info is not None:
                # Absorbing a bigger picture into a smaller one leaves that shot too big for the screen, so a
                # neighbour whose picture is at least as big as this run's is the better host, however short.
                need = picture_zoom(runs[i].rect, ctx.info) * (1 + EQUIVALENT_ZOOM)
                fits = [picture_zoom(r.rect, ctx.info) <= need for r in (left, right)]
                if fits[0] != fits[1]:
                    into_left = fits[0]
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
    ctx = _Context(params, gap, min_samples, end, (info.width, info.height), info)

    runs, chains = _extract_ramps(_build_runs(labelled, rect_tolerance(info)), ctx)
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
