"""Add an ASPECT chip to the Audio / Subtitles row of PM4K's movie screen.

The row already has a dark chip and a value for Audio and for Subtitles; this appends a third pair, ASPECT and the window
property cih.ratio (filled in by ratio/cihratio.py from the analyzer), shown only when the property is not empty. The
watchlist variant of the screen (titles you do not own) is left alone: the analyzer only knows your files.

usage: python ratio_chip.py <skin 1080i folder> [--check]
  Run it on the unpatched screen, before scale_ui.py (reapply.py does).
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MARK = "cih-ratio"
VISIBLE = "!String.IsEmpty(Window.Property(cih.ratio))"
CHIP = f"""<control type="button">
    <description>{MARK}</description>
    <visible>{VISIBLE}</visible>
    <left>30</left>
    <width>auto</width>
    <height>34</height>
    <font>font12</font>
    <align>center</align>
    <aligny>top</aligny>
    <focusedcolor>FFFFFFFF</focusedcolor>
    <textcolor>FFFFFFFF</textcolor>
    <textoffsetx>15</textoffsetx>
    <texturefocus colordiffuse="40000000" border="8">script.plex/white-square-rounded-top-padded.png</texturefocus>
    <texturenofocus colordiffuse="40000000" border="8">script.plex/white-square-rounded-top-padded.png</texturenofocus>
    <label>ASPECT</label>
</control>"""
VALUE = f"""<control type="label">
    <description>{MARK}</description>
    <visible>{VISIBLE}</visible>
    <width>auto</width>
    <height>34</height>
    <font>font12</font>
    <align>left</align>
    <aligny>top</aligny>
    <textcolor>FFFFFFFF</textcolor>
    <label>$INFO[Window.Property(cih.ratio)]</label>
</control>"""


def patch_file(path: Path, check: bool) -> str:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.parse(path, parser=parser).getroot()
    row = None
    for node in root.iter("control"):
        if node.get("type") == "grouplist" and any(
            "Window.Property(audio)" in (c.findtext("visible") or "") for c in node.findall("control")
        ):
            row = node
            break
    if row is None:
        return "no Audio / Subtitles row"
    if any(c.findtext("description") == MARK for c in row.findall("control")):
        return "already has the chip"
    if check:
        return "needs the chip"
    row.append(ET.fromstring(CHIP))
    row.append(ET.fromstring(VALUE))
    ET.indent(root, space="    ")
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return "added the ASPECT chip"


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = Path(argv[0]) / "script-plex-pre_play.xml"
    if not path.is_file():
        print(f"not found: {path}")
        return 2
    print(f"{path.name}: {patch_file(path, '--check' in argv[1:])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
