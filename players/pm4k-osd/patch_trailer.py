"""Install the trailer start-up fix in PM4K's Python: trailer/cihtrailer.py, and the calls that use it.

  - copies trailer/cihtrailer.py to <add-on>/lib/cihtrailer.py (always replaced);
  - patches lib/player.py: Player.play() passes its address through cihtrailer.resolve(), which swaps the many-rendition
    playlist of a Plex Discover trailer for one rendition's playlist (see cihtrailer.py for why);
  - patches lib/windows/preplay.py: when a title page finds its trailer it calls cihtrailer.warm(), which prepares the
    trailer in the background so the Trailer button starts quickly.

usage: python patch_trailer.py <script.plexmod folder> [--check]
  Each patched file keeps its original as <name>.cih-orig (once; preplay.py's is shared with the other patch scripts).
  Every replacement must match exactly once, otherwise the script stops. Running it again is harmless.
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

PLAY_OLD = "    def play(self, *args, **kwargs):\n        self.started = False\n        xbmc.Player.play(self, *args, **kwargs)\n"
PLAY_NEW = (
    "    def play(self, *args, **kwargs):\n        self.started = False\n"
    "        try:  # CIH: one rendition of a Plex Discover trailer instead of all of them (players/pm4k-osd/trailer)\n"
    "            from . import cihtrailer\n"
    "            if args:\n"
    "                args = (cihtrailer.resolve(args[0]),) + tuple(args[1:])\n"
    "        except Exception:\n"
    "            util.ERROR()\n"
    "        xbmc.Player.play(self, *args, **kwargs)\n"
)

PRE_OLD = "                self.trailer = extra\n                self.setProperty('trailer.button', '1')\n"
PRE_NEW = (
    "                self.trailer = extra\n                self.setProperty('trailer.button', '1')\n"
    "                try:  # CIH: get the trailer ready in the background (players/pm4k-osd/trailer)\n"
    "                    from .. import cihtrailer\n"
    "                    cihtrailer.warm(extra)\n"
    "                except Exception:\n"
    "                    util.ERROR()\n"
)

# file under lib/, old text, new text, a marker that is only there once patched
EDITS = [
    ("player.py", PLAY_OLD, PLAY_NEW, "cihtrailer.resolve"),
    ("windows/preplay.py", PRE_OLD, PRE_NEW, "cihtrailer.warm"),
]


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    root = Path(argv[0])
    check = "--check" in argv[1:]
    results = []
    for name, old, new, marker in EDITS:
        path = root / "lib" / name
        if not path.is_file():
            print(f"not found: {path}")
            return 2
        text = path.read_bytes().decode("utf-8")
        crlf = "\r\n" in text
        text = text.replace("\r\n", "\n")
        if marker in text:
            results.append(f"{name} already patched")
            continue
        if check:
            results.append(f"{name} not patched")
            continue
        if text.count(old) != 1:
            print(f"STOPPED: this edit no longer fits {name}")
            return 1
        backup = path.with_name(path.name + ".cih-orig")
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        patched = text.replace(old, new)
        path.write_bytes((patched.replace("\n", "\r\n") if crlf else patched).encode("utf-8"))
        results.append(f"patched {name}")
    if not check:
        shutil.copyfile(HERE / "trailer" / "cihtrailer.py", root / "lib" / "cihtrailer.py")
        results.insert(0, "cihtrailer.py installed")
    print("; ".join(results))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
