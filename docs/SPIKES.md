# SPIKES (Phase 2): decision matrix ready for the route-choice GATE

Spike A (Kodi's internal player) was **measured** on the Shield. Spikes B, C and D were **read from source** (plus a few read-only `adb` queries); none has been run on the Shield. The matrix marks every cell as *measured* (M), *from source* (S) or *untested* (U). The evidence for each cell is in the sections below.

## Decision matrix

Routes: **A** = Kodi's internal player with the in-Kodi add-on. **B** = Plezy's built-in mpv (fork needed for timed zoom). **C** = Plezy hands the video to Kodi (Kodi's player and add-on do the zoom). **D** = mpv-android as Plezy's external player (Lua script).

| Row | A: Kodi + add-on | B: Plezy mpv | C: Plezy to Kodi | D: mpv-android |
|---|---|---|---|---|
| HDR10 kept | **Yes (M, indirect)**: Kodi reports `hdr10` with the zoom changing; the projector shows no HDR overlay, and the user saw its HDR10 picture mode | Likely (S): the plane keeps the decoder's HDR dataspace and zoom does not touch the decoder (U) | As A | **Probably not (S)**: GL output, no HDR code (U) |
| Dolby Vision kept | **U** | U (DV profile 5 takes a different path in the code) | As A: U | Probably not (S, U) |
| Live zoom works | **Yes (M)** | Static only (S); timed zoom needs a fork | Yes (as A) | Plausible (S) via Lua; scripts and timeline delivery unproven (U) |
| Geometry accurate | **Yes (M)** for SDR and HDR10: all seven ratio frames fit; residual about 4 rows from the band measurement | Unknown (U): view sizes are integers; **no vertical shift** (S) | As A | Expected (renderer transform) (U) |
| Zoom latency and jitter, in frames | **M**: fixed delay 7.7 frames; with a lead of 316 ms the spread is 0.23 frames, **25 of 25 cuts within 1 frame, 23 of 25 exact** (PC-driven: 0.93 frames spread) | Not measured; relayout on the main thread, so I expect it not frame-locked (S) | As A | Not measured |
| Visible glitch on change | None seen: no black flash in three frame-by-frame transitions at 60 fps; no skipped frame near 7 keyframes at 240 fps (M, limited) | Unknown; a resized SurfaceView may flash or drop frames (U, risk) | As A | Unknown (U) |
| Ramp smoothness (per-frame updates) | **M, partly**: eased ramp tracks the commanded curve at 0 frames lag, mean error 0.0037 zoom, no reversals; first 54 frames of the linear ramp the same. Near-black ramp and the rest of the linear ramp unmeasured; one unexplained 1-frame excursion at frame 1860 | Unknown; per-frame relayouts are the main risk (U) | As A | Unknown (U) |
| Timed changes feasible | **Yes (M)**, scheduled against Kodi's player clock | Only with a fork (S) | Yes (as A) | Yes in principle (S); script loading and timeline fetch unproven |
| Seek, resume, chapter hooks | Add-on handles the Kodi callbacks; tested offline only, **not yet on the Shield** (U). After a seek the zoom lags about 7.7 frames (not measured) | Position stream and seek exist in Dart (S) | As A, but **resume position and Plex progress are lost**: Kodi ignores Plezy's position and returns no result (S) | Lua events (S); Plezy resume and progress sync **work** (S) |
| Subtitle control (R13, P2) | Kodi subtitle position and skin settings; not tested | Subtitle surface stays full-size, so subtitles land outside the band unless changed (S) | As A | mpv subtitle options (U) |
| Refresh-rate switching | Kodi's own setting; add-on does not touch it; not tested with zoom (U) | Frame-rate matching exists in the code (S, U) | As A | **Absent in the code (S)** |
| Audio passthrough | Kodi's own; not tested with the add-on (U) | Setting exists (S, U) | As A | Only via mpv.conf (S, U) |
| Fits the Plezy workflow | No: playback starts from Kodi's UI (PM4K is already installed there) | **Yes** | Partly: launch from Plezy, lose resume and Plex progress unless the add-on re-implements them | **Yes** (resume and sync work) |
| Build effort | Prototype exists; production needs the analyzer lookup: low to medium | **High**: fork, Flutter and Android toolchain; a frame-locked version means rebuilding the native player | Medium: add-on grows a Plex client for resume and progress | Low to medium if scripts load, otherwise high |
| Maintenance risk | **Low**: Kodi's add-on API is stable | **High**: 223 upstream commits in 30 days, in the files we would change | Low to medium | Low for a script, high for a custom build |

## Recommendation

**Route A: Kodi's internal player with the in-Kodi add-on.** It is the only route with measured evidence for the hard requirements: live timed zoom on the Shield, HDR10 shown while zoomed, 25 of 25 hard cuts within one frame, and ramps followed frame by frame. The other three routes are source readings, and each has a gap that the evidence points to as serious:

- **B** needs a fork and its zoom is a view resize that I expect not to be frame-locked. It has high build and upkeep cost for a result I cannot yet predict.
- **D** probably loses HDR10 and Dolby Vision, which is the project's first constraint. One short test (install mpv-android, set `video-zoom`, play the HDR10 video) would confirm or clear that, but I do not recommend spending it unless you want Plezy as the front end.
- **C** is route A with Plezy as the launcher. It works, but costs resume and Plex progress.

**How playback starts under route A** is your call at the gate, because it decides what the add-on must do:
1. **PM4K** (`script.plexmod`, already in Kodi): plays Plex items in Kodi with Plex resume and progress built in. I have not read its source; the first Phase 5 step would be to check what URL it hands Kodi's player so the add-on can find the Plex part ID.
2. **Plezy hand-off (route C):** keeps your Plezy workflow; the add-on would need to fetch the resume position from Plex and report progress back itself.

**What you are accepting if you choose route A:**
- **R2 accepted at "at most 1 frame", not the target of 0.** In 25 cuts, 2 landed one frame late and none early. The spec asks for your sign-off when the best reachable figure is above 0.
- **A constant 7.7-frame delay** from command to picture. It is cancelled by sending each zoom early (`lead_ms` 316 by default), which works only because the timeline is known in advance. After a seek or at the start of playback, the right zoom takes about 0.3 s to appear; how many frames show the wrong zoom is unmeasured (Kodi also remembers the zoom per file).
- **Open items to carry into the Phase 3 design audit:** Dolby Vision under zoom (untested); HDR at the projector is user-reported and indirect; the lead value comes from one file at one refresh rate; ramp coverage is partial (near-black ramp, rest of the linear ramp, the frame-1860 excursion); seek, chapter and resume handling is tested offline only; subtitles, refresh-rate switching and audio passthrough with the add-on are untested; the Kodi front end (PM4K or hand-off) is undecided.

**Optional on-device checks that would firm this up** (each needs your go-ahead; none is required for the gate): a Dolby Vision file played under zoom on route A; re-filming the full boundary video with the recording kept running at least 30 s after the end; the mpv-android HDR check; the Kodi intent test with the PC test video.

**GATE:** please choose the route (A, B, C or D) and, for route A, the front end (PM4K or Plezy hand-off).

## Spike A: Kodi internal player (Kodi 21.2 on the Shield, MediaCodec on)

Test material was streamed to Kodi over HTTP from the PC (`tools/range_server.py`), so nothing was written to the Shield.

### Verified by running the calls (2026-10-28)

| Question | Result |
|---|---|
| Is a numeric zoom accepted? | Yes. `Player.SetViewMode` with `{"viewmode": {"zoom": z, "verticalshift": v}}`. `JSONRPC.Introspect` describes it as "Zoom where 1.0 means 100%", vertical shift "1.0 means shift to bottom" |
| Precision | Stored as a 32-bit float (0.7565 reads back as 0.7565000057) |
| Range | Zoom is clamped to 0.5 to 2.0 (0.4 gave 0.5, 2.1 gave 2.0) |
| Negative vertical shift | Accepted (-0.05 read back as -0.05) |
| **Integer values ignored** | `"zoom":1` and `"verticalshift":0` return `OK` but change nothing. Only decimals (`1.0`, `0.0`) apply. PowerShell's `ConvertTo-Json` turns `1.0` into `1`; Python's `json.dumps(1.0)` gives `1.0`, so the add-on must always send floats |
| Omitted keys | Keep their previous value (`{"zoom":0.8}` left the shift as it was) |
| Reset | `{"viewmode": "normal"}` returns zoom 1.0, shift 0.0, mode `normal` |
| API call time | A `SetViewMode` call returns in about 7 to 25 ms; the new value reads back on the first poll (about 23 ms). A plain `GetViewMode` round trip averages about 17 ms. **This says nothing about when the picture changes on the screen** (needs the slow-motion test) |
| Absolute seek | Works with `{"value": {"time": {"hours":0,"minutes":1,"seconds":40,"milliseconds":0}}}`, landing within about a second. `{"value": {"seconds": 100}}` is **relative**, not absolute |
| Zoom geometry on screen, SDR, all seven ratios (user's phone photos) | At the section 5.3 zoom values every ratio frame has its top and bottom borders on the screen's top and bottom edges, and its side borders inset by roughly the expected amount (1.33 about 56% of the screen width against 56.7% expected; 1.90 about 81% against 80.9%; 2.00 about 84% against 85.1%). The 2.35 frame (zoom 1.0) shows its border on all four edges; the 2.39 frame (zoom 1.0) shows a thin black bar inside the top and bottom, as expected. A close-up of the 1.33 frame shows the top border about 4 to 5 rows inside the screen's lit top edge and the bottom border about 3 to 4 rows inside, which is what the measured band (rows 258 to 1901) against the design band (263 to 1897) predicts. Photo-to-photo variation in side widths is about 2%, from the camera angle |

A first pass over 1.33, 1.78 and 1.85 showed an unzoomed picture; the photos were saved by ShareX after the run, and my script applied each zoom 1 s after its segment started, so they were most likely taken in that first second. A re-run that set the zoom first (Kodi read back 0.7565, 0.7565 and 0.7872) and fresh photos showed all three zoomed correctly.

### HDR10 run (2026-10-28; 60 fps phone video of the screen, 10 s per ratio)

| Question | Result |
|---|---|
| Does the zoom apply to an HDR10 file? | Yes. All seven ratio frames show the same geometry as in SDR (contact sheet and frames from the video): top and bottom borders on the screen's top and bottom edges for 1.33 to 2.00, the 2.35 border on all four edges |
| What does Kodi report? | `VideoPlayer.HdrType` = `hdr10`, resolution 4K, codec hevc, during every segment, with the zoom changing. This is Kodi's reading of the stream, not what the projector received |
| Is HDR active at the projector? | **User-reported, indirect.** The projector shows no overlay or signal-info popup for HDR. The user reports that its picture settings page is set up for HDR10 while this file plays, and takes that to mean HDR10 is active. That shows the projector switched to its HDR10 picture mode; it does not show that HDR stayed on through each zoom change. Weak corroboration: the HDR10 video looks different from the SDR photos (brighter, bluer; camera exposure also differs) |
| Black flash at a zoom change? | None seen in the three transitions examined frame by frame (19.7 s, 39.5 s, 49.9 s of the video). Each change shows one phone frame that blends the old and new picture (double-exposed text), then the new picture. A 60 fps phone cannot tell whether that blend is real or comes from the projector's processing or the camera |
| Other glitches | Single-frame artifacts at about 20.5 s (a horizontal colour band) and a few in the first 1.5 s, not tied to a zoom change. Cause unknown |

### Frame-accuracy test, Spike A via JSON-RPC from the PC (2026-10-28; 240 fps capture of the boundary video)

**Set-up.** `boundary_hdr10_23976.mkv` played from the PC over HTTP; `tools/spike_a/drive_boundary.py` sent each cut's new zoom `lead` seconds before the cut (leads 0.10 to 0.28 s in seven values, shuffled, four cuts each) and a new zoom for every frame during the ramps (lead 0.20 s). In a rehearsal and the filmed run all 244 commands were accepted, went out within about 2 ms of their scheduled time on Kodi's reported player clock, with a median round trip of about 3 ms (ramp commands 41.0 to 42.6 ms apart, target 41.7). The phone (1080p, 240 fps, steady, whole screen in frame) was analysed with `tools/spike_a/decode_capture.py`, `analyze_boundary.py` and `analyze_ramps.py`: the burned-in 14-bit code gives the content frame number (readable only while the zoom is 1.0, because the code plate shrinks with the zoom), and the width of the dark code plate gives the displayed zoom. The capture is consistent with itself: 10.008 camera frames per content frame against 240 / 23.976 = 10.010.

**Hard cuts: 25 measurable of 28** (cut 1776 is 2.39 to 2.39, so no zoom change exists; cuts 2112 and 2280 fall in the last stretch of the phone clip, which the phone stores at 8 times speed (see "Retraction" below), so they cannot be timed from these clips; two early cuts have wider uncertainty).

| Result | Value |
|---|---|
| Delay from command (timed on the player clock) to the picture changing, measured against the displayed content | mean **7.6 frames (0.316 s)**, standard deviation **0.93 frames (0.039 s)**, range 5.9 to 9.1 frames |
| Depends on the lead I used? | No. Mean delay by lead 0.10 / 0.13 / 0.16 / 0.19 / 0.22 / 0.25 / 0.28 s: 0.329 / 0.290 / 0.323 / 0.340 / 0.330 / 0.304 / 0.311 s; slope of delay against lead +0.05 |
| Depends on direction? | No. Zoom shrinking (to 1.90 or 1.78) 0.318 s; zoom growing (to 2.39) 0.314 s |
| Frame-locked? | **Yes.** The zoom change lands on a content-frame boundary: its offset from the content change is within 0.1 frame of a whole number for the median cut (largest 0.45; measurement resolution about 0.1) |
| What a single fixed lead would have achieved | With the lead set to the mean delay (about 0.316 s): the zoom lands on exactly the right frame on 9 of 25 cuts, within 1 frame on 23 of 25, worst case 2 frames. A lead of 0.31 to 0.33 s gives 8 to 11 exact and 21 to 23 within 1 frame |

Against R2 (target 0 frames, acceptance at most 1 frame): **not met by this route as tested.** A fixed lead gets about 90% of cuts within a frame, but about 10% miss by 2 frames and fewer than half are exactly right. This supersedes the rough 0.2 s estimate below, which came from a cruder timing script and four transitions.

Caveats: one capture; single file and refresh rate; the delay includes any constant offset between Kodi's reported clock and the displayed frame; the commands came from the PC over the network (an add-on inside Kodi may have less jitter, which is the next thing to test); the spread of about 1 frame may come from Kodi's player-clock readout, from its render pipeline, or from both, which this test cannot separate.

**Ramps (per-frame commands).**
- Eased ramp: the displayed zoom follows the commanded curve about **5.7 content frames after the frame each value was meant for** (per-frame lag mean 5.6, standard deviation 0.8, range 4 to 7). The commands were sent 4.8 frames early, so that is about 10 frames from command to picture, against 7.6 frames for the cuts. **The two do not agree and I do not know why**; candidates are back-to-back commands queueing inside Kodi, or an error in my width-to-zoom calibration (about 0.01 in zoom is about 2 frames on this ramp).
- No upward reversals; about 15 of roughly 57 consecutive-frame steps showed no measurable change (threshold about a quarter pixel). Some of those are expected where the eased curve is flat, so this overstates stalls, but a lag that swings between 4 and 7 frames means uneven stepping, which R2 forbids.
- Linear ramp: **only the first part measurable.** It starts at frame 1992, which is where the phone clip's 8-times-speed tail begins (see "Retraction" below). My first reading here, that the video froze and jumped, was wrong: it was the phone's recording, not Kodi. A smaller hold at the start of playback (frame 4 held for about 0.7 s, then a jump to about frame 22 or 23) is a separate observation, not yet explained; an HDMI/HDR mode switch while the video starts is the likeliest cause (untested).
- Near-black ramp: **not measurable** (the code is unreadable on the dark picture).

### Kodi remembers the view per file (tested 2026-10-28)

With the add-on disabled, playing the boundary video again started it at the zoom it had been left at (0.7565, from a stop at 10 s), from the first reading at 0.3 s. A different, previously played file started at its own last view (zoom 1.0) even though the previous file had left Kodi at 0.7565. So the view is saved and restored **per file**. Consequences for the add-on: a title can begin at the wrong zoom, and a zoom left over from the last session shows until the add-on corrects it; zoom commands sent after playback has stopped have no effect (Kodi reads back the old zoom), so the reset at the end cannot undo it. The add-on now corrects the zoom in `onPlayBackStarted`, the earliest callback, and again when the scheduler starts. How many frames show the wrong zoom at the start is not yet measured. Not tested: whether this saved state is written to Kodi's database or kept in memory only, and whether turning off per-file video settings in Kodi avoids it.

### In-Kodi add-on prototype (built and installed on the Shield 2026-10-28)

Installed with `tools/spike_a/install_addon.ps1 -Install` (5 files, Kodi restarted), found by Kodi as v0.1.0 but left disabled, then enabled with `Addons.SetAddonEnabled`. A short run (first 11 s of the boundary video, PC sending playback only) showed the service loading the timeline, setting the starting zoom and sending the cuts at frames 48, 120, 192 and 264 within 0.04 ms of target on its own clock, each `executeJSONRPC` call taking about 0.4 ms (against about 3 ms from the PC over the network). That clock is the add-on's own estimate of Kodi's player clock, so it says nothing yet about when the picture changes; that needs the filmed run. Kodi's log also shows an unrelated, pre-existing error from the `script.toolbox` add-on (it calls `xbmc.translatePath`, removed in Kodi 21).

`players/kodi/service.cih.zoom/`: a Kodi 21 service add-on (Python 3, `xbmc.python` 3.0.1, settings in the Kodi 19+ format). `zoomcore.py` holds the logic with no Kodi imports; `service.py` is the Kodi glue (`xbmc.Player` callbacks: `onAVStarted`, `onPlayBackResumed`, `onPlayBackSeek`, `onPlayBackPaused`, `onPlayBackStopped`, `onPlayBackEnded`). It estimates Kodi's player clock from the median of many `getTime()` readings against a monotonic clock, sends each zoom `lead_ms` (default 316, the measured delay) before its frame by calling `Player.SetViewMode` through `xbmc.executeJSONRPC` (floats only), drops overdue events instead of replaying a backlog, sets the right zoom on start, resume and seek, and resets to 1.0 at the end. Prototype scope: it only acts on URLs matching `/boundary_*.mkv` and fetches `boundary_x.timeline.json` from the same location; the analyzer lookup comes in Phase 5. Every command is logged to `events.jsonl` in the add-on's profile folder.

Offline tests (`python -m unittest discover -s players/kodi/tests -v`, 12 tests, all pass): timeline and zoom-at-frame logic, floats in the request, scheduler accuracy against a simulated noisy clock (worst send error 1.9 ms with a player readout noisy to 15 ms; 7.9 ms at 50 ms; 31 ms at 200 ms, so the test is sensitive), per-frame ramp cadence, overdue-event coalescing, stop flag, and a real-time smoke test of `service.py` against stand-in `xbmc` modules. The tests cannot show how Kodi behaves; that needs the Shield run. `tools/spike_a/install_addon.ps1` installs, removes and pulls logs over adb, and runs nothing unless a switch is given.

### Frame-accuracy test, Spike A with the in-Kodi add-on (2026-10-28; same video, same 240 fps capture method)

Set-up: the add-on installed above, enabled; the PC only started playback and sent no zoom commands. The add-on used one fixed lead of 316 ms for every cut and every ramp frame. Its own log (pulled from the Shield): all 244 commands sent, none failed, none dropped, each within 0.64 ms of target on its own clock estimate (median 0.02 ms), each `executeJSONRPC` call 0.2 to 1.3 ms (median 0.33 ms); the run ended "finished 244 of 244".

**Hard cuts, 25 measurable of 28 (same exclusions as before):**

| | PC-driven over the network (lead varied) | In-Kodi add-on (fixed 316 ms lead) |
|---|---|---|
| Delay from command to picture, mean | 0.316 s (7.6 frames) | 0.319 s (7.7 frames) |
| Spread of that delay, standard deviation | 0.039 s (0.93 frames) | **0.010 s (0.23 frames)** |
| Spread, full range | 3.2 frames | **0.9 frames** |
| Zoom lands on exactly the right frame (fixed lead) | 9 of 25 (calculated from the measured delays) | **23 of 25** |
| Within 1 frame | 23 of 25 | **25 of 25** |
| Worst case | 2 frames | **1 frame (late)** |

The two misses were both one frame late (offsets +0.8 and +0.86 frames), at the first cut (frame 48, shortly after the 0.12 s clock calibration) and at frame 408; none was early. So the fixed 316 ms lead is right, and moving the command inside Kodi cut the spread by about four times. Against R2 (target 0, acceptance at most 1 frame): **acceptance met in this sample (25 of 25 within a frame), target not met (23 of 25 exact).**

**Ramps (a new zoom for every frame, lead 316 ms):**
- Eased: displayed zoom against commanded zoom for the same frame, with no shift: mean absolute error 0.0037, which is one pixel of plate width (0.0033), my measurement resolution; worst 0.0096 (about 1.5 frames of ramp progress, the largest commanded step being 0.0064); no reversals. One isolated one-frame excursion at frame 1860 (0.038, about 11 px), then back on the curve the next frame; whether that is a real one-frame wobble or a measurement blip is not known.
- Linear: the first 54 frames follow the command the same way (mean 0.0037, worst 0.0084, no reversals); the rest of this ramp lies in the phone's 8-times-speed tail and is not measurable from this clip.
- Near-black: still unmeasurable (code unreadable).
- The earlier PC-driven result (displayed zoom about 5.7 frames behind the command's frame, lag swinging between 4 and 7 frames) is gone: with the add-on the lag is 0 frames.

**Retraction: the "playback stall" was an artifact of the phone clip, not Kodi (2026-10-28).** Everything I called a stall, skip or "8 times fast catch-up" near content frame 1994 to 2068 in the three filmed runs, and the hypothesis built on it (zoom commands or keyframes make Kodi's surface renderer stall), is withdrawn.

- The phone files are iPhone slow-motion clips with a 30 fps timeline (709 s for the probe clip, 21,273 file frames) holding frames captured at 240 fps. The last stretch of each clip is stored at normal speed, one file frame per 8 captured frames. At 23.976 fps content that is 8 x (1/240) / (1001/24000) = **0.800 content frames per file frame**, against 0.1 in the slow-motion part.
- Measured after the change of speed: **0.796** (first PC-driven run), **0.800** (add-on run), **0.799** (keyframe-probe run) content frames per file frame, with the content counter advancing sequentially and no skipped numbers. Before it, all three runs sit on the 240 fps line for about 20,000 file frames (the probe run drifts -0.5 content frame over 19,000 file frames, which is the difference between my 10.008 and the true 10.010 file frames per content frame).
- The change of speed sits 596, 539 and 573 file frames before the end of the three clips, at content frames about 1990, 2112 and 2005. It is tied to the end of the clip, not to a content frame or a keyframe. The control (no commands) "played smoothly" because the user watched the real screen, where nothing was wrong.
- Consequence for the earlier numbers: the 25 hard cuts and the first 54 frames of the linear ramp lie before the change of speed and stand. The cuts at 2112 and 2280, the rest of the linear ramp (frames 1992 to 2063) and the near-black ramp (2160 to 2231) fall inside it. In the 8-times-speed tail each file frame covers 0.8 content frame, so they cannot be timed to a frame from these clips.
- Fix for the next filmed runs: keep recording for at least 30 seconds after the video ends, so the whole video falls in the 240 fps part. (Not yet tried; the clip trimming behaviour is inferred from three clips.)

**Keyframe probe run (2026-10-28, IMG_8349).** Set-up: the add-on, with a probe timeline of 18 single small zoom changes between 1.0 and 0.985 (about 4.5 px of plate width), nine near keyframes (offsets -16 to +16 frames from keyframes at 250k) and nine mid-GOP. The add-on's log: 18 of 18 sent, none failed, worst lateness 0.07 ms. In the 240 fps part of the clip (changes at frames 125 to 1875: 15 of the 18, including keyframe neighbours at 238, 492, 746, 1000, 1254, 1508 and 1762) the content counter stayed on the 240 fps line with **no step of more than one content frame** (steps over 1.5 frames in the residual would have shown). No freeze and no skip near any of those keyframes. The changes at 2016, 2125 and 2234 fall in the tail and were not examined; in particular a zoom change right at the keyframe at frame 2000 is untested. Caveat: I only counted readable frames with a stable code, and the code is hard to read for a few frames around each zoom change, so a skip inside that window could be missed. The step is too small (4.5 px against an 8 px matching tolerance) for `analyze_boundary.py`, so this run gives no timing data. **Result: R4 (no stutter caused by the system) has no counter-evidence in the 15 changes examined; the stall hypothesis is dropped.** Still open: the frame-4 hold at the start of playback, and whether the 1-frame excursion at frame 1860 in the eased ramp is real.

## Spike B: Plezy's built-in mpv (source reading, 2026-10-28; nothing run on the Shield yet)

Read from a shallow clone of `github.com/edde746/plezy` at commit `ffe7d79` (version 2.25.0+157, GPL-3.0). Everything below is **from reading the code**; none of it has been run or measured on the Shield, and I did not open the native C patches in the separate `edde746/mpv-build` repository.

| Question (from the spec) | Answer from the source | Where |
|---|---|---|
| Which engine does Plezy use on Android? | mpv by default. ExoPlayer is an opt-in setting (`android_use_exoplayer`); a code comment says every install was moved to mpv. The custom mpv config applies only when mpv is the engine | `settings_service.dart`, `video_player_screen.dart` (lines 1715 to 1851, 2101) |
| How is mpv embedded? | Flutter app with a Kotlin core (`MpvPlayerCore.kt`, 3,334 lines; the `mpv` Kotlin folder is 15 files, about 5,000 lines) over libmpv from a prebuilt, checksum-pinned AAR built by the project's own `mpv-build` repo (mpv 0.41.0, FFmpeg 8.0.1, patched) | `mpv-build.lock.json`, `MpvPlayerCore.kt` |
| Video output and hardware decoding | Hardware sessions use a **forked `vo=mediacodec`**: decoder buffers go straight to a SurfaceView with per-frame presentation timestamps, 10-bit and the decoder's HDR10/HLG dataspace kept, subtitles and OSD on a second, full-size surface. Software sessions use `gpu`/`gpu-next`. A stream the plane cannot take (codec outside the hardware list, a Dolby Vision profile 5 file needing RPU reshaping) is moved to a GL output | `MpvPlayerCore.kt` (lines 171 to 195, 1129 to 1157) |
| Does `video-zoom` work, and how? | It is accepted (log2 value, clamped to -2..+2, so 0.25x to 4x), but **on the hardware path it is not an mpv transform**: Kotlin resizes the video SurfaceView on the main thread (`VideoRectPolicy.sizeFor`, `applyVideoRectLayout`) and the container clips the overflow. The video is always centred (`Gravity.CENTER`) | `VideoRectPolicy.kt`, `MpvPlayerCore.kt` (lines 1954 to 1993, 2741 to 2747) |
| Vertical shift? | **Not available.** No handling of `video-pan-x/y` or `video-align-y` exists in the Kotlin core, so shifting the picture within the band would be new code | search of `MpvPlayerCore.kt` |
| What does the "custom mpv configuration" accept? | A text of `key=value` lines, parsed once and applied **once per playback** with `setProperty` after start-up (a few app-owned properties are skipped). So `video-zoom=-0.4` is one fixed zoom for every title | `video_player_screen.dart` (lines 2105 to 2139) |
| Lua scripts, IPC socket? | **None.** No `input-ipc-server`, script loading or `.lua` anywhere in the source. mpv's `ytdl` is switched off | search of the whole tree |
| Any control channel? | Only a debug one: the "agent control" service is compiled out of release builds (`!kReleaseMode`) and is reachable only through the Dart VM service. Inside the app, Dart can call `setProperty`, `getProperty`, `command` and read a position stream | `agent_control_service.dart`, `player.dart`, `player_streams.dart` |

**What this means for the requirements (my reading, untested):**

- **Static zoom works today with no code** (one line in Plezy's mpv config), but it applies to every title, so it only serves as a test of the geometry and of HDR output, not as a solution.
- **R1 and R2 need a fork.** A fork would add the timeline fetch and scheduler (Dart) and the zoom and shift (Kotlin). The cheapest place to apply a zoom is the SurfaceView relayout above. That is a view change on the Android main thread, queued behind Plezy's serialised write queue, so I expect it not to be frame-locked. Kodi's zoom change was frame-locked and sat 7.7 frames behind the command with a spread of 0.23 frames; whether a relayout can do as well is **unknown until measured**. Per-frame relayouts for ramps (R2) are an open risk: a resized SurfaceView may reallocate its buffer and flash or drop frames.
- **A frame-locked version would live in the native `vo=mediacodec`** (C, in `mpv-build`), applied when each buffer is released. That means rebuilding the libmpv AAR with the Android NDK and carrying a patch against a moving mpv fork. I have not read that code, so I can only say it is the expected place, and the effort is high.
- **R13 (subtitles inside the band):** the subtitle/OSD surface stays full-container, so subtitles would sit outside the band unless margins are changed too.
- **HDR10 and DV:** the plane keeps the decoder's HDR dataspace and a SurfaceView resize does not touch the decoder, so I expect HDR10 to survive a zoom (assumption). Dolby Vision profile 5 goes through a different path; profile 7 and 8 behaviour under zoom is unknown.
- **Other requirements Plezy already covers:** display refresh-rate matching (`FrameRateManager`) and audio passthrough (`audio-spdif`) exist in the code; I have not tested either with this project.

**Fork effort and upkeep (estimate from the repository, not from a build):**

| Item | Finding |
|---|---|
| Patch surface | Kotlin core (zoom, shift, timeline-driven changes), a Dart scheduler and timeline client, settings. A frame-locked version adds native patches and an AAR rebuild |
| Upstream churn | **223 commits in the 30 days to 2026-10-28**, releases 2.20 to 2.25 in the recent list. The file I would change most (`MpvPlayerCore.kt`) is the core of the player, so conflicts on every rebase are likely |
| Build tools | Flutter 3.47 or newer (Dart 3.12), Android SDK and a JDK. None was in the Phase 0 tool check, so these would be new installs (ask first). The stock build downloads the prebuilt AAR |
| Licence | GPL-3.0; a fork stays GPL-3.0 and its source must be published to anyone given the APK |

**Not done yet, and what it needs from you:** the spec's on-device test of a static `video-zoom` with HDR10 and Dolby Vision through Plezy's custom config. It would show whether the surface-resize path puts the 1.78 ratio frame's borders on the screen edges at the same zoom as Kodi (0.7565, which is `video-zoom` of about -0.403) and whether HDR stays on. It changes a setting in Plezy on the Shield, so I will only do it with your go-ahead. Steps would be: Plezy settings, mpv configuration, add the one line, play the HDR10 calibration video from the Plex library, then remove the line.

**Provisional position for the matrix (Spike B column), source reading only:** live timed zoom not possible without a fork; frame accuracy, glitch on change and ramp smoothness unknown and risky; build effort high; maintenance risk high; fits the Plezy workflow best of all routes because it is the front end already.

### Earlier rough latency from the first HDR10 video (4 transitions; superseded by the measurement above)

Method: each zoom command was sent from the PC just before the next ratio frame's own picture change (the video file switches content at 10 s, 20 s, ...). In the phone video I found the frame where the zoom changes and the frame where the content changes, and compared. The command's lead over the content change comes from the logged player clock (read by polling every 100 ms).

| Boundary | Command lead over the content change | Zoom change vs content change in the video | Implied command-to-picture delay |
|---|---|---|---|
| 1.78 to 1.85 | about 0.20 s | Same phone frame | about 0.20 s |
| 1.85 to 1.90 | about 0.27 s | One event only (probably the same frame; the zoom step is small) | about 0.2 to 0.3 s |
| 1.90 to 2.00 | about 0.33 s | Zoom changed 0.17 s before the content | about 0.16 s |
| 2.00 to 2.35 | about 0.20 s | Same phone frame | about 0.20 s |

Reading: a constant delay of about 0.2 s (about 5 frames at 23.976 fps) fits the four points to within about 0.05 s (about 1 frame at 23.976, 3 frames at the phone's 60 fps). Caveats, all material:
- Four transitions, one capture, phone timing only. The acceptance test needs at least 20 boundaries and a frame counter.
- The delay is measured against the *displayed* content, using the player's reported clock to time the command, so it includes any constant offset between Kodi's clock and what is on screen. That is what a scheduler needs, but it may differ between files, refresh rates and Kodi settings.
- The commands were sent from the PC over the network (round trip about 17 ms); an add-on running inside Kodi would have less network delay but its own scheduling jitter.
- If the delay really is steady at about 0.2 s, a fixed `lead_ms` of about 200 would cancel it, which would make ratio changes land within roughly 1 to 2 frames. Not shown yet.

### Frame-accuracy test video (built 2026-10-28; not yet played)

`tools/testpatterns/make_boundary_test.py HDR10 23.976` writes `out/boundary_hdr10_23976.mkv` (97.1 s, 2,328 frames, HEVC 10-bit PQ) and a `.timeline.json` with every cut and the zoom each state needs. SDR and 24 fps variants come from the same script (`SDR`, `24`); not built yet.

| Part | Content |
|---|---|
| Lead-in | 48 frames at 2.39 |
| 24 hard cuts | 72 frames per state, cycling 1.90, 2.39, 1.78, 2.39 (cuts at frames 48, 120, 192, ...) |
| 3 ramp blocks | 48 frames hold at 2.39, then the matte opens 2.39 to 1.78 over 72 frames, then 48 frames hold at 1.78. Block 1 eased (smoothstep), block 2 linear, block 3 eased with a near-black picture |
| Instrumentation | Burned-in frame counter ("F 00123") and a 14-bit binary code of the same number at the centre of the frame (the one point zoom never moves); the matte edges carry a 4 px white border and a tick every 24 px on the picture side; the picture has a line every 40 rows |

Verified by decoding the video (2026-10-28): all 2,328 frames carry a binary code equal to their frame number, and the matte edge rows (top and bottom, measured at a fixed column) equal the plan on every frame, including all 3 x 72 ramp frames. A first build had the matte moving one frame early (the overlay filter's frame counter runs one ahead of the counter text); that was found by this check and fixed.

How it will be used: send each cut's zoom `lead` seconds before the cut, varying the lead from cut to cut. In a slow-motion clip, the picture's displayed height says which state is on screen (correct zoom gives 1634 rows for 1.90 and 1.78 and 1606 for 2.39; a late zoom gives 2022 rows for 1.90, an early one gives 1298), and the code gives the frame number, so the number of wrong frames per cut can be counted and fitted against the lead.

### Not yet confirmed

| Question | State |
|---|---|
| Vertical shift units | Direction and size on screen not measured (the design band is centred, so every table entry uses a shift of 0) |
| HDR10 and Dolby Vision stay active while zoomed, and the HDR10 geometry matches SDR | Not tested |
| Zoom latency and jitter in frames; glitch on change (black flash, dropped frame) | Needs the boundary test video and a slow-motion clip |
| Ramp smoothness (a zoom change every frame for several seconds) | Not tested |
| Whether Kodi saves per-file view state and applies it on resume | Not tested |
| Whether a view-mode change takes effect on the next presented frame (frame-locked) | Not tested |

### Test-rig lessons (apply to every later test)

1. **Stop before you open.** Calling `Player.Open` while another file is playing fails with "error opening" about half the time (the old player is still being torn down). Call `Player.Stop`, wait a few seconds, then open. Files I had blamed (looped or longer files) played fine once this was done.
2. **Do not pause to photograph.** A paused video shows Kodi's seek bar over the bottom of the picture. Photograph while playing.
3. **Send floats.** See above.
4. **Opens fail at random.** `Player.Open` over HTTP sometimes fails with "error opening" (Kodi sends HEAD, HEAD, one ranged GET, then drops the connection). It came in bursts of a few minutes, ended on its own, and hit files that opened fine minutes later (including identical copies). Stop-and-wait did not prevent it; retries eventually worked. Cause unknown. Test scripts must retry and verify that playback actually started.
5. **Repeat mode persists.** A `Player.SetRepeat` to `one` from an earlier test stayed set, so the HDR10 file looped at the end. Reset repeat explicitly at the start of a run.

## Spike C: Plezy hands off to Kodi as the external player (source reading, 2026-10-28)

Read from Plezy 2.25.0 (commit `ffe7d79`) and Kodi's `21.2-Omega` source (`XBMCApp.cpp`, the Android manifest template). One read-only query to your Plex server confirmed the URL shape (token not printed). **Nothing was run on the Shield.**

| Question (from the spec) | Answer | Basis |
|---|---|---|
| Can Plezy launch Kodi? | **Yes, as a custom player.** Android external players are started with `startActivityForResult` and an `ACTION_VIEW` intent (`type=video/*`, data = the stream URL). The built-in list is VLC, mpv, MX Player and Just Player, plus System Default; a custom entry of type "command" takes an Android package name, so `org.xbmc.kodi` fits. Plezy offers external players on Android, and the setting "use external player" decides when it is used | `external_player_service.dart`, `external_player_models.dart`, `ExternalPlayerChannel.kt`, `platform_detector.dart` |
| What does it hand over? | The Plex **part URL**: server base URL + the part's `key` + `?X-Plex-Token=<token>`. Verified on your server: the key is `/library/parts/<partId>/<updatedAt>/file.mkv`, and `<partId>` equals the part's `id` in the metadata. Extras added to the intent: title, file name, a start position (MX Player and Vimu key names), `return_result`, and external subtitle files | `plex_playback_mapper.dart` (line 89), Plex API query, `ExternalPlayerChannel.buildIntent` |
| Does Kodi accept it? | Kodi declares a `VIEW` filter for `video/*` with `http` and `https` among its schemes. `onNewIntent` takes the data URL as a string and posts a plain play request. It reads **no extras**: no start position, no title | Kodi manifest template lines 65 to 89, `XBMCApp.cpp` lines 1479 to 1531 and 1703 to 1730 |
| Does watched progress sync back? | **Not by itself.** Plezy reports progress only from a position that the external player returns in its result, and Kodi sets no result (`onActivityResult` is empty; no `setResult` in the file). Kodi is also declared `singleInstance`, which I expect makes Android return a cancelled result at once rather than at the end of playback (not verified). So Plezy would learn nothing and Plex would not record progress | `external_player_service.dart` (`_reportExternalProgress`), `XBMCApp.cpp` |
| Resume from position? | **No.** The start position goes in extras Kodi ignores, so Kodi starts at 0 unless it has its own bookmark for that exact URL | same |
| Can a Kodi add-on get the Plex part ID? | **Yes.** `xbmc.Player().getPlayingFile()` returns the URL, and the part ID is the number after `/library/parts/`. That matches the spec's lookup by part ID (R9). Not run on the Shield | URL shape above, Kodi Python API |

**What this means (my reading):**

- The hand-off gets the video into Kodi's internal player, which is Spike A's route (frame-accurate zoom with the in-Kodi add-on, 25 of 25 cuts within 1 frame). The zoom side works; the gaps are around it.
- **Gaps:** no resume, no watched-state sync, and the user is left in Kodi's own menus afterwards. Whether Back returns to Plezy is untested. To close them, the add-on would have to read the viewing offset from Plex by part ID, seek to it, and report progress to Plex itself (the Plex `timeline` call). That reimplements what PM4K already does, and it puts the token in the add-on.
- **Token in the URL:** the URL carries the Plex token, so it appears in Kodi's log and playing-file field. The add-on must redact it, as the spec already requires for logs.
- **Alternative in the same family:** PM4K (`script.plexmod`, already installed in Kodi) plays Plex items inside Kodi's own player and handles Plex resume and progress itself. It reaches the same Spike A route without an app-to-app hand-off. Its UI was your reason for considering it. I have not looked at its source.

**Not done, and what it would involve:** an on-device test of the intent path. The safe version uses the PC's test-video URL (no Plex token) and sends `adb shell am start -a android.intent.action.VIEW -d <url> -t video/* org.xbmc.kodi`, which would play the boundary video on the projector and exercise the add-on. I would not use a Plex URL for that test, because Android can echo intent data, token included, into its logs. It plays a video on the Shield, so I will only run it with your go-ahead.

**Provisional position for the matrix (Spike C column), source reading only:** zoom, frame accuracy, glitch behaviour and ramps inherit Spike A (route is Kodi's internal player); HDR10 kept (measured in Spike A), DV untested; seek/resume hooks: the add-on has them, but resume and progress sync to Plex do not exist yet; fits the Plezy workflow partly (launch from Plezy, lose its resume and progress); build effort medium (add-on grows a Plex client); maintenance risk lower than a Plezy fork, since Kodi's add-on API is stable.

## Spike D: mpv-android as Plezy's external player (source reading plus read-only checks, 2026-10-28)

Read from `github.com/mpv-android/mpv-android` at commit `d25d7c1` (latest release tag dated 2026-09-17), and from Plezy's launcher code (Spike C). Four read-only `adb` queries on the Shield changed nothing. **mpv-android is not installed on the Shield and nothing was played.**

| Question (from the spec) | Answer | Basis |
|---|---|---|
| Does Plezy launch it, with resume and progress sync? | **Yes.** Plezy lists mpv as a built-in external player with package `is.xyz.mpv`, using the same `ACTION_VIEW` intent as in Spike C. mpv-android reads the `position` extra (milliseconds, turned into mpv's `start`), `subs` and `title`, and **returns `position` and `duration` when it closes**, which are the keys Plezy reads. So resume and watched-progress sync work, unlike Kodi. The code has a FIXME that it does not always report OK versus cancelled correctly | `external_player_models.dart`, `MPVActivity.kt` (lines 330 to 343, 1143 to 1179), `ExternalPlayerChannel.kt` |
| Video output and HDR10 / Dolby Vision | Output is mpv's OpenGL ES renderer (`vo=gpu`, or `gpu-next` as an opt-in setting) with `gpu-context=android` and hardware decoding. **Nothing in the app sets HDR output, colour spaces or Dolby Vision**: no matches for HDR, Dolby, colourspace or target colour settings in `app/src/main`. I therefore expect HDR10 to be tone-mapped to SDR and Dolby Vision to be lost. Two supporting facts from Plezy's code: its own comment says it built the forked `vo=mediacodec` to keep 10-bit and the HDR dataspace, which a GL pass does not, and that `gpu-next` with hardware decoding gives a blue screen on the Shield (#2010) | `MPVView.kt` (lines 20 to 115), search of `app/src/main`, `MpvPlayerCore.kt` (lines 171 to 195). **Untested** |
| Refresh-rate switching, audio passthrough | Neither is in the app's code: it only reads the display's rate into `display-fps-override`, never requests a display mode, and sets no `audio-spdif`. mpv.conf could add passthrough; I did not test that | search of `app/src/main` |
| Does `video-zoom` work while in HDR? | `video-zoom`, `video-pan-y`, `video-align-y` are real mpv renderer transforms on this path, applied in the GL pass, so geometry and vertical shift are available without any new native code. **Whether HDR survives is the open question above** | mpv behaviour on the `gpu` output; not run |
| Lua scripts | mpv is built with Lua 5.2.4. mpv's config folder is the app's private files folder, which mpv loads `scripts/` from, but `adb` cannot write there (the app is not debuggable, my assumption). The in-app config editor edits only `mpv.conf` and `input.conf`, so the route is a `script=` line in `mpv.conf` pointing at a file in shared storage. Whether the app can read that path on Android 11 is **unverified** | `Utils.kt`, `BaseMPVView.kt`, `ConfigEditDialogPreference.kt`, `AndroidManifest.xml` |
| Can a script fetch a timeline over HTTP? | Not directly. The Shield has **no `curl` or `wget`** (checked); `toybox` has `nc`, so a script could run `sh -c` with `nc` for a plain HTTP GET (`subprocess` from Lua). That works only on a plain-HTTP analyzer on the LAN and is **untested**. The simpler alternative is a timeline file placed in shared storage | `adb shell` queries on the Shield |
| Timing | A Lua script can watch `time-pos` or the frame number and set `video-zoom` for each frame. Whether the change lands on the right frame (and not a frame late) is not known; it would be measured the same way as for Kodi | mpv scripting model; not measured |

**What this means (my reading):**

- This route has the **best geometry tools** (renderer transform, vertical shift, scripting, resume and progress sync with Plezy) but it **very probably fails R3**: no HDR10 or Dolby Vision output and no refresh-rate switching are visible in the code. The user's priority is to keep 4K HDR10 and Dolby Vision, so this is a decisive question, and it can be settled by one short test.
- The test: install mpv-android on the Shield (the APK is a download from the project's release page, so I need your go-ahead and would state the file, source and size first), set `video-zoom=-0.403` in its `mpv.conf`, play the HDR10 calibration video through Plezy's hand-off, and check whether the projector stays in HDR10 and whether the 1.78 ratio frame fits. Dolby Vision would need a DV file played the same way.
- Setting up scripts, storage access and a timeline source adds work, and every part of it is wasted if HDR fails, so I would run the HDR test first.
- Upkeep: mpv-android is actively maintained (pushed today). Building it from source needs a Linux build environment (its build scripts download and compile mpv and FFmpeg), so a custom build would be heavy; a script-only approach needs no build.

**Provisional position for the matrix (Spike D column), source reading only:** live timed zoom plausible with a script; HDR10 and Dolby Vision probably lost (decisive, untested); refresh-rate switching absent; audio passthrough only via mpv.conf; resume and progress sync with Plezy work; build effort low if scripts load, otherwise high; maintenance risk low for a script, high for a fork.
