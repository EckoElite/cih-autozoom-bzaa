# Progress: analyzer thread

Newest first. Shared history before 2026-11-09 is in PROGRESS.md.

## 2026-11-10: container memory ballooned to 18 GB (0.3.20)

Not a leak. `fit_pattern` searched a starts x lengths x frames grid built as one array, so memory and time grew with the
cube of the ramp's length in frames. Every ramp of the dense pass goes through it (once per moving edge).

| Ramp length (frames) | Peak memory before | Time before | Peak memory after | Time after |
|---|---|---|---|---|
| 60 | 39 MB | 0.3 s | n/a (unchanged path) | n/a |
| 120 | 295 MB | 2.0 s | n/a | n/a |
| 245 (EEAAO 104893) | 2,430 MB | 29 s | 80 MB | 0.7 s |
| 500 | about 19 GB (extrapolated, n cubed) | minutes | 80 MB | 0.4 s |
| 2000 | not run | not run | 80 MB | 0.6 s |

Several workers (the 6 in the container, plus 3 express) each fitting a long ramp at once, plus ffmpeg, reaches 18 GB.
After a kill the file is retried, so it would balloon again at the same ramp until the fixed image runs.

Fix: the grid is evaluated in blocks of 2M elements (`GRID_CHUNK_ELEMENTS`), and the first search is capped at about 160
starts x 160 lengths on at most 96 frames, then narrowed by tenths down to 0.05 frame. For ramps up to 80 frames the
searched grid is identical; against the committed code, 30 of 32 noisy synthetic ramps (up to 110 frames) give the same
model, start and length to 0.11 frame, and 2 ease-in ramps at 110 frames move by 0.1 frame. The real 245-frame ramp gives
the same result as 0.3.19 (245 frames, worst readable error 3.0 px, last frames 0 to 2 px off). New test
`test_fitting_a_long_ramp_takes_bounded_memory_and_time` (245, 700 and 1,500 frames; under 150 MB and 10 s).

Also: the Unraid template gets `--memory=8g --memory-swap=8g` in Extra Parameters (installed containers keep their old
settings until edited), so a runaway stops that container instead of the server. Not verified: that no other path grows
with ramp length (the frames are streamed one at a time; `_fit_edge` and the isotonic fit are linear), and the container's
real peak with 0.3.20.

## 2026-11-10: ramp-tail errors fixed (0.3.19, pushed; CI green, image built, 62b87e4)

Both defects from the check below had causes in `ramps.py`; neither is a measurement problem.

