"""Spike A zoom test video: the seven ratio frames from make_calibration.py, no counter.

Reuses tools/testpatterns/out/png/ratio_<r>_<variant>.png (run make_calibration.py first).
Segment k starts at k * seg_s seconds: 1.33, 1.78, 1.85, 1.90, 2.00, 2.35, 2.39.

Usage: python make_zoom_test.py [SDR|HDR10] [seg_s]     (defaults: SDR, 30 s per ratio)
The output name carries the segment length when it is not 30 s, e.g. zoomtest_hdr10_10s_2160p.mkv.
"""
import subprocess
import sys
from pathlib import Path

import make_calibration as cal

# swscale is far faster than zscale for the SDR file; HDR10 needs zscale for PQ / BT.2020
FAST_SDR = "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic,format=yuv420p"


def main():
    variant = (sys.argv[1] if len(sys.argv) > 1 else "SDR").upper()
    seg_s = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    out_dir = Path(__file__).resolve().parent / "out"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    labels = [label for label, _, _ in cal.RATIOS]
    for label in labels:
        png = out_dir / "png" / f"ratio_{label}_{variant.lower()}.png"
        cmd += ["-loop", "1", "-framerate", cal.FPS, "-t", str(seg_s), "-i", str(png)]
    n = len(labels)
    convert = FAST_SDR if variant == "SDR" else cal.CONVERT[variant]
    chains = [f"[{i}:v]setsar=1[v{i}]" for i in range(n)]
    chains.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]")
    chains.append(f"[cat]{convert}[out]")
    script = out_dir / "tmp" / f"zt_{variant.lower()}.txt"
    script.write_text(";\n".join(chains), encoding="utf-8")
    enc = list(cal.ENCODE[variant])
    enc[enc.index("-preset") + 1] = "ultrafast"
    tag = "" if seg_s == 30 else f"_{seg_s}s"
    out = out_dir / f"zoomtest_{variant.lower()}{tag}_2160p.mkv"
    cmd += ["-/filter_complex", str(script), "-map", "[out]", "-r", cal.FPS, *enc, str(out)]
    subprocess.run(cmd, check=True)
    print("built", out)


if __name__ == "__main__":
    main()
