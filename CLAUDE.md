# CIH Auto-Zoom: Build & Test Instructions for Claude Code

> **Setup:** Put this file as `CLAUDE.md` at the root of an empty folder on the Windows PC (suggested: `C:\Users\Chaz\dev\cih-autozoom`). Start Claude Code in that folder.
>
> **Before doing anything:** read this whole file.

---

## 0. How to work from this file

- This file is the spec, the plan and the rules. Work phase by phase (§7).
  - Every phase ends with an **Exit check**.
  - Phases marked **GATE** stop and wait for the user's decision.
- Keep `docs/PROGRESS.md` current with:
  - the phase you're in;
  - what is **verified**, and how;
  - what is **assumed**;
  - what's next.
- Never report something as verified unless you ran it, or the user confirmed it on the screen. Label every assumption.
- **About the user:**
  - Very technical: PeopleSoft/SQL Server admin and developer. Comfortable with config files, logs and math.
  - Prefers tables and exact numbers.
  - Expects honest corrections when something was wrong.
- **How to write to the user:**
  - Keep status updates short.
  - When something needs the user's hands or eyes (the projector screen, the TV remote, the Unraid UI), give exact numbered steps.

---

## 1. Goal

Get Constant Image Height (CIH) playback on a 2.35:1 screen. The projector lens is fixed to fill the screen width, so the 16:9 output frame overhangs the top and bottom of the screen.

Every video must be scaled automatically so its picture sits inside the screen at constant height, with no manual action:
- any source ratio;
- films whose ratio changes mid-film.

All of this must work while keeping 4K HDR10 and Dolby Vision playback on an NVIDIA Shield TV Pro.

---

## 2. The system (facts supplied by the user)

| Item | Value |
|---|---|
| Screen | 140" diagonal, 2.35:1, Elunevision Reference Studio 4K, 1.0-gain matte white |
| Projector | XGIMI Titan Noir Max. Lens zoomed **once** to fill the screen width; the 16:9 frame overhangs top and bottom. No per-title lens changes. |
| Playback device | NVIDIA Shield TV Pro. Android TV 11 (API 30). Kodi reports output 3840×2160 @ 59.94 Hz. LAN IP is kept in `.env` as `SHIELD_IP` (may change; confirm). |
| Kodi | 21.2 Omega (Python 3). Skin: ScopeNox, the LLuth0r "ScopeNox-Updated" fork. |
| Current front end | **Plezy**. Open-source Plex/Jellyfin/Emby client (GPL-3.0, Flutter, `github.com/edde746/plezy`). Uses mpv as its Android playback engine and can launch an external player. |
| Media server | Plex Media Server on an **Unraid** NAS; media lives on the NAS |
| Libraries to handle | Movies, TV shows, concerts/other. **2,000+ titles.** |
| 3D | 3D MVC ISO/MKV plays on a separate Zidoo Z9X Pro, never on the Shield. The analyzer **skips** ISOs and 3D files (filename tags `3d`, `hsbs`, `fsbs`, `hou`, `fou`, or an MVC stream). |
| Dev machine | Windows gaming PC, where Claude Code runs. Installed tools are **unknown**, so check. **No SSH to the NAS**: it's reachable only through SMB shares and the Unraid web UI. |
| Shield access | ADB over the network (the user will turn on Network debugging) |
| Deploy path | GitHub repo → GitHub Actions → image on GHCR → Unraid container template |
| Test films owned | Nolan IMAX films (confirm exact titles, editions and file paths with the user), *Avatar: The Way of Water*, and *The Hunger Games: Catching Fire* (2013), the **reference ramp test**: see T8. Confirm the user owns it and get the file path. |

---

## 3. Decisions already made (don't reopen without the user)

| ID | Decision |
|---|---|
| D1 | Geometry: the lens stays fixed to fill the width. All picture scaling happens in the player. The projector is never touched. |
| D2 | 16:9 and narrower content is shown **in full**, shrunk to screen height with black sides. Never cropped. |
| D3 | Variable-aspect-ratio (VAR) films **follow every ratio change**. |
| D4 | Ratio detection is **precomputed offline**: each file is analyzed once on the NAS. **No live screen capture.** (See L1.) |
| D5 | The analyzer runs as a Docker container on Unraid, deployed from GHCR through an Unraid template. |
| D6 | Which player applies the zoom is decided **after the prototype spikes** (Phase 2 GATE). |
| D7 | Subtitles are low priority (P2): nice to have; at minimum, document how to keep them on-screen. |
| D8 | *(Default chosen by Claude in chat; the user may change it.)* Timelines are stored centrally in the container's appdata (SQLite plus JSON) and served over HTTP. Media mounts are **read-only**. Writing sidecar files next to media is optional and off by default. |

---

## 4. Lessons from earlier attempts (do not repeat these)

These come from an earlier chat in which Claude built and then found problems in several components.

