# Progress: analyzer thread

Newest first. Shared history before 2026-11-09 is in PROGRESS.md.

## 2026-11-09: stretch summary (tests, flaky ramp test, container reachability)

| Item | Result | How verified |
|---|---|---|
| Flaky ramp test in `test_refine.py` | **Not reproduced.** 6 runs of all 24 tests in a row passed (126 to 267 s each; the slowest runs overlapped other work on this PC). No fix made. The earlier "flaky" note in the history was about the Shield add-on timing tests, which were already fixed with a `wait_for` helper | Ran `pytest analyzer/tests/test_refine.py` 6 times |
| Rest of the analyzer suite | 406 passed, 1 skipped (380 s) | `pytest analyzer/tests --ignore=test_refine.py` |
| Lint | `ruff check` and `ruff format --check analyzer` clean | Ran both |
| Container reachability | At about this time the analyzer port from `.env` (`ANALYZER_URL`) refused connections (curl code 000 on `/api/v1/health` and `/status`) while the NAS host answered ping. Probably stopped, paused or being updated; I did not touch it | curl and ping |
| EEAAO timeline check | **Not done**: needs the container's stored timeline (unreachable) or a multi-hour local dense run on `Samples/`. Waiting for the user | none |

## 2026-11-09: Love It or List It S10E02 inspected; broken-file wording corrected

| Item | Result | How verified |
|---|---|---|
| S10E02 is genuinely damaged, not a false alarm | Segment header says 1,386,951,306 bytes, disk has 1,385,517,707 (1,433,599 short) | Parsed the EBML header and Segment size on the NAS copy (read-only) |
| It is **not** cut short at the end | Cues (18,212 bytes) and Tags (790 bytes) are intact at the end of the file | Parsed both elements, they end 791 bytes before EOF |
| Where it breaks | Cluster chain is clean for 845 clusters, then breaks after the cluster at 1,294.761 s (21:34.8). Its declared end (byte 692,868,091) is not a cluster; the next valid cluster starts at byte 693,784,796 with timecode 1,300.550 s. So about 5.8 s of picture around 21:35 to 21:40 sits in a damaged stretch (about 917 KB of unreadable bytes) | Walked the cluster chain header by header |
| Symptom in ffmpeg | `ffmpeg -ss 2590` took 23 s and printed EBML errors at byte 692,xxx,xxx and 1,382,024,112 | Ran it, read-only |
| Analyzer behaviour | Already reported as `broken` by the size check (0.3.18); nothing to analyze | Existing scan log |

The user should replace this file with a good copy (or accept a glitch near 21:35).

Change: the message said every short file was "cut short, including the seek index". That is wrong for this file, so it now reads "file is incomplete ... N MB missing, so it was cut short or lost data and cannot be seeked reliably". Tests in `analyzer/tests/test_truncated.py` updated; ruff clean.

