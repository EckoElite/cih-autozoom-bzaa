"""Shrink PM4K's browsing screens into the 2.35:1 band of the 16:9 screen.

PM4K lays every screen (home, movie pages, search, settings, menus) out over the whole 1920x1080 GUI, so the top and
bottom of each land above and below the screen (the home page's Settings button, for one). This adds one window-level
animation to each screen: a zoom to BAND_PERCENT around the centre, which fits the whole 16:9 layout into the band
(black bars at the sides, like 16:9 video). Nothing else in the files changes.

The zoom only applies while no video is playing (`!Player.HasVideo`). During playback PM4K's own overlay is laid out
by apply_osd_margin.py and the menus opened from it (audio / subtitle dropdowns) are positioned against that layout, so
they must stay unscaled. Menus opened while browsing are positioned against the browsing layout, so they scale with it.

usage: python apply_window_scale.py <skin folder> [--check]
  <skin folder> is script.plexmod/resources/skins/Main/1080i (a pulled copy; the script never touches the Shield).
  Patched files are written in place; each original is kept next to it as <name>.cih-orig (once).
  --check only reports which screens are patched.

Screens left alone: the playback overlay and its settings panel (apply_osd_margin.py), the invisible video window,
busy spinners and plain background / blank windows. A PM4K update, or PM4K re-rendering its templates, brings the
originals back, so re-apply after either.
"""

import sys
from pathlib import Path

BAND_TOP = 132  # 263 / 2, as in apply_osd_margin.py
BAND_BOTTOM = 948  # 1897 / 2
BAND_PERCENT = round((BAND_BOTTOM - BAND_TOP) / 1080 * 100, 2)  # 75.56

MARKER = "<!-- cih-window-scale -->"
ANIMATION = (
    f'    {MARKER}\n'
    f'    <animation effect="zoom" start="100" end="{BAND_PERCENT}" center="960,540" time="0" '
    'condition="!Player.HasVideo">Conditional</animation>\n'
)

SKIP = {
    "script-plex-seek_dialog.xml",  # playback overlay: apply_osd_margin.py
    "script-plex-video_settings_dialog.xml",  # its settings panel: apply_osd_margin.py
    "script-plex-video_player.xml",  # the invisible video window
    "script-plex-busy.xml",
    "script-plex-busy_msg.xml",
    "script-plex-background.xml",
    "script-plex-blank.xml",
    "script-plex-signin_background.xml",
    "script-plex-signin_blank.xml",
    "script-plex-slideshow.xml",
    "script-plex-photo.xml",
}

ANCHOR = "<controls>"


def targets(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("script-plex-*.xml") if p.name not in SKIP)


def patched_text(text: str) -> str:
    """text with the animation placed just before the window's <controls>; raises if that spot is not unique"""
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    at = [i for i, line in enumerate(lines) if line.strip() == ANCHOR]
    if len(at) != 1:
        raise ValueError(f"expected one line with only {ANCHOR}, found {len(at)}")
    lines.insert(at[0], ANIMATION.rstrip("\n"))
    out = "\n".join(lines)
    return out.replace("\n", "\r\n") if crlf else out


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder = Path(argv[0])
    check = "--check" in argv[1:]
    if not folder.is_dir():
        print(f"not a folder: {folder}")
        return 2
    bad = 0
    for path in targets(folder):
        text = path.read_bytes().decode("utf-8")
        if MARKER in text:
            print(f"already patched  {path.name}")
            continue
        if check:
            print(f"not patched      {path.name}")
            continue
        try:
            new = patched_text(text)
        except ValueError as err:
            print(f"SKIPPED          {path.name}: {err}")
            bad += 1
            continue
        backup = path.with_name(path.name + ".cih-orig")
        if not backup.exists():
            backup.write_bytes(text.encode("utf-8"))
        path.write_bytes(new.encode("utf-8"))
        print(f"patched          {path.name}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
