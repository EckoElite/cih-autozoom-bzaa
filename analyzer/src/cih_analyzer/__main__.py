"""`python -m cih_analyzer`: inspect files (default) or analyze them into timelines.

python -m cih_analyzer FILE...                  print the probe result or skip reason for each file
python -m cih_analyzer --analyze FILE           print the timeline JSON for one file (reads the file, writes nothing)
python -m cih_analyzer --analyze-envelope FILE  the same inside an envelope with the raw ramp measurements,
                                                which is what the scheduler's worker processes print
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from collections.abc import Callable

from . import __version__
from .analyze import analyze_file
from .skip import inspect_file

USAGE = (
    "usage: python -m cih_analyzer [--analyze | --analyze-envelope] [--sample-interval SECONDS] "
    "[--progress-file PATH] <file> [<file> ...]"
)


def progress_writer(path: str) -> Callable[[str, float], None]:
    """Keep `path` holding the latest `{"phase", "fraction"}`, replaced whole so a reader never sees half of it."""
    scratch = path + ".tmp"

    def write(phase: str, fraction: float) -> None:
        try:
            with open(scratch, "w", encoding="utf-8") as fh:
                json.dump({"phase": phase, "fraction": round(fraction, 4)}, fh)
            os.replace(scratch, path)
        except OSError:
            pass  # progress is a courtesy; a full disk or a vanished folder must not stop the analysis

    return write


def main(argv: list[str]) -> int:
    interval: float | None = None
    progress_path: str | None = None
    if "--sample-interval" in argv:
        at = argv.index("--sample-interval")
        try:
            interval = float(argv[at + 1])
        except (IndexError, ValueError):
            print(USAGE, file=sys.stderr)
            return 2
        argv = argv[:at] + argv[at + 2 :]
    if "--progress-file" in argv:
        at = argv.index("--progress-file")
        if at + 1 >= len(argv):
            print(USAGE, file=sys.stderr)
            return 2
        progress_path = argv[at + 1]
        argv = argv[:at] + argv[at + 2 :]
    mode = argv[0] if argv and argv[0] in ("--analyze", "--analyze-envelope") else None
    paths = argv[1:] if mode else argv
    if not paths or (mode and len(paths) != 1):
        print(USAGE, file=sys.stderr)
        return 2
    if mode:
        try:
            outcome = analyze_file(
                paths[0],
                sample_interval_s=interval,
                progress=progress_writer(progress_path) if progress_path else None,
            )
        except Exception as exc:  # noqa: BLE001 - say what went wrong in one line, with an exit code of its own
            print(f"{type(exc).__name__}: {' '.join(str(exc).split())[:400]}", file=sys.stderr)
            return 3
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
