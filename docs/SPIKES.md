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

### Rough zoom latency from that video (4 transitions, **not** the Phase 2 acceptance test)

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
