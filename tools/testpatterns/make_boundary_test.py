"""Phase 2 frame-accuracy test video: hard cuts and ramps with a burned-in frame counter.

Picture model: a mid-grey picture with fine horizontal lines, covered by a black matte whose edge is at
row T(n) at the top and 2160 - T(n) at the bottom (so the active picture has height 2160 - 2*T).
Each matte edge carries a 4 px white border and short tick marks on the picture side, so the position of
the edge, and any spill or bars after the player zooms, can be judged in slow motion.

Timeline (frame numbers are 0-based, first frame of the new state):
  0 .. 47            2.39 lead-in
  48 + 72k           cut k = 0..23 to the next state of the cycle 1.90, 2.39, 1.78, 2.39 (72 frames each)
  1776 + 168j        ramp block j = 0 (eased), 1 (linear), 2 (near-black picture, eased):
                       +0 .. 47 hold 2.39, +48 .. 119 matte opens 2.39 -> 1.78 (72 frames), +120 .. 167 hold 1.78
  2280 .. 2327       2.39 tail
A frame counter ("F 00123") and a 14-bit binary code of the same number (white = 1, MSB left, cyan end
markers) are drawn at the centre of the frame, which is the one point the zoom never moves.

Outputs (in tools/testpatterns/out/):
  boundary_<variant>_<fps>.mkv           the video
  boundary_<variant>_<fps>.timeline.json cuts and ramps with the zoom value each state needs (from geometry.json)

Usage: python make_boundary_test.py [SDR|HDR10] [23.976|24]      (defaults: HDR10, 23.976)
"""
import json
import subprocess
import sys
from pathlib import Path

import make_calibration as cal

W, H = 3840, 2160
BAND_H = 1634                       # design band height from config/geometry.json
LEAD_IN = 48
SEG = 72
N_CUTS = 24
CYCLE = ["1.90", "2.39", "1.78", "2.39"]
HOLD, RAMP = 48, 72
RAMP_BLOCK = HOLD + RAMP + HOLD     # 168
RAMP_KINDS = ["eased", "linear", "eased_nearblack"]
TAIL = 48
CODE_BITS = 14

# picture height and matte edge row T for each ratio state, as drawn by make_calibration.active_rect
T_OF = {"2.39": 277, "1.90": 69, "1.78": 0}
H_OF = {k: H - 2 * v for k, v in T_OF.items()}

FRAMES_A0 = LEAD_IN
FRAMES_B0 = LEAD_IN + N_CUTS * SEG
TOTAL = FRAMES_B0 + len(RAMP_KINDS) * RAMP_BLOCK + TAIL


def zoom_for_height(h):
    return min(1.0, BAND_H / h)


def ease(kind, u):
    return u * u * (3 - 2 * u) if kind.startswith("eased") else u


def ramp_T(kind, i):
    """Matte edge row at ramp frame i (0..RAMP-1), same rounding as the ffmpeg expression."""
    u = i / (RAMP - 1)
    return int(round(T_OF["2.39"] * (1 - ease(kind, u))))


def build_states():
    """List of (start, end_exclusive, spec); spec is ('const', ratio) or ('ramp', kind, ramp_start)."""
    st = [(0, LEAD_IN, ("const", "2.39"))]
    for k in range(N_CUTS):
        s = LEAD_IN + k * SEG
        st.append((s, s + SEG, ("const", CYCLE[k % 4])))
    for j, kind in enumerate(RAMP_KINDS):
        s = FRAMES_B0 + j * RAMP_BLOCK
        st.append((s, s + HOLD, ("const", "2.39")))
        st.append((s + HOLD, s + HOLD + RAMP, ("ramp", kind, s + HOLD)))
        st.append((s + HOLD + RAMP, s + RAMP_BLOCK, ("const", "1.78")))
    st.append((TOTAL - TAIL, TOTAL, ("const", "2.39")))
    return st


