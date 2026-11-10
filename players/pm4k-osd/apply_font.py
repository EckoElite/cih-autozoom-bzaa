"""Give PM4K the typeface of the Plex apps (Roboto) instead of the ScopeNox skin's fonts.

PM4K names its fonts font10, font12, font13 and font14 and leaves their definition to the active Kodi skin; ScopeNox
maps them to Orbitron (titles) and Titillium Web (everything else). This script

  1. adds four fonts, plex10 to plex14, to every fontset of the skin's Font.xml (Roboto Medium for titles, Roboto
     Regular for the rest; the .ttf files are the Shield's own /system/fonts, copied into the skin's fonts folder);
  2. rewrites `<font>fontN</font>` to `<font>plexN</font>` in every PM4K screen, so only PM4K changes and the rest of
     the skin keeps its fonts.

usage: python apply_font.py <skin 1080i folder> <skin Font.xml> [--check]
  Both are pulled copies; nothing here touches the Shield. Font.xml is saved once as Font.xml.cih-bak. Run it after scale_ui.py and apply_osd_margin.py.
  A PM4K update, or a theme change that makes PM4K render its screens again, brings the old names back: run again.
"""

import re
import sys
from pathlib import Path

# PM4K's name -> (new name, font file, size). The sizes are the old ones adjusted for Roboto's wider letters.
# The .ttf files are Roboto with Noto Emoji merged in (build_emoji_font.py), so characters like a star or a heart in a
# title are drawn instead of a box. Kodi only looks up glyphs up to U+FFFF, so emoji above that (a flame, a clapper board)
# still show as a box whatever the font holds.
FONTS = {
    "font10": ("plex10", "RobotoEmoji-Medium.ttf", 22),  # poster titles, small headings
    "font12": ("plex12", "RobotoEmoji-Regular.ttf", 25),  # body text, buttons
    "font13": ("plex13", "RobotoEmoji-Regular.ttf", 22),
    "font14": ("plex14", "RobotoEmoji-Regular.ttf", 19),
}
OLD_FILES = {"Roboto-Medium.ttf": "RobotoEmoji-Medium.ttf", "Roboto-Regular.ttf": "RobotoEmoji-Regular.ttf"}  # first version
FONT_FILES = sorted({file for _, file, _ in FONTS.values()})


def font_block(name: str, filename: str, size: int) -> str:
    return f"\t\t<font>\n\t\t\t<name>{name}</name>\n\t\t\t<filename>{filename}</filename>\n\t\t\t<size>{size}</size>\n\t\t</font>\n"


def patch_font_xml(path: Path, check: bool) -> str:
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    if "<name>plex10</name>" in text:
        old = re.findall(r"(<name>plex1[0-4]</name>\s*<filename>)(Roboto-(?:Medium|Regular)\.ttf)(</filename>)", text)
        if not old:
            return "already has the plex fonts"
        if check:
            return "has the plex fonts, but with the files without emoji"
        text = re.sub(
            r"(<name>plex1[0-4]</name>\s*<filename>)(Roboto-(?:Medium|Regular)\.ttf)(</filename>)",
            lambda m: m.group(1) + OLD_FILES[m.group(2)] + m.group(3),
            text,
        )
        path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
        return f"pointed {len(old)} plex fonts at the files with emoji"
    if check:
        return "needs the plex fonts"
    block = "".join(font_block(new, file, size) for new, file, size in FONTS.values())
    parts = text.split("\t</fontset>")
    if len(parts) < 2:
        raise SystemExit(f"{path}: no </fontset> found")
    text = ("\t" + "</fontset>").join(p + ("\t<!-- cih: Plex typeface -->\n" + block if i < len(parts) - 1 else "") for i, p in enumerate(parts))
    backup = path.with_name(path.name + ".cih-bak")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    return f"added {len(FONTS)} fonts to {len(parts) - 1} fontsets"


def patch_screens(folder: Path, check: bool) -> list[str]:
    report = []
    pattern = re.compile(r"<font>(font1[0-4])</font>")
    for path in sorted(folder.glob("script-plex-*.xml")):
        raw = path.read_bytes().decode("utf-8")
        found = pattern.findall(raw)
        if not found:
            continue
        report.append(f"{'would patch' if check else 'patched':<12} {path.name} ({len(found)})")
        if check:
            continue
        path.write_bytes(pattern.sub(lambda m: f"<font>{FONTS[m.group(1)][0]}</font>", raw).encode("utf-8"))
    return report


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder, font_xml = Path(argv[0]), Path(argv[1])
    check = "--check" in argv[2:]
    print(f"{font_xml.name}: {patch_font_xml(font_xml, check)}")
    for line in patch_screens(folder, check):
        print(line)
    print("font files to copy into the skin's fonts folder:", ", ".join(FONT_FILES))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
