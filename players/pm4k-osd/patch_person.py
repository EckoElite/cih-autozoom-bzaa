"""Install the person page in PM4K's Python and open it from cast, director and writer entries.

  - copies person/person.py to <add-on>/lib/windows/person.py (always replaced);
  - patches lib/windows/mixins/roles.py so that clicking a role whose entry has a Plex person key (`tagKey`) opens the
    person page instead of the per-section grid; entries without a key keep the old behaviour.
  The skin file for the page (script-cih-person.xml) is written by person/make_person_xml.py.

usage: python patch_person.py <script.plexmod folder> [--check]
  <script.plexmod folder> is a pulled copy of the add-on; roles.py is patched in place and the original kept as
  roles.py.cih-orig (once). Every replacement must match exactly once, otherwise the script stops.
"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

EDITS = [
    (
        "        mli = self.rolesListControl.getSelectedItem()\n        if not mli:\n            return\n\n        sectionRoles = ",
        "        mli = self.rolesListControl.getSelectedItem()\n        if not mli:\n            return\n\n"
        "        # CIH: a Plex-style person page (players/pm4k-osd/person) when the entry has a person key\n"
        "        from .. import person\n"
        "        if person.personKey(mli.dataSource):\n"
        "            self.processCommand(opener.handleOpen(person.PersonWindow, role=mli.dataSource))\n"
        "            return\n\n"
        "        sectionRoles = ",
    ),
]


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    root = Path(argv[0])
    path = root / "lib" / "windows" / "mixins" / "roles.py"
    if not path.is_file():
        print(f"not found: {path}")
        return 2
    text = path.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    patched = "person.personKey" in text
    if "--check" in argv[1:]:
        print("patched" if patched else "not patched")
        return 0
    shutil.copyfile(HERE / "person" / "person.py", root / "lib" / "windows" / "person.py")
    if patched:
        print("person.py installed; roles.py already patched")
        return 0
    for old, new in EDITS:
        if text.count(old) != 1:
            print(f"STOPPED: this edit no longer fits roles.py: {old[:60]!r}")
            return 1
        text = text.replace(old, new)
    backup = path.with_name(path.name + ".cih-orig")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))
    print("person.py installed; patched roles.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
