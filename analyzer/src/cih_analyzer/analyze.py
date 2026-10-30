"""One file in, one validated timeline out (spec Phase 4 steps 1 to 7): probe and skip rules, coarse pass, segmentation,
refinement, then the schema-v1 timeline.

Segment times follow the player's clock. A segment starts on the first frame at its ratio and ends where the next
change begins: for a cut that is the same instant, for a ramp it is the ramp's first frame, and the ramp's own
per-frame edges fill the stretch in between. A segment's `boundary` says how it was entered: `frame` is its first
frame, and the first segment is entered by a cut at frame 0.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import __version__
from .coarse import coarse_scan, limit_for
from .fingerprint import quick_hash
from .known import is_known_variable
from .probe import ProbeError, VideoInfo
from .refine import RefineParams, RefineResult, refine
from .schema import (
    Active,
    Boundary,
    FileInfo,
    Params,
    PlexIds,
    Ramp,
    RampFrame,
    Segment,
    Timeline,
    Video,
)
from .segmentation import CoarseResult, SegmentationParams, segment_samples
from .skip import SkipConfig, inspect_file
from .survey import judge, survey_scan, worth_surveying

STAGE_SHARE = 0.5  # of the progress bar, the first pass takes this much and the checking of changes the rest
SURVEY_SHARE = 0.4  # of the whole bar, when a survey is made first


@dataclass(frozen=True)
class Analysis:
    """Exactly one of `timeline` and `skip` is set. `ramp_raw` keeps what was measured on every ramp frame (the
    timeline stores only the fitted curve), for the status page's raw-versus-fitted plot."""

    timeline: Timeline | None
    skip: str | None = None
    ramp_raw: tuple[dict, ...] = ()


def ramp_raw_of(result: RefineResult) -> tuple[dict, ...]:
    """Per ramp: its first frame and, for every frame, the measured edges (None where unreadable)."""
    out = []
    for b in result.boundaries:
        if b.kind != "ramp" or not b.ramp:
            continue
        frames = [
            {
                "f": f.f,
                "t": round(f.t, 4),
                "raw": None
                if f.raw is None
                else {"top": f.raw.top, "bottom": f.raw.bottom, "left": f.raw.left, "right": f.raw.right},
                "readable": f.readable,
            }
            for f in b.ramp
        ]
        out.append({"start_frame": b.ramp[0].f, "frames": frames})
    return tuple(out)


def _video(info: VideoInfo) -> Video:
    return Video(
        width=info.width,
        height=info.height,
        sar=info.sar,
        dar=info.dar,
        fps=info.fps,
        start_time=info.start_time,
        duration=info.duration,
        hdr=info.hdr,
    )


def build_timeline(
    path: str,
    info: VideoInfo,
    result: RefineResult,
    seg_params: SegmentationParams,
    *,
    display_path: str | None = None,
    plex: PlexIds | None = None,
) -> Timeline:
    """Assemble the schema-v1 timeline from refinement output."""
    stat = Path(path).stat()
    segments: list[Segment] = []
    for i, seg in enumerate(result.segments):
        if i == 0:
            boundary = Boundary(kind="cut", frame=0, scene_cut=None)
        else:
            b = result.boundaries[i - 1]
            boundary = Boundary(kind=b.kind, frame=b.frame, scene_cut=b.scene_cut)
        x, y, w, h = seg.rect
        segments.append(
            Segment(
                start=max(0.0, seg.start),
                end=seg.end,
                active=Active(x=x, y=y, w=w, h=h),
                aspect=seg.aspect,
                preset=seg.preset,
                confidence=seg.confidence,
                boundary=boundary,
            )
        )
    ramps: list[Ramp] = []
    for b in result.boundaries:
        if b.kind != "ramp" or not b.ramp:
            continue
        frames = [
            RampFrame(
                f=f.f,
                t=f.t,
                top=f.fitted.top,
                bottom=f.fitted.bottom,
                left=f.fitted.left,
                right=f.fitted.right,
                readable=f.readable,
            )
            for f in b.ramp
        ]
        ramps.append(
            Ramp(
                start=frames[0].t,
                end=frames[-1].t,
                start_frame=frames[0].f,
                end_frame=frames[-1].f,
                confidence=b.confidence,
                frames=frames,
            )
        )
    flags = list(result.flags)
    flags += [
        f"boundary:{f}" for b in result.boundaries for f in b.flags if f in ("unresolved", "low_confidence")
    ]
    if any(b.scene_cut is False for b in result.boundaries):
        flags.append("cut_without_scene_change")
    if any("needs_review" in s.flags for s in result.segments):
        flags.append("needs_review")
    return Timeline(
        analyzer_version=__version__,
        params=Params(
            min_segment_s=seg_params.min_segment_s,
            dark_yavg=seg_params.dark_yavg,
            cropdetect_limit=limit_for(info),
        ),
        file=FileInfo(
            path=display_path or path, size=stat.st_size, mtime=stat.st_mtime, quick_hash=quick_hash(path)
        ),
        plex=plex or PlexIds(),
        video=_video(info),
        segments=segments,
        ramps=ramps,
        flags=list(dict.fromkeys(flags)),
    )


