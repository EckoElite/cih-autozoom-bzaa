# Zoom frame-skip spike: report

Spec for the spike: `ZOOM_SPIKE.md` (user's file, reviewed 2026-11-03; two corrections needed before Test 2: `Transaction.setPosition` and `setScale` are API 33+, so on the Shield's API 30 use `setGeometry` (API 29) or reflection; add a mode with `setFixedSize` and a heavy 4K control clip).

## Test 1, first result (2026-11-03, real file, no code changed)

Played the *Everything Everywhere All at Once* ramp (film frames 41508 to 41642) with the add-on at `ramp_stride` 2 while capturing Android `logcat` from the Shield (`adb logcat -v threadtime`).

| Measure | Result |
|---|---|
| Add-on zoom updates in the ramp | 69 (66 changed the zoom; the last 3 repeat 1.000) |
| `XBMCVideoView: Changed` lines in the ramp | 66, one per changed zoom, sizes 1510x849 to 1920x1080 |
| `Created` / `Destroyed` during the ramp | none (the surface is resized, not recreated) |
| Gap between `Changed` lines | median 83.0 ms (82 to 84), i.e. every 2 film frames |
| Delay from the add-on's call to `Changed` | median 140.4 ms (139.7 to 141.3), very steady |

Each zoom update makes Kodi resize its video `SurfaceView` (`setSurfaceRect`: new margins, `requestLayout`), which reports a new surface size. The count matches the loss we measured in the 240 fps clips: stride 2 shows 12 pictures a second (24 minus 12 changes), stride 4 shows 18 (24 minus 6). **Verdict: hypothesis supported (every change is one surface resize; the frame loss follows the resize count). Not yet shown directly:** that a presented frame is missing at each `Changed` (needs `dumpsys SurfaceFlinger --latency` on the video layer during a ramp).

Raw capture: the `logcat_ramp.txt` in the session scratchpad (not in the repo).