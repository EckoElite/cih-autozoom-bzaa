"""Titles known to change ratio, which skip the survey and get the detailed pass.

The survey (survey.py) is cheap and catches a film that changes often. Its weak spot is a film with only a few short
shots at another ratio, which is exactly what IMAX-camera films are. A title on this list is a hint, not a verdict: it
costs the detailed pass, and nothing is concluded from the list. A file that turns out to be constant is analyzed as
usual, just without the shortcut.

The bundled list (`data/known_variable.txt`) was put together from Wikipedia's "List of IMAX films" (films shot partly
on IMAX film cameras) and from widely documented variable-ratio releases. The ratio a given *file* has depends on which
release it came from, so the list is wide on purpose. More titles can be added in a text file named by
`KNOWN_VARIABLE_FILE` (one `Title (Year)` per line).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

BUNDLED = Path(__file__).parent / "data" / "known_variable.txt"
_NAME = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:18|19|20)\d\d)\)")


def normalize(title: str) -> str:
    """A title reduced to what must match: lower case, `&` as `and`, no leading article, letters and digits only."""
    text = title.lower().replace("&", " and ")
    text = re.sub(r"^(the|a|an)\s+", "", text.strip())
    return re.sub(r"[^a-z0-9]+", "", text)


def parse_title(name: str) -> tuple[str, int] | None:
    """`(normalized title, year)` from a name like `Dunkirk (2017) 4k HDR.mkv` or `Dunkirk (2017)`."""
    found = _NAME.match(name)
    return (normalize(found["title"]), int(found["year"])) if found else None


def load(path: Path | str | None = None) -> frozenset[tuple[str, int]]:
    """The titles in one list file; lines without a `(year)` and lines starting with `#` are ignored."""
    if path is None:
        path = BUNDLED
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(
        t for line in lines if not line.lstrip().startswith("#") and (t := parse_title(line.strip()))
    )


@lru_cache(maxsize=4)
def known_titles(extra: str = "") -> frozenset[tuple[str, int]]:
    return load() | (load(extra) if extra else frozenset())


def is_known_variable(path: str, extra: str = "") -> bool:
    """True if the file's name, or the name of the folder it is in, is a listed title with the same year."""
    p = Path(path)
    titles = known_titles(extra)
    return any((t := parse_title(name)) is not None and t in titles for name in (p.name, p.parent.name))
