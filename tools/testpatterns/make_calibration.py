"""Phase 1 calibration videos (3840x2160, 23.976 fps): one SDR and one HDR10 file.

Each file is a sequence of still patterns:
  1. ROW CHART (30 s): numbered horizontal lines every 4 px across rows 200-328 and 1832-1960,
     numbered vertical ticks every 10 px at the left and right frame edges, a centre cross,
     a 4 px white frame border, and cyan marks at the nominal 2.35:1 band rows (263 / 1896).
  2. RATIO FRAMES (10 s each): 4:3, 16:9, 1.85, 1.90, 2.00, 2.35, 2.39 active areas matted
     (black) inside the 16:9 frame, each with a 4 px coloured border on the active-area edge.

Only ffmpeg is used (drawbox / drawtext / zscale / libx265).

Usage: python make_calibration.py [out_dir]     (default: tools/testpatterns/out)
"""
import subprocess
import sys
from pathlib import Path

W, H = 3840, 2160
FPS = "24000/1001"
CHART_S = 30
RATIO_S = 10
FONT = "C\\:/Windows/Fonts/consola.ttf"

# (label, ratio, border colour)
RATIOS = [
    ("1.33", 4 / 3, "orange"),
    ("1.78", 16 / 9, "lime"),
    ("1.85", 1.85, "yellow"),
    ("1.90", 1.90, "magenta"),
    ("2.00", 2.00, "cyan"),
    ("2.35", 2.35, "red"),
    ("2.39", 2.39, "dodgerblue"),
]


def even(v):
    return int(round(v / 2.0)) * 2


def active_rect(ratio):
    """Active picture rectangle (x, y, w, h) for a given display ratio, centred in the 16:9 frame."""
    if ratio >= W / H:
        w, h = W, even(W / ratio)
    else:
        w, h = even(H * ratio), H
    return (W - w) // 2, (H - h) // 2, w, h


def box(x, y, w, h, color, t="fill"):
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t={t}"


def text(s, x, y, size, color="white"):
    """drawtext; x may be an expression such as (w-text_w)/2.

    s must not contain a colon, quote or percent sign: a colon would be split as a filter option
    (the filtergraph unquotes the string before the filter parses its options).
    """
    return (f"drawtext=fontfile='{FONT}':text='{s}':x={x}:y={y}:fontsize={size}:fontcolor={color}")


