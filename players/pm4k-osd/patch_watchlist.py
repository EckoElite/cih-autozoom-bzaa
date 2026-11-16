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

FEEDBACK = '            self.setProperty("wl.feedback", method == "addToWatchlist" and "added" or "removed")  # CIH\n'
STATE_LINE = '            self.is_watchlisted = method == "addToWatchlist"\n'
BOOL_LINE = '            self.setBoolProperty("is_watchlisted", method == "addToWatchlist")\n'
CLEAR = '            self.setProperty("wl.feedback", "")  # CIH\n'
CHECKED = '            self.setBoolProperty("is_watchlisted", state)\n'


def patch_text(text: str) -> tuple[str, bool]:
    """returns the patched text and whether anything changed; raises ValueError when PM4K's code no longer fits"""
    before = text
    # first version of this patch put the feedback after the state change, which flashed the old message for a moment
    text = text.replace(BOOL_LINE + FEEDBACK, BOOL_LINE)
    if FEEDBACK + STATE_LINE not in text:
        if text.count(STATE_LINE) != 1:
            raise ValueError("self.is_watchlisted = method ... not found exactly once")
        text = text.replace(STATE_LINE, FEEDBACK + STATE_LINE)
    if CLEAR not in text:
        if text.count(CHECKED) != 1:
            raise ValueError("the state check callback was not found exactly once")
        text = text.replace(CHECKED, CHECKED + CLEAR)
    return text, text != before


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
    if "--check" in argv[1:]:
        try:
            print("not patched" if patch_text(text)[1] else "patched")
        except ValueError as err:
            print(f"does not fit: {err}")
        return 0
    try:
        text, changed = patch_text(text)
    except ValueError as err:
        print(f"STOPPED: this edit no longer fits watchlist.py: {err}")
        return 1
    if not changed:
        print("already patched")
        return 0
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print("patched watchlist.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
