"""Write PM4K's skin file for the person page (script-cih-person.xml) from the screens PM4K already has.

The artist screen supplies the header (photo, name, one line of facts, biography, the round buttons) and the movie screen
supplies the poster row with its watched marks and progress bars; this puts nine of those rows under the artist header, in
a vertical list like the movie screen's, adds a second line (year and role) under each title and a gold check on titles
you own. Reading PM4K's own files means the page follows its theme and its fonts. The file is named script-cih-person.xml
(not script-plex-*) on purpose: PM4K re-renders all its screens when the number of script-plex-*.xml files differs from
its templates, which would undo every patch at each start.

usage: python make_person_xml.py <skin 1080i folder>
  Run it before scale_ui.py (which then scales the new file with the others) and apply_font.py. Originals (*.cih-orig)
  are used where a screen is already patched.
"""

import copy
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import button_hints  # noqa: E402  (players/pm4k-osd/button_hints.py)

PERSON_HINTS = {"301": "Biography", "302": "Play all", "303": "Shuffle", "304": "Sort and filter"}

ROW_IDS = list(range(400, 409))  # list ids; the row groups are 100 higher
TEMPLATE_LIST = "403"
TEMPLATE_GROUP = "503"
OUT = "script-cih-person.xml"
GOLD = "FFE5A00D"
ROWS_VIEW_HEIGHT = "860"  # what is left of the 1080 rows below the slid-up header: the row list scrolls inside this
ROW_HEIGHT = "560"  # the movie screen's 520, plus room for the second text line under each title


def original(folder: Path, name: str) -> Path:
    backup = folder / (name + ".cih-orig")
    return backup if backup.exists() else folder / name


def child_text(node: ET.Element, tag: str) -> str | None:
    found = node.find(tag)
    return found.text.strip() if found is not None and found.text else None


def find_control(root: ET.Element, control_id: str) -> ET.Element:
    for node in root.iter("control"):
        if node.get("id") == control_id:
            return node
    raise SystemExit(f"control {control_id} not found")


def parent_map(root: ET.Element) -> dict:
    return {child: parent for parent in root.iter() for child in parent}


def badge_and_subtitle(layout: ET.Element, label_font: str) -> None:
    """under the title label: a second line (year / role); on the poster: a gold check for owned titles"""
    parents = parent_map(layout)
    for node in list(layout.iter("control")):
        text = child_text(node, "label")
        if node.get("type") != "label" or text != "$INFO[ListItem.Label]":
            continue
        group = parents[node]
        posy = float(child_text(node, "posy") or 0)
        # a copy of the title label (so the second line scrolls when it is too long, exactly when the title does)
        sub = copy.deepcopy(node)
        for tag, value in (("posy", f"{posy + 32:g}"), ("height", "30"), ("font", label_font), ("textcolor", "FFDDDDDD"),
                           ("label", "$INFO[ListItem.Label2]")):
            element = sub.find(tag)
            if element is None:
                element = ET.SubElement(sub, tag)
            element.text = value
        badge = ET.fromstring(
            '<control type="group"><visible>!String.IsEmpty(ListItem.Property(owned))</visible><posx>0</posx><posy>0</posy>'
            '<control type="image"><posx>0</posx><posy>0</posy><width>36</width><height>36</height>'
            f'<texture colordiffuse="{GOLD}">script.plex/white-square-bl-rounded_w.png</texture></control>'
            '<control type="image"><posx>9</posx><posy>9</posy><width>18</width><height>18</height>'
            '<texture colordiffuse="FF000000" fallback="script.plex/indicators/watched_2024.png">script.plex/indicators/watched_2024.png</texture></control>'
            "</control>"
        )
        # on the watchlist: a dark badge with a gold bookmark, under the gold check when the title is also in the library
        marks = []
        for condition, top in (
            ("!String.IsEmpty(ListItem.Property(watchlisted)) + !String.IsEmpty(ListItem.Property(owned))", 38),
            ("!String.IsEmpty(ListItem.Property(watchlisted)) + String.IsEmpty(ListItem.Property(owned))", 0),
        ):
            marks.append(
                ET.fromstring(
                    f'<control type="group"><visible>{condition}</visible><posx>0</posx><posy>{top}</posy>'
                    '<control type="image"><posx>0</posx><posy>0</posy><width>36</width><height>36</height>'
                    '<texture colordiffuse="CC000000">script.plex/white-square-bl-rounded_w.png</texture></control>'
                    # the button icon is a 180 x 145 picture with a small bookmark in its middle: drawn at 45% so that the
                    # bookmark itself is about 18 x 22 and centred on the badge
                    '<control type="image"><posx>-22.5</posx><posy>-14.5</posy><width>81</width><height>65</height>'
                    f'<texture colordiffuse="{GOLD}">script.plex/buttons/player/modern/watchlisted.png</texture></control>'
                    "</control>"
                )
            )
        items = list(group)
        at = items.index(node)
        for offset, extra in enumerate([sub, badge, *marks], start=1):
            group.insert(at + offset, extra)


