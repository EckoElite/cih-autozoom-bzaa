"""Test clips and ground-truth timelines for the zoom frame-skip spike (docs/ZOOM_SPIKE_REPORT.md).

Every clip is 60 s at 23.976 fps: a 16:9 frame with the active picture matted inside it, a 1 px white border on the
picture's edge, tick marks every 8 px (scaled with the frame) along its top and bottom edges, and a large burned-in
frame counter in the middle. The matte changes as follows (the frame numbers are in the JSON):

  0-10 s 2.39 | 10 s cut to 1.90 | 20 s cut to 2.39 | 30-33 s eased ramp 2.39 -> 1.78 | 33-40 s 1.78 |
  40 s cut to 2.39 | 45-47 s linear ramp 2.39 -> 1.78 | 47-60 s cuts every 2 s between 2.39 and 1.85

usage: python make_clips.py <out dir> [clip ...]        clips: sdr1080 hdr4k hdr4k_heavy (default: all three)
Needs ffmpeg (libx264, libx265, drawtext) and numpy. The heavy clip adds frame-to-frame noise so the bitrate is high
(about 80 Mbit/s), to load the decoder the way a real remux does.
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

FPS_NUM, FPS_DEN = 24000, 1001
FPS = FPS_NUM / FPS_DEN
SECONDS = 60
FRAMES = int(SECONDS * FPS)  # 1438
FONT = "C\\:/Windows/Fonts/arial.ttf"


def smoothstep(x: float) -> float:
    return x * x * (3 - 2 * x)


def ratio_rect(ratio: float, width: int, height: int) -> tuple[int, int, int, int]:
    h = min(height, int(round(width / ratio / 2)) * 2)
    return 0, (height - h) // 2, width, h


def timeline(width: int, height: int) -> tuple[list[list[int]], list[dict]]:
    """(rect per frame, the changes: kind and frame numbers)."""
    r239, r190, r178, r185 = (ratio_rect(r, width, height) for r in (2.39, 1.90, 16 / 9, 1.85))
    rects: list[list[int]] = []
    changes: list[dict] = []

    def at(n: int) -> list[int]:
        t = n / FPS
        if t < 10:
            return list(r239)
        if t < 20:
            return list(r190)
        if t < 30:
            return list(r239)
        if t < 33:  # eased ramp opening to 16:9
            return lerp(r239, r178, smoothstep((t - 30) / 3))
        if t < 40:
            return list(r178)
        if t < 45:
            return list(r239)
        if t < 47:  # linear ramp
            return lerp(r239, r178, (t - 45) / 2)
        slot = int((t - 47) // 2)
        return list(r239 if slot % 2 == 0 else r185)

    def lerp(a, b, k):
        h = int(round((a[3] + (b[3] - a[3]) * k) / 2)) * 2
        return [0, (height - h) // 2, width, h]

    for n in range(FRAMES):
        rects.append(at(n))
        if n and rects[n] != rects[n - 1]:
            kind = "ramp" if rects[n][3] not in (r239[3], r190[3], r178[3], r185[3]) or (
                rects[n - 1][3] not in (r239[3], r190[3], r178[3], r185[3])) else "cut"
            changes.append({"frame": n, "kind": kind})
    return rects, changes


def pattern(width: int, height: int) -> np.ndarray:
    """Colour bars over a gradient with a faint checker, so scaling and motion are visible."""
    x = np.arange(width)[None, :]
    y = np.arange(height)[:, None]
    bars = (x * 8 // width)
    colours = np.array([[235, 235, 235], [235, 235, 16], [16, 235, 235], [16, 235, 16],
                        [235, 16, 235], [235, 16, 16], [16, 16, 235], [90, 90, 90]], dtype=np.float32)
    img = colours[bars] * (0.35 + 0.5 * (y / height))[..., None]
    check = (((x // 64) + (y // 64)) % 2)[..., None] * 18.0
    return np.clip(img + check, 16, 235).astype(np.uint8)


def draw(frame: np.ndarray, base: np.ndarray, rect: list[int], scale: int, noise: np.ndarray | None) -> None:
    x, y, w, h = rect
    frame[:] = 16
    patch = base[y:y + h, x:x + w]
    if noise is not None:
        patch = np.clip(patch.astype(np.int16) + noise[y:y + h, x:x + w], 16, 235).astype(np.uint8)
    frame[y:y + h, x:x + w] = patch
    b = scale  # border thickness
    frame[y:y + b, x:x + w] = 255
    frame[y + h - b:y + h, x:x + w] = 255
    frame[y:y + h, x:x + b] = 255
    frame[y:y + h, x + w - b:x + w] = 255
    step = 8 * scale
    for tx in range(x, x + w, step):
        frame[y:y + 6 * scale, tx:tx + scale] = 255
        frame[y + h - 6 * scale:y + h, tx:tx + scale] = 255


def encoder(kind: str, width: int, height: int, out: Path) -> list[str]:
    text = (f"drawtext=fontfile='{FONT}':text='%{{n}}':x=(w-text_w)/2:y=(h-text_h)/2:fontsize={height // 5}"
            ":fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=20")
    head = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-framerate", f"{FPS_NUM}/{FPS_DEN}", "-i", "-"]
    if kind == "sdr1080":
        return [*head, "-vf", f"{text},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-g", "24", "-pix_fmt", "yuv420p",
                "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", str(out)]
    params = ("colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:range=limited"
              ":master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
              ":max-cll=1000,400:repeat-headers=1:keyint=24:min-keyint=24:no-open-gop=1:log-level=error")
    rate = ["-crf", "16"] if kind == "hdr4k" else ["-b:v", "80M", "-maxrate", "95M", "-bufsize", "160M"]
    return [*head, "-vf", f"{text},scale=out_color_matrix=bt2020nc:out_range=tv,format=yuv420p10le",
            "-c:v", "libx265", "-preset", "veryfast", *rate, "-pix_fmt", "yuv420p10le", "-x265-params", params,
            "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", "-color_range", "tv",
            str(out)]


def make(kind: str, out_dir: Path) -> None:
    width, height = (1920, 1080) if kind == "sdr1080" else (3840, 2160)
    name = {"sdr1080": "clip_1080_sdr", "hdr4k": "clip_4k_hdr10", "hdr4k_heavy": "clip_4k_hdr10_heavy"}[kind]
    rects, changes = timeline(width, height)
    (out_dir / f"timeline_{name}.json").write_text(json.dumps(
        {"clip": f"{name}.mkv", "width": width, "height": height, "fps": FPS, "frames": FRAMES,
         "rects": rects, "changes": changes}), encoding="utf-8")
    base = pattern(width, height)
    rng = np.random.default_rng(1)
    noises = [rng.integers(-40, 40, size=(height, width, 1), dtype=np.int16) for _ in range(6)] \
        if kind == "hdr4k_heavy" else [None]
    frame = np.empty((height, width, 3), dtype=np.uint8)
    proc = subprocess.Popen(encoder(kind, width, height, out_dir / f"{name}.mkv"), stdin=subprocess.PIPE)  # noqa: S603
    for n, rect in enumerate(rects):
        draw(frame, base, rect, 1 if width == 1920 else 2, noises[n % len(noises)])
        proc.stdin.write(frame.tobytes())
        if n % 240 == 0:
            print(f"{name}: frame {n}/{FRAMES}", flush=True)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit(f"ffmpeg failed for {name}")
    print(f"{name}: done, {len(changes)} changes", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for k in sys.argv[2:] or ["sdr1080", "hdr4k", "hdr4k_heavy"]:
        make(k, out)