| ID | Finding | Consequence |
|---|---|---|
| L1 | Kodi's `xbmc.RenderCapture` returns **black frames on Android** when MediaCodec (Surface) decoding is on, which has been the case since Kodi 16. Turning Surface off costs HDR. | No live-capture detection on the Shield. Capture works on **Windows** Kodi, so use it only for automated test measurements (T4). |
| L2 | The RenderCapture flag API was removed in Kodi v17: `CAPTURE_FLAG_CONTINUOUS`, `getCaptureState()`, `waitForCaptureStateChangeEvent()`. | The current API is `capture(w, h)` then `getImage(msecs)`, returning a BGRA bytearray. |
| L3 | Zoom factors given earlier (1.32 / 1.27 / 1.175) were **inverted**. | Correct behaviour is to **shrink** non-scope content; see §5. |
| L4 | Two chat-built add-ons are broken (L1, L2, L3): `script.cihautonox.tools` (a patched copy of drthaipham/CIHAutoNox) and `service.cihautonox.autozoom`. | If either is installed on the Shield, tell the user, then disable or remove it **with permission**. |
| L5 | The upstream CIHAutoNox repo has indentation errors in `GetAspectRatioFromFrame()` and uses Python 2 (`urllib2`). Its `SCRunActivity()` is a no-op, and it never sends a zoom command at all. | Reference only. Don't reuse the code. |
| L6 | LLuth0r's ScopeNox-Autozoom (the skin's "autozoom 16:9" setting) infers ratio from **file resolution**. That's wrong for 4K and blind to mattes and VAR. | It must be **off**, so it doesn't fight this system's zoom. |
| L7 | Kodi 21 requires `xbmc.python` ≥ 3.0.0 (Omega ships 3.0.1). Skins need `xbmc.gui` 5.17.0. | Set this in every `addon.xml`. |
| L8 | Earlier code called builtins with a space before the parenthesis, e.g. `Skin.setbool (osd169)`. Whether Kodi tolerates that was never verified. | Always write `Skin.SetBool(name)`. |
| L9 | Android 11 scoped storage means Kodi's file browser may not see files other apps wrote unless it has "All files access". | Add-ons live under `/sdcard/Android/data/org.xbmc.kodi/files/.kodi/addons/`. Prefer adb push or Kodi repo/zip install over file-manager copies. |
| L10 | Kodi configures external players in `userdata/playercorefactory.xml`, not `advancedsettings.xml`. | Use the correct file if the external-player route is chosen. |

---

## 5. Geometry and zoom math (verify on hardware, don't trust)

### 5.1 Definitions

- **Output frame R**: 3840×2160 (16:9 ≈ 1.7778).
- **Screen ratio S**: nominally 2.35. Phase 1 measures what's actually on the wall.
- **Visible band**: the rows of R that land on the screen.
  - Nominal height = 3840 ÷ 2.35 = **1634 px**.
  - Nominal top row = **263**, bottom row = **1897**, but only if the image is vertically centred.
  - Phase 1 measures the real `band_top` and `band_bottom`, because lens shift may offset the band.
- **Active picture A**: the content area after removing baked-in black bars (mattes) on all four sides.
  - Its aspect `a` is computed with the pixel aspect ratio (SAR) applied, i.e. it's the display aspect ratio (DAR).

### 5.2 Rule

**Contain:** scale the active picture so it fits entirely inside the band, centred on the band.

*Optional setting `scope_fill`:* for content wider than S (2.39 or 2.40), fill the band height and crop a sliver off the sides instead. This is a user choice; ask in Phase 4.

Players normally fit the **whole video frame** (container plus mattes) into R. Define:
- `(w0, h0)`: displayed size of the active picture at the player's default fit, in raster pixels;
- `bandW = 3840`;
- `bandH = band_bottom − band_top`.

Then:
- **Scale:** `s = min(bandW / w0, bandH / h0)`, or `max(...)` when `scope_fill` applies to `a > S`.
- **Vertical shift:** whatever moves the centre of the active picture onto the centre of the band. This matters if mattes are asymmetric or the band is off-centre.

### 5.3 Nominal table (centred band, 16:9 or native-ratio container)

Regenerate this table from measured geometry in Phase 1. Don't hard-code it.

| Active ratio | Scale `s` | Displayed active size (px) |
|---|---|---|
| 1.33 (4:3) | 0.7565 | 2179 × 1634 |
| 1.78 (16:9) | 0.7565 | 2905 × 1634 |
| 1.85 | 0.7872 | 3023 × 1634 |
| 1.90 (IMAX digital) | 0.8085 | 3105 × 1634 |
| 2.00 | 0.8511 | 3268 × 1634 |
| 2.20 | 0.9362 | 3595 × 1634 |
| 2.35 | 1.0000 | 3840 × 1634 |
| 2.39 | 1.0000 fit / 1.0170 fill | 3840 × 1607 fit / 3840 × 1634 fill (≈0.8% cropped each side) |
| 2.40 | 1.0000 fit / 1.0213 fill | 3840 × 1600 fit / 3840 × 1634 fill (≈1% cropped each side) |

### 5.4 Mapping to players (each verified in Phase 2)

**Kodi**

```
JSON-RPC Player.SetViewMode {"viewmode": {"zoom": s, "verticalshift": v}}
```

Things to verify on the device:
- that numeric `zoom` is accepted (believed range 0.5–2.0);
- that it's relative to the Normal view mode fit;
- the units of `verticalshift`;
- that the default view mode is **Normal**;
- whether Kodi saves per-file view settings, which could fight the system.

**mpv**

- `video-zoom = log2(s)`.
- Vertical placement via `video-align-y` / `video-pan-y`. Pan is a fraction of the displayed video size; verify.

**All players**

The player's clock may not equal raw stream timestamps: some files have a non-zero `start_time`. Normalize every timeline to the **player's timebase** (0 = first frame) and verify this in Phase 2.

---

## 6. Requirements (traceability IDs; every one needs a design element *and* a test)

