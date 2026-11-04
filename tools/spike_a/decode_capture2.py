"""Decode a slow-motion capture of the boundary test video, any zoom level, one camera frame at a time.

Reads the phone video through ffmpeg (a crop around the code plate), and for every camera frame measures
  width   the width in camera pixels of the dark code plate on one row (proportional to the displayed zoom)
  code    the 14-bit content frame number, with the bit positions scaled by the measured zoom so the code stays
          readable while the picture is shrunk (decode_capture.py only reads it at zoom 1.0); None when unreadable
Output: an .npz with arrays `width` (nan when not measured), `code` (-1 when unreadable) and `contrast`.

The geometry constants describe one capture (IMG_8384: 1080p, tilted about 5.5 degrees, plate centre near
x=846, y=685 at zoom 1.0). Change them for another set-up (look at one frame with the picture at zoom 1.0).

Usage: python decode_capture2.py <phone video> <out.npz> [ffmpeg path]
"""
import subprocess
import sys

import numpy as np

# crop window in camera pixels (x, y, w, h)
#   IMG_8384: CX, CY = 540, 580;  MARK_L (708, 697);  MARK_R (984, 720);  CENTRE (846.5, 685);  ROW_Y 698
#   IMG_8388 (below): the camera sat about 44 px further left and 5 px higher
CX, CY, CW, CH = 494, 575, 620, 220
# at zoom 1.0 (camera pixels, full frame): the two cyan end markers of the code row, the plate centre
MARK_L = (663.0, 691.0)
MARK_R = (941.0, 715.0)
CENTRE = (802.5, 679.5)
ROW_Y = 693                      # row band (full frame) used for the plate edges; the code cells stay inside the plate
PLATE_W1 = 307.0                 # plate width at zoom 1.0 (camera pixels)
CELL = 5                         # half-size of the sampling window around a bit centre, scaled with the zoom


def bit_centres(z):
    """Camera-frame (crop coordinates) centres of the 14 code cells for zoom z (about the plate centre)."""
    pts = []
    for b in range(14):
        f = (b + 1) / 15.0
        x = MARK_L[0] + (MARK_R[0] - MARK_L[0]) * f
        y = MARK_L[1] + (MARK_R[1] - MARK_L[1]) * f
        pts.append((CENTRE[0] + (x - CENTRE[0]) * z - CX, CENTRE[1] + (y - CENTRE[1]) * z - CY))
    return pts


def edges(frame):
    """Plate left/right edge columns (crop coordinates) from the mean of a few rows, or None."""
    y0 = ROW_Y - CY
    prof = frame[y0 - 6:y0 + 7].mean(axis=0)
    bright = 0.5 * (prof[10:70].mean() + prof[CW - 70:CW - 10].mean())
    cx = int(CENTRE[0] - CX)
    dark = float(np.percentile(prof[cx - 90:cx + 90], 8))  # inside the plate at every zoom used
    thr = 0.5 * (bright + dark)
    if bright - dark < 40:
        return None
    left = right = None
    for x in range(int(CENTRE[0] - CX) - 170, int(CENTRE[0] - CX)):
        if prof[x] < thr <= prof[x - 1]:
            left = x - 1 + (prof[x - 1] - thr) / max(prof[x - 1] - prof[x], 1e-6)
            break
    for x in range(int(CENTRE[0] - CX) + 170, int(CENTRE[0] - CX), -1):
        if prof[x] < thr <= prof[x + 1]:
            right = x + 1 - (prof[x + 1] - thr) / max(prof[x + 1] - prof[x], 1e-6)
            break
    if left is None or right is None:
        return None
    return left, right


def decode(frame, z):
    half = max(2, int(round(CELL * z)))
    vals = []
    for cx, cy in bit_centres(z):
        x, y = int(round(cx)), int(round(cy))
        if x - half < 0 or y - half < 0 or x + half >= CW or y + half >= CH:
            return -1, 0.0
        vals.append(frame[y - half:y + half + 1, x - half:x + half + 1].mean())
    vals = np.array(vals)
    contrast = float(vals.max() - vals.min())
    if contrast < 60:
        return -1, contrast
    thr = 0.5 * (vals.max() + vals.min())
    bits = vals > thr
    return int("".join("1" if b else "0" for b in bits), 2), contrast


def main():
    video, out = sys.argv[1], sys.argv[2]
    ffmpeg = sys.argv[3] if len(sys.argv) > 3 else "ffmpeg"
    cmd = [ffmpeg, "-v", "error", "-i", video, "-vf", f"crop={CW}:{CH}:{CX}:{CY},format=gray", "-f", "rawvideo", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)  # noqa: S603
    n = CW * CH
    widths, codes, contrasts = [], [], []
    i = 0
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        frame = np.frombuffer(buf, dtype=np.uint8).reshape(CH, CW).astype(np.float32)
        e = edges(frame)
        if e is None:
            widths.append(np.nan)
            codes.append(-1)
            contrasts.append(0.0)
        else:
            w = e[1] - e[0]
            c, k = decode(frame, w / PLATE_W1)
            widths.append(w)
            codes.append(c)
            contrasts.append(k)
        i += 1
        if i % 2000 == 0:
            print(f"{i} frames", flush=True)
    np.savez(out, width=np.array(widths), code=np.array(codes), contrast=np.array(contrasts))
    print(f"{i} frames written to {out}")


if __name__ == "__main__":
    main()
