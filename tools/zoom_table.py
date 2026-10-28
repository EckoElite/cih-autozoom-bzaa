"""Regenerate the section 5.3 zoom table from config/geometry.json (never hard-code it).

For an active picture of display ratio a, matted inside the 16:9 output frame R (3840x2160), the player's
default fit shows it at (w0, h0) raster pixels. Contain-fit scale: s = min(bandW / w0, bandH / h0).
With scope_fill, content wider than the screen ratio uses max() instead.
Vertical shift (px) moves the active picture's centre onto the design band's centre.

Usage: python zoom_table.py [path/to/geometry.json]
"""
import json
import sys
from pathlib import Path

RATIOS = [("1.33 (4:3)", 4 / 3), ("1.78 (16:9)", 16 / 9), ("1.85", 1.85), ("1.90", 1.90),
          ("2.00", 2.00), ("2.20", 2.20), ("2.35", 2.35), ("2.39", 2.39), ("2.40", 2.40)]


def default_fit(a, out_w, out_h):
    """Displayed size (w0, h0) of an active picture of ratio a centred in the out_w x out_h frame."""
    if a >= out_w / out_h:
        return out_w, out_w / a
    return out_h * a, out_h


def zoom_for(a, geo, scope_fill=False):
    out_w, out_h = geo["output"]["width"], geo["output"]["height"]
    band = geo["design_band"]
    band_w, band_h = out_w, band["bottom_exclusive"] - band["top"]
    w0, h0 = default_fit(a, out_w, out_h)
    fit = min(band_w / w0, band_h / h0)
    fill = max(band_w / w0, band_h / h0)
    s = fill if (scope_fill and a > geo["screen_ratio_nominal"]) else fit
    shift_px = band["top"] + band_h / 2 - out_h / 2  # active picture is centred in the frame
    return {"s": s, "fit": fit, "fill": fill, "w": w0 * s, "h": h0 * s, "shift_px": shift_px}


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "config" / "geometry.json"
    geo = json.loads(path.read_text(encoding="utf-8"))
    sf = bool(geo.get("scope_fill", False))
    band = geo["design_band"]
    print(f"design band rows {band['top']} to {band['bottom_exclusive']} "
          f"(height {band['bottom_exclusive'] - band['top']}), scope_fill={sf}")
    print("| Active ratio | Scale s | Displayed active size (px) | Vertical shift (px) |")
    print("|---|---|---|---|")
    for label, a in RATIOS:
        z = zoom_for(a, geo, sf)
        extra = ""
        if a > geo["screen_ratio_nominal"]:
            extra = f" (fit {z['fit']:.4f} / fill {z['fill']:.4f})"
        print(f"| {label} | {z['s']:.4f}{extra} | {z['w']:.0f} x {z['h']:.0f} | {z['shift_px']:+.1f} |")


if __name__ == "__main__":
    main()
