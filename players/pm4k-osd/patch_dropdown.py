"""Make PM4K's dropdown menus follow the band-fitted browsing screens (see scale_ui.py).

PM4K places a dropdown from hard-coded 1920x1080 positions in its Python (dropdown.py, called from every screen). Once
the screens are scaled into the 2.35:1 band those positions no longer sit on their buttons. While no video is playing
this maps each position the same way scale_ui.py maps the screens, keeps the menu inside the band, and limits it to the
rows that fit. During playback (menus opened from the playback overlay) nothing changes.

usage: python patch_dropdown.py <script.plexmod folder> [--check]
  <script.plexmod folder> is a pulled copy of the add-on; lib/windows/dropdown.py is patched in place and the original
  kept as dropdown.py.cih-orig (once). Every replacement must match exactly once, otherwise the script stops.
"""

import sys
from pathlib import Path

EDITS = [
    (
        "    dropWidth = 360\n    borderOff = -20\n",
        """    dropWidth = 360
    borderOff = -20

    # CIH: browsing screens are scaled into the 2.35:1 band (players/pm4k-osd/scale_ui.py); this uses the same maths
    CIH_SCALE = 0.7556
    CIH_BAND_TOP = 132
    CIH_BAND_BOTTOM = 948

    @staticmethod
    def cihBrowsing():
        return not xbmc.getCondVisibility('Player.HasVideo')

    def cihMapPos(self, pos):
        if not pos or not self.cihBrowsing():
            return pos
        x, y = pos
        s, w = self.CIH_SCALE, self.dropWidth
        centre = x + w / 2.0
        if centre < 0.35 * 1920:
            nx = x * s
        elif centre > 0.65 * 1920:
            nx = 1920 - (1920 - x - w) * s - w
        else:
            nx = 960 + (centre - 960) * s - w / 2.0
        if isinstance(y, (int, float)):
            y = int(round(self.CIH_BAND_TOP + y * s))
        return int(round(nx)), y

    def cihTop(self):
        return self.CIH_BAND_TOP if self.cihBrowsing() else 0

    def cihBottom(self):
        return self.CIH_BAND_BOTTOM if self.cihBrowsing() else self.height

    def cihMaxRows(self):
        if not self.cihBrowsing():
            return 14
        return max(3, int((self.CIH_BAND_BOTTOM - self.CIH_BAND_TOP - util.vscalei(86)) // self.optionHeight))
""",
    ),
    ("        self.pos = kwargs.get('pos')\n", "        self.pos = self.cihMapPos(kwargs.get('pos'))\n"),
    (
        "        self.setBoolProperty('scroll', optLen > 14)\n"
        "        self.optionsList = kodigui.ManagedControlList(self, self.OPTIONS_LIST_ID, 14)\n",
        "        maxRows = self.cihMaxRows()\n"
        "        self.setBoolProperty('scroll', optLen > maxRows)\n"
        "        self.optionsList = kodigui.ManagedControlList(self, self.OPTIONS_LIST_ID, maxRows)\n",
    ),
    (
        "        height = min(self.optionHeight * 14, optLen",
        "        height = min(self.optionHeight * maxRows, optLen",
    ),
    (
        "        if isinstance(y, int) and y + height > self.height:\n"
        "            while y + height > self.height and y > 0:\n"
        "                y -= self.optionHeight\n"
        "            y = max(0, y)\n",
        "        if isinstance(y, int) and y + height > self.cihBottom():\n"
        "            while y + height > self.cihBottom() and y > self.cihTop():\n"
        "                y -= self.optionHeight\n"
        "            y = max(self.cihTop(), y)\n",
    ),
]


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = Path(argv[0]) / "lib" / "windows" / "dropdown.py"
    if not path.is_file():
        print(f"not found: {path}")
        return 2
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    if "cihMapPos" in text:
        print("already patched")
        return 0
    if "--check" in argv[1:]:
        print("not patched")
        return 0
    for old, new in EDITS:
        if text.count(old) != 1:
            print(f"STOPPED: this edit no longer fits dropdown.py: {old[:60]!r}")
            return 1
        text = text.replace(old, new)
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print("patched dropdown.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
