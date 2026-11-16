"""Name the focused round button in PM4K's button rows (Info, Play, Trailer, Add to watchlist ...).

PM4K's detail screens show a row of icon-only buttons. This adds one text label per button to the end of that row, shown
only while that button has focus, so the name appears right after the last icon and nothing else moves. It is a
grouplist, so the invisible labels take no room. The watchlist button shows its state: "Add to watchlist" or "In
watchlist", and, after you click it, "Added to watchlist" or "Removed from watchlist" (gold) until you leave the page
(the window property wl.feedback, set by patch_watchlist.py).

Applied to the movie screen and its watchlist variant here, and to the person page by person/make_person_xml.py. Other
screens are left alone.

usage: python button_hints.py <skin 1080i folder> [--check]
  Run it on the unpatched screens, before scale_ui.py (reapply.py does).
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GOLD = "FFE5A00D"
WHITE = "FFFFFFFF"
MARK = "cih-hint"
LABEL_WIDTH = 460
LABEL_HEIGHT = 40
ROW_WIDTH = "1500"  # wide enough that the longest row plus its label never makes the row scroll

# button id -> text, for the movie screen and its watchlist variant
MOVIE_HINTS = {
    "304": "Info",
    "302": "Play",
    "2302": "Checking availability",
    "2303": "Choose where to watch",
    "2304": "Play",
    "2305": "Not in your library",
    "303": "Trailer",
    "307": "Versions",
    "305": "Audio and subtitles",
    "306": "More",
}

# (condition, text, gold?) for the two watchlist buttons: 308 is shown when the title is not on the watchlist, 309 when it is
WATCHLIST_HINTS = [
    ("Control.HasFocus(308) + String.IsEmpty(Window.Property(wl.feedback))", "Add to watchlist", False),
    ("Control.HasFocus(308) + !String.IsEmpty(Window.Property(wl.feedback))", "Removed from watchlist", True),
    ("Control.HasFocus(309) + String.IsEmpty(Window.Property(wl.feedback))", "In watchlist", False),
    ("Control.HasFocus(309) + !String.IsEmpty(Window.Property(wl.feedback))", "Added to watchlist", True),
]


def number(node: ET.Element, tag: str) -> str | None:
    found = node.find(tag)
    return found.text.strip() if found is not None and found.text else None


def hint_label(condition: str, text: str, height: str, gold: bool = False, font: str = "font12") -> ET.Element:
    label = ET.Element("control", {"type": "label"})
    for tag, value in (
        ("description", MARK),
        ("visible", condition),
        ("posx", "40"),
        ("posy", f"{max(0, (float(height) - LABEL_HEIGHT) / 2 - 6):g}"),  # scaling keeps a text box's height, so the label is short and centred on the icon
        ("width", str(LABEL_WIDTH)),
        ("height", str(LABEL_HEIGHT)),
        ("font", font),
        ("align", "left"),
        ("aligny", "center"),
        ("textcolor", GOLD if gold else WHITE),
        ("label", text),
    ):
        ET.SubElement(label, tag).text = value
    fade = ET.SubElement(label, "animation", {"effect": "fade", "start": "0", "end": "100", "time": "150", "reversible": "false"})
    fade.text = "Visible"
    return label


def add_hints(row: ET.Element, hints: dict[str, str], watchlist: bool = True, font: str = "font12") -> int:
    """append the labels to a button row (a grouplist); returns how many were added (0 when already there)"""
    if any(number(c, "description") == MARK for c in row.findall("control")):
        return 0
    buttons = {b.get("id"): b for b in row.findall("control") if b.get("type") == "button"}
    height = number(next(iter(buttons.values())), "height") or "121"
    added = 0
    for button_id, text in hints.items():
        if button_id in buttons:
            row.append(hint_label(f"Control.HasFocus({button_id})", text, height, font=font))
            added += 1
    if watchlist and "308" in buttons:
        for condition, text, gold in WATCHLIST_HINTS:
            row.append(hint_label(condition, text, height, gold, font=font))
            added += 1
    return added


def widen(row: ET.Element, align_left_at: str | None = None) -> None:
    """give the row room for its label (and left-align it where it was centred)"""
    width = row.find("width")
    if width is not None:
        width.text = ROW_WIDTH
    if align_left_at is not None:
        align = row.find("align")
        if align is not None:
            align.text = "left"
        posx = row.find("posx")
        if posx is not None:
            posx.text = align_left_at


def patch_file(path: Path, check: bool) -> str:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    tree = ET.parse(path, parser=parser)
    root = tree.getroot()
    row = next((c for c in root.iter("control") if c.get("id") == "300" and c.get("type") == "grouplist"), None)
    if row is None:
        return "no button row"
    if any(number(c, "description") == MARK for c in row.findall("control")):
        return "already has the hints"
    if check:
        return "needs the hints"
    added = add_hints(row, MOVIE_HINTS)
    widen(row)
    ET.indent(root, space="    ")
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return f"added {added} hints"


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    folder = Path(argv[0])
    check = "--check" in argv[1:]
    for name in ("script-plex-pre_play.xml", "script-plex-pre_play-wl.xml"):
        path = folder / name
        if path.is_file():
            print(f"{name}: {patch_file(path, check)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
