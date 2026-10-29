"""Path mapping (spec R9): the same file has several names, and any of them must find its timeline.

A film is `/movies/X` to Plex, `/mnt/user/media/Movies/4K Movies/X` on the Unraid host, `\\\\NAS\\share\\...\\X` over
SMB and `/media/movies/X` inside this container. A rule lists those equivalent roots, container path first. A path is
rewritten by the longest alias root it starts with. Paths are case-sensitive (Unraid is), and a root only matches at a
path boundary (`/movies` does not match `/movies2/x`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_SMB_URL = re.compile(r"^smb://", re.IGNORECASE)


def normalize(path: str) -> str:
    """Forward slashes, `smb://host/x` as `//host/x`, no trailing slash. Case is kept."""
    text = path.strip().replace("\\", "/")
    text = _SMB_URL.sub("//", text)
    head = "//" if text.startswith("//") else ""
    body = re.sub(r"/{2,}", "/", text[len(head) :])
    if len(body) > 1:
        body = body.rstrip("/")
    return head + body


def _under(path: str, root: str) -> str | None:
    """The part of `path` below `root`, "" if equal, None if it is not inside it."""
    if path == root:
        return ""
    if root == "/":
        return path[1:] if path.startswith("/") else None
    if path.startswith(root + "/"):
        return path[len(root) + 1 :]
    return None


class PathMap:
    def __init__(self, rules: Iterable[Iterable[str]] = ()):
        self.rules = [[normalize(alias) for alias in rule] for rule in rules]
        self._aliases = sorted(
            ((alias, index) for index, rule in enumerate(self.rules) for alias in rule),
            key=lambda item: -len(item[0]),
        )

    def to_local(self, path: str) -> str:
        """The container path for `path`; a path no rule covers is returned normalized and otherwise unchanged."""
        norm = normalize(path)
        for alias, index in self._aliases:
            rest = _under(norm, alias)
            if rest is not None:
                local = self.rules[index][0]
                return local if not rest else (local.rstrip("/") + "/" + rest)
        return norm

    def aliases(self, local_path: str) -> list[str]:
        """Every name of a container path, itself first."""
        norm = normalize(local_path)
        for rule in self.rules:
            rest = _under(norm, rule[0])
            if rest is not None:
                names = [norm]
                for alias in rule[1:]:
                    names.append(alias if not rest else alias.rstrip("/") + "/" + rest)
                return names
        return [norm]

    def covers(self, path: str) -> bool:
        norm = normalize(path)
        return any(_under(norm, alias) is not None for alias, _ in self._aliases)
