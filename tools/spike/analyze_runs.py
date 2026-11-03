"""Summarise zoom-spike runs from the app's CSV files (see run_spike.py).

usage: python analyze_runs.py <timeline json> <run csv> [<run csv> ...]
Per run it prints: frames rendered of the clip's frames, rendered-time gaps over 1.5 frame periods (a skipped or
repeated frame; at 24 fps on a 60 Hz display the normal gaps are 33 and 50 ms, so 1.5 periods is 62.5 ms), how late
frames were released and rendered against their due time, and for the zoom changes: how many were applied, how long
each call took, and the apply time against the due time of the frame it was for.
"""

import csv
import json
import statistics as st
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))] if values else float("nan")


def analyse(timeline, rows, name):
    fps = timeline["fps"]
    period = 1e9 / fps
    frames = timeline["frames"]
    rects = timeline["rects"]
    rendered = {}
    for r in rows:
        if r["type"] == "rendered":
            rendered[round(int(r["key"]) * fps / 1e6)] = int(r["t_ns"])
    due = {}
    for r in rows:
        if r["type"] == "release":
            due[int(r["key"])] = int(r["extra"].split("=")[1])
    order = sorted(rendered)
    gaps = [(rendered[b] - rendered[a]) / 1e6 for a, b in zip(order, order[1:])]
    long_gaps = [g for g in gaps if g > 1.5 * period / 1e6]
    late = [(rendered[n] - due[n]) / 1e6 for n in order if n in due]
    print(f"== {name}")
    print(f"frames rendered {len(rendered)} of {frames}; missing {frames - len(rendered)}; "
          f"gaps > {1.5 * period / 1e6:.1f} ms: {len(long_gaps)} (largest {max(gaps) if gaps else 0:.1f} ms)")
    if late:
        print(f"rendered minus due (ms): median {st.median(late):.1f}, 95th {pct(late, 0.95):.1f}, max {max(late):.1f}")
    zooms = [r for r in rows if r["type"] == "zoom"]
    changes = [n for n in range(1, frames) if rects[n] != rects[n - 1]]
    if zooms:
        calls = [int(r["extra"].split("call_us=")[1]) / 1000 for r in zooms]
        offs = []
        for r in zooms:
            n = int(r["key"])
            if n in due:
                offs.append((int(r["t_ns"]) - due[n]) / 1e6)
        print(f"zoom changes applied {len(zooms)} of {len(changes) + 1} in the timeline; call time ms: median "
              f"{st.median(calls):.2f}, max {max(calls):.2f}")
        if offs:
            print(f"apply time minus the frame's due time (ms): median {st.median(offs):.1f}, min {min(offs):.1f}, max {max(offs):.1f}")
        # how many long gaps sit next to a zoom change (within 2 frame periods after it)
        times = sorted(int(r["t_ns"]) for r in zooms)
        near = 0
        for a, b in zip(order, order[1:]):
            if (rendered[b] - rendered[a]) / 1e6 > 1.5 * period / 1e6:
                if any(0 <= rendered[b] - t <= 2.5 * period for t in times):
                    near += 1
        print(f"long gaps within 2.5 frames after a zoom change: {near} of {len(long_gaps)}")
    for r in rows:
        if r["type"] in ("info", "error"):
            print(f"{r['type']}: {r['extra']}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    tl = json.load(open(sys.argv[1], encoding="utf-8"))
    for p in sys.argv[2:]:
        analyse(tl, load(p), p)