def chart_filters(variant):
    f = [box(0, 0, W, H, "white", 4)]  # frame border (left and right sides land on screen sides)

    # horizontal numbered lines, each with a label staggered across 8 columns so none overlap
    for group_start in (200, 1832):
        for y in range(group_start, group_start + 132, 4):
            major = y % 20 == 0
            f.append(box(0, y, W, 2, "yellow" if major else "gray"))
    for group_start in (200, 1832):
        for y in range(group_start, group_start + 132, 4):
            major = y % 20 == 0
            col = (y // 4) % 12  # 12 columns x 4 px pitch = 48 px between labels in one column
            lx = 120 + col * 290
            f.append(box(lx - 8, y - 20, 112, 42, "black"))
            f.append(text(str(y), lx, y - 17, 36, "yellow" if major else "white"))

    f.append(text("TOP GROUP - rows 200 to 328", "(w-text_w)/2", 360, 44, "yellow"))
    f.append(text("BOTTOM GROUP - rows 1832 to 1960", "(w-text_w)/2", 1740, 44, "yellow"))

    # nominal 2.35:1 band marks (centred): rows 263 and 1896, at the far left and right
    for y in (263, 1896):
        f.append(box(24, y, 80, 2, "cyan"))
        f.append(box(W - 104, y, 80, 2, "cyan"))

    # vertical numbered ticks at the left / right frame edges (distance in px from the frame edge)
    for k in range(0, 13):
        d = k * 10
        xl = d
        xr = W - 2 - d
        f.append(box(xl, 600, 2, 960, "gray"))
        f.append(box(xr, 600, 2, 960, "gray"))
    for k in range(0, 13):
        d = k * 10
        ly = 620 + k * 70  # one slot per tick, so no label box clips another
        f.append(box(d + 6, ly - 4, 96, 42, "black"))
        f.append(text(f"L{d}", d + 10, ly, 32, "white"))
        f.append(box(W - 2 - d - 102, ly - 4, 96, 42, "black"))
        f.append(text(f"R{d}", W - 2 - d - 98, ly, 32, "white"))

    # centre cross
    f.append(box(W // 2 - 100, H // 2, 200, 2, "white"))
    f.append(box(W // 2, H // 2 - 100, 2, 200, "white"))

    # instructions
    lines = [
        (f"CIH CALIBRATION - {variant} - 3840x2160", 64, "white", 640),
        ("Read the row number on the line sitting exactly on the screen TOP edge", 40, "white", 740),
        ("then the row number on the line at the screen BOTTOM edge", 40, "white", 800),
        ("Left and right - read the L or R tick number at each side edge of the screen", 40, "white", 860),
        ("Cyan marks = nominal 2.35 to 1 band if centred (rows 263 and 1896)", 40, "cyan", 920),
    ]
    for s, size, color, y in lines:
        f.append(text(s, "(w-text_w)/2", y, size, color))
    return f


def ratio_filters(variant, label, ratio, color):
    x, y, w, h = active_rect(ratio)
    f = [
        box(x, y, w, h, "0x202020"),
        box(x, y, w, h, color, 4),
        box(x + w // 2 - 100, y + h // 2, 200, 2, "white"),
        box(x + w // 2, y + h // 2 - 100, 2, 200, "white"),
        text(f"{label} to 1", "(w-text_w)/2", y + h // 2 - 220, 120, color),
        text(f"active {w}x{h} at x={x} y={y} - rows {y} to {y + h - 1}", "(w-text_w)/2", y + h // 2 + 120, 44, "white"),
        text(f"{variant} ratio frame - borders should meet the screen edges once zoom is applied",
             "(w-text_w)/2", y + h // 2 + 190, 36, "gray"),
    ]
    return f


def render_png(path, filters, tmp):
    script = tmp / (path.stem + ".fc.txt")
    script.write_text(",\n".join(filters), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=1",
         "-/vf", str(script), "-frames:v", "1", str(path)],
        check=True)


CONVERT = {
    "SDR": "format=gbrp,zscale=min=gbr:pin=bt709:tin=bt709:m=bt709:p=bt709:t=bt709:rin=full:r=limited,format=yuv420p",
    "HDR10": ("format=gbrp,zscale=min=gbr:pin=bt709:tin=bt709:p=bt2020:t=smpte2084:m=bt2020nc"
              ":rin=full:r=limited:npl=203,format=yuv420p10le"),
}

ENCODE = {
    "SDR": ["-c:v", "libx265", "-preset", "veryfast", "-crf", "14", "-tag:v", "hvc1",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
            "-x265-params", "log-level=error:repeat-headers=1:colorprim=bt709:transfer=bt709:colormatrix=bt709"],
    "HDR10": ["-c:v", "libx265", "-preset", "veryfast", "-crf", "14", "-tag:v", "hvc1",
              "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", "-color_range", "tv",
              "-x265-params",
              "log-level=error:hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"
              ":master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
              ":max-cll=1000,400"],
}


def build_video(variant, out_dir, tmp):
    pngs = []
    chart = out_dir / "png" / f"chart_{variant.lower()}.png"
    render_png(chart, chart_filters(variant), tmp)
    pngs.append((chart, CHART_S))
    for label, ratio, color in RATIOS:
        p = out_dir / "png" / f"ratio_{label}_{variant.lower()}.png"
        render_png(p, ratio_filters(variant, label, ratio, color), tmp)
        pngs.append((p, RATIO_S))

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for p, secs in pngs:
        cmd += ["-loop", "1", "-framerate", FPS, "-t", str(secs), "-i", str(p)]
    chains = []
    for i in range(len(pngs)):
        chains.append(
            f"[{i}:v]drawtext=fontfile='{FONT}':text='t=%{{pts\\:hms}}':x=(w-text_w)/2:y=1020"
            f":fontsize=56:fontcolor=white:box=1:boxcolor=black@0.8:boxborderw=8,setsar=1[v{i}]")
    n = len(pngs)
    chains.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]")
    chains.append(f"[cat]{CONVERT[variant]}[out]")
    script = tmp / f"fc_{variant.lower()}.txt"
    script.write_text(";\n".join(chains), encoding="utf-8")
    out = out_dir / f"cal_{variant.lower()}_2160p.mkv"
    cmd += ["-/filter_complex", str(script), "-map", "[out]", "-r", FPS, *ENCODE[variant], str(out)]
    subprocess.run(cmd, check=True)
    return out


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "out"
    (out_dir / "png").mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "tmp"
    tmp.mkdir(exist_ok=True)
    for variant in ("SDR", "HDR10"):
        print("built", build_video(variant, out_dir, tmp))


if __name__ == "__main__":
    main()
