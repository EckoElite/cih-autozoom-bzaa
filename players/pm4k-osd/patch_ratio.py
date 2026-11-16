"""Install the aspect-ratio tag in PM4K's Python: ratio/cihratio.py, and the call that starts it from the movie page.

  - copies ratio/cihratio.py to <add-on>/lib/windows/cihratio.py (always replaced);
  - patches lib/windows/preplay.py: PrePlayWindow.setInfo calls cihratio.start(...) for titles in your library.
  The chip that shows the tag is added to the skin by ratio_chip.py.

usage: python patch_ratio.py <script.plexmod folder> [--check]
  preplay.py keeps its original as preplay.py.cih-orig (once, shared with patch_person.py). The replacement must match
  exactly once, otherwise the script stops. Running it again is harmless.
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD = "        self.populateRatings(self.video, self)\n\n        if not self.fromWatchlist:\n            self.setAudioAndSubtitleInfo()\n"
NEW = (
    "        self.populateRatings(self.video, self)\n\n        if not self.fromWatchlist:\n"
    "            from . import cihratio  # CIH: the analyzer's aspect ratio as a tag\n"
    "            cihratio.start(self, self.video)\n"
    "            self.setAudioAndSubtitleInfo()\n"
)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    root = Path(argv[0])
    path = root / "lib" / "windows" / "preplay.py"
    if not path.is_file():
        print(f"not found: {path}")
        return 2
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    patched = "cihratio.start" in text
    if "--check" in argv[1:]:
        print("patched" if patched else "not patched")
        return 0
    shutil.copyfile(HERE / "ratio" / "cihratio.py", root / "lib" / "windows" / "cihratio.py")
    if patched:
        print("cihratio.py installed; preplay.py already patched")
        return 0
    if text.count(OLD) != 1:
        print("STOPPED: this edit no longer fits preplay.py")
        return 1
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace(OLD, NEW).replace("\n", "\r\n") if crlf else text.replace(OLD, NEW)).encode("utf-8"))
    print("cihratio.py installed; patched preplay.py")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