def t_expression(states):
    """Matte edge row as an ffmpeg expression of the overlay filter's `n`.

    The overlay filter's `n` runs one ahead of the frame index that drawtext's `n` shows (measured: the
    matte moved one frame early), so every frame index here is written as `m` = n - 1.
    """
    parts = []
    for start, end, spec in states[:-1]:
        parts.append((end, spec))
    last_spec = states[-1][2]

    def val(spec):
        if spec[0] == "const":
            return str(T_OF[spec[1]])
        _, kind, s0 = spec
        u = f"((n-1-{s0})/{RAMP - 1})"
        e = f"(pow({u},2)*(3-2*{u}))" if kind.startswith("eased") else u
        return f"round({T_OF['2.39']}*(1-{e}))"

    expr = val(last_spec)
    for end, spec in reversed(parts):
        expr = f"if(lt(n-1,{end}),{val(spec)},{expr})"
    return expr


def build_timeline(fps_label):
    cuts, prev = [], "2.39"
    for k in range(N_CUTS):
        new = CYCLE[k % 4]
        cuts.append({"frame": LEAD_IN + k * SEG, "from": prev, "to": new,
                     "zoom_from": zoom_for_height(H_OF[prev]), "zoom_to": zoom_for_height(H_OF[new])})
        prev = new
    ramps = []
    for j, kind in enumerate(RAMP_KINDS):
        s = FRAMES_B0 + j * RAMP_BLOCK
        cuts.append({"frame": s, "from": prev, "to": "2.39",
                     "zoom_from": zoom_for_height(H_OF[prev]), "zoom_to": zoom_for_height(H_OF["2.39"])})
        zooms = [zoom_for_height(H - 2 * ramp_T(kind, i)) for i in range(RAMP)]
        ramps.append({"kind": kind, "start_frame": s + HOLD, "end_frame": s + HOLD + RAMP - 1, "zoom": zooms})
        prev = "1.78"
    cuts.append({"frame": TOTAL - TAIL, "from": prev, "to": "2.39",
                 "zoom_from": zoom_for_height(H_OF[prev]), "zoom_to": zoom_for_height(H_OF["2.39"])})
    return {"schema": 1, "fps": fps_label, "frames": TOTAL, "band_height": BAND_H,
            "matte_edge_rows": T_OF, "cuts": cuts, "ramps": ramps}