| ID | Requirement |
|---|---|
| R1 | Automatic §5 zoom on every playback, zero user action. |
| R2 | VAR films: zoom changes at each ratio change, **seamlessly**. No visible spill past the screen edges and no visible bars inside them.<br>• Hard-cut changes (the normal case) **snap** on the exact boundary frame. No animated zoom.<br>• Gradual matte moves ("ramps", e.g. the *Catching Fire* launch-tube rise) are followed **continuously, frame by frame**. The zoom tracks the measured matte curve from the ramp's first frame to its last:<br>&nbsp;&nbsp;– **no jump:** never snap to the final ratio partway through, or before or after the ramp;<br>&nbsp;&nbsp;– **no jitter:** the picture edge moves in one direction only, with no reversals, stalls or uneven steps.<br>• Target: **0 frames** error. Acceptance: **≤ 1 frame** at 24p, measured per §8 T6 and T8, unless Phase 2 proves no route can reach it. In that case report the best achievable figure and get the user's sign-off. |
| R3 | Works with 4K HDR10, Dolby Vision (profiles 5 and 8 at minimum), HLG and SDR, **without losing** HDR/DV output, refresh-rate switching or audio passthrough. |
| R4 | No stutter or dropped frames caused by this system. |
| R5 | Seek, chapter skip, pause/resume, resume-from-position and next episode all get the correct zoom immediately. |
| R6 | When no timeline exists:<br>• fall back to "contain" using the **container's DAR**, which never cuts picture and is correct for native-ratio encodes;<br>• request priority analysis;<br>• log it.<br>When the analyzer is unreachable: use the local cache, else the fallback. |
| R7 | Analyzer covers all libraries and skips ISO/3D/audio. It is resumable and idempotent, re-analyzes when a file changes, and is throttled so Plex streaming isn't affected. |
| R8 | Analyzer accuracy: no false ratio changes from dark scenes, fades, credits on black, burned-in subtitles in the matte area, or studio logos. Must pass the §8 synthetic tests and the T6 real-film checks. |
| R9 | Timeline lookup by NAS/SMB/Plex path (with path mapping), Plex part ID / rating key, and content hash. |
| R10 | Deployed via GHCR and an Unraid template. **No secrets** in the repo, the image or logs; the Plex token comes from a container env var. |
| R11 | Manual override from the remote: cycle ratio presets, or disable for this title. Persisted per title. |
| R12 | Every setting is changeable without code edits: screen ratio, band rows, `scope_fill`, `lead_ms`, `edge_bias`, analyzer URL, on/off, log level. |
| R13 | *(P2)* Subtitles land inside the visible band. |
| R14 | Container status page: queue, progress, errors, per-title timeline view. Each ramp gets a **per-frame plot** of the measured edge and the fitted curve, so the user can review it. |
| R15 | Conflicting components are disabled with permission: L4 add-ons, L6 skin autozoom. |
| R16 | Documentation: install, user guide, troubleshooting, recalibration. |

---

## 7. Phases

### Phase 0: Environment and discovery

1. **Check the PC** for: `git`, GitHub CLI `gh`, Python 3.12+, `ffmpeg`/`ffprobe`, Docker Desktop (with WSL2), and `adb` (Android platform-tools).
   - Show a present/missing table.
   - **Ask before installing**; suggest the `winget` commands.
   - Docker Desktop is optional: CI can build and test the image if the user declines.
2. **Ask the user** for:
   - their GitHub username;
   - a repo name (suggest `cih-autozoom`);
   - whether the repo is public or private.
     - A private GHCR image means Unraid must log in to GHCR with a personal access token.
     - Public is simpler; the repo holds no secrets either way.
3. **Set up the Shield connection.**
   1. Have the user enable Developer options, then Network debugging.
   2. Run `adb connect <ip>:5555` and have the user accept the prompt on the TV.
   3. Record the model, Android version and Kodi version: `adb shell dumpsys package org.xbmc.kodi`, then pick out `versionName`.
4. **Set up Kodi remote control.**
   1. Have the user turn on Settings → Services → Control → *Allow remote control via HTTP* and set a username and password.
   2. Verify `JSONRPC.Version` from the PC.
   3. Keep the credentials in `.env` only (gitignored).
5. **Inventory Kodi add-ons** with `Addons.GetAddons`. Flag L4/L6 items and **ask** before disabling anything.
6. **Collect from the user:**
   - the Plex server URL;
   - the Plex token, stored in `.env` only and never echoed back in full;
   - every library's paths in three forms: the SMB share path, the Unraid path (`/mnt/user/...`), and the path as Plex sees it.
7. **Get a local sample set**: the user copies these to the PC.
   - one Nolan VAR film;
   - one native 2.39 encode (e.g. 3840×1600);
   - one scope film matted inside a 16:9 frame, if any exist;
   - one 16:9 TV episode;
   - one Dolby Vision file;
   - one 4:3 file;
   - one DVD-style file with non-square pixels, if available.

**Exit check:** `docs/PROGRESS.md` holds the environment table and every fact above. Nothing is silently assumed.

### Phase 1: Geometry calibration

1. **Build calibration videos** in `tools/testpatterns/` with ffmpeg, two versions: 3840×2160 SDR, and 10-bit HEVC tagged HDR10. They contain:
   - numbered horizontal lines every 4 px across rows 200–330 and 1830–1960;
   - a centre cross and edge ticks;
   - "ratio frames" with coloured 4 px borders at 1.33, 1.78, 1.85, 1.90, 2.00, 2.35 and 2.39, each matted inside 16:9.
2. **Have the user play them** on the Shield in Kodi (view mode Normal, zoom 1.0) and report:
   - which numbered row sits exactly on the screen's **top** edge and which on the **bottom**;
   - whether the left and right edges of the image meet the screen edges (any overscan or shortfall).
