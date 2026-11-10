"""Keep emoji that Kodi cannot draw out of PM4K's text.

Kodi looks glyphs up by the low 16 bits of a character, so anything above U+FFFF (a flame, a clapper board, popcorn) is
drawn as a box whatever the font holds; symbols below it (a star, a heart) draw fine from the merged font
(build_emoji_font.py). This makes PM4K drop the characters above U+FFFF, with the joiners and variation selectors that
go with them and the extra space they leave, from every list item label and every window property it sets (library
names, collection names, titles, summaries). "Movies Now | <flame> Trending" shows as "Movies Now | Trending".

usage: python patch_emoji.py <script.plexmod folder> [--check]
  <script.plexmod folder> is a pulled copy of the add-on; lib/windows/kodigui.py is patched in place and the original
  kept as kodigui.py.cih-orig (once). Every replacement must match as many times as listed, otherwise the script stops.
"""

import sys
from pathlib import Path

HELPER = '''MONITOR = None


def cihNoAstral(text):
    """CIH: Kodi draws no glyph above U+FFFF (an emoji such as a flame shows as a box): drop those characters, the
    zero width joiners and variation selectors that go with them, and the extra space they leave."""
    if not isinstance(text, str) or text.isascii() or max(text) <= '\\uffff':
        return text
    kept = ''.join(c for c in text if c <= '\\uffff' and c not in '\\u200d\\ufe0f')
    out = []
    for line in kept.split('\\n'):
        while '  ' in line:
            line = line.replace('  ', ' ')
        out.append(line.strip(' '))
    return '\\n'.join(out)
'''

WINDOW_SET = "    def setProperty(self, key, value):\n        if self._closing:\n            return\n"

# (old, new, how many times old occurs)
EDITS = [
    ("MONITOR = None\n", HELPER, 1),
    (WINDOW_SET, "    def setProperty(self, key, value):\n        value = cihNoAstral(value)\n        if self._closing:\n            return\n", 2),
    (
        "        self._listItem = xbmcgui.ListItem(label, label2, path=path)\n",
        "        label, label2 = cihNoAstral(label), cihNoAstral(label2)\n        self._listItem = xbmcgui.ListItem(label, label2, path=path)\n",
        1,
    ),
    ("    def setLabel(self, label):\n        self.label = label\n", "    def setLabel(self, label):\n        label = cihNoAstral(label)\n        self.label = label\n", 1),
    ("    def setLabel2(self, label):\n        self.label2 = label\n", "    def setLabel2(self, label):\n        label = cihNoAstral(label)\n        self.label2 = label\n", 1),
    (
        "    def setProperty(self, key, value):\n        if self._manager:\n",
        "    def setProperty(self, key, value):\n        value = cihNoAstral(value)\n        if self._manager:\n",
        1,
    ),
]


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = Path(argv[0]) / "lib" / "windows" / "kodigui.py"
    if not path.is_file():
        print(f"not found: {path}")
        return 2
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    if "cihNoAstral" in text:
        print("already patched")
        return 0
    if "--check" in argv[1:]:
        print("not patched")
        return 0
    for old, new, count in EDITS:
        if text.count(old) != count:
            print(f"STOPPED: this edit no longer fits kodigui.py ({text.count(old)} matches, expected {count}): {old[:60]!r}")
            return 1
        text = text.replace(old, new)
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print("patched kodigui.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
