# ZOOMCORE: how the player applies the zoom (Phase 5)

The route chosen at the Phase 2 gate: Kodi's own player, with an add-on (`players/kodi/service.cih.zoom`, Kodi 21,
Python 3) behind PM4K. This file is the spec of what the add-on does. The pure parts (no Kodi imports) are tested on a PC:
`plan.py` (the math), `identify.py` (which title), `client.py` (the analyzer), `zoomcore.py` (set points, clock,
scheduler). `service.py` is the Kodi glue and `override.py` the remote-button script.

## What was observed on the Shield (2026-10-30, read-only, PM4K playing *Buddy*)

| Fact | Value |
|---|---|
| The URL Kodi plays (`Player.Filenameandpath`) | `https://10-29-20-200.<hash>.plex.direct:32400/library/parts/551450/<timestamp>/?X-Plex-Token=...&...` : a Plex *part* URL, no file name, trailing slash. The part id is in the path |
| The library item's own path (`Player.GetItem` `file`) | `/movies/Buddy (2026)/Buddy (2026) WEBDL-2160p.mkv` : the Plex-side path |
| Stream size labels | `Player.Process(videowidth)` reads `3,840`, height `2,074`, DAR `1.85`: thousands separators, DAR rounded to two places |
| Whether `xbmc.Player().getPlayingFile()` returns the URL or the item path | **Not yet checked**; the add-on asks about both |

The analyzer had no Plex id for this title: the Plex path (`/movies/...`) is not under any `PATH_MAP` rule, so the
sync attached ids only to the music videos (1,430 of 38,059 parts). From analyzer 0.3.8 the sync also matches a Plex
path to a scanned file by its folder and file name when no rule covers it (`NameIndex`), never guessing between two files.

## Playback start

1. `onPlayBackStarted`: if the add-on is enabled and the playback is a video that is not live TV, a recording or
   stereoscopic 3D, identify the title from the played URL and the item path (`identify_all`): a Plex part id, a rating
   key (transcode) or a path. Tokens in URLs are never kept or logged.
2. Ask the analyzer (`GET /api/v1/timeline` with every key we have, `X-Api-Key` header, **1 s timeout**). A title it
   does not have (404) is queued at priority (`POST /api/v1/analyze`). A timeline that arrives is copied to the add-on's
   profile folder; an unreachable analyzer, or an older copy while a new one is made, uses that copy.
3. Turn the timeline into a **plan** and send the zoom for frame 0 at once (Kodi restores each file's last view, so
   the first frames could otherwise be wrong).
4. `onAVStarted`: start the scheduler (below).

## The math (`plan.py`; spec section 5)

Kodi's Normal view fits the whole frame into 3840x2160 keeping its display ratio. For an active rectangle (w, h) in
source pixels, shown at (w0, h0) = (w x shown_width / frame_width, h x shown_height / frame_height):

    zoom = min(3840 / w0, band_height / h0)             band_height = band_bottom - band_top   (design: 263 to 1897 = 1634)

`scope_fill` (off): for a picture wider than the screen, `zoom = band_height / h0` (crops a sliver off the sides).
The analyzer centres every rectangle on the frame and the band is centred on the output, so **vertical shift is 0**
(Kodi's `verticalshift` units are unmeasured: an asymmetric matte stays a Gap, see AUDIT_1). Checked against the table
of section 5.3 in `tests/test_plan.py`.

## Set points

* **Cut**: one set point at the segment's first frame. `edge_bias` (default `no_spill`) moves it one frame: a *shrink*
  (zoom falls) one frame early, a *grow* one frame late, so a residual error shows as a frame of thin bars and never as
  light past the screen's edge. `no_bars` is the reverse, `centered` leaves it. Never moved onto or before the
  previous set point.
* **Ramp**: one set point per frame from the ramp's first frame, the zoom of that frame's *fitted* edges. No bias and
  no easing of our own; no jump to the final ratio. A seek into a ramp starts at that frame's value (the zoom at a
  frame is the last set point at or before it).
* **Override** (the analyzer returns it with the timeline): `disable` keeps Normal (1.0); a preset applies one zoom
  for that ratio throughout.
* Frame numbers use the exact frame rate (`23.976` is 24000/1001); the analyzer's times are on the player's clock
  (0 is the first frame).

## Scheduling (`zoomcore.py`, measured in Phase 2)

Each set point is sent `lead_ms` (316) before its frame's time: the command reaches the picture about 0.316 s
later, with a spread of 0.23 frames in the add-on (25 of 25 cuts within a frame, 23 of 25 exact). The player's clock
is read by polling and refined by the median of recent samples. On start, seek (also chapter), resume and normal speed
the schedule is rebuilt from the current frame; pause and fast-forward or rewind stop it. Events more than a frame
overdue are dropped, keeping only the newest.

## No timeline yet (R6)

Fallback zoom: the whole frame contained in the band by its display ratio (`full_frame_zoom`; 0.7565 for 16:9). It
never cuts picture. The add-on keeps asking every `poll_s` (20 s, floor 5) without queueing again, and switches over
at the current position when the timeline arrives. This is also the hybrid mode of DD30 for titles not yet analyzed;
the analyzer's playhead-first partial analysis is not built, so a long film waits for its whole analysis.

## Stop

Stop or end: stop the threads, send zoom 1.0 (Normal), forget the title, so the next file and the menus start clean.

## Override from the remote (R11)

`RunScript(service.cih.zoom)` (`override.py`): one press per step along automatic, off, 1.33, 1.78, 1.85, 2.00, 2.35,
2.39, automatic; saved in the analyzer per title (`POST /api/v1/override`) and shown as a notification; the script sets
a window property and the service reloads. The button is the user's choice (`keymap.example.xml` is an example only).

## Settings (`resources/settings.xml`, R12)

`enabled`, `analyzer_url`, `api_key` (hidden), `request_analysis`, `poll_s`, `lead_ms`, `edge_bias`, `band_top`,
`band_bottom`, `scope_fill`. Everything the user or Claude might tune is here; the screen ratio follows from the band.

## Log

`events.jsonl` in the add-on's profile folder: start (frame, zoom, clock offset, override), every event sent (target,
lateness, call time), fallback, finished or interrupted. kodi.log gets one line per title (kind and analyzer status).
No URL, token or key is written.

## Not done / unverified

* Not yet run on the Shield: the new add-on is tested on a PC with stand-in Kodi modules only (T3). Installing it is a
  change on the Shield and needs the user's go-ahead.
* `getPlayingFile()` against `Player.Filenameandpath` under PM4K: both are read, one result still to see.
* Wrong zoom for about 0.3 s after a seek or at start (G5, accepted); not yet measured with PM4K's start-then-seek.
* Kodi's per-file view memory against the early start zoom: observed only in Spike A.
* `verticalshift` units, subtitles (R13), HDR/DV under the new add-on, ramps in a real film on the Shield (T8).
