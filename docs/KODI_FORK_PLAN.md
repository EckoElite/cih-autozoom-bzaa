# Plan: a Kodi 21.2 build that no longer drops a frame at every zoom change

Why: `docs/ZOOM_SPIKE_REPORT.md` (Test 1b). Kodi's `CMediaCodecVideoBuffer::RenderUpdate` (in `xbmc/cores/VideoPlayer/DVDCodecs/Video/DVDVideoCodecAndroidMediaCodec.cpp`, present unchanged in the `21.2-Omega` tag) releases a frame without rendering it whenever the destination rectangle changes, because the video view is resized asynchronously. Measured on the Shield: 66 zoom changes, 66 missed frames, each exactly one frame; no loss while the zoom is held.

## Facts checked (2026-11-03)

| Item | Finding |
|---|---|
| Installed Kodi | 21.2 from Google Play, arm64-v8a, targetSdk 34, signed by the Play key |
| Kodi source | tag `21.2-Omega` exists; `Omega` branch is now 21.3 |
| Android build (Kodi's `docs/README.Android.md`) | Linux host, JDK 17, NDK r21e as documented (to confirm for 21.2), SDK platform 34, build-tools 33.0.1; steps: `tools/depends` bootstrap, configure, `make`; then `make -C tools/depends/target/cmakebuildsys`, `make`, `make apk`. Kodi has no GitHub Actions workflow for Android; build time and disk use are not documented |
| Package name | `APP_PACKAGE org.xbmc.kodi` is a build variable in `version.txt`. Whether changing it builds and runs cleanly is **not yet known** (a 2016 forum thread reports build errors with a non-default name; a 2023 one reports a hard-coded data path in a hand-edited APK) |
| Repo | public, so GitHub's free 4-vCPU Linux runners apply; each job has a 6-hour limit and about 14 GB of disk |
| Shield storage | 11 GB total, 1.1 GB free. Kodi's data folder is 1.6 GB (936 MB userdata, 658 MB add-ons), so a second full copy does not fit |

## Stages (each ends with a result I report before the next starts)

| # | Stage | Done when | Needs from you |
|---|---|---|---|
| 1 | **Prove the toolchain.** A GitHub Actions job builds an **unmodified** Kodi 21.2-Omega APK for arm64 (clone the tag, build depends, configure, `make apk`), with the depends cache, extra disk freed, and `APP_PACKAGE` set to `org.xbmc.kodi.cih`. | An APK artifact, and the log of what failed on the way | OK to add a workflow to the repo (a branch) |
| 2 | **Apply the patch.** Our change as one patch file in the repo, applied by the job after the clone. First version: render the frame instead of skipping it when the rectangle changes. | APK built from patched source | none |
| 3 | **Install beside your Kodi** (new package name, your Kodi untouched). Copy over only what is needed: PM4K, our add-on, the ScopeNox skin and settings, no thumbnails. | New Kodi starts, PM4K signs in, our add-on runs | Your OK to `adb install`; your Plex PIN once |
| 4 | **Measure.** Same SurfaceFlinger capture on the EEAAO ramp at stride 1 and 2; then your 240 fps film; HDR and Dolby Vision on the projector. | Missed frames per ramp (target 0), alignment in frames, HDR kept | You film and read the projector |
| 5 | **Optional: frame-synced geometry.** Apply the rectangle with a `SurfaceControl` transaction with the frame (mode B of the spike), which also shortens the 140 ms resize delay. Only if stage 4 shows the first version is not enough. | Measured against stage 4 | none |
| 6 | **Decide and hand over.** Keep or revert; document the update procedure (re-apply the patch on a new Kodi release). | `docs/` updated | Your decision |

## If package-name change does not work (stage 1 fallback)

Build with the stock name `org.xbmc.kodi` and install over your Play Store copy. That needs: a backup of the 1.6 GB data folder to this PC first (`adb pull`), uninstalling the Play version (the signature differs, and uninstalling deletes its data folder), installing our build, and restoring the backup. Reversible by reinstalling Kodi from Play and restoring the backup, but it replaces your working Kodi, so I would ask again before doing it.

## Risks and honest unknowns

- **Build effort.** Kodi's Android dependency build is large and the first attempts commonly fail on tool versions. I cannot give a reliable time; plan for several CI runs in stage 1, each possibly 1 to 3 hours. After the first success the depends cache should cut later runs a lot (not measured).
- **Disk on the runner.** About 14 GB free; Kodi's depends plus build may exceed it. The job removes unused preinstalled software to make room.
- **The simple patch may be enough or may not.** Removing the skip lets the frame show at whatever size the asynchronous resize has reached; the add-on's 316 ms lead already covers that delay for cuts, and for ramps each frame's size should simply trail slightly. Stage 4 tells us. Stage 5 is the fuller fix.
- **HDR and Dolby Vision** are untested under a rebuilt Kodi (the spike tested HDR10 clips only, and only by the compositor's frame record).
- **Updates.** Play Store Kodi will move on (21.3 is out); our build stays at 21.2 until re-based. The patch is a few lines, so re-applying should be quick.
- **Licence.** Kodi is GPL-2.0; the patch and build files live in this public repo.
- **Signing.** CI signs with a debug key. For later in-place updates a fixed keystore stored as a repository secret would be needed (not required for the first tests).

## What I will not do without asking

Install anything on the Shield, uninstall your Kodi, add secrets to the repo's settings, or merge the spike branch into `main`.

## Results so far (2026-11-03/04)

Test app: `org.xbmc.kodi.cih` beside the Play Store Kodi. Material played: *Everything Everywhere All at Once*, the matte ramp at film frames 41508 to 41642 (135 frames, 5.59 s). Frame delivery is read from `dumpsys SurfaceFlinger --latency` of Kodi's video layer (`tools/spike/watch_kodi_sf.py`); the 240 fps phone clips are the user's.

| Build | `ramp_stride` | Zoom commands sent | Resizes applied by Android | Frames shown in the ramp | Missed frames |
|---|---|---|---|---|---|
| Play Store Kodi 21.2 (before) | 2 | 69 | 66 | about 12 pictures a second (every change drops one frame) | about 66 |
| Patch 0001 (render the frame) | 2 | 69 | 66 | 576 in 23.98 s (whole window), gaps 41.5 to 42.0 ms | 0 |
| Patch 0001 | 1 | 135 | **89** (the UI thread merged 46) | 575 in 23.94 s, gaps 41.5 to 42.0 ms | 0 |
| Patches 0001 + 0002 (SurfaceControl) | 1 | 135 | all of them, no layout resizes (logcat: 1 `Changed` line, `placing the picture with SurfaceControl, view 1920x1080`) | 134 in the 5.59 s of the ramp, gaps 41.5 to 41.9 ms; no gap outside 20.8 to 62.5 ms in a 13.6 s window | 0 |

- User verdicts: patch 0001 at stride 1 "looked better" than stride 2, with "some stutter"; patches 0001 + 0002 at stride 1 "looks smoother, not perfect, I would not notice it without paying close attention".
- The add-on's calls took 0.25 to 3.4 ms and went out at most 4 ms late (patch 0002 run).
- **HDR10:** user confirmed the projector shows HDR10 during playback of the patches 0001 + 0002 build (2026-11-04). Dolby Vision not checked.
- **Phone clip IMG_8381 (patches 0001 + 0002, stride 1, 240 fps):** the picture's right edge moves from 1335 to 1459 px (x of the 1920 px frame) over about 19 to 24.5 s, and each step of it coincides with a new picture frame to within a few milliseconds (28 of 44 steps within 6 ms of the nearest frame update; the same method on IMG_8380, the earlier build, gives 27 of 34). That only shows the geometry changes arrive with a frame, not with the right one: a whole-frame lead error looks the same, and the film has no frame counter, so the lead needs the boundary test video (below). The left picture edge was not usable in this clip (the camera sees a fixed feature of the room next to it), so no width figure is given.
- **Not measured yet:** where the zoom lands against the picture (patch 0002 applies the geometry as soon as the frame is queued, so the add-on's `lead_ms` of 316 ms, tuned to the slow layout path, may now be too early; needs a 240 fps film); HDR10 and Dolby Vision on the projector; seeking into a ramp; the Yellow override key.
- Build notes: CI signs every build with a new debug key, so an installed test app cannot be updated in place (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`); each new build means uninstall, install, re-copy the data (the copy is scripted, 167 MB). A fixed signing key would avoid that; not set up (it needs a key kept outside the repo). With the dependency cache restored a build takes 14 minutes (61 minutes without).

## Boundary-test calibration of patches 0001 + 0002 (2026-11-03, user clip IMG_8384, 240 fps)

Method as Phase 2 (`docs/SPIKES.md`): `boundary_hdr10_23976.mkv` served from the PC, `tools/spike_a/drive_boundary.py` sends each cut's zoom 0.10 to 0.28 s before the cut (our add-on switched off for the run), the burned-in 14-bit counter gives the content frame and the dark plate's width gives the zoom. New decoder `decode_capture2.py` reads the counter at any zoom (it scales the bit positions with the measured plate width); `analyze_boundary2.py` finds, per cut, the camera frame where the new content starts (local robust line through the decoded frames) and where the plate width crosses its midpoint.

| Result (21 of 28 cuts usable: 3 cuts in the clip's 8x-speed tail, 4 with no usable width step) | Patched Kodi, SurfaceControl | Phase 2, Play Store Kodi |
|---|---|---|
| Delay from the command (on the player clock) to the picture, mean | **0.266 s** (6.4 frames) | 0.316 s (7.6 frames) |
| Spread of that delay (standard deviation) | 0.031 s (**0.74 frames**) | 0.039 s (0.93 frames) |
| Range | 0.215 to 0.317 s | 5.9 to 9.1 frames |
| Frame-locked (zoom lands on a content-frame boundary) | yes: the offsets from the content change are whole numbers within about 0.2 frame | yes |
| With one fixed lead equal to the mean: exact / within 1 frame | 12 of 21 / 15 of 21 (criterion: the delay's distance from the mean) | 9 of 25 / 23 of 25 |

Reading: the SurfaceControl path moved the delay by only about 50 ms and trimmed the spread a little; it did not make the landing exact. The remaining spread is not the UI thread. The commands reach Kodi at an arbitrary moment relative to its frame cycle, and Kodi applies a new rectangle when it next renders a frame (`RenderUpdate`), which is some frames before that frame is shown; the picture changes at the display time of whichever frame is on screen then. Part of the spread may also be the PC driver's own timing (HTTP round trips of 3 to 20 ms while it estimates Kodi's clock); an in-Kodi add-on has no network leg, so its figure may be better, and that has not been measured on this build.

Settings changed on the **test Kodi only** (not the daily Kodi): `lead_ms` 266, `edge_bias` `centered` (the user prefers the zoom to aim for the exact frame over the shrink-early bias; the daily Kodi keeps its settings until told otherwise).

What would make it exact (not done; needs the user's decision): apply each frame's rectangle at that frame's display time. `RenderUpdate` already receives the frame's `displayTime`; the Java side could schedule the `SurfaceControl` transaction for just before that time, so the geometry follows the frame instead of the render moment. Landing would then depend only on which frame's render sees the new zoom, which the add-on can aim at much better than a fixed delay. An even more direct form would let the add-on name the frame. Both are changes to Kodi's native renderer and need more builds and filmed runs.

## Frame-locked zoom table (patch 0003) on the Shield (2026-11-04)

Build: CI run 41781145170 (renderer reads `special://temp/cih_zoom.txt` with POSIX open/read; add-on v0.3.0 with ack handshake).
Test app `org.xbmc.kodi.cih`, `lead_ms` 266 / `edge_bias` centered (unused in table mode), `ramp_stride` 1, 23.976 Hz output, HDR10.

| Check | Result | Evidence |
|---|---|---|
| Table loaded, acknowledged, add-on keeps it (no fallback) | Yes | kodi.log: "CIH zoom table loaded: 1060 entries", add-on "zoom table written and taken" |
| EEAAO ramp 1731.23-1736.61 s | 130 zoom steps, one per video frame (41.0-42.0 ms apart), 0.7865 -> 1.0, monotonic | kodi.log "CIH zoom ... at picture time ..." |
| User's eyes (EEAAO ramp) | "pretty smooth, best it has been so far" | user, 2026-11-04 |
| Boundary video, all 216 table entries | applied, none missed; log picture time within +-0.5 ms of each entry's time | kodi.log vs `drive_*.table.txt` |
| Video layer frame pacing (SurfaceFlinger) | 2221 frames, 0 long gaps, 0 short gaps, mean 41.71 ms | `watch_kodi_sf.py` capture |
| Cut timing on the wall (240 fps clip IMG_8388, 25 usable cuts) | zoom lands -0.12 content frames from the new picture (mean; sd 0.09; range -0.26 .. +0.08). All 25 within +-0.5 frame | `decode_capture2.py` + `analyze_boundary2.py` |
| Ramp shape on the wall (eased, linear) | follows the table with an offset of -0.05 content frame; plate-width noise 3.3 / 5.1 px rms (about 1 px per frame step), so per-step smoothness cannot be resolved from plate width | clip IMG_8388 |

For comparison, timed commands (patch 0001 + 0002) gave a mean latency of 0.266 s with 0.74-frame sd.
Cuts now land on the exact frame within the camera's resolution (0.1 frame), so R2's "<= 1 frame, target 0" is met for cuts.

Not yet verified: Dolby Vision, seek into the middle of a ramp (table lookup is by picture time, so expected to work), the Yellow override key,
the third (near-black) ramp and cuts 1776/2112 (skipped by the analysis: no width step / no code), per-step ramp smoothness by camera, and the daily Kodi (this is only in the `.cih` test app).

Test notes: the boundary driver's `--table` mode needs `service.cih.zoom` disabled (it withdraws tables it did not write) and writes the table after the
stop and before the open (stopping the renderer removes it). `decode_capture2.py` geometry constants are per camera position (IMG_8384, IMG_8388 noted in the file).