def analyze_file(
    path: str | os.PathLike[str],
    *,
    display_path: str | None = None,
    plex: PlexIds | None = None,
    skip_config: SkipConfig | None = None,
    seg_params: SegmentationParams | None = None,
    refine_params: RefineParams | None = None,
    threads: int | None = None,
    sample_interval_s: float | None = None,
    survey_interval_s: float | None = None,
    known_variable_file: str = "",
    progress: Callable[[str, float], None] | None = None,
) -> Analysis:
    """Analyze one file. Skipped files (ISO, 3D, extras, audio-only, broken) come back with a reason, not an error;
    anything else that goes wrong while decoding raises `ProbeError`.

    With `survey_interval_s` the file is first surveyed (one keyframe every that many seconds, found by seeking);
    a file that shows one picture throughout skips the detailed pass that reads every keyframe."""
    path = os.fspath(path)
    inspected = inspect_file(path, skip_config or SkipConfig())
    if inspected.info is None:
        return Analysis(None, inspected.skip)
    info = inspected.info
    seg_params = seg_params or SegmentationParams()
    scan_args = {} if sample_interval_s is None else {"min_interval_s": sample_interval_s}
    known = survey_interval_s is not None and is_known_variable(path, known_variable_file)
    surveying = survey_interval_s is not None and not known and worth_surveying(info, survey_interval_s)

    def stage(phase: str, base: float, span: float) -> Callable[[float], None] | None:
        """Report `phase` as part of the whole bar (an estimate; which part takes longer depends on the film)."""
        if progress is None:
            return None
        return lambda share: progress(phase, base + span * min(1.0, max(0.0, share)))

    verdict = None
    if surveying:
        try:
            verdict = judge(
                survey_scan(
                    path, info, interval=survey_interval_s, progress=stage("surveying", 0.0, SURVEY_SHARE)
                ),
                info,
                seg_params,
                survey_interval_s,
            )
        except ProbeError:
            verdict = (
                None  # an unreadable survey decides nothing; the detailed pass either copes or reports it
            )
    if verdict is not None and verdict.constant:
        coarse = CoarseResult((verdict.segment,), (), ("surveyed",), {"survey": verdict.usable})
        checking = stage("checking", SURVEY_SHARE, 1.0 - SURVEY_SHARE)
    else:
        start = SURVEY_SHARE if surveying else 0.0
        read_share = (1.0 - start) * STAGE_SHARE
        samples = coarse_scan(
            path, info, threads=threads, progress=stage("reading", start, read_share), **scan_args
        )
        coarse = segment_samples(samples, info, seg_params)
        if known:
            coarse = replace(coarse, flags=(*coarse.flags, "known_variable"))
        checking = stage("checking", start + read_share, 1.0 - start - read_share)
    result = refine(path, info, coarse, refine_params or RefineParams(), progress=checking)
    timeline = build_timeline(path, info, result, seg_params, display_path=display_path, plex=plex)
    return Analysis(timeline, ramp_raw=ramp_raw_of(result))
