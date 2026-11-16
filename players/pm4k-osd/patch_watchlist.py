"""Give the watchlist button feedback: PM4K's watchlist mixin sets the window property wl.feedback.

  - after a successful add or remove: wl.feedback = "added" or "removed" (button_hints.py shows "Added to watchlist" or
    "Removed from watchlist" next to the button);
  - whenever PM4K checks the item's watchlist state (opening the page, coming back to it): wl.feedback is cleared, so the
    button goes back to "In watchlist" or "Add to watchlist".

usage: python patch_watchlist.py <script.plexmod folder> [--check]
  <script.plexmod folder> is a pulled copy of the add-on; lib/windows/mixins/watchlist.py is patched in place and the
  original kept as watchlist.py.cih-orig (once). Every replacement must match exactly once, otherwise the script stops.
"""

import sys
from pathlib import Path

EDITS = [
    (
        '            self.setBoolProperty("is_watchlisted", method == "addToWatchlist")\n',
        '            self.setBoolProperty("is_watchlisted", method == "addToWatchlist")\n'
        '            self.setProperty("wl.feedback", method == "addToWatchlist" and "added" or "removed")  # CIH\n',
    ),
    (
        '            self.setBoolProperty("is_watchlisted", state)\n',
        '            self.setBoolProperty("is_watchlisted", state)\n            self.setProperty("wl.feedback", "")  # CIH\n',
    ),
]


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = Path(argv[0]) / "lib" / "windows" / "mixins" / "watchlist.py"
    if not path.is_file():
        print(f"not found: {path}")
        return 2
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    if "wl.feedback" in text:
        print("already patched")
        return 0
    if "--check" in argv[1:]:
        print("not patched")
        return 0
    for old, new in EDITS:
        if text.count(old) != 1:
            print(f"STOPPED: this edit no longer fits watchlist.py: {old[:60]!r}")
            return 1
        text = text.replace(old, new)
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print("patched watchlist.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
