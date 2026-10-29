# PROGRESS

**Current phase:** 1 (Geometry calibration) complete on 2026-10-28 (SDR numbers accepted by the user); Phase 2 (player spikes) next
**Last updated:** 2026-10-28

Site-specific facts (LAN addresses, share and library paths, film titles, per-file probe results) are kept in `docs/LOCAL_FACTS.md`, which is gitignored so this public repo does not carry them. Secrets live only in `.env` (also gitignored).

## PC environment (verified by running the commands)

| Tool | Status | Version / note |
|---|---|---|
| Windows | Present | Windows 11 Pro 10.0.26300 |
| winget | Present | v1.29.380 |
| Python | Present | 3.12.10 |
| git | Installed (user approved) | 2.56.0.windows.3 |
| gh | Installed (user approved) | 2.103.3, logged in (HTTPS; scopes gist, read:org, repo, workflow) |
| ffmpeg / ffprobe | Installed (user approved) | 9.0.2 full build |
| adb | Installed (user approved) | 1.0.41 (platform-tools 37.0.1) |
| WSL2 | Present | WSL 3.0.3.0, default distro `docker-desktop` |
| Docker Desktop | Installed (user approved) | docker 29.9.2; `docker info` reaches the server |

## Verified

| Item | How |
|---|---|
| Shield reachable over adb | `adb connect` then `adb devices` shows state `device` |
| Shield: SHIELD Android TV (`darcy`), Android 11, API 30 | `adb shell getprop` |
| Kodi 21.2 installed and running | `dumpsys package org.xbmc.kodi` (versionName 21.2) |
| Kodi JSON-RPC over HTTP works with the `.env` credentials | `JSONRPC.Version` returned 13.5.0 |
| L4 add-ons (`cihautonox`) are not installed | `Addons.GetAddons` and an on-disk folder search |
| L6 `script.scopenox-autozoom` was enabled, now disabled | `Addons.SetAddonEnabled`, then re-read as `enabled=False` (with the user's permission) |
| Test files exist on the NAS and probe cleanly (read-only) | ffprobe over SMB: all are 16:9 containers (3840x2160 HEVC Main10, or 1080p H.264), SAR 1:1, `start_time` 0, 23.976 fps (one at 24). HDR10 on the 4K files; Dolby Vision profile 7 on one, profile 8 on two |
| Local clips keep their Dolby Vision profiles | ffprobe on the stream-copied clips |
| Plex server reachable, token valid | `/identity` (v1.43.5.11079, claimed); `/library/sections` with the token returns 13 libraries; an invalid token gets 401 |
| Library sizes | Plex API `totalSize` per section: Movies 2,344 titles, TV Shows 558 shows, plus smaller movie, show and music-video libraries (details in `LOCAL_FACTS.md`). Matches the spec's "2,000+ titles" |
| Plex path to SMB path mapping works for the Movies library | For all 7 test films, the Plex file path mapped to the SMB share opens with `Test-Path` |
| Phase 1 calibration videos built (SDR and HDR10) | `tools/testpatterns/make_calibration.py` writes `cal_sdr_2160p.mkv` and `cal_hdr10_2160p.mkv` to the gitignored `tools/testpatterns/out/`. ffprobe: HEVC, 3840x2160, 23.976 fps, 2,399 frames, 100.06 s. SDR is 8-bit BT.709 limited; HDR10 is 10-bit BT.2020 PQ with mastering display and MaxCLL SEI present. signalstats: HDR10 peak luma code about 591 to 604, consistent with 203 nit reference white in PQ limited range |
| Calibration pattern content checked visually | Rendered stills inspected: numbered rows 200 to 328 and 1832 to 1960 every 4 px, L/R edge ticks every 10 px, cross, 4 px frame border, cyan marks at rows 263 and 1896; ratio-frame rectangles match the spec (2.35 gives y=263, h=1634; 2.39 gives h=1606 because the generator rounds to even, vs 1607 in the spec table) |
| Stand-in samples generated and read back correctly | `tools/testpatterns/make_stand_in_samples.py`; ffprobe and cropdetect on the outputs |

## Open findings

| Finding | State |
|---|---|
| One TV clip the user described as 4:3 measures about 1.667 wide in the section cut (single-frame cropdetect, threshold not tuned for PQ). Another clip expected to be scope measures 1.85 | Preliminary. For the analyzer to resolve; do not treat either as a ground truth |
| The user plays from Plezy and is considering PM4K (`script.plexmod`, already installed in Kodi) | If PM4K is adopted, playback is in Kodi's own player, which makes Spike A the main route. Decided at the Phase 2 gate |
| The ScopeNox skin may have its own autozoom setting | Not checked yet |
| Interlaced handling | The stand-in DVD-style sample is progressive, so interlaced sources are untested |
| The user said the library screenshots were complete, but Plex has 13 libraries and the screenshots showed 11 | The two extra (a 3D library and a YouTube library) have no known Unraid host path. 3D is skipped by design; the YouTube library needs a Phase 4 decision |

## Assumed (not verified)

- Everything in spec section 2 about the screen, projector, Plex and Unraid comes from the user and has not been checked from this PC.
- The Unraid host paths for the libraries come from the user's screenshots. Only the Movies mapping has been checked end to end (Plex path to SMB path); the other libraries' mappings are not yet checked.
- The SMB share root is assumed to correspond to the Unraid user-share root.
- Spec section 4 lessons (L1 to L10) are taken as given.

## Decisions taken in chat

| Item | Value |
|---|---|
| Repo | `EckoElite/cih-autozoom-bzaa`, public. The name carries a suffix to be less guessable |
| Project folder | `Documents\Claude\cih-autozoom` |
| Public repo hygiene | Site-specific facts stay in the gitignored `docs/LOCAL_FACTS.md` |

## Still to collect

Carried forward (none blocks Phase 1):

- [ ] Real native 2.39 and non-square-pixel samples (user will look; stand-ins generated for now)
- [ ] Variable-ratio clips, chosen from analyzer output rather than guessed timestamps
- [ ] Phase 4 decisions: analyze the YouTube library, extras and trailers; the host path for the YouTube library if analyzed

## Phase 0 exit check

| Spec item | State |
|---|---|
| PC tools present (git, gh, Python, ffmpeg, Docker with WSL2, adb) | Done, verified |
| GitHub username, repo name, visibility | Done: public repo created and pushed |
| Shield connection over adb | Done, verified |
| Kodi remote control and JSON-RPC check | Done, verified |
| Kodi add-on inventory, L4/L6 flagged, permission asked | Done; L6 add-on disabled with permission |
| Plex URL and token, library paths | Done, verified (token valid; 13 libraries; movies path chain checked) |
| Local sample set | Partly: real clips for 16:9, 2.2:1, DV profiles 7 and 8, and a clip the user called 4:3 (measured about 1.67 in the section cut); stand-ins for native 2.39 and non-square pixels. Variable-ratio clips deferred to analyzer output |

## Phase 1 first pass (user's phone photos of `cal_sdr_2160p.mkv`, Kodi internal player, view mode Normal, zoom 1.0; read by eye from 12 photos, preliminary)

| Item | Reading | Confidence |
|---|---|---|
| Left and right edges | All left ticks L0 to L120 and right ticks R0 to R120 are on screen, and the 4 px white frame border is visible at both side edges. The image is not clipped sideways | Good (photos show it directly) |
| Top edge | About row 258, plus or minus 4. The first full label at the screen's top edge is row 288; labels for rows 256 to 268 are partly cut by the edge | Fair: from the earlier photos only, taken at an angle; no clean close-up of the top yet |
| Bottom edge | About row 1900 (last visible row), plus or minus 3. Replay with no pause bar (3 more photos): labels 1880 to 1892 whole, 1896 slightly clipped, 1900 to 1912 progressively clipped | Good |
| Band | About rows 258 to 1900, so about 1643 rows tall, centred at about row 1079.5 (frame centre 1080). Implied ratio 3840 / 1643 = 2.337 if the full width is on screen | Fair |
| 2.35 ratio frame (rows 263 to 1896) | The orange border is visible on all four screen edges | Good, but it only bounds the band to within a few px |
| 2.39 frame (rows 277 to 1882) | Border visible on all four edges | Consistent with the above |
| 2.00, 1.90, 1.85 frames | Top and bottom borders off screen, side borders on screen | Consistent |

`config/geometry.json` is written as **provisional**. The design band stays at the nominal rows 263 to 1897 (height 1634), because that band is inside the measured band even at the worst ends of the tolerances, so fitting to it cannot spill past the screen. `tools/zoom_table.py` regenerates the section 5.3 table from it; the output matches the spec to within 0.0001 (the spec's own rounding). Vertical shift is 0 px for every ratio, since the band is centred.

The user accepted the SDR numbers and the regenerated table on 2026-10-28 and chose to skip the HDR10 reading and the straight-on top-edge close-up. So: **assumed, not measured**, that HDR10 and Dolby Vision output land on the same rows as SDR (same output frame; the projector's HDR mode could change image size or position). This is checked in Phase 2 by looking at a zoomed ratio frame while HDR is active. The measured edges came from photos taken at an angle; tolerances above are my estimates.

Kodi/serving notes: Kodi plays the original 100 s file from the PC's range server, but files made by looping the 10 s chart with `-stream_loop` fail to open (Kodi sends HEAD, HEAD, a ranged GET, then drops the connection; cause not found). A paused video shows Kodi's seek bar over the bottom of the picture, so the chart must be photographed while playing, not paused.

## Phase 2 status (in progress; details and numbers in `docs/SPIKES.md`)

**Spike A (Kodi internal player), verified by running it:**

| Item | State |
|---|---|
| Numeric zoom via `Player.SetViewMode` while HDR10 plays (MediaCodec on) | Works live, no black flash seen. The projector shows no HDR overlay, but the user confirmed its picture menu was in HDR10 mode |
| Floats only; clamped to 0.5 to 2.0; Kodi remembers the view per file | Verified (a JSON `1` is ignored, `1.0` is accepted) |
| Frame timing of a zoom change, commanded from the PC | Delay 7.6 frames, standard deviation 0.93 frames; frame-locked |
| Same, commanded from an add-on inside Kodi (prototype installed on the Shield with the user's approval) | Delay 7.7 frames, standard deviation 0.23 frames; 23 of 25 cuts exact, 25 of 25 within 1 frame. R2 acceptance (at most 1 frame) met in this sample, target (0) not |
| Per-frame ramp following | Eased ramp tracks the commanded curve at 0 frames lag with the add-on (mean error 0.0037 in zoom, about one pixel of measurement resolution) |
| Playback stall near content frame 2000 | **Retracted 2026-10-28**: an artifact of the iPhone slow-motion clip (its last stretch is stored at 8 times speed). Keyframe probe found no skip near 7 keyframes |

**Open in Spike A:** Dolby Vision with zoom; how many frames show the wrong zoom at the start of playback (per-file saved view); near-black ramp and the linear ramp's later part (re-film with the recording kept running at least 30 s after the video ends); the frame-4 hold at the start of playback (likely an HDMI mode switch, untested); a zoom change exactly at the keyframe at frame 2000; the one-frame excursion at frame 1860.

**Spikes B, C and D: source reading done (2026-10-28), no on-device tests yet.** B (Plezy's mpv): a timed zoom needs a fork; zoom is a SurfaceView relayout, frame accuracy unknown. C (Plezy to Kodi): works, but Kodi ignores the resume position and returns no result, so resume and Plex progress sync would be lost. D (mpv-android): best geometry tools, resume and sync work, but HDR10 and Dolby Vision output and refresh-rate switching are probably lost. Pending on-device tests, each needing your go-ahead: B static zoom setting in Plezy, C intent test with the PC test video, D install of mpv-android (a download) and an HDR check.

**Decision matrix written and the route-choice GATE passed (2026-10-28):** route A (Kodi's internal player with the in-Kodi add-on), PM4K as the front end. See `docs/SPIKES.md`. Phase 2's optional on-device checks (Dolby Vision under zoom, a re-filmed full boundary video, mpv-android HDR, the Kodi intent test) are not done and are not required.

**DEFERRED TO-DO (user, 2026-10-28): add the Shield shortcut to PM4K later.** Plan below; needs the user's go-ahead before any change on the Shield, then a short test. Steps when picked up: push `autoexec.py` (one line, `xbmc.executebuiltin('RunScript(script.plexmod)')`) to Kodi's userdata over adb; help the user add Kodi to the Projectivy home row; confirm it starts PM4K from a cold start; document removal (delete the file).

**Shield shortcut to PM4K (user request, read-only findings 2026-10-28, nothing changed yet):** the home launcher on the Shield is Projectivy (not the stock Android TV home). Kodi has a launcher entry (`org.xbmc.kodi/.Splash`). PM4K 1.0.6 (`script.plexmod`) is installed as a script add-on with a service, and Kodi's favourites already hold a "PM4K for Plex" item. Kodi's userdata has no `autoexec.py`. PM4K has no path mapping configured. A direct Android shortcut into PM4K is not possible, because Kodi's intent handling only plays media; the route is a Shield home shortcut to Kodi plus a Kodi `autoexec.py` that starts PM4K at Kodi start-up.

**Assumed, not verified:** HDR10 and Dolby Vision land on the same rows as SDR (see Phase 1); the 316 ms add-on lead carries over to other files, refresh rates and Kodi settings (one file, one capture).

## Phase 4 status (analyzer, 2026-10-28): slices 1 to 3 of 5 done

Environment: project `.venv` (Python 3.12.10, user-approved) with fastapi 0.162.0, uvicorn 0.54.0, pydantic 2.14.1, numpy 2.5.4 (added in slice 3 for full-resolution frame measurement), pytest 9.2.0, ruff 0.17.3, httpx 0.28.1 (pinned in `analyzer/requirements*.txt`).

| Slice | Content | State |
|---|---|---|
| 1 | `analyzer/`: ffprobe layer (video stream chosen by disposition, cover art ignored, display ratio from SAR, VFR and interlace flags, HDR classification incl. Dolby Vision profile, HDR10+ from frame side data), skip rules (ISO, non-video, 3D tags, MVC, extras and trailers, audio-only, broken), quick hash, timeline schema v1 with validation, CLI `python -m cih_analyzer <file>` | **Done.** 62 tests pass, ruff clean. Verified on generated videos (SDR, HDR10, HLG, cover art, non-square pixels, non-zero start time, interlaced, audio-only, corrupt, Unicode name) and on the real sample clips |
| 2 | Coarse pass (keyframe `cropdetect` + `signalstats`), sample gating, aspect snapping, segmentation with hysteresis | **Done.** 106 tests pass, ruff clean. `coarse.py` (keyframe sampling, parser, bit depth from the pixel format, default limit 0.094) and `segmentation.py` (dark and tiny-crop gating, snapping to the nine standard ratios within 1.5%, runs, ramp candidates, flicker merging by real run duration, `needs_verification` flags). Synthetic T2 videos with known ratios cover variable ratios, fade to black, subtitles in the matte, credits on black, studio logo, 4:3 pillarbox, non-square pixels, a ramp, PQ 10-bit and a non-zero start time. See the findings below |
| 3 | Boundary refinement, cut or ramp classification, ramp fitting, whole-file `analyze_file()` and `python -m cih_analyzer --analyze FILE` | **Done, with one real-film failure (below).** 145 tests pass, ruff clean. New modules: `frames.py` (full-resolution luma by ffmpeg pipe, edge measurement against a matte-calibrated threshold), `ramps.py` (progress per edge, isotonic fit, cut or ramp, scene-cut flag; pure functions), `refine.py` (segment probes, boundary windows, widening, stride narrowing), `analyze.py` (timeline assembly, schema-validated). Synthetic T2 videos prove: cuts on the exact frame (also with a non-zero start time), linear ramp with exact start and end frame, eased ramp within 2 frames at its soft ends, a ramp in a near-black shot, a 14 s ramp longer than the decoded window, a ramp where the sides move, a cut hidden in a fade through black, subtitles in the matte, a false 14 s segment refuted, a wrong coarse rectangle corrected. Fitted ramp edges are strictly monotone and within 2 px of the true edge on every frame in those tests |
| 4 | Scheduler, SQLite, Plex mapping, path mapping, HTTP API, status page | Not started |
| 5 | Dockerfile, GitHub Actions, Unraid template, install text | Not started |

Findings in slice 2 (measured; the first two corrected my own earlier statements):
- **Threshold:** `cropdetect`'s `limit` must be above the black level. Limited-range black is about 0.063 in both 8 and 10 bits; below that the whole frame counts as picture. My earlier "stable from 0.02 to 0.1" on the *Carrie* clip was vacuous below 0.063 (corrected in `docs/AUDIT_1.md`). Calibrated on noisy letterboxed SDR and PQ fixtures: the matte is found from 0.063 up; default 0.094.
- **Luma scale:** `signalstats`' `YBITDEPTH` is the estimated bits needed by the values (7 for ordinary 8-bit video), not the format's depth; using it doubled the brightness of 8-bit sources. The bit depth now comes from the pixel format.
- **Real clips (keyframe pass, about 5 to 23 s of scan per 90 to 180 s clip):** *Carrie* S01E06 is one 2.00:1 segment (no mattes); the 2.00 zoom is 0.8510. South Park (1080p), the DVD-style clip with non-square pixels (16:9 after the pixel aspect ratio) and the 3840x1600 clip each give one segment at 1.78, 1.78 and 2.40. *Below* S01E01 is a 2.20 picture in a 16:9 frame; with the first rules, dark scenes whose black edges look like mattes produced a string of false segments (1.73, 1.81, 2.15 ...). Two rule changes fixed most of them: a run's duration is its own sample span plus one sampling interval (not the distance to the next run), and a non-standard ratio must last 12 s. One 3-second "1.90" blip remains in *Below* and is marked `needs_verification`; whether it is a real insert or a dark scene cannot be decided from keyframes alone. *Scott Pilgrim* gives 1.78 (10 s), 1.85, and a low-agreement 1.90 tail that is probably credits.
- **Crystal Lake S01E07 ("4:3" per the user's description)** measures a constant **1.667 (5:3)**: the picture area is 3600x2160 with 120 px bars at each side on all 88 usable samples. It is a non-standard ratio in the preset list, so it is reported raw. Either the episode really is 5:3, or the user's description was approximate.
- **Open for slice 3:** refinement must re-sample `needs_verification` segments densely (a false crop from a dark scene is contradicted within seconds, a real matte is not), narrow each boundary window to the exact frame, and classify cuts and ramps. (Done; see the slice 3 findings.)

Findings in slice 3 (measured on the real clips in `samples/`, read-only, on this PC; nothing on the NAS or the Shield):
- **Below S01E01 (4K, Dolby Vision P8, 182 s):** the 3 s "1.90" blip is **refuted**: full-resolution probes inside it show the 2.20 picture, so it was a dark scene. Result is one 2.20 segment, full-resolution rectangle (0, 207, 3840, 1745). Refinement took 33 s.
- **Crystal Lake S01E07:** 1.667 (5:3) confirmed at full resolution (3600x2160, 120 px bars each side). It is not 4:3 as described.
- **South Park, DVD-style clip, 3840x1600 clip, *Carrie*:** one segment each (1.78, 1.78, 2.40, 2.00), rectangles exact, 0.5 to 17 s each.
- **Scott Pilgrim (4K, Dolby Vision P7, 181 s): NOT solved.** A per-frame edge dump shows a real, slow expansion: after about 1.5 s of a dark opening the picture appears as an inset, nearly 16:9 window (rows 198 to 1960, columns 330 to 3507) that grows steadily until it fills the 1.85 frame at **17.2 s** (a 15 s ramp, about 360 frames). The coarse pass called this "1.78 for 10 s, then 1.85" because a slow ramp keeps its shape. Refinement finds the **end** correctly (17.267 s), but the **start** is wrong (0.29 s) and the fit exceeds 2 px: the opening frames are a dark scene whose sides fall below the edge threshold, so only the bright doorway reads as picture (columns 1176 to 2690). The result is flagged `low_confidence` and `needs_review`, not silently wrong. The 1.90 "credits" tail was refuted. This clip is the real-world case the ramp logic must eventually pass (it behaves like the *Catching Fire* reference test).
- **Edge accuracy:** HEVC at low quality rings 1 to 2 px at a matte edge (10-bit synthetic fixture: top 44 and bottom 316 against a true 46 and 314), inside the 2 px budget; the 4 px-wide texture of the dim test picture shows the same effect sideways.
- **Time:** refinement per boundary is dominated by decoding 4K frames; the Scott Pilgrim clip (about 20 s decoded at full rate) took 114 s. Throughput for whole films is measured in Phase 6, not estimated here.
- **Design choices made in this slice (see AUDIT_1 DD17 to DD20):** numpy added; picture edges found against a threshold just above the measured matte noise (not a fixed limit); a segment's probes can confirm, refute (fold into a neighbour), or correct it; a window that does not end in steady picture is widened (up to 24 s each side) instead of cut off; anything unsettled is flagged, never guessed.

Findings in slice 1: the original *Carrie* file reads as `DV-P8` with HDR10+ and HDR10 extras (stream 0; the cover-art stream is ignored). A stream-copied clip loses the stream-level Dolby Vision record, so the analyzer reports `HDR10+` plus a `DV-RPU` extra from the frames; real files carry the record. Synthetic HDR fixtures need `setparams` as well as encoder flags, or the colour tags are lost.

## Phase 4 decisions taken (2026-10-28)

| Question | Answer |
|---|---|
| `scope_fill` for 2.39 and 2.40 | **Off**: fit with thin bars, nothing cropped |
| Extras and trailers | **Skip** (feature films and episodes only); extras use the contain-by-display-ratio fallback |
| Pause while Plex transcodes | **Yes**: pause the analyzer during transcodes; low CPU and disk priority regardless |
| YouTube library | **Skip** (user does not watch it from Plex); no host path needed |
| Quiet hours | Not asked; default is none, configurable (`QUIET_HOURS`) |

## Phase 3 GATE passed (2026-10-28)

The user answered all five items: G1 accept R2 at 1 frame, G2 defer subtitles (R13), G3 Dolby Vision not a concern (HDR10+ sample: *Carrie* S01E06, checked: crop detection works, no mattes, 2.00:1 native container, zoom 0.8510), G4 *Catching Fire* confirmed, G5 accept a short wrong-zoom time after seek or start. Details in `docs/AUDIT_1.md` section 10. Phase 4 questions are the next thing to ask.

## Phase 3 status (design audit, 2026-10-28): written

`docs/AUDIT_1.md` traces R1 to R16, D1 to D8, L1 to L10, the section 10 failure modes and every assumption above. Result: no requirement missing from the design, no lesson repeated, **six Gaps** to close (missing Dolby Vision profile 5 / HDR10+ / interlaced samples; GHCR package visibility; the override button and PM4K's key handling; six missing tests; Kodi's vertical-shift units; a test pattern that stays readable in dim ramps) and **five decisions for the user** (G1 R2 acceptance at 1 frame, G2 defer subtitles, G3 samples, G4 Catching Fire path, G5 wrong zoom for about 0.3 s after a seek or at start). The section 5 math was re-derived independently (`tools/audit_math.py`) and agrees. Read-only findings from PM4K's source and the Shield: PM4K plays through `xbmc.Player`, sets no view mode, resumes by start-then-seek, and hands Kodi the Plex part URL (or a mapped path); the skin's `enableautozoom` is false.

## Next

Answer the gate items in `docs/AUDIT_1.md`, then Phase 4 (analyzer service). Phase 5's first step is a read-only log of what PM4K passes to Kodi on a real title. The Shield shortcut to PM4K stays a deferred to-do (above).
