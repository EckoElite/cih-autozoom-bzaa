"""The survey: a cheap first look at a file, before the detailed first pass (spec Phase 4 step 2).

Most films never change ratio. Reading every keyframe of a 60 GB remux to learn that is slow, because the whole file
has to be read. The survey instead seeks to one place every `interval` seconds (default 8), decodes the keyframe
there, and measures the picture the way the detailed pass does. If every usable sample shows the same picture
(within the zoom the player would not notice), the file is taken to be constant and the survey's one segment goes to
the refinement pass, which re-measures it at full resolution. If any sample differs, or too few can be read, nothing
is decided and the detailed pass runs as before.

What the survey can miss: a change shorter than the interval that no sample lands in, in a film that otherwise never
changes. The detailed pass has the same blind spot for anything shorter than its keyframe spacing; the survey's is
wider, which is the price of reading a few percent of the file.
"""

from __future__ import annotations

import shutil
import statistics
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .coarse import SCALE_WIDTH, Sample, build_filter, limit_for, parse_metadata
from .geometry import EQUIVALENT_ZOOM, picture_zoom
from .probe import ProbeError, VideoInfo
from .segmentation import CoarseSegment, SegmentationParams, active_aspect, snap_aspect, unusable_reason

SURVEY_INTERVAL_S = 8.0
MIN_SLOTS = 10  # a file shorter than this many intervals is simply read whole
MIN_USABLE_SAMPLES = 8  # fewer readable samples than this say nothing about the file
MIN_USABLE_SHARE = 0.3  # of all the samples: a film that is mostly dark cannot be surveyed
MIN_PICTURE_AREA = 0.5  # of the frame: a smaller "picture" is credits or a title card, not a shot
BRIGHT_YAVG = 40.0  # mean luma (0..255) a sample needs to vouch for a picture different from the usual one
LOPSIDED_SLACK = (
    0.02  # of the frame: how much room a crop's larger margin needs to count as opened on both sides
)
SEEK_TIMEOUT_S = 180.0
MAX_FAILED_SHARE = 0.25

Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class Verdict:
    """`constant` is True when the survey settles the file. `segment` is the whole-file segment to refine then;
    `reason` says why not otherwise: `varies` (a sample showed a different picture) or `unclear` (too few readable)."""

    constant: bool
    reason: str
    segment: CoarseSegment | None = None
    usable: int = 0
    total: int = 0


def worth_surveying(info: VideoInfo, interval: float) -> bool:
    return interval > 0 and info.duration >= MIN_SLOTS * interval


