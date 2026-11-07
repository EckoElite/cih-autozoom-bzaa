"""Shrink PM4K's browsing screens so their layout fits the 2.35:1 band, using the full screen width.

PM4K lays each screen out over 1920x1080, but only rows 132 to 948 of that are on a 2.35:1 screen. Zooming the whole
window leaves black bars at the sides; this instead scales the sizes and positions of the UI elements (posters, rows,
header, gaps) by SCALE, so the layout fits 816 rows and the rows run the full width, showing more items per row. Text
keeps its size (fonts are not touched).

What is scaled, per control: posx, posy, width, height, left, right, top, bottom, itemgap, textoffsetx/y; the width /
height of item layouts; slide distances; hit rectangles. What is not: full-screen background images, and any width of
1800 or more (a row or group that spans the screen keeps spanning it). A fixed list keeps its focused slot where it was.
The window origin is moved to WINDOW_TOP (a few rows above the band) so the backgrounds cover the whole band; top-level
controls are moved down by the difference. Two black bars drawn on top hide everything above and below the band, so no
light spills onto the wall around the screen.

usage: python scale_ui.py <skin folder> [--check] [--only home,settings]
  <skin folder> is script.plexmod/resources/skins/Main/1080i (a pulled copy; the script never touches the Shield).
  Patched files are written in place; each original is kept next to it as <name>.cih-orig (once).

A PM4K update, or PM4K re-rendering its templates, brings the originals back, so re-apply after either.
"""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BAND_TOP = 132  # 263 / 2, as in apply_osd_margin.py
BAND_BOTTOM = 948  # 1897 / 2
SCALE = round((BAND_BOTTOM - BAND_TOP) / 1080, 4)  # 0.7556
WINDOW_TOP = 126  # a little above BAND_TOP: the measured top edge is 258 +/- 4 px, i.e. rows 127 to 129
TOP_OFFSET = BAND_TOP - WINDOW_TOP

MARKER = " cih-scale-ui "
KEEP_WIDTH = 1800  # widths from here up are left alone
H_TAGS = ("posx", "left", "right", "width", "textoffsetx")
V_TAGS = ("posy", "top", "bottom", "height", "textoffsety")
BOTH_TAGS = ("itemgap",)
NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)(r?)$")

SKIP = {
    "script-plex-seek_dialog.xml",  # playback overlay: apply_osd_margin.py
    "script-plex-video_settings_dialog.xml",
    "script-plex-video_player.xml",
    "script-plex-busy.xml",
    "script-plex-busy_msg.xml",
    "script-plex-background.xml",
    "script-plex-blank.xml",
    "script-plex-signin_background.xml",
    "script-plex-signin_blank.xml",
    "script-plex-slideshow.xml",
    "script-plex-photo.xml",
    # menus whose size and position PM4K's Python sets in absolute pixels (dropdown.py, optionsdialog.py): scaling their
    # XML would not match, so they are left alone until their Python is adjusted too
    "script-plex-dropdown.xml",
    "script-plex-dropdown_header.xml",
    "script-plex-options_dialog.xml",
    "script-plex-options_dialog_big.xml",
    "script-plex-settings_select_dialog.xml",
    "script-plex-track_context.xml",
}


def fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def scaled(text: str | None, factor: float = SCALE) -> str | None:
    """text with its number scaled (a trailing r is kept), or None when it is not a plain number"""
    if text is None:
        return None
    match = NUMBER.match(text.strip())
    if not match:
        return None
    return fmt(float(match.group(1)) * factor) + match.group(2)


def number(element: ET.Element | None) -> float | None:
    if element is None or element.text is None:
        return None
    match = NUMBER.match(element.text.strip())
    return float(match.group(1)) if match else None


def is_fullscreen_image(control: ET.Element) -> bool:
    if control.get("type") != "image":
        return False
    width, height = number(control.find("width")), number(control.find("height"))
    return width is not None and height is not None and width >= 1900 and height >= 1000


def is_background(control: ET.Element) -> bool:
    """a control that holds nothing but full-screen images"""
    if control.get("type") == "image":
        return is_fullscreen_image(control)
    kids = control.findall("control")
    return control.get("type") == "group" and bool(kids) and all(is_background(kid) for kid in kids)


