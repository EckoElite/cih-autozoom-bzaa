"""For each cut of the boundary test: how many content frames after (+) or before (-) the new picture does the zoom change?

Inputs: the .npz from decode_capture2.py (per camera frame: plate width, content frame code) and the driver's plan
(`drive_*.plan.json`, one entry per command with the lead it was sent at). Only cuts that lie in the 240 fps part of
the clip are used (the end of an iPhone slow-motion clip is stored at 8 times speed: there the code advances at
a different rate per file frame, and such cuts are skipped).

Per cut (content frame f, old and new zoom):
  k_c  camera frame at which content frame f starts: a local robust line k = a + R*c through the decoded
       (camera frame, code) pairs within +-30 content frames of the cut, evaluated at c = f minus half a content frame
       (a code value c is shown from k_c(c) to k_c(c)+R, and the line passes through the middle of that stretch)
  k_z  camera frame at which the plate width crosses the midpoint of its levels before and after the cut
       (linear interpolation between camera frames)
  delta = (k_z - k_c) / R content frames; the lead it was sent at; latency = lead + delta * frame time
The result per cut and a summary by lead go to stdout; --json writes the rows.

Usage: python analyze_boundary2.py <decoded.npz> <plan.json> [--json out.json]
"""
import json
import statistics as st
import sys

import numpy as np

FRAME_S = 1001 / 24000.0
R0 = 240.0 * FRAME_S                    # camera frames per content frame, 10.01


def local_line(k, c, f, half=30):
    """Robust fit k = a + R*c around content frame f; returns (a, R, n) or None."""
    m = (c >= f - half) & (c <= f + half)
    kk, cc = k[m].astype(float), c[m].astype(float)
    if len(kk) < 20:
        return None
    for _ in range(4):
        A = np.vstack([np.ones_like(cc), cc]).T
        a, R = np.linalg.lstsq(A, kk, rcond=None)[0]
        res = kk - (a + R * cc)
        keep = np.abs(res) <= max(1.5, 2.5 * np.std(res))
        if keep.all() or keep.sum() < 20:
            break
        kk, cc = kk[keep], cc[keep]
    return a, R, len(kk)


def crossing(width, k0, k1, level):
    """First camera frame position in [k0, k1) where the width crosses `level` (interpolated), or None."""
    s = np.sign(width[k0:k1] - level)
    start = s[:5].mean()
    for i in range(1, len(s)):
        if s[i] != 0 and s[i] != np.sign(start) and not np.isnan(width[k0 + i]):
            a, b = width[k0 + i - 1], width[k0 + i]
            frac = 0.0 if b == a else (level - a) / (b - a)
            return k0 + i - 1 + min(max(frac, 0.0), 1.0)
    return None


def main():
    d = np.load(sys.argv[1])
    width, code = d["width"], d["code"]
    plan = json.load(open(sys.argv[2], encoding="utf-8"))
    cuts = [e for e in plan if e["kind"] == "cut"]
    idx = np.arange(len(code))
    good = code >= 0
    k, c = idx[good], code[good]
    rows = []
    for e in sorted(cuts, key=lambda e: e["frame"]):
        f = e["frame"]
        fit = local_line(k, c, f)
        if fit is None:
            rows.append({"frame": f, "lead": e["lead"], "skip": "no code"})
            continue
        a, R, n = fit
        if abs(R - R0) > 0.4:
            rows.append({"frame": f, "lead": e["lead"], "skip": f"R={R:.2f} (not the 240 fps part)"})
            continue
        k_c = a + R * (f - 0.5)
        lo, hi = int(k_c - 4 * R), int(k_c + 4 * R)
        before = np.nanmedian(width[int(k_c - 3 * R):int(k_c - 0.5 * R)])
        after = np.nanmedian(width[int(k_c + 3 * R):int(k_c + 5 * R)])
        if abs(after - before) < 15:
            rows.append({"frame": f, "lead": e["lead"], "skip": f"no width step ({before:.0f} -> {after:.0f})"})
            continue
        k_z = crossing(width, lo, hi, 0.5 * (before + after))
        if k_z is None:
            rows.append({"frame": f, "lead": e["lead"], "skip": "no crossing"})
            continue
        delta = (k_z - k_c) / R
        rows.append({"frame": f, "lead": e["lead"], "zoom_to": e.get("zoom"), "fit_pts": n, "R": round(R, 3),
                     "k_c": round(k_c, 2), "k_z": round(k_z, 2), "delta_frames": round(float(delta), 3),
                     "latency_s": round(float(e["lead"] + delta * FRAME_S), 4)})
    print("frame  lead   zoom->   R      k_c       k_z      delta(frames)  latency(s)")
    for r in rows:
        if "skip" in r:
            print(f"{r['frame']:5d}  {r['lead']:.2f}   skipped: {r['skip']}")
        else:
            print(f"{r['frame']:5d}  {r['lead']:.2f}  {r['zoom_to']:.4f}  {r['R']:.3f}  {r['k_c']:8.1f}  {r['k_z']:8.1f}  {r['delta_frames']:+7.2f}      {r['latency_s']:.3f}")
    ok = [r for r in rows if "skip" not in r and r["latency_s"] == r["latency_s"]]  # drops NaN rows
    if ok:
        lat = [r["latency_s"] for r in ok]
        dl = [r["delta_frames"] for r in ok]
        print(f"\n{len(ok)} cuts used of {len(rows)}; latency mean {st.mean(lat):.3f} s, median {st.median(lat):.3f} s, "
              f"sd {st.pstdev(lat):.3f} s ({st.pstdev(lat) / FRAME_S:.2f} frames), range {min(lat):.3f} to {max(lat):.3f}")
        print("by lead (mean latency, n):", {f"{L:.2f}": (round(st.mean([r['latency_s'] for r in ok if r['lead'] == L]), 3),
              sum(1 for r in ok if r['lead'] == L)) for L in sorted({r['lead'] for r in ok})})
        mean_lat = st.mean(lat)
        print(f"with the lead set to the mean latency ({mean_lat * 1000:.0f} ms) the zoom would land "
              f"exact on {sum(1 for r in ok if abs((r['latency_s'] - mean_lat) / FRAME_S) < 0.5)}, "
              f"within 1 frame on {sum(1 for r in ok if abs((r['latency_s'] - mean_lat) / FRAME_S) < 1.0)} of {len(ok)}")
    if "--json" in sys.argv:
        json.dump(rows, open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
