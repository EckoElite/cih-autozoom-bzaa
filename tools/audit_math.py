"""Independent re-derivation of the section 5 zoom math for Audit 1 (docs/AUDIT_1.md).

Deliberately does not reuse tools/zoom_table.py. Two different routes to the same number, both starting from
config/geometry.json only:

  1. Target-rectangle route: the largest rectangle of the active ratio that fits the design band is
     width_t = min(bandW, bandH * a); the player already draws the active picture w0 pixels wide, so s = width_t / w0.
  2. Brute-force route: step the scale up in 0.0001 increments, place the scaled active rectangle about the frame
     centre, and keep the largest scale whose rectangle lies inside the band rows and the frame width.

It then checks (a) the two routes agree, (b) the fitted rectangle lies inside the *measured* band even at the
worst ends of its tolerances (so no light can spill past the screen), (c) Kodi's accepted zoom range, and
(d) the native-ratio-container and non-square-pixel cases.

Usage: python tools/audit_math.py [path/to/geometry.json]
"""
import json
import sys
from pathlib import Path

KODI_ZOOM_MIN, KODI_ZOOM_MAX = 0.5, 2.0


def drawn_width(a, frame_w, frame_h):
    """Width in output pixels at which Kodi's Normal mode draws an active picture of display ratio a that sits
    in a frame_w x frame_h (16:9) container: full width if it is at least as wide as the frame, else full height."""
    return frame_w if a >= frame_w / frame_h else frame_h * a


def route_target(a, geo):
    fw, fh = geo["output"]["width"], geo["output"]["height"]
    band = geo["design_band"]
    band_h = band["bottom_exclusive"] - band["top"]
    width_t = min(fw, band_h * a)
    return width_t / drawn_width(a, fw, fh)


def route_brute(a, geo):
    fw, fh = geo["output"]["width"], geo["output"]["height"]
    band = geo["design_band"]
    w0 = drawn_width(a, fw, fh)
    h0 = w0 / a
    centre = fh / 2.0
    best = 0.0
    for i in range(1, 30001):
        s = i / 10000.0
        top, bottom = centre - h0 * s / 2.0, centre + h0 * s / 2.0
        if top >= band["top"] - 1e-9 and bottom <= band["bottom_exclusive"] + 1e-9 and w0 * s <= fw + 1e-9:
            best = s
    return best


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "config" / "geometry.json"
    geo = json.loads(path.read_text(encoding="utf-8"))
    fw, fh = geo["output"]["width"], geo["output"]["height"]
    band, meas = geo["design_band"], geo["measured"]
    band_h = band["bottom_exclusive"] - band["top"]
    safe_top = meas["band_top"] + meas["band_top_tol"]
    safe_bottom = meas["band_bottom_exclusive"] - meas["band_bottom_tol"]
    print(f"design band rows {band['top']}..{band['bottom_exclusive']} (h {band_h}); "
          f"worst-case measured band {safe_top}..{safe_bottom}")
    print(f"{'ratio':>6} {'s target':>9} {'s brute':>8} {'agree':>6} {'rows used':>13} {'inside worst band':>18} "
          f"{'zoom in Kodi range':>19}")
    ok_all = True
    for a in (4 / 3, 16 / 9, 1.85, 1.90, 2.00, 2.20, 2.35, 2.39, 2.40):
        s1, s2 = route_target(a, geo), route_brute(a, geo)
        agree = abs(s1 - s2) <= 0.00011
        h = drawn_width(a, fw, fh) / a * s1
        top, bottom = fh / 2 - h / 2, fh / 2 + h / 2
        inside = top >= safe_top - 1e-9 and bottom <= safe_bottom + 1e-9
        in_range = KODI_ZOOM_MIN <= s1 <= KODI_ZOOM_MAX
        ok_all &= agree and inside and in_range
        print(f"{a:6.3f} {s1:9.4f} {s2:8.4f} {str(agree):>6} {top:6.1f}-{bottom:6.1f} {str(inside):>18} {str(in_range):>19}")
    # scope_fill for content wider than the screen: fill the band height, crop the sides
    for a in (2.39, 2.40):
        w0 = drawn_width(a, fw, fh)
        s_fill = band_h / (w0 / a)
        crop_each_side = (w0 * s_fill - fw) / 2
        print(f"scope_fill {a:.2f}: s = {s_fill:.4f}, crops {crop_each_side:.1f} px per side "
              f"({100 * crop_each_side / (w0 * s_fill):.2f}% of the picture width)")
    # native-ratio container (e.g. 3840x1600 file): Kodi's Normal mode draws it full width, 1600 rows tall
    w0, h0 = fw, fw / (3840 / 1600)
    print(f"3840x1600 container: fit s = {min(fw / w0, band_h / h0):.4f} (rows {band['top'] + (band_h - h0) / 2:.0f}.."
          f"{band['top'] + (band_h + h0) / 2:.0f}), fill s = {max(fw / w0, band_h / h0):.4f}")
    # non-square pixels: 720x480 with 32:27 pixels is 16:9 on display; Kodi draws by display ratio, so it equals a=16/9
    dar = 720 * 32 / (480 * 27)
    print(f"720x480 SAR 32:27: DAR {dar:.4f}, zoom {route_target(dar, geo):.4f} (same as 16:9)")
    # vertical shift needed for a matte that is not centred in the frame: rows between the picture centre and 1080
    print("vertical shift for an off-centre active picture = (picture centre row - band centre row) rows; "
          "Kodi's verticalshift units are NOT yet measured, so no zoom/shift pair can be stated for that case")
    print("ALL CHECKS PASS" if ok_all else "CHECK FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