def scale_control(control: ET.Element, top_level: bool) -> None:
    if is_fullscreen_image(control):
        return
    keep_focus_slot = None
    layout = control.find("itemlayout")
    focus = control.find("focusposition")
    if control.get("type") == "fixedlist" and layout is not None and focus is not None:
        old_width = layout.get("width")
        if old_width and number(control.find("posx")) is not None:
            keep_focus_slot = (number(control.find("posx")), float(focus.text), float(old_width))
    for tag in H_TAGS + V_TAGS + BOTH_TAGS:
        element = control.find(tag)
        if element is None:
            continue
        old = number(element)
        if old is None or (tag == "width" and old >= KEEP_WIDTH):
            continue
        new = scaled(element.text)
        if new is not None:
            element.text = new
    if keep_focus_slot is not None:
        posx, slot, old_width = keep_focus_slot
        new_width = old_width * SCALE
        control.find("posx").text = fmt(posx + slot * old_width - slot * new_width)
    if top_level and not is_background(control):
        posy = control.find("posy")
        if posy is None:
            posy = ET.Element("posy")
            posy.text = "0"
            control.insert(0, posy)
        value = number(posy)
        if value is not None:
            posy.text = fmt(value + TOP_OFFSET)
    for tag in ("itemlayout", "focusedlayout"):
        for node in control.findall(tag):
            for attr in ("width", "height"):
                if node.get(attr):
                    node.set(attr, scaled(node.get(attr)) or node.get(attr))
    hitrect = control.find("hitrect")
    if hitrect is not None:
        for attr in ("x", "y", "w", "h"):
            if hitrect.get(attr):
                hitrect.set(attr, scaled(hitrect.get(attr)) or hitrect.get(attr))
    for anim in list(control.findall("animation")):
        scale_animation(anim)
    for kid in control.findall("control"):
        scale_control(kid, False)
    for tag in ("itemlayout", "focusedlayout"):
        for node in control.findall(tag):
            for kid in node.findall("control"):
                scale_control(kid, False)


def scale_slide(node: ET.Element) -> None:
    end = node.get("end")
    if end is None:
        return
    parts = [part.strip() for part in end.split(",")]
    new = [scaled(part) for part in parts]
    if None in new:
        return
    node.set("end", ",".join(new))
    start = node.get("start")
    if start:
        parts = [part.strip() for part in start.split(",")]
        new = [scaled(part) for part in parts]
        if None not in new:
            node.set("start", ",".join(new))


def scale_animation(anim: ET.Element) -> None:
    if anim.get("effect") == "slide":
        scale_slide(anim)
    for effect in anim.findall("effect"):
        if effect.get("type") == "slide":
            scale_slide(effect)


def add_masks(controls: ET.Element) -> None:
    """Black bars over everything above and below the band, drawn last (on top).

    The window now starts above the band, and PM4K draws content that has slid out of view (a row scrolled up, the rows
    below the screen) just outside it, where the projector would light the wall: they are covered here.
    """
    bars = [(-400, TOP_OFFSET + 400), (BAND_BOTTOM - WINDOW_TOP, 1300)]  # (posy, height) in window coordinates
    for posy, height in bars:
        bar = ET.Element("control", {"type": "image"})
        for tag, value in (("posx", "-200"), ("posy", str(posy)), ("width", "2320"), ("height", str(height))):
            ET.SubElement(bar, tag).text = value
        ET.SubElement(bar, "texture", {"colordiffuse": "FF000000"}).text = "script.plex/white-square.png"
        controls.append(bar)


def patched_tree(path: Path) -> bytes:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.parse(path, parser=parser).getroot()
    if root.tag != "window":
        raise ValueError("not a window file")
    coords = root.find("coordinates")
    if coords is None or coords.find("posy") is None:
        raise ValueError("no <coordinates><posy>")
    coords.find("posy").text = str(WINDOW_TOP)
    controls = root.find("controls")
    if controls is None:
        raise ValueError("no <controls>")
    for control in controls.findall("control"):
        scale_control(control, True)
    add_masks(controls)
    root.insert(0, ET.Comment(MARKER))
    ET.indent(root, space="    ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder = Path(argv[0])
    check = "--check" in argv[1:]
    only = None
    if "--only" in argv:
        only = {f"script-plex-{n}.xml" for n in argv[argv.index("--only") + 1].split(",")}
    if not folder.is_dir():
        print(f"not a folder: {folder}")
        return 2
    bad = 0
    for path in sorted(folder.glob("script-plex-*.xml")):
        if path.name in SKIP or (only is not None and path.name not in only):
            continue
        text = path.read_text(encoding="utf-8")
        if MARKER in text:
            print(f"already patched  {path.name}")
            continue
        if check:
            print(f"not patched      {path.name}")
            continue
        try:
            new = patched_tree(path)
        except (ValueError, ET.ParseError) as err:
            print(f"SKIPPED          {path.name}: {err}")
            bad += 1
            continue
        backup = path.with_name(path.name + ".cih-orig")
        if not backup.exists():
            backup.write_bytes(text.encode("utf-8"))
        path.write_bytes(new)
        print(f"patched          {path.name}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
