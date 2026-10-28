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
| Zoom geometry on screen (user's photos) | At the section 5.3 zoom values, the 1.90 frame (0.8085) and the 2.00 frame (0.8510) have their top and bottom borders on the screen's top and bottom edges and their side borders inset by about the expected amount (about 81% and 85% of the screen width). The 2.35 frame (zoom 1.0) shows its border on all four edges; the 2.39 frame (zoom 1.0) shows a thin black bar inside the top and bottom, as expected |

### Not yet confirmed

| Question | State |
|---|---|
| 1.33, 1.78 and 1.85 frames at their zoom values | The three photos show the picture **unzoomed**. The photos' timestamps are when ShareX saved them, not when they were taken, and my script applies each zoom 1 s after its segment starts, so they may simply have been taken in that first second. Needs a re-run with the zoom applied before the photo |
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
