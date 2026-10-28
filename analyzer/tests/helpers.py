"""Builders shared by the segmentation tests."""

from __future__ import annotations

from cih_analyzer.coarse import Sample
from cih_analyzer.probe import VideoInfo


def make_info(
    width: int = 3840, height: int = 2160, sar: str = "1:1", duration: float = 100.0, **kw
) -> VideoInfo:
    sar_n, sar_d = (int(p) for p in sar.split(":"))
    fields = {
        "stream_index": 0, "codec": "hevc", "profile": "Main 10", "width": width, "height": height, "sar": sar,
        "dar": width * sar_n / (height * sar_d), "fps": 24.0, "vfr": False, "start_time": 0.0, "duration": duration,
        "nb_frames": None, "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084", "color_primaries": "bt2020",
        "interlaced": False, "hdr": "HDR10", "hdr_extra": (), "container": "matroska,webm", "has_mvc": False,
    }  # fmt: skip
    fields.update(kw)
    return VideoInfo(**fields)


def sample(t: float, aspect: float, *, yavg: float = 80.0, width: int = 3840, height: int = 2160) -> Sample:
    """A keyframe whose crop is the letterbox or pillarbox of `aspect` in a width x height frame."""
    if aspect >= width / height:
        h = 2 * round(width / aspect / 2)
        return Sample(t, 0, (height - h) // 2, width, h, yavg)
    w = 2 * round(height * aspect / 2)
    return Sample(t, (width - w) // 2, 0, w, height, yavg)


def run(start: float, stop: float, aspect: float, step: float = 1.0, **kw) -> list[Sample]:
    """Samples every `step` seconds from `start` up to (not including) `stop`, all at `aspect`."""
    n = round((stop - start) / step)
    return [sample(start + i * step, aspect, **kw) for i in range(n)]
