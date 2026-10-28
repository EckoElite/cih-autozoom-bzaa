"""`python -m cih_analyzer <file>...`: print the probe result or skip reason for each file."""

from __future__ import annotations

import dataclasses
import json
import sys

from .skip import inspect_file


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m cih_analyzer <file> [<file> ...]", file=sys.stderr)
        return 2
    for path in argv:
        result = inspect_file(path)
        payload = {"skip": result.skip} if result.skip else dataclasses.asdict(result.info)
        print(json.dumps({"path": path, **payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
