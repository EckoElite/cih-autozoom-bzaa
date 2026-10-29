"""`python -m cih_analyzer`: inspect files (default) or analyze them into timelines.

python -m cih_analyzer FILE...                  print the probe result or skip reason for each file
python -m cih_analyzer --analyze FILE           print the timeline JSON for one file (reads the file, writes nothing)
python -m cih_analyzer --analyze-envelope FILE  the same inside an envelope with the raw ramp measurements,
                                                which is what the scheduler's worker processes print
"""

from __future__ import annotations

import dataclasses
import json
import sys

from . import __version__
from .analyze import analyze_file
from .skip import inspect_file

USAGE = "usage: python -m cih_analyzer [--analyze | --analyze-envelope] [--sample-interval SECONDS] <file> [<file> ...]"


def main(argv: list[str]) -> int:
    interval: float | None = None
    if "--sample-interval" in argv:
        at = argv.index("--sample-interval")
        try:
            interval = float(argv[at + 1])
        except (IndexError, ValueError):
            print(USAGE, file=sys.stderr)
            return 2
        argv = argv[:at] + argv[at + 2 :]
    mode = argv[0] if argv and argv[0] in ("--analyze", "--analyze-envelope") else None
    paths = argv[1:] if mode else argv
    if not paths or (mode and len(paths) != 1):
        print(USAGE, file=sys.stderr)
        return 2
    if mode:
        outcome = analyze_file(paths[0], sample_interval_s=interval)
        if outcome.timeline is None:
            print(json.dumps({"path": paths[0], "skip": outcome.skip}, ensure_ascii=False))
            return 1
        if mode == "--analyze":
            print(outcome.timeline.to_json(indent=1))
        else:
            print(
                json.dumps(
                    {
                        "version": __version__,
                        "timeline": json.loads(outcome.timeline.to_json()),
                        "ramp_raw": list(outcome.ramp_raw),
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    for path in paths:
        result = inspect_file(path)
        payload = {"skip": result.skip} if result.skip else dataclasses.asdict(result.info)
        print(json.dumps({"path": path, **payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
