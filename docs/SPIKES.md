# SPIKES (Phase 2): in progress

Only Spike A has been started. Nothing below is a recommendation yet; the decision matrix is filled in at the end of Phase 2.

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

## Spike B, C, D

Not started.