def run(args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def make_overlays(tmp):
    """Matte overlays: black matte, 4 px white border, 16 px of tick marks on the picture side."""
    # opaque strip (picture grey, white ticks every 24 px): drawgrid on a transparent strip leaves no ticks
    tick = (f"color=c=0x505050:s={W}x16,"
            f"drawgrid=width=24:height=1000:thickness=3:color=white@1:y=-500")
    top = (f"[0:v]format=rgba[m];[1:v]format=rgba[b];[2:v]format=rgba[t];[m][b][t]vstack=inputs=3")
    bot = (f"[0:v]format=rgba[t];[1:v]format=rgba[b];[2:v]format=rgba[m];[t][b][m]vstack=inputs=3")
    inputs_top = ["-f", "lavfi", "-i", f"color=c=black:s={W}x1080", "-f", "lavfi", "-i", f"color=c=white:s={W}x4",
                  "-f", "lavfi", "-i", tick]
    inputs_bot = ["-f", "lavfi", "-i", tick, "-f", "lavfi", "-i", f"color=c=white:s={W}x4",
                  "-f", "lavfi", "-i", f"color=c=black:s={W}x1080"]
    run([*inputs_top, "-filter_complex", top, "-frames:v", "1", str(tmp / "matte_top.png")])
    run([*inputs_bot, "-filter_complex", bot, "-frames:v", "1", str(tmp / "matte_bottom.png")])


def build_filter(states, fps):
    texpr = t_expression(states)
    chains = []
    # picture: mid-grey with a horizontal line every 40 rows
    chains.append(f"[0:v]drawgrid=width=4000:height=40:thickness=2:color=0x909090,format=rgb24[pic]")
    # near-black picture during the third ramp block
    nb0 = FRAMES_B0 + 2 * RAMP_BLOCK
    chains.append(f"color=c=0x0b0b0b:s={W}x{H}:r={fps},format=rgb24[dark]")
    chains.append(f"[pic][dark]overlay=eval=init:enable='between(n,{nb0},{nb0 + RAMP_BLOCK - 1})'[pic2]")
    chains.append(f"[pic2][1:v]overlay=x=0:y='({texpr})-1080':eval=frame:format=rgb:repeatlast=1[m1]")
    chains.append(f"[m1][2:v]overlay=x=0:y='{H}-({texpr})-20':eval=frame:format=rgb:repeatlast=1[m2]")
    cur = "m2"
    # ratio labels (current state) and the frame counter plate
    lab = []
    for start, end, spec in states:
        text = spec[1] if spec[0] == "const" else {"eased": "ramp eased", "linear": "ramp linear",
                                                    "eased_nearblack": "ramp near-black"}[spec[1]]
        lab.append(f"drawtext=fontfile='{cal.FONT}':text='{text}':x=(w-text_w)/2:y=1040:fontsize=64"
                   f":fontcolor=yellow:enable='between(n,{start},{end - 1})'")
    chains.append(f"[{cur}]" + ",".join(lab) + "[m3]")
    plate = [f"drawbox=x=1400:y=1120:w=1040:h=330:color=black:t=fill",
             f"drawtext=fontfile='{cal.FONT}':text='F %{{eif\\:n\\:d\\:5}}':x=(w-text_w)/2:y=1130:fontsize=150"
             f":fontcolor=white"]
    bit_w, gap = 56, 8
    x0 = (W - (CODE_BITS * (bit_w + gap) - gap)) // 2
    y0 = 1340
    plate.append(f"drawbox=x={x0 - 40}:y={y0}:w=30:h={bit_w}:color=cyan:t=fill")
    plate.append(f"drawbox=x={x0 + CODE_BITS * (bit_w + gap) + 10}:y={y0}:w=30:h={bit_w}:color=cyan:t=fill")
    for b in range(CODE_BITS):
        x = x0 + b * (bit_w + gap)
        k = CODE_BITS - 1 - b
        plate.append(f"drawbox=x={x}:y={y0}:w={bit_w}:h={bit_w}:color=0x303030:t=fill")
        plate.append(f"drawbox=x={x}:y={y0}:w={bit_w}:h={bit_w}:color=white:t=fill"
                     f":enable='gte(mod(floor(n/pow(2,{k})),2),1)'")
    chains.append("[m3]" + ",".join(plate) + "[m4]")
    return chains


def main():
    variant = (sys.argv[1] if len(sys.argv) > 1 else "HDR10").upper()
    fps_arg = sys.argv[2] if len(sys.argv) > 2 else "23.976"
    fps = "24000/1001" if fps_arg == "23.976" else "24/1"
    out_dir = Path(__file__).resolve().parent / "out"
    tmp = out_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    make_overlays(tmp)
    states = build_states()
    chains = build_filter(states, fps)
    chains.append(f"[m4]{cal.CONVERT[variant]}[out]")
    script = tmp / f"boundary_{variant.lower()}.txt"
    script.write_text(";\n".join(chains), encoding="utf-8")

    enc = list(cal.ENCODE[variant])
    stem = f"boundary_{variant.lower()}_{fps_arg.replace('.', '')}"
    out = out_dir / f"{stem}.mkv"
    cmd = ["-f", "lavfi", "-i", f"color=c=0x505050:s={W}x{H}:r={fps}",
           "-loop", "1", "-framerate", fps, "-i", str(tmp / "matte_top.png"),
           "-loop", "1", "-framerate", fps, "-i", str(tmp / "matte_bottom.png"),
           "-/filter_complex", str(script), "-map", "[out]", "-frames:v", str(TOTAL), "-r", fps, *enc, str(out)]
    run(cmd)
    (out_dir / f"{stem}.timeline.json").write_text(json.dumps(build_timeline(fps_arg), indent=1), encoding="utf-8")
    print("built", out)


if __name__ == "__main__":
    main()
