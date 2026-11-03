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
## Test 2, results so far (2026-11-03; test app `tools/spike/zoomspike-android`, built by CI on branch `spike/zoomspike`)

Method: the app plays a 60 s clip (hard cuts, an eased ramp, a linear ramp, alternating cuts) with `MediaCodec` and moves the picture into the 2.35 band per frame in one of five ways. Frames reaching the screen are read from `dumpsys SurfaceFlinger --latency` of the video layer (three windows per run: first cut, eased ramp, alternating cuts). The `MediaCodec` "frame rendered" callback is **not** used: it skips about 2 frames a second even in the no-zoom baseline. Display 59.94 Hz, so 24p plays as 2- and 3-refresh gaps; a gap of 4 or more refreshes is a skipped frame, a gap of 0 or 1 a frame shown late and followed by a catch-up. API 30 has no public `Transaction.setPosition`/`setScale` (API 33), so modes B and C use `setGeometry` (API 29).

| Mode | How | Light 4K HDR10 clip | Heavy 4K HDR10 clip (82 Mbit/s) |
|---|---|---|---|
| N | no zoom | clean | 4 skipped frames in 3 windows (my decode loop, not the zoom) |
| A | layout resize (Kodi's method) | **1 run with hiccups** (34 of 36 odd gaps within 3 refreshes after a zoom change, against 10 to 19% by chance); **2 later runs clean** | 3 runs clean |
| A2 | A with the surface fixed to the video size | clean (127 zoom changes) | 2 skipped frames in one window, otherwise clean |
| B | `setGeometry` on the SurfaceView's own layer | clean | clean |
| C | `setGeometry` on our own child layer | worse than baseline (about 90 one-refresh gaps in 47 s, one long stall) | not run |

Counts: mode A was run 3 times on the light clip (1 dirty) and 3 times on the heavy clip (0 dirty); A2, B and C once each on the light clip, A2 and B once on the heavy clip.

**What this shows:** a layout resize can cost frames (the one dirty run is tied to the zoom changes), but it did so in only 1 of 6 runs of mode A, so **the app does not reproduce Kodi's loss** (one frame per applied change, steadily, in EEAAO). Kodi's loss therefore comes from something beyond the SurfaceView resize alone. Not shown by this test: that A2 or B would fix Kodi.

**Not yet tested:** zoom landing on the right frame (needs the 240 fps film), HDR10 staying on during B and A2 (needs the projector's signal info), Dolby Vision, a surface buffer size other than the video's (override built, not run).

**Other observation (Kodi's real ramp log, 2026-11-03):** during the ramp Kodi also sends a `Player.OnAVChange` notification about every 125 ms, which PM4K's Python monitor receives; not yet examined.

**Next step (not run):** read the `SurfaceFlinger` presentation record of Kodi's own video layer (`SurfaceView - org.xbmc.kodi/...`) during an EEAAO ramp at stride 1, 2 and with no zoom, to see the loss at the compositor and whether it has the shape of the resize or of something else.

## Test 1b: Kodi's own video layer during the EEAAO ramp (2026-11-03) and the cause found in Kodi's source

Capture: `tools/spike/watch_kodi_sf.py` (read-only; `dumpsys SurfaceFlinger --latency` of `SurfaceView - org.xbmc.kodi/...` every 5 s, plus logcat). Playback: EEAAO from the add-on with `ramp_stride` 2; the Shield's display ran at 23.976 Hz (Kodi switched it), so one frame is one refresh. 1,987 buffers merged, none never shown.

| Stretch | Frames | Gaps of 2 frame periods (a frame missed) | Gaps shorter than half a period |
|---|---|---|---|
| Zoom held, before the ramp (80 to 112 s) | 768 | 0 | 0 |
| **Ramp span (5.42 s, 66 zoom changes)** | about 66 missed | **66, every one exactly 2.0 frame periods** | 0 |
| Zoom held, after the ramp (126 to 140 s) | 337 | 0 | 0 |

Frames shown inside the ramp: 12.2 a second, matching the 240 fps phone clip (stride 2: 12 pictures a second). Logcat in the same session: 66 `XBMCVideoView: Changed` lines in the ramp.

**Cause (Kodi 21 Omega source, `DVDVideoCodecAndroidMediaCodec.cpp`, `CMediaCodecVideoBuffer::RenderUpdate`):** each time the destination rectangle differs from the video view's current rectangle, Kodi calls `setSurfaceRect` (an asynchronous layout change on the UI thread) and, in the same call, releases that frame **without rendering it** (`releaseOutputBuffer(render=false)`), with a code comment saying the view change is asynchronous so the frame is skipped. So Kodi drops exactly one frame by design at every geometry change. This explains: one missed frame per applied zoom change; stride 2 giving 12 pictures a second and stride 4 giving 18; stride 12 giving 22; clean playback when the zoom is held; and why the spike app, which does not skip a frame at a rectangle change, did not reproduce the loss (the earlier suspicion that the layout resize itself costs the frame was wrong, apart from one unexplained dirty run).

**What it means for a fix:** the loss is a deliberate skip in Kodi's native renderer, not an Android limit. A Kodi build that renders the frame instead of skipping it removes the loss. A better version (not needed to remove the loss) would apply the new rectangle in step with the frame, with a `SurfaceControl` transaction (`setGeometry`, API 29), which also shortens the 140 ms delay between the add-on's call and the resize. The spike showed that mode B (that transaction on the SurfaceView's layer) presents frames cleanly with HDR10 clips on this Shield; HDR staying on and the zoom landing on the right frame are still to be confirmed by the user.
