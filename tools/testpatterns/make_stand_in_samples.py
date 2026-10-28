"""Generate stand-in sample files for Phase 0 (output goes to samples/, which is gitignored).

  native_239_3840x1600_hdr10.mkv   HEVC Main10, 3840x1600 (2.40 raster), HDR10-tagged, no mattes
  dvd_720x480_sar32-27_169.mkv     MPEG-2, 720x480, SAR 32:27 -> DAR 16:9 (non-square pixels)

Content is synthetic (ffmpeg testsrc2), so these only exercise container and pixel-aspect handling.

Usage: python make_stand_in_samples.py [out_dir]
"""
import subprocess
import sys
from pathlib import Path

DURATION_S = 20


def run(args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "samples"
    out.mkdir(parents=True, exist_ok=True)

    run([
        "-f", "lavfi", "-i", f"testsrc2=size=3840x1600:rate=24000/1001:duration={DURATION_S}",
        "-vf", "format=yuv420p10le",
        "-c:v", "libx265", "-preset", "ultrafast", "-crf", "24",
        "-x265-params", "hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"
                        ":master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
                        ":max-cll=1000,400:log-level=error",
        "-tag:v", "hvc1",
        str(out / "native_239_3840x1600_hdr10.mkv"),
    ])

    run([
        "-f", "lavfi", "-i", f"testsrc2=size=720x480:rate=24000/1001:duration={DURATION_S}",
        "-vf", "setsar=32/27,format=yuv420p",
        "-c:v", "mpeg2video", "-b:v", "6M", "-maxrate", "8M", "-bufsize", "2M",
        str(out / "dvd_720x480_sar32-27_169.mkv"),
    ])


if __name__ == "__main__":
    main()
