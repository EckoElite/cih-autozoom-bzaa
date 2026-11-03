"""Read `dumpsys SurfaceFlinger --latency` snapshots taken by run_spike.py and report which frames reached the screen.

usage: python analyze_sf.py <sf_*.txt> [<sf_*.txt> ...]
Each file holds the display refresh period on its first line, then up to about 127 recent buffers of the video layer
as three columns: the time the app asked for the frame to be shown, the time it was actually shown, and the time the
buffer was ready. A buffer that was queued but never shown (replaced by a newer one) has the largest 64-bit number as
its shown time. At 24 fps on a 59.94 Hz display the shown-to-shown gaps are 2 or 3 refreshes (3:2 pulldown); a gap of
4 or more refreshes is a skipped frame, and a buffer that was never shown is a dropped one.
"""

import sys
from collections import Counter

NEVER = 9223372036854775807


def analyse(path: str) -> None:
    lines = [ln.split() for ln in open(path, encoding="utf-8").read().splitlines() if ln.strip()]
    if len(lines) < 3:
        print(f"{path}: no frames in the dump")
        return
    period = int(lines[0][0])
    rows = [(int(ln[0]), int(ln[1]), int(ln[2])) for ln in lines[1:] if len(ln) >= 3]
    rows = [r for r in rows if r[0] > 0]
    never = sum(1 for r in rows if r[1] >= NEVER)
    shown = [r for r in rows if 0 < r[1] < NEVER]
    gaps = Counter(round((b[1] - a[1]) / period) for a, b in zip(shown, shown[1:]))
    late = [(r[1] - r[0]) / 1e6 for r in shown]
    skipped = sum(n for g, n in gaps.items() if g >= 4)
    print(f"{path.split('/')[-1].split(chr(92))[-1]}: {len(rows)} buffers, never shown {never}, refresh {1e9 / period:.2f} Hz; "
          f"gaps in refreshes {sorted(gaps.items())}; gaps >= 4 refreshes: {skipped}; "
          f"shown minus requested (ms) median {sorted(late)[len(late) // 2]:.1f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    for p in sys.argv[1:]:
        analyse(p)
