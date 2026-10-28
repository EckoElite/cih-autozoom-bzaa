"""ffprobe wrapper: choose the real video stream of a file and describe it.

Design points (docs/AUDIT_1.md, DD12 to DD16):
  * The video stream is chosen by disposition, never by index: attached pictures (cover art, e.g. the JPEG
    stream in some MP4s) are ignored, and the real video may be stream 1 or later.
  * The display aspect ratio is computed from the frame size and the pixel aspect ratio, never from a tag.
  * Times are the file's own; the caller normalises to the stream's start time.
  * HDR type is read from the stream's side data (Dolby Vision configuration record) and, for PQ streams,
    from the first frames' side data (HDR10+ dynamic metadata).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

PROBE_TIMEOUT_S = 90.0
VFR_TOLERANCE = 0.005
INTERLACED_ORDERS = {"tt", "bb", "tb", "bt"}


class ProbeError(Exception):
    """ffprobe could not read the file (missing, corrupt, timed out, or ffprobe itself is absent)."""


class NoVideoStream(ProbeError):
    """The file has no video stream other than attached pictures (an audio-only file)."""


@dataclass(frozen=True)
class VideoInfo:
    stream_index: int
    codec: str
    profile: str | None
    width: int
    height: int
    sar: str
    dar: float
    fps: float
    vfr: bool
    start_time: float
    duration: float
    nb_frames: int | None
    pix_fmt: str | None
    color_transfer: str | None
    color_primaries: str | None
    interlaced: bool
    hdr: str
    hdr_extra: tuple[str, ...]
    container: str
    has_mvc: bool


def _run_ffprobe(args: list[str], timeout: float = PROBE_TIMEOUT_S) -> dict:
    exe = shutil.which("ffprobe")
    if exe is None:
        raise ProbeError("ffprobe not found on PATH")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed executable, arguments are not shell-interpreted
            [exe, "-v", "error", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out after {timeout:.0f} s") from exc
    if proc.returncode != 0:
        raise ProbeError(
            proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else f"ffprobe exit {proc.returncode}"
        )
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe produced unreadable output") from exc


def _rate(text: str | None) -> float:
    """'24000/1001' to 23.976; '0/0', 'N/A' and missing values to 0."""
    if not text or "/" not in text:
        return 0.0
    num, den = text.split("/", 1)
    try:
        return float(Fraction(int(num), int(den))) if int(den) else 0.0
    except ValueError:
        return 0.0


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _sar(stream: dict) -> tuple[int, int]:
    text = stream.get("sample_aspect_ratio") or "1:1"
    try:
        num, den = (int(part) for part in text.split(":", 1))
    except ValueError:
        return 1, 1
    return (num, den) if num > 0 and den > 0 else (1, 1)


def pick_video_stream(streams: Iterable[dict]) -> dict:
    """The default non-attached video stream, else the largest, else the first."""
    candidates = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not candidates:
        raise NoVideoStream("no video stream (audio-only or cover art only)")
    candidates.sort(
        key=lambda s: (
            -int((s.get("disposition") or {}).get("default", 0)),
            -(int(s.get("width", 0)) * int(s.get("height", 0))),
            int(s.get("index", 0)),
        )
    )
    return candidates[0]


def classify_hdr(stream: dict, frame_side_data: Iterable[str] = ()) -> tuple[str, tuple[str, ...]]:
    """The primary HDR label (spec schema: SDR, HDR10, HDR10+, HLG, DV-P5, DV-P7, DV-P8) and other signalling.

    Dolby Vision wins as the primary label because it decides how the stream must be decoded; HDR10+ and the
    HDR10 base layer it carries are returned as extras. Profiles outside the spec's list are kept as DV-Pn.
    """
    transfer = stream.get("color_transfer")
    base: list[str] = []
    if transfer == "smpte2084":
        base.append("HDR10+" if any("SMPTE2094-40" in t for t in frame_side_data) else "HDR10")
    elif transfer == "arib-std-b67":
        base.append("HLG")
    dovi = next(
        (
            d
            for d in stream.get("side_data_list") or []
            if d.get("side_data_type") == "DOVI configuration record"
        ),
        None,
    )
    if dovi is not None:
        label = f"DV-P{dovi.get('dv_profile')}"
        extras = list(base)
        if "HDR10+" in extras:
            extras.append("HDR10")
        elif not extras and dovi.get("dv_bl_signal_compatibility_id") == 1:
            extras.append("HDR10")
        return label, tuple(dict.fromkeys(extras))
    # No configuration record (some muxers drop it) but Dolby Vision RPU data in the frames: say so, since the
    # profile cannot be known from the frames alone.
    rpu_only = ("DV-RPU",) if any("Dolby Vision RPU" in t for t in frame_side_data) else ()
    if base:
        return base[0], (("HDR10",) if base[0] == "HDR10+" else ()) + rpu_only
    return "SDR", rpu_only


def _frame_side_data(path: str | Path, index: int) -> list[str]:
    """Side-data type names on the first frames of stream `index` (HDR10+ dynamic metadata shows up here)."""
    frames = _run_ffprobe(
        [
            "-select_streams",
            str(index),
            "-read_intervals",
            "%+#3",
            "-show_frames",
            "-show_entries",
            "frame=side_data_list",
            "-of",
            "json",
            "-i",
            str(path),
        ]
    )
    return [
        d.get("side_data_type", "") for f in frames.get("frames") or [] for d in f.get("side_data_list") or []
    ]


def probe_video(path: str | Path) -> VideoInfo:
    """Describe the video of `path`. Raises ProbeError (or NoVideoStream) when it cannot."""
    meta = _run_ffprobe(["-show_format", "-show_streams", "-of", "json", "-i", str(path)])
    stream = pick_video_stream(meta.get("streams") or [])
    fmt = meta.get("format") or {}
    index = int(stream["index"])
    side_data = _frame_side_data(path, index) if stream.get("color_transfer") == "smpte2084" else []
    hdr, extra = classify_hdr(stream, side_data)
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if width <= 0 or height <= 0:
        raise ProbeError("video stream has no frame size")
    sar_n, sar_d = _sar(stream)
    avg, nominal = _rate(stream.get("avg_frame_rate")), _rate(stream.get("r_frame_rate"))
    fps = avg or nominal
    vfr = bool(avg and nominal and abs(avg - nominal) / nominal > VFR_TOLERANCE)
    start = _number(stream.get("start_time"), _number(fmt.get("start_time")))
    duration = _number(stream.get("duration"), _number(fmt.get("duration")))
    frames_text = stream.get("nb_frames")
    profile = stream.get("profile")
    return VideoInfo(
        stream_index=index,
        codec=str(stream.get("codec_name", "")),
        profile=profile,
        width=width,
        height=height,
        sar=f"{sar_n}:{sar_d}",
        dar=width * sar_n / (height * sar_d),
        fps=fps,
        vfr=vfr,
        start_time=start,
        duration=duration,
        nb_frames=int(frames_text) if frames_text and str(frames_text).isdigit() else None,
        pix_fmt=stream.get("pix_fmt"),
        color_transfer=stream.get("color_transfer"),
        color_primaries=stream.get("color_primaries"),
        interlaced=stream.get("field_order") in INTERLACED_ORDERS,
        hdr=hdr,
        hdr_extra=extra,
        container=str(fmt.get("format_name", "")),
        has_mvc=any(
            "MVC" in str(s.get("profile", "")).upper() or "STEREO HIGH" in str(s.get("profile", "")).upper()
            for s in meta.get("streams") or []
            if s.get("codec_type") == "video"
        ),
    )