| Defect | Cause (verified by running the production `refine_boundary` on the local file) | Fix |
|---|---|---|
| Ramp 46888 to 46910: last two frames 8 and 16 px short of the new edge | `_strictly_between` makes the pattern curve rise strictly by adding one step (a thousandth of the distance, or a tenth of one over the frame count) per frame. It runs over every frame of the fitted span, and that span can run on past the ramp. 35 flat frames after a 231 px ramp pulled the end back by `35 x 1/59/10 x 231` = 13.7 px. The same sum at the start lifted a ramp that sits still for 170 frames by up to 16 px (found on the 245-frame ramp: fit 56.6 against raw 41 at frame 105058; the container's output showed 8.7 px at its worst frame because its window differed) | The step is capped so the whole nudge stays under 0.25 px: `eps = min(1e-3, 1/(n+1)/10, 0.25/(span_px x (n+1)))`. Still strictly rising, no rounding on storage |
| Ramp 104893 to 105137: last 3 frames (matte stops at 87%, the next shot cuts to the new ratio) dragged toward the new edge, then counted as misreads and filled | `_fit_edge` smooths with a moving average padded by 0 at the start and by the full distance at the end, which assumes the ramp reaches the new edge. A ramp that a cut ends part-way gets its last frames pulled up, so they miss the 2 px tolerance, and up to 3% of frames may miss it and are then called misreads | The padding is the series' own first and last values |

**Tests:** two new tests in `analyzer/tests/test_ramps.py`. Both **fail on the 0.3.18 `ramps.py` and pass on the fix** (checked by running them against a copy of the old file): `test_a_long_wait_after_a_ramp_does_not_hold_its_last_frames_back` (calls `_ramp` with 80 flat frames after a 22-frame ramp; old code: last ramp frame 3.62 px against a true 2.04, new code 2.04) and `test_a_ramp_that_a_cut_ends_part_way_is_not_dragged_toward_the_new_edge` (150-frame ramp stopping at 60%; old code: last 3 frames 2.1, 4.9 and 7.6 px from the raw edge, the last two flagged unreadable).

**Real data (local file, production `refine_boundary`, same windows for old and new):**

| Ramp | Worst readable frame, fit against raw top edge: 0.3.18 | Fix |
|---|---|---|
| 46888 to 46910 (last frame 46910, raw 41) | 49.1 at 46910 (8 px) | 41.2 |
| 104893 to 105137 | 16.2 px (frames 105058 to 105069, fit 56.6 to 57.6 against raw 41 to 54) | 3.0 px, no frame over 3 px |

**Not changed by this fix:** the container's own copy of these ramps. It needs 0.3.19 in the container and Everything Everywhere re-analyzed (a minor version bump does not re-queue titles by itself).

## 2026-11-10: Everything Everywhere timeline from the container (0.3.18, dense pass) checked against the local file

The container is up (`http://10.29.20.200:8765`, 0.3.18, scheduler **paused**, 619 queued, 3,507 done). My note of 2026-11-09 that it was "unreachable" was wrong: `ANALYZER_URL` is empty in `.env`, so I had queried nothing. The status route is `/health` (not `/api/v1/health`).

**Timeline:** 248 segments, 12 ramps, flags `dense var ramp boundary:low_confidence cut_without_scene_change position_uncertain`, DV-P7, 23.976 fps, 8,359.382 s. (The 0.3.3 run had 126 segments and 12 ramps.)

**Check method (verified by running it):** decoded the ramp stretches of the local copy at full vertical resolution (width averaged down to 480), tied every frame to its absolute timestamp with `-copyts` plus `showinfo`, read the first and last row whose mean exceeds black by 12 (10-bit), and compared with the stored fitted edge for the same frame number. This threshold reads the picture edge 1 to 2 px *inside* the true edge, so a mean difference of +1 to +2 px is the method, not an error. Frame numbers are `round(pts * 24000/1001)`; they match the timeline's own `t`.

| Ramp (frames) | n | Mean top diff px | 95th pct | Max | Notes |
|---|---|---|---|---|---|
| 41509 to 41641 | 133 | +1.0 | 2.5 | 3.2 | agrees |
| 45370 to 45395 | 26 | +0.9 | 2.2 | 2.3 | confidence 0.7, bottom mean +5.5 |
| **46888 to 46910** | 23 | +0.5 | 5.7 | **15.9** | **last two frames wrong, see below** |
| 47642 to 47744 | 103 | +1.4 | 2.6 | 3.4 | agrees |
| 60674 to 60677 | 4 | +0.5 | 1.0 | 1.0 | agrees |
| 65139 to 65297 | 159 | +1.3 | 2.5 | 3.0 | agrees |
| 66613 to 66627 | 15 | +1.5 | 3.0 | 3.7 | **the 46:18 ramp the user flagged: exact.** Real picture moves from 66614; plateau from 66628 in both |
| 73560 to 73640 | 81 | +2.0 | 3.3 | 3.7 | agrees |
| **104893 to 105137** | 245 | +0.9 | 2.4 | **8.7** | **last three frames filled, 9 to 11 px off** |
| 128878 to 128901 | 24 | +1.5 | 3.0 | 3.3 | agrees |
| 135190 to 135194 | 5 | +1.0 | 1.0 | 1.0 | agrees |
| 142545 to 142554 | 10 | +1.9 | 3.5 | 4.0 | agrees |

Ten of 12 ramps follow the picture within 4 px on every frame, with start and end frames matching the real motion.

**Two defects (both found by this check, neither fixed yet):**
1. **Ramp 46888 to 46910** (2.39 opening to 1.85, 23 frames, confidence 1.0). The fit runs at a constant 10.5 px/frame, then stops short: fitted top 58.1 and 57.9 at frames 46909 and 46910, while the raw values are 50 and 41 (I measured 52 and 42) and the next segment (from 46911) is at top 40. Those two frames are marked `readable`, yet differ by 8 and 16 px from their own raw readings, which breaks the "fit within 2 px of every readable frame" rule. Effect on screen: for about 83 ms the picture is zoomed for a shape 1.6% smaller than the real one, so about 14 px of overspill beyond the screen edges.
2. **Ramp 104893 to 105137** (245 frames, 10.2 s, confidence 0.927). Frames 105135 to 105137 are `FILLED` (unreadable) and the fill runs toward the next segment's edge (fitted 242.1, 248.1, 253.7) instead of following the real edge (measured 241, 243, 245; raw 240, 242, 243). Up to 8.7 px, same kind of small overspill for 3 frames. The other 6 unreadable frames in this ramp (104919 to 104922, 105048, 105064) did not show up as outliers.

I have not found the cause yet. A local re-run with `analyze_transition` on a 6 s window failed ("no_old_state") because a hand-made window does not carry the production reference edges; the real path (refine and dense) has to be reproduced instead. Candidate causes: the last frames being treated as unreadable or as exceptions (`PATTERN_MAX_PX` 3.5, up to 10% exceptions) and replaced by the pattern or by the fill; the pattern fit's end being pulled to the following segment's edge.

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

