"""Decode a slow-motion capture of the boundary test video into per-camera-frame measurements.

Inputs (made with ffmpeg from the phone video; see docs/SPIKES.md for the exact command):
  plate.raw  gray frames of the code-plate region, PLATE_W x PLATE_H per camera frame
  row.raw    gray frames of one averaged picture row, ROW_W bytes per camera frame
Outputs a JSON file with, per camera frame: the decoded content frame number (or null), the picture's left and
right edge columns (or null), and the mean level of the picture row.

The geometry constants describe one capture (camera 1920x1080, tilted about 3.5 degrees, screen centred);
change them for a different set-up. Bit b (0 = MSB) of the 14-bit code has its centre at
x = X0 + PITCH * b, y = Y0 + SLOPE * (x - X_REF), in plate coordinates.

Usage: python decode_capture.py <plate.raw> <row.raw> <out.json> [X0 PITCH Y0 SLOPE]
"""
import json
import sys
from pathlib import Path

PLATE_W, PLATE_H = 340, 60
ROW_W = 1920
CODE_BITS = 14
X0, PITCH = 45.0, 19.3          # centre of bit 0 and bit spacing, plate coordinates
Y0, SLOPE, X_REF = 18.8, 0.057, 45.0
HALF_X, HALF_Y = 4, 3           # sampling window half-size around each bit centre
SCAN_LO, SCAN_HI = 185, 1375    # columns of the camera row that lie inside the screen (excludes the lit wall)


def window_mean(buf, off, cx, cy):
    s = n = 0
    for y in range(int(cy) - HALF_Y, int(cy) + HALF_Y + 1):
        if 0 <= y < PLATE_H:
            base = off + y * PLATE_W
            for x in range(int(cx) - HALF_X, int(cx) + HALF_X + 1):
                if 0 <= x < PLATE_W:
                    s += buf[base + x]
                    n += 1
    return s / n


def bit_centres():
    out = []
    for b in range(CODE_BITS):
        x = X0 + PITCH * b
        out.append((x, Y0 + SLOPE * (x - X_REF) + 0.0))
    return out


def main():
    global X0, PITCH, Y0, SLOPE, X_REF
    if len(sys.argv) > 7:   # optional geometry for a different framing: X0 PITCH Y0 SLOPE
        X0, PITCH, Y0, SLOPE = (float(v) for v in sys.argv[4:8])
        X_REF = X0
    plate = Path(sys.argv[1]).read_bytes()
    row = Path(sys.argv[2]).read_bytes()
    n = len(plate) // (PLATE_W * PLATE_H)
    assert len(row) // ROW_W == n, (len(row) // ROW_W, n)
    centres = bit_centres()
    samples = []
    for k in range(n):
        off = k * PLATE_W * PLATE_H
        samples.append([window_mean(plate, off, cx, cy) for cx, cy in centres])
    flat = sorted(v for s in samples for v in s)
    # two-mode threshold: Otsu on the pooled bit samples
    hist = [0] * 256
    for v in flat:
        hist[min(255, int(v))] += 1
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    best, thr, w0, s0 = -1.0, 128, 0, 0
    for t in range(256):
        w0 += hist[t]
        if w0 == 0 or w0 == total:
            continue
        s0 += t * hist[t]
        m0, m1 = s0 / w0, (sum_all - s0) / (total - w0)
        var = w0 * (total - w0) * (m0 - m1) ** 2
        if var > best:
            best, thr = var, t
    print(f"{n} camera frames; bit threshold {thr}")
    codes, margins = [], []
    for s in samples:
        code = 0
        marg = 255.0
        for v in s:
            code = (code << 1) | (1 if v > thr else 0)
            marg = min(marg, abs(v - thr))
        codes.append(code)
        margins.append(marg)

    rows = []
    for k in range(n):
        r = row[k * ROW_W:(k + 1) * ROW_W]
        rows.append(r)
    out = {"frames": n, "threshold": thr, "code": codes, "margin": margins}
    out["row_mean_centre"] = [sum(r[800:1000]) / 200.0 for r in rows]
    # picture edges: first/last column (within the screen span) brighter than half the centre level
    left, right = [], []
    for r, c in zip(rows, out["row_mean_centre"]):
        t = c * 0.5
        lo, hi = None, None
        for x in range(SCAN_LO, SCAN_HI):
            if sum(r[x:x + 5]) / 5.0 > t:
                lo = x
                break
        for x in range(SCAN_HI, SCAN_LO, -1):
            if sum(r[x - 5:x]) / 5.0 > t:
                hi = x
                break
        left.append(lo)
        right.append(hi)
    out["left"], out["right"] = left, right
    Path(sys.argv[3]).write_text(json.dumps(out), encoding="utf-8")
    print("wrote", sys.argv[3])


if __name__ == "__main__":
    main()
