"""Analyse a slow-motion capture of the boundary test: for each cut, when did the zoom change relative to the
content change?

Inputs
  decoded.json   from decode_capture.py: per camera frame, the decoded content frame number "code" (reliable only
                 while the zoom is 1.0, because the code plate moves when the picture is zoomed)
  plate.json     per camera frame, the left/right edge columns of the dark code plate on one row; its width is
                 proportional to the zoom (about W1 px at zoom 1.0)
  plan.json      the driver's event plan (leads), and the timeline json (zoom values), from tools/spike_a
For each cut:
  k_c  camera frame at which the new content first appears. Found by fitting camera frame against decoded content
       frame number on the zoom-1.0 side of the cut (the content advances at a steady rate in camera frames), then
       evaluating the fit at the cut frame.
  k_z  camera frame at which the plate width crosses the midpoint between the old and new zoom.
  delta_content_frames = (k_z - k_c) / R, negative when the zoom changed before the content.
  latency_s = lead + delta_s: the delay from the command (sent `lead` before the cut, by the player clock) to the
  picture change, measured against the displayed content.

Usage: python analyze_boundary.py decoded.json plate.json plan.json timeline.json out.json
"""
import json
import statistics
import sys
from pathlib import Path

FRAME_S = 1001 / 24000.0
W_SLOPE, W_OFF = 302.0, 2.5       # plate width ~ W_SLOPE * zoom + W_OFF (camera pixels), from the zoom-1.0 and 0.8081 / 0.7565 clusters


def width_of(zoom):
    return W_SLOPE * zoom + W_OFF


def robust_line(points, trim=0.2):
    """Least-squares line k = a + R * c with the worst `trim` fraction of residuals dropped (two passes)."""
    pts = list(points)
    for _ in range(3):
        n = len(pts)
        mc = sum(c for _, c in pts) / n
        mk = sum(k for k, _ in pts) / n
        sxx = sum((c - mc) ** 2 for _, c in pts)
        if sxx == 0:
            return None
        r = sum((c - mc) * (k - mk) for k, c in pts) / sxx
        a = mk - r * mc
        res = sorted(pts, key=lambda p: abs(p[0] - (a + r * p[1])))
        pts = res[: max(6, int(len(res) * (1 - trim)))]
    return a, r


def main():
    decoded = json.loads(Path(sys.argv[1]).read_text())
    plate = json.loads(Path(sys.argv[2]).read_text())
    plan = json.loads(Path(sys.argv[3]).read_text())
    timeline = json.loads(Path(sys.argv[4]).read_text())
    code, width = decoded["code"], plate["width"]
    n = decoded["frames"]
    w1 = width_of(1.0)
    # frames where the code is trustworthy: plate at full size (+/- 5 px) and a plausible number
    good = [k for k in range(n) if width[k] is not None and abs(width[k] - w1) <= 5 and 0 <= code[k] <= 2327]
    # global rate: camera frames per content frame, from a trimmed fit over the whole clip
    g = robust_line([(k, code[k]) for k in good if code[k] > 0], trim=0.3)
    R_global = g[1]
    print(f"global camera frames per content frame: {R_global:.3f}  (240 fps / 23.976 = {240 * 1001 / 24000:.3f})")
    lead_by_frame = {e["frame"]: e["lead"] for e in plan if e["kind"] == "cut"}

    def label(k, w_old, w_new):
        """'old', 'new' or None for one camera frame, by the plate width (tolerance 8 px, spikes ignored)."""
        w = width[k]
        if w is None:
            return None
        if abs(w - w_old) <= 8 and abs(w - w_old) <= abs(w - w_new):
            return "old"
        if abs(w - w_new) <= 8:
            return "new"
        return None

    results = []
    for cut in timeline["cuts"]:
        F = cut["frame"]
        # anchor on the decodable frames closest to the cut (the clip has stalls elsewhere, e.g. at video start)
        near = [(k, code[k]) for k in good if abs(code[k] - F) <= 20]
        if len(near) < 8:
            results.append({"frame": F, "from": cut["from"], "to": cut["to"],
                            "error": f"only {len(near)} decodable camera frames near the cut"})
            continue
        # offset of the line k = a + R_global * code, from the median over the nearby decodable frames
        a = statistics.median(k - R_global * c for k, c in near)
        k_c = a + R_global * F - R_global / 2.0    # first camera frame of content frame F
        w_old, w_new = width_of(cut["zoom_from"]), width_of(cut["zoom_to"])
        lo, hi = max(8, int(k_c) - 500), min(n - 40, int(k_c) + 500)
        lab = {k: label(k, w_old, w_new) for k in range(max(0, lo - 60), min(n, hi + 60))}
        k_z = None
        for k in range(lo, hi):
            if lab.get(k) != "new":
                continue
            prev = [lab.get(j) for j in range(k - 60, k) if lab.get(j) is not None]
            if not prev or prev[-1] != "old":
                continue
            after = [lab.get(j) for j in range(k, k + 60) if lab.get(j) is not None]
            if len(after) >= 5 and all(x == "new" for x in after[:12]):
                k_z = k
                break
        if k_z is None:
            results.append({"frame": F, "from": cut["from"], "to": cut["to"], "error": "zoom change not found"})
            continue
        # the change happened somewhere after the last 'old' frame and at/before k_z: use the midpoint
        last_old = max(j for j in range(k_z - 60, k_z) if lab.get(j) == "old")
        k_zf = (last_old + k_z) / 2.0
        delta_frames = (k_zf - k_c) / R_global
        uncertainty = (k_z - last_old) / 2.0 / R_global
        lead = lead_by_frame[F]
        results.append({
            "frame": F, "from": cut["from"], "to": cut["to"], "lead_s": lead,
            "k_content": k_c, "k_zoom": k_zf,
            "delta_content_frames": delta_frames, "delta_uncertainty_frames": uncertainty,
            "latency_s": lead + delta_frames * FRAME_S,
            "fit_points": len(near),
        })
    Path(sys.argv[5]).write_text(json.dumps(results, indent=1))
    ok = [r for r in results if "latency_s" in r]
    print(f"{len(ok)} of {len(results)} cuts measured")
    print(f"{'frame':>5} {'change':>10} {'lead s':>7} {'zoom - content (frames)':>24} {'latency s':>10}")
    for r in results:
        if "latency_s" in r:
            print(f"{r['frame']:5d} {r['from'] + '>' + r['to']:>10} {r['lead_s']:7.2f} "
                  f"{r['delta_content_frames']:+17.2f} +/-{r['delta_uncertainty_frames']:.2f} {r['latency_s']:10.3f}")
        else:
            print(f"{r['frame']:5d} {r.get('from', '')}>{r.get('to', '')}  {r.get('error')}")
    if len(ok) >= 3:
        lat = [r["latency_s"] for r in ok]
        print(f"\nlatency s: mean {statistics.mean(lat):.3f}  median {statistics.median(lat):.3f}  stdev {statistics.stdev(lat):.3f}"
              f"  min {min(lat):.3f}  max {max(lat):.3f}")
        # slope of latency against lead (0 means a constant latency, 1 means the zoom change is pinned to something else)
        xs = [r["lead_s"] for r in ok]
        mx, ml = statistics.mean(xs), statistics.mean(lat)
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - ml) for x, y in zip(xs, lat)) / sxx if sxx else float("nan")
        print(f"slope of latency vs lead: {slope:+.2f}")


if __name__ == "__main__":
    main()
