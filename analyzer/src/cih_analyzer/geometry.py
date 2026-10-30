"""What a picture rectangle means for playback: how far the player scales it to fit the screen band."""

from __future__ import annotations

from .probe import VideoInfo

Rect = tuple[int, int, int, int]
EQUIVALENT_ZOOM = 0.03  # two rectangles whose zoom differs by less than this are the same for playback
_BAND = (
    3840 / 2.35 / 2160
)  # the screen band's height as a share of the 16:9 output frame (nominal 2.35 screen)


def picture_zoom(rect: Rect, info: VideoInfo) -> float:
    """How much the player would scale this picture to fit the screen band (relative; the 16:9 output frame and a
    nominal 2.35 screen), used only to tell whether two rectangles differ enough to matter. A smaller number means
    a bigger picture."""
    pw, ph = rect[2] / info.width, rect[3] / info.height
    dar = info.dar if info.dar > 0 else 16 / 9
    if dar >= 16 / 9:  # the frame fills the output's width
        ph *= (16 / 9) / dar
    else:  # the frame fills the output's height
        pw *= dar / (16 / 9)
    return min(1 / pw, _BAND / ph)


def same_zoom(a: float, b: float) -> bool:
    return abs(a / b - 1) < EQUIVALENT_ZOOM