def make_row(template: ET.Element, index: int, list_id: int) -> ET.Element:
    text = ET.tostring(template, encoding="unicode")
    group_id = list_id + 100
    text = text.replace(f'id="{TEMPLATE_GROUP}"', f'id="{group_id}"')
    text = text.replace(f'id="{TEMPLATE_LIST}"', f'id="{list_id}"')
    text = text.replace(f"Container({TEMPLATE_LIST})", f"Container({list_id})")
    text = text.replace(f"HasFocus({TEMPLATE_LIST})", f"HasFocus({list_id})")
    text = text.replace(f"<defaultcontrol>{TEMPLATE_LIST}</defaultcontrol>", f"<defaultcontrol>{list_id}</defaultcontrol>")
    text = text.replace("Window.Property(related.header)", f"Window.Property(row.{list_id}.title)")
    text = re.sub(r"<onup>\d+</onup>", f"<onup>{list_id - 1 if index else 300}</onup>", text)
    if index < len(ROW_IDS) - 1:
        text = re.sub(r"(<onup>\d+</onup>)", rf"\1<ondown>{list_id + 1}</ondown>", text, count=1)
    row = ET.fromstring(text)
    row.find("height").text = ROW_HEIGHT
    return row


def build(folder: Path) -> str:
    artist = ET.parse(original(folder, "script-plex-artist.xml")).getroot()
    movie = ET.parse(original(folder, "script-plex-pre_play.xml")).getroot()

    controls = artist.find("controls")
    block = find_control(controls, "50")
    for drop in ("100", "500"):
        node = find_control(block, drop)
        block.remove(node)
    # the artist screen's own buttons chain down to list 400; keep them, point their down move at the first row
    for node in block.iter("ondown"):
        if node.text and node.text.strip() in ("400", "401"):
            node.text = "400"

    # slide the header out of the way as soon as any row has focus (the artist screen does it from the second row on), so
    # the focused row's titles are on the screen
    for anim in block.findall("animation"):
        if "hub.focus" in (anim.get("condition") or ""):
            anim.set("condition", "!String.IsEmpty(Window.Property(hub.focus))")

    # the names of the round buttons, shown while one has focus (the row starts where it did when it was centred)
    row = find_control(block, "300")
    button_hints.add_hints(row, PERSON_HINTS, watchlist=False)
    button_hints.widen(row, align_left_at="621")

    template = find_control(movie, TEMPLATE_GROUP)
    row_font = "font13"
    rows_holder = copy.deepcopy(find_control(movie, "60"))
    for old in rows_holder.findall("control"):
        rows_holder.remove(old)
    for tag, value in (("posx", "0"), ("posy", "585"), ("height", ROWS_VIEW_HEIGHT)):
        node = rows_holder.find(tag)
        if node is None:
            node = ET.SubElement(rows_holder, tag)
        node.text = value
    for index, list_id in enumerate(ROW_IDS):
        row = make_row(template, index, list_id)
        for layout in list(row.iter("itemlayout")) + list(row.iter("focusedlayout")):
            badge_and_subtitle(layout, row_font)
        rows_holder.append(row)
    block.append(rows_holder)

    ET.indent(artist, space="    ")
    return ET.tostring(artist, encoding="unicode", xml_declaration=False)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder = Path(argv[0])
    text = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + build(folder) + "\n"
    (folder / OUT).write_bytes(text.encode("utf-8"))
    print(f"wrote {OUT} ({len(ROW_IDS)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
