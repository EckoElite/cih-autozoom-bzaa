"""Install the person page in PM4K's Python and open it from cast, director and writer entries.

  - copies person/person.py to <add-on>/lib/windows/person.py (always replaced);
  - patches lib/windows/mixins/roles.py so that clicking a role whose entry has a Plex person key opens the person page
    instead of the per-section grid; entries without a key keep the old behaviour, except on titles you do not own
    (there the old behaviour cannot work, so nothing happens, as before);
  - patches the movie, show and episode screens (preplay.py, subitems.py, episodes.py): they ignored a click on the cast
    row of a title that is not in your library (opened from the watchlist or from Discover); now the click goes through.
  The skin file for the page (script-cih-person.xml) is written by person/make_person_xml.py.

usage: python patch_person.py <script.plexmod folder> [--check]
  <script.plexmod folder> is a pulled copy of the add-on; each patched file keeps its original as <name>.cih-orig (once).
  Every replacement must match exactly once, otherwise the script stops. Running it again is harmless.
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS = Path("lib") / "windows"

ROLES_OLD = "        mli = self.rolesListControl.getSelectedItem()\n        if not mli:\n            return\n\n        sectionRoles = "
ROLES_NEW = (
    "        mli = self.rolesListControl.getSelectedItem()\n        if not mli:\n            return\n\n"
    "        # CIH: a Plex-style person page (players/pm4k-osd/person) when the entry has a person key\n"
    "        from .. import person\n"
    "        if person.personKey(mli.dataSource):\n"
    "            self.processCommand(opener.handleOpen(person.PersonWindow, role=mli.dataSource))\n"
    "            return\n\n"
    "        if getattr(self, 'fromWatchlist', False):  # CIH: the per-section grid below needs a library title\n"
    "            return\n\n"
    "        sectionRoles = "
)
ROLES_GUARD_OLD = "            return\n\n        sectionRoles = "
ROLES_GUARD_NEW = (
    "            return\n\n"
    "        if getattr(self, 'fromWatchlist', False):  # CIH: the per-section grid below needs a library title\n"
    "            return\n\n"
    "        sectionRoles = "
)

CAST_GUARD_BLOCK = (
    "        elif controlID == self.ROLES_LIST_ID:\n            if self.fromWatchlist:\n                return\n"
    "            if not self.roleClicked():\n                return\n"
)
CAST_BLOCK = "        elif controlID == self.ROLES_LIST_ID:\n            if not self.roleClicked():  # CIH: titles you do not own too\n                return\n"

# file -> list of (old, new)
SIMPLE = {
    "preplay.py": [(CAST_GUARD_BLOCK, CAST_BLOCK)],
    "episodes.py": [
        (CAST_GUARD_BLOCK, CAST_BLOCK),
        (
            "    def roleClicked(self):\n        if self.fromWatchlist:\n            return\n\n        return super(EpisodesWindow, self).roleClicked()\n",
            "    def roleClicked(self):\n        return super(EpisodesWindow, self).roleClicked()  # CIH: titles you do not own too\n",
        ),
    ],
    "subitems.py": [
        (
            "        elif controlID == self.ROLES_LIST_ID:\n            if not self.fromWatchlist:\n                if not self.roleClicked():\n                    return\n",
            CAST_BLOCK,
        ),
    ],
}


def patch_roles(text: str) -> tuple[str, bool]:
    if "person.personKey" in text:
        if "fromWatchlist" in text:
            return text, False
        if text.count(ROLES_GUARD_OLD) != 1:
            raise ValueError("roles.py: the person block is not followed by sectionRoles exactly once")
        return text.replace(ROLES_GUARD_OLD, ROLES_GUARD_NEW), True
    if text.count(ROLES_OLD) != 1:
        raise ValueError("roles.py: roleClicked no longer starts as expected")
    return text.replace(ROLES_OLD, ROLES_NEW), True


def patch_simple(name: str, text: str) -> tuple[str, bool]:
    changed = False
    for old, new in SIMPLE[name]:
        if new in text:
            continue
        if text.count(old) != 1:
            raise ValueError(f"{name}: an edit no longer fits ({old[:50]!r})")
        text = text.replace(old, new)
        changed = True
    return text, changed


def patch_file(path: Path, patcher, check: bool) -> str:
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    new, changed = patcher(text)
    if not changed:
        return "already patched"
    if check:
        return "not patched"
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((new.replace("\n", "\r\n") if crlf else new).encode("utf-8"))
    return "patched"


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    root = Path(argv[0])
    check = "--check" in argv[1:]
    jobs = [("mixins/roles.py", patch_roles)] + [(name, lambda t, n=name: patch_simple(n, t)) for name in SIMPLE]
    summary = []
    try:
        for name, patcher in jobs:
            path = root / WINDOWS / name
            if not path.is_file():
                print(f"not found: {path}")
                return 2
            summary.append(f"{name} {patch_file(path, patcher, check)}")
    except ValueError as err:
        print(f"STOPPED: {err}")
        return 1
    if not check:
        shutil.copyfile(HERE / "person" / "person.py", root / WINDOWS / "person.py")
        summary.insert(0, "person.py installed")
    print("; ".join(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