def seek_targets(info: VideoInfo, interval: float) -> list[float]:
    """The middle of each `interval`-long slot of the file."""
    slots = max(1, int(info.duration // interval))
    step = info.duration / slots
    return [(k + 0.5) * step for k in range(slots)]


def _seek_sample(
    exe: str, path: str, info: VideoInfo, vf: str, at: float, timeout: float
) -> list[Sample] | None:
    """The keyframe at or before `at` (and the next one, which ffmpeg decodes anyway), measured. None if ffmpeg failed."""
    cmd = [
        exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-copyts", "-noaccurate_seek",
        "-skip_frame", "nokey", "-ss", f"{at:.3f}", "-i", path, "-map", f"0:{info.stream_index}",
        "-an", "-sn", "-dn", "-frames:v", "1", "-vf", vf, "-f", "null", "-",
    ]  # fmt: skip
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)  # noqa: S603 - fixed command, no shell
    except (subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0:
        return None
    return parse_metadata(done.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n"), info)


def survey_scan(
    path: str | Path,
    info: VideoInfo,
    *,
    interval: float = SURVEY_INTERVAL_S,
    limit: float | None = None,
    scale_width: int = SCALE_WIDTH,
    timeout: float = SEEK_TIMEOUT_S,
    progress: Callable[[float], None] | None = None,
    start_at: float = 0.0,
    on_batch: Callable[[dict[int, list[Sample]], dict], None] | None = None,
) -> list[Sample]:
    """Measure the picture at one place every `interval` seconds. Raises ProbeError if ffmpeg is missing or too many
    seeks fail (a damaged file); a few failures are skipped.

    The places are visited from `start_at` (where a player is) to the end of the file, then from the start up to
    `start_at`. `on_batch(samples_by_slot, where)` is called early (after each of the first 5 places, then every 4) so a caller can use
    what is known about the stretch around `start_at` long before the survey is done; `where` says how far each
    direction has got: `{"step", "slots", "first", "forward", "wrapped"}`."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise ProbeError("ffmpeg not found on PATH")
    vf = build_filter(info, limit if limit is not None else limit_for(info), scale_width, 0.0)
    targets = seek_targets(info, interval)
    slots = len(targets)
    step = info.duration / slots
    first = min(slots - 1, max(0, int(start_at // step))) if slots else 0
    order = [*range(first, slots), *range(0, first)]
    found: dict[float, Sample] = {}
    by_slot: dict[int, list[Sample]] = {}
    failed = 0
    for n, slot in enumerate(order, start=1):
        got = _seek_sample(exe, str(path), info, vf, targets[slot], timeout)
        if got is None:
            failed += 1
        else:
            by_slot[slot] = got
            for sample in got:
                found.setdefault(round(sample.t, 2), sample)
        if on_batch and (n <= 5 or (n - 5) % 4 == 0 or n == slots):
            forward = min(n, slots - first)
            on_batch(
                dict(by_slot),
                {"step": step, "slots": slots, "first": first, "forward": forward, "wrapped": n - forward},
            )
        if progress:
            progress(n / slots)
    if failed > MAX_FAILED_SHARE * slots:
        raise ProbeError(f"survey: {failed} of {slots} seeks failed")
    return [found[t] for t in sorted(found)]


def _rect_of(samples: list[Sample]) -> Rect:
    rects = Counter((s.x, s.y, s.w, s.h) for s in samples)
    best, count = rects.most_common(1)[0]
    if count > 1 or len(rects) == 1:
        return best
    x, y, w, h = (round(statistics.median(v)) for v in zip(*rects, strict=True))
    return x, y, w, h


def _lopsided(s: Sample, host: Rect, info: VideoInfo) -> bool:
    """True if `s` is bigger than `host` on one side only (a subtitle, a bright object in the matte): a real bigger
    picture opens the matte on both sides, so even the crop's larger margin is smaller than the host's."""
    skew_x = s.skew if s.skew_x is None else s.skew_x
    skew_y = s.skew if s.skew_y is None else s.skew_y
    grown_y = s.h > host[3] + LOPSIDED_SLACK * info.height
    grown_x = s.w > host[2] + LOPSIDED_SLACK * info.width
    return (grown_y and s.y + skew_y > host[1] - LOPSIDED_SLACK * info.height) or (
        grown_x and s.x + skew_x > host[0] - LOPSIDED_SLACK * info.width
    )


def _seeks(samples: list[Sample], interval: float) -> list[list[Sample]]:
    """The samples grouped by the seek that produced them (one seek returns a keyframe and the next, a second apart)."""
    groups: list[list[Sample]] = []
    for s in sorted(samples, key=lambda x: x.t):
        if groups and s.t - groups[-1][-1].t < interval / 2:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


def judge(
    samples: list[Sample],
    info: VideoInfo,
    params: SegmentationParams | None = None,
    interval: float = SURVEY_INTERVAL_S,
) -> Verdict:
    """Decide from the survey's samples whether the file shows one picture throughout.

    The two ways to be wrong are not alike. Taking a bigger picture for the usual one leaves it too big for the screen,
    so any bright sample whose picture is clearly bigger than the usual one (and grew on both sides, not one) sends
    the file to the detailed pass. Taking a smaller picture for the usual one only leaves bars, and a dark scene's
    crop looks exactly like a smaller picture, so a smaller picture counts only when two seeks in a row are bright and
    show it."""
    params = params or SegmentationParams()
    usable = [s for s in samples if unusable_reason(s, info, params) is None]
    if len(usable) < max(MIN_USABLE_SAMPLES, MIN_USABLE_SHARE * len(samples)):
        return Verdict(False, "unclear", usable=len(usable), total=len(samples))
    shots = [s for s in usable if s.w * s.h >= MIN_PICTURE_AREA * info.width * info.height]
    if len(shots) < MIN_USABLE_SAMPLES:
        return Verdict(False, "unclear", usable=len(usable), total=len(samples))
    rect = _rect_of(shots)
    usual = picture_zoom(rect, info)
    zoom_of = {id(s): picture_zoom((s.x, s.y, s.w, s.h), info) for s in shots}
    for s in shots:
        if (
            s.yavg >= BRIGHT_YAVG
            and zoom_of[id(s)] < usual / (1 + EQUIVALENT_ZOOM)
            and not _lopsided(s, rect, info)
        ):
            return Verdict(False, "varies", usable=len(shots), total=len(samples))
    smaller_run = 0
    for group in _seeks(samples, interval):
        smaller = any(
            id(s) in zoom_of and s.yavg >= BRIGHT_YAVG and zoom_of[id(s)] > usual * (1 + EQUIVALENT_ZOOM)
            for s in group
        )
        smaller_run = smaller_run + 1 if smaller else 0
        if smaller_run >= 2:
            return Verdict(False, "varies", usable=len(shots), total=len(samples))
    label, preset = snap_aspect(active_aspect(Sample(0.0, *rect, 0.0), info))
    segment = CoarseSegment(
        start=0.0,
        end=max(info.duration, shots[-1].t),
        aspect=label,
        preset=preset,
        rect=rect,
        samples=len(shots),
        confidence=1.0,
        needs_verification=True,  # the refinement pass probes it at full resolution before anything is trusted
    )
    return Verdict(True, "constant", segment, usable=len(shots), total=len(samples))