3. **Write `config/geometry.json`** containing `band_top`, `band_bottom`, the measured ratio, and whether the band is centred. Regenerate the §5 table with a script.

**Exit check:** `geometry.json` is committed, the table is regenerated, and the user has confirmed.

### Phase 2: Player spikes → **GATE**

**Goal:** find which player can apply a **timed zoom during playback while keeping HDR10/DV**. Build throwaway prototypes only. Test each with the calibration videos plus one real HDR10 sample and one real DV sample.

**Spike A: Kodi's internal player.**
- Send `Player.SetViewMode` numeric zoom and vertical shift **live**, with MediaCodec (Surface) on.
- Check:
  - the zoom applies without a black flash or rebuffer;
  - ratio-frame borders meet the screen edges;
  - HDR and DV stay active (projector signal info);
  - how fast a change takes effect;
  - per-file view-state saving;
  - that resume from position works.

**Spike B: Plezy's built-in mpv.**
- Read Plezy's source to answer:
  - How is mpv embedded on Android (video output, hardware decoding)?
  - Does its "custom mpv configuration" accept `video-zoom` and alignment properties, or scripts?
  - Is there any control channel?
- Test `video-zoom` with HDR10/DV on the Shield through its custom config.
- Estimate the effort for a fork: patch surface, and how hard it would be to keep in step with upstream releases.

**Spike C: Plezy handing off to Kodi as the external player.**
- Check:
  - what Plezy hands Kodi (a Plex part URL?);
  - whether Kodi plays it;
  - whether watched progress syncs back to Plezy/Plex;
  - whether a Kodi add-on can extract the Plex part ID from that URL.

**Spike D: mpv-android as Plezy's external player.**
- Check HDR10/DV output on the Shield, `video-zoom` while in HDR, support for Lua scripts, and whether a script can fetch a timeline over HTTP.

**Frame-accuracy test (every spike).** This decides whether R2 can be met, so measure it, don't estimate it.
1. Generate a boundary test video: a burned-in frame counter, coloured ratio-frame borders, and hard cuts between 2.39 → 1.90 → 2.39 → 1.78 at known frame numbers. Make SDR and HDR10 versions, at 23.976 and 24 fps.
   - **Add ramp sections:** a 3 s eased (non-linear) matte opening from 2.39 to 1.78, a linear one, and the same ramp in a near-black shot.
   - Paint fine tick marks on the picture's top and bottom edges so on-screen smoothness can be judged in slow motion.
2. Drive the zoom from a timeline at those frames, using that route's real mechanism.
3. Measure:
   - **latency:** frames between the cut and the zoom taking effect;
   - **jitter:** spread over at least 20 boundaries;
   - whether the change itself causes a black flash or dropped frame.
4. **How to measure on the Shield:** frames can't be captured there (L1). Have the user film the screen in slow motion (240 fps) with a phone, framed to include the screen edges, and send the clip. Read the burned-in counter against the moment the borders move. On Windows Kodi/mpv, also measure automatically (T4).
5. **Ramp smoothness:** for each route, can it apply a zoom change **every frame** for several seconds?
   - Measure in the slow-motion clip whether the picture edge moves without reversals, stalls or doubled steps.
   - Check whether per-frame property updates themselves cause dropped frames.
   
   This is a pass/fail row in the decision matrix.
6. **Frame-locked options to investigate:** a property set from a script is asynchronous, so it may land a frame or more late with variable jitter. For each route, check whether the zoom can be tied to the frame's presentation time. Candidates:
   - in a Plezy/mpv fork, applying the change in the render path at a given presentation timestamp;
   - in Kodi, whether a view-mode change takes effect on the next presented frame, so a fixed `lead_ms` can cancel the latency.
   
   Report what each route can actually reach.

**Deliverable:** `docs/SPIKES.md`.
- A decision matrix:
  - **Rows:** HDR10 kept · DV kept · live zoom works · geometry accurate · **zoom latency and jitter in frames** (§7 Phase 2, frame-accuracy test) · **visible glitch on change** (black flash, dropped frame) · **ramp smoothness** (per-frame updates, no jitter) · timed changes feasible · seek/resume hooks · subtitle control · refresh-rate switching · audio passthrough · fits the Plezy workflow · build effort · maintenance risk.
  - **Columns:** A–D.
- One recommendation.

**GATE:** the user picks the route.

### Phase 3: Audit pass 1 (design audit) → **GATE**

See §9. No production code before this gate.

### Phase 4: Analyzer service (`analyzer/`)

