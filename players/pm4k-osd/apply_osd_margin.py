"""Move PM4K's on-screen playback controls into the 2.35:1 band of the 16:9 screen.

PM4K lays its playback overlay (title bar, progress bar, time labels, skip button, settings dialog) out over the whole
1920x1080 GUI. On a 2.35:1 screen the top and bottom of that overlay land above and below the screen. This rewrites
the hard-coded positions in two PM4K skin files so everything sits between BAND_TOP and BAND_BOTTOM (the same rows as
config/geometry.json, in the skin's 1080-high coordinates: 263 / 2 and 1897 / 2).

usage: python apply_osd_margin.py <skin folder> [--check]
  <skin folder> is script.plexmod/resources/skins/Main/1080i (a pulled copy; the script never touches the Shield).
  Writes the patched files in place and keeps each original next to it as <name>.cih-orig (once).
  --check only reports whether the files are already patched.

A PM4K update brings its own copies back, so re-apply after updating. Every replacement must match exactly once; if
PM4K changes these files the script stops and says which edit no longer fits.
"""

import sys
from pathlib import Path

BAND_TOP = 132  # 263 / 2 = 131.5, rounded in
BAND_BOTTOM = 948  # 1897 / 2 = 948.5, rounded in
SHIFT = 1080 - BAND_BOTTOM  # 132: how far the bottom-anchored parts move up

# (file, old text, new text); old texts are copied from PM4K 1.0.6 and must occur exactly once
EDITS = [
    ("script-plex-seek_dialog.xml",
     "<posy>0</posy>\n            <width>1920</width>\n            <height>140</height>",
     f"<posy>{BAND_TOP}</posy>\n            <width>1920</width>\n            <height>140</height>"),  # top bar
    ("script-plex-seek_dialog.xml",
     "<posy>140r</posy>\n            <width>1920</width>\n            <height>140</height>",
     f"<posy>{BAND_BOTTOM - 140}</posy>\n            <width>1920</width>\n            <height>140</height>"),  # bottom bar
    ("script-plex-seek_dialog.xml",
     "<posx>0</posx>\n        <posy>40</posy>",
     f"<posx>0</posx>\n        <posy>{40 + BAND_TOP}</posy>"),  # title and clock
    ("script-plex-seek_dialog.xml",
     "<posx>0</posx>\n        <posy>115r</posy>",
     f"<posx>0</posx>\n        <posy>{BAND_BOTTOM - 115}</posy>"),  # elapsed / remaining time
    ("script-plex-seek_dialog.xml",
     "<posx>0</posx>\n        <posy>140r</posy>\n        <control type=\"image\">",
     f"<posx>0</posx>\n        <posy>{BAND_BOTTOM - 140}</posy>\n        <control type=\"image\">"),  # progress track
    ("script-plex-seek_dialog.xml",
     "<control type=\"group\" id=\"803\">\n    <bottom>0</bottom>",
     f"<control type=\"group\" id=\"803\">\n    <bottom>{SHIFT}</bottom>"),  # playback info panel
    ("script-plex-seek_dialog.xml",
     "<posx>0</posx>\n    <posy>752</posy>",
     f"<posx>0</posx>\n    <posy>{752 - SHIFT}</posy>"),  # seek thumbnail
    ("script-plex-seek_dialog.xml",
     '<hitrect x="460" y="998" w="1000" h="55" />',
     f'<hitrect x="460" y="{998 - SHIFT}" w="1000" h="55" />'),  # (mouse only)
    ("script-plex-seek_dialog.xml",
     "<posy>116r</posy>\n        <width>1200</width>",
     f"<posy>{BAND_BOTTOM - 116}</posy>\n        <width>1200</width>"),  # button row
    ("script-plex-seek_dialog.xml",
     "<posx>0</posx>\n        <posy>940</posy>",
     f"<posx>0</posx>\n        <posy>{940 - SHIFT}</posy>"),  # seek bar
    ("script-plex-seek_dialog.xml",
     "<posx>-8</posx>\n        <posy>917</posy>",
     f"<posx>-8</posx>\n        <posy>{917 - SHIFT}</posy>"),  # seek handle
    ("script-plex-seek_dialog.xml",
     "<posx>0</posx>\n    <posy>896</posy>",
     f"<posx>0</posx>\n    <posy>{896 - SHIFT}</posy>"),  # seek time bubble
    ("script-plex-seek_dialog.xml",
     "<right>30</right>\n    <top>797</top>",
     f"<right>30</right>\n    <top>{797 - SHIFT}</top>"),  # skip intro / credits button
    ("script-plex-video_settings_dialog.xml",
     '<posx>460</posx>\n    <posy>240.0</posy>',
     '<posx>460</posx>\n    <posy>195</posy>'),  # audio / subtitle / video settings panel (770 high: 155 to 925)
    ("script-plex-video_settings_dialog.xml",
     "<top>320.0</top>",
     "<top>275</top>"),  # its scroll bar
]


def patched(text: str, old: str, new: str) -> str:
    return text.replace(old, new, 1)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder = Path(argv[0])
    check = "--check" in argv[1:]
    texts: dict[str, str] = {}
    crlf: dict[str, bool] = {}
    for name in {e[0] for e in EDITS}:
        raw = (folder / name).read_bytes().decode("utf-8")
        crlf[name] = "\r\n" in raw
        texts[name] = raw.replace("\r\n", "\n")
    done = 0
    for name, old, new in EDITS:
        text = texts[name]
        if text.count(new) >= 1 and text.count(old) == 0:  # (two edits can share the same new text)
            done += 1
            continue
        if text.count(old) != 1:
            print(f"cannot apply to {name}: found {text.count(old)} copies of\n{old}")
            return 1
        texts[name] = patched(text, old, new)
    if check:
        print(f"{done} of {len(EDITS)} edits already in place")
        return 0 if done == len(EDITS) else 1
    for name, text in texts.items():
        path = folder / name
        backup = path.with_name(path.name + ".cih-orig")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        out = text.replace("\n", "\r\n") if crlf[name] else text
        path.write_bytes(out.encode("utf-8"))
    print(f"applied {len(EDITS) - done} edits ({done} were already in place); originals kept as *.cih-orig")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
