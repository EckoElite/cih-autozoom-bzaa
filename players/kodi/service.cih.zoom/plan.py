"""From the analyzer's timeline (schema v1) to the zoom values Kodi should show: the section 5 math.

Pure Python, no Kodi imports, so it is tested on a PC.

Kodi's "Normal" view fits the whole video frame into the 3840x2160 output, keeping its display aspect ratio. The
active picture (the frame without its baked-in mattes) therefore appears at a size we can compute, and the zoom that
makes it just fit the screen band is

    s = min(band_width / w0, band_height / h0)          (contain; the rule of spec section 5.2)

where (w0, h0) is the active picture's displayed size at Normal. With `scope_fill`, a picture wider than the screen
is instead scaled to fill the band's height (the sides are cropped). The analyzer centres every rectangle on the
frame and the design band is centred on the output, so no vertical shift is needed (the shift's units were never
measured; see docs/AUDIT_1.md).
"""

from __future__ import annotations

from dataclasses import dataclass

PRESETS = (4 / 3, 16 / 9, 1.85, 1.90, 2.00, 2.20, 2.35, 2.39, 2.40)
EDGE_BIASES = ("no_spill", "no_bars", "centered")


@dataclass(frozen=True)
class Geometry:
    """The output frame and the part of it that lands on the screen (config/geometry.json, `design_band`)."""

    out_w: int = 3840
    out_h: int = 2160
    band_top: int = 263
    band_bottom: int = 1897  # exclusive
    scope_fill: bool = False

    @property
    def band_h(self) -> int:
        return self.band_bottom - self.band_top

    @property
    def screen_ratio(self) -> float:
        return self.out_w / self.band_h


@dataclass(frozen=True)
class Video:
    """The stream the timeline describes: size in pixels and pixel aspect ratio."""

    width: int
    height: int
    sar: float = 1.0

    @property
    def dar(self) -> float:
        return self.width * self.sar / self.height


def parse_sar(text: str | None) -> float:
    try:
        n, _, d = (text or "1:1").partition(":")
        return float(n) / float(d or 1)
    except (ValueError, ZeroDivisionError):
        return 1.0


def video_of(timeline: dict) -> Video:
    v = timeline["video"]
    return Video(int(v["width"]), int(v["height"]), parse_sar(v.get("sar")))


def displayed_size(video: Video, geo: Geometry, w: float, h: float) -> tuple[float, float]:
    """Size on the output of a w x h region of the frame (source pixels) when Kodi shows the frame at Normal."""
    if video.dar >= geo.out_w / geo.out_h:
        shown_w = float(geo.out_w)
    else:
        shown_w = geo.out_h * video.dar
    shown_h = shown_w / video.dar
    return w * shown_w / video.width, h * shown_h / video.height


def zoom_for_size(w0: float, h0: float, geo: Geometry) -> float:
    """The zoom that fits a picture displayed at w0 x h0 (at Normal) to the band."""
    if w0 <= 0 or h0 <= 0:
        return 1.0
    fit = min(geo.out_w / w0, geo.band_h / h0)
    if geo.scope_fill and w0 / h0 > geo.screen_ratio:
        return geo.band_h / h0
    return fit


def rect_zoom(video: Video, geo: Geometry, w: float, h: float) -> float:
    return zoom_for_size(*displayed_size(video, geo, w, h), geo)


def ratio_zoom(video: Video, geo: Geometry, ratio: float) -> float:
    """The zoom for a picture of display ratio `ratio` that fills the frame's width, or its height if narrower:
    what a manual preset means."""
    if ratio >= video.dar:
        w, h = video.width, video.width * video.sar / ratio
    else:
        w, h = video.height * ratio / video.sar, video.height
    return rect_zoom(video, geo, w, h)


def full_frame_zoom(video: Video, geo: Geometry) -> float:
    """The R6 fallback: contain the whole frame by its container's display ratio (never cuts picture)."""
    return rect_zoom(video, geo, video.width, video.height)


def _r(z: float) -> float:
    return round(float(z), 6)


def _biased_frame(frame: int, zoom_from: float, zoom_to: float, bias: str, floor: int) -> int:
    """Where a cut's set point goes. A shrink applied a frame late leaves one frame too big for the screen (light past
    its edges); a grow applied a frame late leaves one frame of thin bars. `no_spill` therefore puts shrinks a frame
    early and grows a frame late, `no_bars` the reverse; nothing moves for `centered`, nor if it would collide with
    the previous set point."""
    if bias == "centered" or zoom_to == zoom_from:
        return frame
    shrinks = zoom_to < zoom_from
    shift = -1 if shrinks == (bias == "no_spill") else 1
    return frame + shift if frame + shift > floor else frame


def build_plan(
    timeline: dict,
    geo: Geometry = Geometry(),  # noqa: B008 - a frozen dataclass
    edge_bias: str = "no_spill",
    override: dict | None = None,
) -> dict:
    """The timeline as set points, in the shape zoomcore schedules:

        {"fps": float, "initial": float,
         "cuts":  [{"frame", "zoom_from", "zoom_to"}, ...],
         "ramps": [{"start_frame", "zoom": [one value per frame]}, ...]}

    `override` is the analyzer's per-title override, `{"mode": "preset" | "disable", "preset": ratio}`: a disabled
    title stays at Normal, a preset applies one zoom throughout.
    """
    video = video_of(timeline)
    fps = float(timeline["video"]["fps"])
    mode = (override or {}).get("mode")
    if mode == "disable":
        return {"fps": fps, "initial": 1.0, "cuts": [], "ramps": [], "override": "disable"}
    if mode == "preset" and override.get("preset"):
        z = _r(ratio_zoom(video, geo, float(override["preset"])))
        return {"fps": fps, "initial": z, "cuts": [], "ramps": [], "override": f"preset {override['preset']}"}

    segments = timeline["segments"]
    zooms = [rect_zoom(video, geo, s["active"]["w"], s["active"]["h"]) for s in segments]
    cuts: list[dict] = []
    floor = -1
    ramps: list[dict] = []
    for r in timeline.get("ramps", []):
        values = []
        for f in r["frames"]:
            values.append(_r(rect_zoom(video, geo, f["right"] - f["left"], f["bottom"] - f["top"])))
        ramps.append({"start_frame": int(r["frames"][0]["f"]), "zoom": values})
    ramp_frames = {r["start_frame"] + i for r in ramps for i in range(len(r["zoom"]))}
    for i in range(1, len(segments)):
        boundary = segments[i]["boundary"]
        frame = int(boundary["frame"])
        if boundary["kind"] == "cut":
            frame = _biased_frame(frame, zooms[i - 1], zooms[i], edge_bias, floor)
            if frame in ramp_frames:
                frame = int(boundary["frame"])
        floor = frame
        cuts.append({"frame": frame, "zoom_from": _r(zooms[i - 1]), "zoom_to": _r(zooms[i])})
    return {"fps": fps, "initial": _r(zooms[0]) if zooms else 1.0, "cuts": cuts, "ramps": ramps}