**Stack**
- Python 3.12 and a pinned static ffmpeg build in the image, able to decode HEVC, AV1, VP9, H.264 and MPEG-2.
- FastAPI + uvicorn, SQLite in WAL mode.
- Runs non-root as `PUID=99 PGID=100` (Unraid's nobody:users).

**Per-file pipeline**

1. **`ffprobe` the file.** Collect:
   - duration and `start_time`;
   - codec, width/height, SAR/DAR, frame rate;
   - HDR type: transfer `smpte2084` = HDR10, `arib-std-b67` = HLG, Dolby Vision configuration record = DV profile.

   Apply the skip rules:
   - ISO, 3D or audio-only files;
   - broken files: log them and move on.
2. **Coarse pass.**
   - Decode keyframes only (`-skip_frame nokey`), downscale for speed (e.g. 960 px wide; scale the results back up).
   - Run filters `cropdetect` (with `reset` so each sample is independent) plus `signalstats`.
   - Record per sample: timestamp, crop rectangle, average brightness (`YAVG`).
   - Tune the `cropdetect` black limit separately for SDR, HDR10/PQ (10-bit limited range) and DV profile 5, using test fixtures.
3. **Gate the samples.**
   - Drop samples darker than a threshold: fades, night scenes, credits on black.
   - Treat burned-in subtitle text inside a matte as noise: a change must persist across N samples **and** the minimum duration.
4. **Turn each crop rectangle into an active aspect** (with SAR applied). Snap it to presets {1.33, 1.78, 1.85, 1.90, 2.00, 2.20, 2.35, 2.39, 2.40} within ±1.5%; otherwise keep the raw value.
   - Detect **width** cropping too, so pillarboxed 4:3 and windowboxed content come out right.
5. **Segment** with hysteresis and a minimum segment length (default 3 s, configurable), merging any flicker. **Ramps are exempt:** their in-between states must never be merged away or snapped to a preset.
6. **Refine each boundary.** Decode every frame within ±(keyframe interval + 1 s) of each change and find the first frame of the new ratio. Record:
   - that frame's presentation time, normalized to `start_time`;
   - its frame number.
   
   Classify the boundary:
   - **`cut`:** the matte changes in one frame. Confirm a scene change at that frame (e.g. ffmpeg `scdet`); a matte change *without* a scene cut is suspicious, so flag it for review.
   - **`ramp`:** the matte edge moves over several consecutive frames (an on-screen frame reveal). Handle it like this:
     - **Find the full extent.** Widen the full-rate decode window step by step until the matte has been stable for at least 1 s on **both** sides. A long ramp must not be cut off at the refinement window's edge.
     - **Measure every frame.** Record the top and bottom matte edges, and the sides if they move, for every frame of the ramp. Ramps are short, so store **every frame**; don't thin to keyframes.
     - **Dark frames.** Ramps often happen in dim shots (the *Catching Fire* tube is dim), where the dark-sample gate or `cropdetect` can confuse dark picture with matte. On ramp frames, find the edge by where the **true matte black** ends, not by an average-brightness test. Mark frames where the edge can't be read and fill them from the fitted curve.
     - **Remove measurement noise.** Raw per-frame edges carry ±1–2 px of noise, which would play back as jitter. Fit a **monotonic** smooth curve (e.g. isotonic regression, then a monotone spline) that stays within 2 px of every readable measurement and keeps the true start and end frames. Store the fitted per-frame edges as the timeline, and keep the raw values for the status-page plot.
     - **Flag doubt.** If too many frames are unreadable, the fit exceeds 2 px, or the motion isn't one-directional, flag the ramp `low_confidence` for the user to review.
7. **Write** the timeline JSON and the index row.

**Timeline schema v1**

```json
{
  "schema": 1,
  "analyzer_version": "1.0.0",
  "params": {"min_segment_s": 3, "dark_yavg": 0, "cropdetect_limit": 0},
  "file": {"path": "/media/movies/...", "size": 0, "mtime": 0,
           "quick_hash": "sha1(first 4MiB + last 4MiB + size)"},
  "plex": {"part_id": 0, "rating_key": 0},
  "video": {"width": 3840, "height": 2160, "sar": "1:1", "dar": 1.7778,
            "fps": 23.976, "start_time": 0.0, "duration": 0.0,
            "hdr": "SDR|HDR10|HDR10+|HLG|DV-P5|DV-P7|DV-P8"},
  "segments": [
    {"start": 0.0, "end": 0.0,
     "active": {"x": 0, "y": 276, "w": 3840, "h": 1608},
     "aspect": 2.388, "preset": 2.39, "confidence": 0.98,
     "boundary": {"kind": "cut", "frame": 0, "scene_cut": true}}
  ],
  "ramps": [
    {"start": 0.0, "end": 0.0, "start_frame": 0, "end_frame": 0,
     "confidence": 0.0,
     "frames": [{"f": 0, "t": 0.0, "top": 0.0, "bottom": 0.0,
                 "left": 0.0, "right": 0.0, "readable": true}]}
  ],
  "flags": ["var", "low_confidence"]
}
```

**Scheduler**
- Scan the media roots on a timer. Unraid user shares are a FUSE filesystem, so **don't rely on inotify**. Also poll Plex for recently added items.
- Queue priority:
  1. on-demand requests from players;
  2. recently added;
  3. backlog, newest first.
- Throttling:
  - default concurrency 1, running at `nice 19` / idle I/O priority;
  - optional quiet hours;
  - optional pause while Plex reports transcode sessions (`/status/sessions`).
- Resumable after restarts.
- Re-analyze when size or mtime changes, or on a major `analyzer_version` bump.

**Hardware decode**
- If `/dev/dri` exists, benchmark VAAPI/QSV decoding against software decoding.
- Keep software as the default unless the hardware path produces **identical crop results** on the fixtures.

**API** (port default 8765, LAN only, optional API key)

| Route | Purpose |
|---|---|
| `GET /api/v1/timeline` | Look up by `path=`, `part_id=`, `rating_key=` or `hash=`. Returns 404 if missing; adding `&analyze=1` queues it at priority. |
| `POST /api/v1/analyze` | Queue a file now. |
| `GET/POST /api/v1/override` | Per-title manual preset or disable. |
| `GET /api/v1/status`, `/queue`, `/health` | Monitoring. |
| Web UI `/` | Queue, progress, errors, title search, timeline bar visualization. |

**Path mapping**
- A config list of equivalent roots, for example `/media/movies` ⇄ `/mnt/user/media/movies` ⇄ `smb://NAS/media/movies` ⇄ the path Plex reports.
- Keep paths case-sensitive.

**Plex mapping**
- Regularly read Plex library sections → items → Media/Part (part ID, file path, size).
- Get the token from the `PLEX_TOKEN` env var, and **redact `X-Plex-Token` from every logged URL**.

**Unraid template** (`unraid/cih-analyzer.xml`)
- Repository `ghcr.io/<user>/cih-analyzer:<tag>` and a WebUI port.
- **Paths:**
  - each library mounted read-only at `/media/...`;
  - `/config` → `/mnt/user/appdata/cih-analyzer`.
- **Variables:**
  - `PLEX_URL`, and `PLEX_TOKEN` (masked);
  - `PUID`, `PGID`, `TZ`;
  - `CONCURRENCY`, `QUIET_HOURS`, `SCAN_INTERVAL`, `API_KEY`.
- **Device:** optional `/dev/dri`.
- Write step-by-step install text for the Unraid web UI: Docker tab → Add Container, either from the template URL or by filling the fields manually. The user has no SSH.

**CI** (`.github/workflows/`)
- `ruff`, unit and synthetic-video tests, image build.
- Push to GHCR on `main` and version tags; amd64 only, since Unraid is x86-64.

### Phase 5: Player integration (the route chosen at the Phase 2 gate)

**Shared logic** (spec it once in `docs/ZOOMCORE.md` and implement it in the chosen player):
- **On playback start:**
  - Identify the item from its path, Plex part ID or URL.
  - Fetch the timeline with a 1 s timeout, keeping an in-memory and small on-device cache.
  - If there's no timeline, apply the R6 fallback.
  - Apply the zoom for the current position **immediately**.
- **During playback:**
  - Schedule each boundary against the player's clock, not a coarse poll. Fire at `boundary_time − lead_ms`, where `lead_ms` is that route's measured latency from the Phase 2 frame-accuracy test (it's a setting, but the measured value is the default).
  - **Cuts snap:** apply the new zoom in one step. There is **no transition-speed setting**: any animated zoom across a hard cut would show spill or bars for its whole duration.
  - **Ramps:** apply the stored per-frame curve. For each displayed frame, set the zoom (and vertical shift) computed from **that frame's** fitted matte edges, tied to its presentation time with the same `lead_ms` compensation.
    - Never skip ahead to the final ratio; never ease on a curve of its own.
    - On a seek into the middle of a ramp, start at that frame's value.
    - `edge_bias` doesn't apply inside a ramp.
    - **If Phase 2 shows the chosen route can't do smooth per-frame updates, don't fall back silently.** Stop at the Phase 2 GATE, show the user the measured behaviour, and let them choose. A frame-locked change in the player's render path (a fork) is the expected fix.
  - **`edge_bias`** (for whatever residual error remains):
    - `no_spill` (default): apply a *shrink* one frame early and a *grow* one frame late. Any error then shows as a frame of thin bars inside the screen, never as light past its edges.
    - `no_bars`: the reverse.
    - `centered`: no bias.
  - On **seek / chapter / resume**: recompute right away.
  - On **stop / end**: reset to Normal, zoom 1.0, so the next file and the menus start clean.
- **Override (R11):** a remote button cycles presets or disables the zoom for this title; persist it through the API. Ask the user which button.
- **Never touch** refresh-rate, audio or HDR settings.

**If Kodi** (`players/kodi/service.cih.zoom`)
- Python 3, `xbmc.python` 3.0.1, settings in the Kodi 19+ `settings.xml` format.
- `xbmc.Player` callbacks: `onAVStarted`, `onPlayBackSeek`, `onPlayBackSeekChapter`, `onPlayBackResumed`, `onPlayBackStopped`, `onPlayBackEnded`.
- Send zoom through `xbmc.executeJSONRPC`. Map the override button with a keymap plus `RunScript`.
- Excluded: Live TV, pictures, audio, 3D.
- Setting the ScopeNox masking booleans is optional (L8 syntax).

**If Plezy fork** (`players/plezy/`)
- Stays GPL-3.0. Keep the change isolated in one module of the mpv playback layer, so rebasing on upstream releases stays easy.
- Build the APK and install it with `adb install`, asking first.
- Document the rebase procedure.

**If Plezy → Kodi external**
- Plezy launches Kodi; the Kodi service pulls the Plex part ID out of the URL.
- Confirm progress sync works (Spike C).

**If mpv-android**
- A Lua script, plus a way to fetch timelines (Spike D decides the mechanism).

### Phase 6: Deployment

1. Tag `v1.0.0` and let CI publish the image.
2. The user installs the Unraid container from your written steps.
3. Verify `/health` from the PC.
4. Install the player component through adb, **asking first**, then verify it.
5. **Library rollout** in order: test films → Movies (newest first) → TV → other.
6. Report throughput (titles/hour, CPU load) and an ETA for 2,000+ titles. The user decides on throttling.

### Phase 7: Acceptance testing on the real system

Run T5 and T6 from §8, then fill in `docs/TEST_REPORT.md`.

### Phase 8: Audit pass 2 (implementation audit)

See §9.

### Phase 9: Handoff

Deliver:
- `docs/INSTALL.md`
- `docs/USER_GUIDE.md`:
  - how it behaves;
  - the override button;
  - how to re-analyze a title;
  - how to recalibrate after moving the projector.
- `docs/TROUBLESHOOTING.md`
- the final `PROGRESS.md`

---

## 8. Testing strategy

| Layer | What | Where |
|---|---|---|
| T1 Unit | Zoom math (re-derived from `geometry.json`), preset snapping, SAR handling, segmentation on synthetic sample streams, path mapping, schema validation, API routes | PC + CI |
| T2 Synthetic video | Videos generated by ffmpeg inside the tests (small, plus one 4K 10-bit HDR10). Ratio changes at known times: 2.39 → 1.90 → 2.39 → 1.78. Also covers:<br>• fades to black between ratios;<br>• a near-black night scene with mattes;<br>• burned-in subtitle text inside the bottom matte;<br>• a studio logo at 1.78 before a scope film;<br>• credits on black;<br>• 4:3 pillarboxed inside 16:9;<br>• windowboxed content;<br>• 720×480 non-square-pixel content;<br>• a file with non-zero `start_time`;<br>• **ramps**, all opening from 2.39 to 1.78: a linear one over 2 s; an eased one over 3 s; one in a near-black shot (edge barely above matte black); one spanning more than the refinement window.<br>Assert:<br>• cut boundaries land on the **exact frame**;<br>• every boundary is classified correctly as `cut` or `ramp`;<br>• fitted ramp edges stay within 2 px of the true edge on every frame;<br>• the fitted edges are strictly monotonic (no reversals);<br>• the ramp's start and end frames are exact;<br>• **zero** false segments. | PC + CI |
| T3 Player logic | Mocked Kodi modules (`kodistubs` package) or the equivalent for the chosen route, on a simulated clock: start, seek across boundaries, chapter skip, resume, pause, stop, next item, missing timeline, API down, override | PC + CI |
| T4 Automated geometry | Kodi for Windows on the PC (ask before installing) runs the same add-on. RenderCapture **works on Windows**, so capture frames of the calibration and ratio-frame videos and measure the displayed active rectangle against the expected one (±2 px at 4K-equivalent). Only applies if the Kodi route is chosen; otherwise find the equivalent (e.g. mpv screenshot on desktop). | PC |
| T5 Shield device | 1. Install via adb.<br>2. Drive playback over Kodi JSON-RPC (`Player.Open`, `Player.Seek`).<br>3. Read the log from `/sdcard/Android/data/org.xbmc.kodi/files/.kodi/temp/kodi.log`.<br>4. Measure CPU with `adb shell top -n 1`.<br>**Visual checks are the user's:** ratio-frame borders meet all four screen edges; HDR/DV flag in the projector's signal info; no dropped frames (user watches the Kodi debug overlay). Video frames can't be captured on the Shield (L1), and `adb screencap` normally shows black over video too. | Shield + user |
| T6 Real films | For the Nolan films and *Way of Water*: list every detected segment, seek to each boundary −5 s, and have the user confirm timing and fit. For at least 10 boundaries, the user films the screen in slow motion (240 fps), and you count the frames between the cut and the zoom change, plus any spill or bars. Compare segment counts with a published source **only if a reliable one is found (cite it)**; never invent timestamps. Plus one **full continuous watch** of a Nolan film with no false switches. | Shield + user |
| T8 *Catching Fire* ramp (reference test) | The launch-tube rise into the arena, where the frame expands as Katniss enters (per the director's description; the exact mechanics are **unverified** until the analyzer measures them).<br>1. Analyze the user's copy. Report whether the change is a `cut` or a `ramp`, its exact start and end frames, and the measured start and end ratios. If it turns out to be a cut, report that honestly; the synthetic ramps then carry the ramp acceptance.<br>2. The user reviews the status-page ramp plot (raw vs fitted).<br>3. Play it on the Shield from 30 s before the rise. The user films the screen in slow motion (240 fps), with tick marks visible at the screen's top and bottom edges.<br>4. **Pass** requires all of:<br>&nbsp;&nbsp;• the zoom starts within 1 frame of the ramp's first frame and ends within 1 frame of its last;<br>&nbsp;&nbsp;• no jump to the final ratio at any point;<br>&nbsp;&nbsp;• the picture edge moves one way only, with no reversals or stalls visible in the slow-motion clip;<br>&nbsp;&nbsp;• no light past the screen edges and no bars inside them beyond what the clip can resolve;<br>&nbsp;&nbsp;• no dropped frames.<br>5. Repeat after seeking into the middle of the ramp. | Shield + user |
| T7 Performance | Analyzer throughput on Unraid, memory, API latency (< 100 ms on the LAN), and playback CPU with the system on vs off | NAS + Shield |

Every requirement ID in `docs/TEST_REPORT.md` gets: pass/fail, the evidence (command, log excerpt, or user confirmation with date), and any open risk.

---

## 9. Audit passes (mandatory, two of them)

**Scope of both audits: everything.**
- R1–R16, D1–D8, L1–L10.
- The §5 math.
- Analyzer, API, storage, scheduler, deployment, the player component.
- Tests, docs, security.
- The §10 failure modes.

Write the results to `docs/AUDIT_1.md` and `docs/AUDIT_2.md`, one row per item: **status (OK / Gap / Risk) · evidence · fix · owner**.

### Audit 1: design audit (Phase 3, after the spike gate, before production code)

1. Trace every R to a design element **and** at least one planned test. A missing link is a Gap.
2. Go through every assumption in `PROGRESS.md`. Each must be either verified, or listed as a Risk with a planned test.
3. Re-derive the §5 math **independently** from `geometry.json`. Don't copy the table.
4. Walk every §10 failure mode. Each must be designed for, or explicitly accepted by the user.
5. Confirm none of L1–L10 is repeated in the design.
6. **GATE:** present the Gaps and Risks to the user before Phase 4.

### Audit 2: implementation audit (Phase 8, after acceptance testing)

1. Run it with a **separate subagent that has not seen the implementation work**. Give it only this file, the repo and `TEST_REPORT.md`. It must:
   - read every source file;
   - run the full test suite from a clean clone;
   - rebuild the image;
   - re-derive the math;
   - confirm every R has passing evidence;
   - hunt for:
     - hardcoded IPs, paths or tokens;
     - swallowed exceptions, unbounded loops, missing network timeouts;
     - Python 2 leftovers and Kodi API misuse (L2, L8);
     - containers running as root;
     - secrets anywhere in git history (run `gitleaks`).
2. The main agent fixes the findings. The auditor then **re-checks each changed item** (a second look at the fixes only).
3. Report any remaining Risks to the user in plain language.

---

## 10. Failure modes to design for

**Picture content**
- Dark scenes; fades; credits on black.
- Ramps (gradual matte moves), including ramps in dim shots, very long ramps, and ramps where the sides move too.
- Burned-in subtitles inside a matte.
- Studio logos at a different ratio.
- Windowboxed content (bars on all four sides); 4:3 pillarboxed inside 16:9; asymmetric mattes.

**File properties**
- Non-square pixels (SAR ≠ 1:1); interlaced sources; variable frame rate or HFR (*Way of Water*).
- Non-zero `start_time`.
- Very long files; corrupt files; Unicode file names; case-sensitive paths.

**HDR formats**
- DV profile 5 (different colour encoding: confirm crop detection still finds the black bars).
- DV profile 7 (dual layer), HDR10+, HLG, SDR.

**Library changes**
- A file not analyzed yet.
- A file replaced by a new edition: hash mismatch triggers re-analysis.
- Two versions of the same movie (different Plex parts).
- Extras and trailers.

**Infrastructure**
- NAS or API unreachable.
- Shield offline from the analyzer: use the cache, else the fallback.
- Plex token rotated.
- Container updated: appdata persists; schema migrations.
- GHCR authentication, if the image is private.

**Player behaviour**
- Kodi per-file view-state saving conflicting with programmatic zoom.
- The skin's own masking or autozoom fighting the system (L6).

---

## 11. Definition of done

- R1–R12 and R14–R16 pass with evidence. R13 passes, or is deferred with the user's agreement.
- **On the Shield:** for the 1.33, 1.78, 1.85, 1.90, 2.00, 2.35 and 2.39 ratio frames, the user confirms the borders meet the screen edges with no visible spill or shortfall.
- **VAR films:**
  - every boundary is spot-checked;
  - every change meets R2 (≤ 1 frame, measured with the slow-motion method), with no black flash or dropped frame at the change;
  - one full continuous watch shows no false switches;
  - the *Catching Fire* reference test (T8) passes, or the user signs off on the measured result.
- HDR10 and DV are confirmed active by the projector's signal info **while zoomed**.
- The analyzer has processed the test films and at least one full library section without crashing. Throughput and the ETA for the full library are documented.
- Audits 1 and 2 are complete; every Gap is closed or accepted by the user.

---

## 12. Rules

- **Ask before:**
  - installing software;
  - creating or pushing to GitHub;
  - any change on the Shield (installing or removing add-ons/APKs, changing Kodi settings);
  - anything touching the NAS.
- Media is read-only: never modify, move or delete media files.
- No secrets in git, images or logs. Use `.env` (gitignored) locally and env vars in the container.
- Don't present unverified results as verified. When only the user can check something (the projector screen, the signal info), say so and give exact steps.
- Make small, reversible changes and commit often with clear messages.
- Respect licences: Plezy is GPL-3.0. Avoid reusing CIHAutoNox code (GPL-2.0).
- Update `docs/PROGRESS.md` at the end of every phase.

---

## 13. Questions to ask at the right moment

| When | Ask |
|---|---|
| Phase 0 | GitHub username, repo name, public or private · Plex URL and token · library paths (SMB, Unraid, Plex) · exact test film titles, editions and paths |
| Phase 1 | Which numbered rows sit on the screen's top and bottom edges · any left/right spill |
| Phase 2 | Route choice (GATE) |
| Phase 3 | Accept or fix each Gap/Risk (GATE) |
| Phase 4 | `scope_fill` for 2.39/2.40 content: fit with thin bars, or fill with about 1% cropped per side · quiet hours · pause during Plex sessions · analyze extras/trailers? |
| Phase 5 | Which remote button for the override · `edge_bias` (default `no_spill`) |
| Phase 6 | Throttling after seeing the measured throughput |

---

## 14. Repo layout

```
cih-autozoom/
├─ CLAUDE.md                  # this file
├─ .env.example               # placeholders only; real .env is gitignored
├─ config/geometry.json       # measured in Phase 1
├─ analyzer/                  # Phase 4: Python 3.12, FastAPI, SQLite, ffmpeg
│  ├─ src/  tests/  Dockerfile
├─ players/                   # Phase 5: only the route chosen at the gate
│  ├─ kodi/service.cih.zoom/
│  └─ plezy/                  # fork patch + build notes, if chosen
├─ tools/testpatterns/        # calibration + synthetic test video generators
├─ unraid/cih-analyzer.xml    # Unraid template
├─ .github/workflows/         # lint, test, build, push to GHCR
└─ docs/  PROGRESS.md  SPIKES.md  ZOOMCORE.md  AUDIT_1.md  AUDIT_2.md
          TEST_REPORT.md  INSTALL.md  USER_GUIDE.md  TROUBLESHOOTING.md
```
