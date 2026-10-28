"""Ramp smoothness from the slow-motion capture: how does the displayed zoom follow the per-frame commands?

For each ramp block the displayed zoom is read from the code-plate width (see analyze_boundary.py), the content
frame of each camera frame is anchored on the decodable zoom-1.0 hold before the ramp, and for every ramp frame the
"lag" is computed: the number of content frames between the frame whose commanded zoom equals the displayed zoom
and the frame actually on screen. A constant lag means the picture follows the commanded curve smoothly.

Usage: python analyze_ramps.py decoded.json plate.json timeline.json
"""
import json
import statistics
import sys
from pathlib import Path

W_SLOPE, W_OFF = 302.0, 2.5
BLOCK0, BLOCK, HOLD, RAMP = 1776, 168, 48, 72


def main():
    decoded = json.loads(Path(sys.argv[1]).read_text())
    plate = json.loads(Path(sys.argv[2]).read_text())
    timeline = json.loads(Path(sys.argv[3]).read_text())
    code, width, n = decoded["code"], plate["width"], decoded["frames"]
    w1 = W_SLOPE + W_OFF
    R = 10.008
    good = [k for k in range(n) if width[k] is not None and abs(width[k] - w1) <= 5 and 0 <= code[k] <= 2327]
    for j, ramp in enumerate(timeline["ramps"]):
        S = BLOCK0 + j * BLOCK
        f0 = ramp["start_frame"]
        z = ramp["zoom"]
        near = [(k, code[k]) for k in good if S + 3 <= code[k] <= S + HOLD - 3]
        if len(near) < 30:
            print(f"ramp {ramp['kind']}: only {len(near)} decodable frames in the hold before it; skipped")
            continue
        a = statistics.median(k - R * c for k, c in near)
        # displayed zoom per content frame (median over its camera frames, spikes rejected)
        per = {}
        for k in range(max(0, int(a + R * (f0 - 8))), min(n, int(a + R * (f0 + RAMP + 16)))):
            c = round((k - a) / R)
            w = width[k]
            if w is None or w < 200 or w > w1 + 6:
                continue
            per.setdefault(c, []).append((w - W_OFF) / W_SLOPE)
        disp = {c: statistics.median(v) for c, v in per.items() if len(v) >= 3}

        def cmd(f):
            if f < f0:
                return 1.0
            if f >= f0 + RAMP:
                return z[-1]
            return z[f - f0]

        # best integer-or-half-frame shift d: displayed(c) ~ commanded(c - d)
        best = None
        for d10 in range(0, 200):
            d = d10 / 10.0
            err = 0.0
            m = 0
            for c, s in disp.items():
                f = c - d
                lo, hi = int(f // 1), int(f // 1) + 1
                t = f - lo
                zc = cmd(lo) * (1 - t) + cmd(hi) * t
                err += (s - zc) ** 2
                m += 1
            rms = (err / m) ** 0.5
            if best is None or rms < best[0]:
                best = (rms, d)
        rms, d = best
        # per-frame lag by inverting the commanded curve (ramp interior only)
        lags = []
        for c in sorted(disp):
            s = disp[c]
            if not (z[-1] + 0.01 < s < 1.0 - 0.01):
                continue
            f_star = min(range(f0, f0 + RAMP), key=lambda f: abs(z[f - f0] - s))
            lags.append((c, c - f_star))
        seq = [disp[c] for c in sorted(disp) if f0 - 2 <= c <= f0 + RAMP + 12]
        steps = [b - a_ for a_, b in zip(seq, seq[1:])]
        reversals = sum(1 for x in steps if x > 0.004)
        stalls = sum(1 for x in steps[8:-8] if abs(x) < 0.0008)
        lv = [l for _, l in lags]
        print(f"\nramp {ramp['kind']} (frames {f0}..{f0 + RAMP - 1}): content frames measured {len(disp)}")
        print(f"  best shift {d:.1f} content frames (RMS error {rms:.4f} in zoom)")
        if lv:
            print(f"  per-frame lag: mean {statistics.mean(lv):.2f}  stdev {statistics.stdev(lv):.2f}  min {min(lv):.1f}  max {max(lv):.1f}"
                  f"  (n={len(lv)})")
            runs = ", ".join(f"{c}:{l:+.0f}" for c, l in lags[::6])
            print(f"  lag by content frame (every 6th): {runs}")
        print(f"  upward steps in displayed zoom (reversals > 0.004): {reversals}; near-zero steps inside the ramp (stalls): {stalls}")
        if "--steps" in sys.argv:
            print("  content  commanded z (frame-lag-shifted)  displayed z   step_cmd   step_disp")
            prev_c = prev_d = None
            for c in sorted(disp):
                if not (f0 - 2 <= c <= f0 + RAMP + 10):
                    continue
                zc = cmd(int(round(c - d)))
                sd = "" if prev_d is None else f"{disp[c] - prev_d:+.4f}"
                sc = "" if prev_c is None else f"{zc - prev_c:+.4f}"
                print(f"  {c:7d}  {zc:10.4f}  {disp[c]:12.4f}  {sc:>9}  {sd:>9}")
                prev_c, prev_d = zc, disp[c]


if __name__ == "__main__":
    main()
