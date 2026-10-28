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

## Spike B, C, D

Not started.
