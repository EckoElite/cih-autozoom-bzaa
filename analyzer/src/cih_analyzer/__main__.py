"""`python -m cih_analyzer`: inspect files (default) or analyze them into timelines.

python -m cih_analyzer FILE...            print the probe result or skip reason for each file
python -m cih_analyzer --analyze FILE     print the timeline JSON for one file (reads the file, writes nothing)
"""

from __future__ import annotations

import dataclasses
import json
import sys

from .analyze import analyze_file
from .skip import inspect_file


def main(argv: list[str]) -> int:
    analyze = bool(argv) and argv[0] == "--analyze"
    paths = argv[1:] if analyze else argv
    if not paths or (analyze and len(paths) != 1):
        print("usage: python -m cih_analyzer [--analyze] <file> [<file> ...]", file=sys.stderr)
        return 2
    if analyze:
        outcome = analyze_file(paths[0])
        if outcome.timeline is None:
            print(json.dumps({"path": paths[0], "skip": outcome.skip}, ensure_ascii=False))
            return 1
        print(outcome.timeline.to_json(indent=1))
        return 0
    for path in paths:
        result = inspect_file(path)
        payload = {"skip": result.skip} if result.skip else dataclasses.asdict(result.info)
        print(json.dumps({"path": path, **payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
