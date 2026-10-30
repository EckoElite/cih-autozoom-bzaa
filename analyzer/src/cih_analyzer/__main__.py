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
    "[--survey-interval SECONDS] [--start-at SECONDS] [--progress-file PATH] [--partial-file PATH] "
    "<file> [<file> ...]"
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


def partial_writer(path: str) -> Callable[[dict], None]:
    """Keep `path` holding the latest provisional timeline, replaced whole so a reader never sees half of it."""
    scratch = path + ".tmp"

    def write(timeline: dict) -> None:
        try:
            with open(scratch, "w", encoding="utf-8") as fh:
                json.dump(timeline, fh, separators=(",", ":"))
            os.replace(scratch, path)
        except OSError:
            pass  # a courtesy, like progress

    return write


def main(argv: list[str]) -> int:
    interval: float | None = None
    survey: float | None = None
    start_at = 0.0
    progress_path: str | None = None
    partial_path: str | None = None
    for flag in ("--sample-interval", "--survey-interval", "--start-at"):
        if flag in argv:
            at = argv.index(flag)
            try:
                value = float(argv[at + 1])
            except (IndexError, ValueError):
                print(USAGE, file=sys.stderr)
                return 2
            argv = argv[:at] + argv[at + 2 :]
            if flag == "--sample-interval":
                interval = value
            elif flag == "--start-at":
                start_at = value
            else:
                survey = value
    for flag in ("--progress-file", "--partial-file"):
        if flag in argv:
            at = argv.index(flag)
            if at + 1 >= len(argv):
                print(USAGE, file=sys.stderr)
                return 2
            if flag == "--progress-file":
                progress_path = argv[at + 1]
            else:
                partial_path = argv[at + 1]
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
                survey_interval_s=survey,
                known_variable_file=os.environ.get("KNOWN_VARIABLE_FILE", ""),
                progress=progress_writer(progress_path) if progress_path else None,
                start_at=start_at,
                on_partial=partial_writer(partial_path) if partial_path else None,
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
