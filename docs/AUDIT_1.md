# AUDIT 1: design audit (Phase 3, 2026-10-28)

Scope: every requirement (R1 to R16), decision (D1 to D8) and lesson (L1 to L10), the section 5 math, the section 10 failure modes, and every assumption in `PROGRESS.md`. The design audited is the spec (sections 7 and 8) as narrowed by the Phase 2 gate: **route A, Kodi's internal player with the in-Kodi add-on, PM4K as the front end**. No production code exists yet; the prototype add-on and the Phase 2 measurements are the only implementation evidence.

Status key: **OK** = a design element and a planned test exist and nothing known contradicts them. **Gap** = a missing link between a requirement, a design element and a test, or a thing the design needs that nobody has decided or built. **Risk** = a known uncertainty with a planned test or an acceptance decision. Owner: **C** = Claude, **U** = the user.

New facts gathered for this audit (all read-only; nothing on the Shield was changed):

- **PM4K plays through Kodi's own player.** `PlexPlayer(xbmc.Player)` in `lib/player.py` calls `xbmc.Player.play(url, listitem)`, so the usual Kodi callbacks fire and the add-on can listen to them. A search of PM4K's Python found **no view-mode, zoom or stretch calls**, so PM4K does not fight the zoom.
- **PM4K resumes by starting at 0 and then seeking.** `SeekPlayerHandler.onAVStarted` calls `seekAbsolute`, which calls `seekTime` (or a relative `Seek` builtin). The add-on will see a start followed by a seek on every resume.
- **What PM4K hands to the player** (`plexnet/plexplayer.py`): for direct play, `server.buildUrl(part.getAbsolutePath("key"))`, which is the Plex part URL `/library/parts/<partId>/<updatedAt>/file.<ext>`, with the token added when the request goes to the server; if a path mapping exists, the mapped (local or SMB) path instead. The list item also carries the Plex-side file path as the video tag's path (`vi.setPath(part.file)`). Transcodes use `/video/:/transcode/universal/...` with the rating key in `path=`. **Not yet checked on the Shield:** what `getPlayingFile()` actually returns while PM4K plays.
- **Kodi settings now:** ScopeNox `enableautozoom` is **false** (verified; this closes the earlier open finding); `scopemask` is true; `videoplayer.adjustrefreshrate` is 1; MediaCodec surface is on; subtitle alignment is 2. No `autoexec.py`. PM4K has no path mapping configured.

---

## 1. Requirements: design element, planned test, status

| ID | Design element (spec section) | Planned test | Status | Evidence | Fix / next step | Owner |
|---|---|---|---|---|---|---|
| R1 | Add-on: on `onAVStarted` look up the title, apply the zoom for the current position (Phase 5) | T3 (mocked Kodi), T5, T6 | **Risk** | Prototype fires on the test file only; PM4K plays through `xbmc.Player` (source), so callbacks should fire as in Spike A. Kodi remembers the zoom per file, so the first frames can show the wrong zoom | Phase 5 step 1: log what PM4K plays on a real title; measure how many frames show the wrong zoom at start | C |
| R2 | Scheduler fires each zoom at boundary minus `lead_ms`; cuts snap; ramps per-frame; `edge_bias` (Phase 5) | T3, T5, T6, T8 | **Risk** | Measured: constant 7.7-frame delay, spread 0.23 frames, 25 of 25 cuts within 1 frame, 23 of 25 exact; eased ramp at 0 frames lag. Not met: target of 0 frames. Not measured: near-black ramp, rest of the linear ramp, the frame-1860 excursion | Confirm the "at most 1 frame" acceptance (gate item G1). Implement `edge_bias` as a per-direction lead offset of one frame and test it in T6. Re-film with a pattern that stays readable in dim ramps (see DD11) | U, C |
| R3 | Add-on never touches HDR, refresh rate or audio (Phase 5 rule) | T5 (projector signal info), T6 | **Risk** | HDR10: Kodi reports `hdr10` while zooming; projector shows no overlay, user saw its HDR10 picture mode (indirect). Dolby Vision, HLG: untested. Refresh-rate switching and passthrough with the add-on: untested, though the add-on touches neither | T5 plan: play the user's DV profile 7 and 8 clips under zoom and read the projector's picture mode; check refresh-rate switching and passthrough once with the add-on on | C, U |
| R4 | No new load in the player path: 0.33 ms per command, about 24 commands per second at most (ramps) | T5 (debug overlay), T7 (CPU on/off) | **Risk** | No skipped frame near 7 keyframes at 240 fps (15 changes). The earlier "stall" was retracted as a phone artifact | Add a dropped-frame check to T5 that does not rely on eyes alone (Kodi debug overlay plus a slow-motion clip kept running 30 s past the end) | C |
| R5 | Callbacks `onPlayBackSeek`, `onPlayBackSeekChapter`, `onPlayBackResumed`, stop, end (Phase 5) | T3 (simulated clock), T5 | **Risk** | Handled in the prototype, tested offline only. PM4K resume is start-then-seek (source). A command takes about 0.32 s to reach the picture, so after a seek the old zoom shows for about 7.7 frames; unmeasured | Measure the post-seek and start-up wrong-zoom time in Phase 5. If it is more than a few frames, decide whether to accept it (G5) | C, U |
| R6 | No timeline: fall back to contain by the container's display ratio, request priority analysis, log it; analyzer down: local cache, else fallback (Phase 5, R6) | T3 ("missing timeline", "API down") | **OK** | Design is complete in the spec. Transcodes (no part ID in the URL) take the same fallback and look up by rating key | Add the transcode case to T3 | C |
| R7 | Scanner, queue, throttle, resume, re-analyze on change, skip ISO/3D/audio (Phase 4) | T2, T7 | **Risk** | Unraid CPU and throughput unknown; 2,344 movies and 558 shows. Two extra Plex libraries (3D, YouTube) have no host path | Measure throughput in Phase 6 (T7). User decides on the YouTube library in Phase 4 | C, U |
| R8 | Coarse pass, dark-sample gate, hysteresis, boundary refinement, ramp fit (Phase 4) | T2 synthetic video; T6 real films | **Gap** | No Dolby Vision profile 5 sample exists (the user has profiles 7 and 8), and the spec requires profile 5 crop detection to be confirmed. HDR10+ and interlaced samples are also missing | Ask the user for a profile 5 file if one exists (G3); otherwise record profile 5 as untested. Synthesize HLG and HDR10 fixtures; the stand-in DVD-style clip is progressive | U, C |
| R9 | Timeline lookup by path (with mapping), part ID, rating key, hash (Phase 4 API) | T1 (path mapping), T3 | **Risk** | Part URL shape confirmed on the user's server (`/library/parts/<id>/<n>/file.mkv`, id equals the part's `id`). PM4K builds the same URL (source). With a path mapping PM4K hands a mapped path instead, and a transcode hands a rating key. What Kodi reports on the device: unverified | Phase 5 step 1 logs `getPlayingFile()` and the video tag path (token redacted). The add-on sends only the part ID, path or rating key to the analyzer, never the URL or token | C |
| R10 | GHCR image, Unraid template, env-var token, no secrets (Phase 4, section 12) | CI; `gitleaks` in Audit 2 | **Gap** | A public repository does not by itself make the GHCR package public; Unraid would then need a login. The add-on will see the Plex token inside the playing URL | Add to the install steps: set the package to public after the first push and test an anonymous `docker pull` from the PC. Design rule: the add-on never logs or forwards the URL, only IDs and paths | C |
| R11 | Remote button cycles presets or disables, persisted via the API (Phase 5) | T3, T5 | **Gap** | PM4K's fullscreen video has its own seek and info dialogs that take key presses; no button has been chosen or tested. The spec persists the override through the API, so an offline API loses it | Pick the button with the user (Phase 5 question) after testing which keys PM4K leaves free. Keep a local per-title store in the add-on profile as a fallback, and sync it to the API when reachable | C, U |
| R12 | Add-on settings in the Kodi 19+ format: on/off, `lead_ms`, `edge_bias`, `scope_fill`, band rows, ratio, analyzer URL, log level (Phase 5) | T3 | **OK** | The prototype already has `enabled` and `lead_ms`; the rest are the spec's list and read from `geometry.json` | None | C |
| R13 | Subtitles inside the band (P2) | T5 visual | **Risk** | Not designed. Kodi has a subtitle alignment setting (current value 2; meaning not verified) | Defer R13 with the user's agreement (G2), and write the Kodi setting into the user guide | U |
| R14 | Status page: queue, errors, timeline bar, per-frame ramp plot (Phase 4) | API tests; manual review | **OK** | Spec is specific. T8 needs a real ramp; whether *Catching Fire* is among the seven test films is unconfirmed (G4) | Confirm the file and path | U |
| R15 | Disable L4 add-ons and the L6 skin autozoom, with permission (Phase 0) | Phase 0 verification | **OK** | L4 add-ons are not installed; `script.scopenox-autozoom` is disabled; the skin's `enableautozoom` is false; PM4K sets no view mode (source) | None | C |
| R16 | Install, user guide, troubleshooting, recalibration (Phase 9) | Review | **OK** | Planned in the spec. New items to document: front end is PM4K; the deferred shortcut; removing the add-on | None now | C |

## 2. Assumptions listed in `PROGRESS.md`

| Assumption | Status | Evidence or planned test |
|---|---|---|
| Screen, projector, Plex and Unraid facts from the user (spec section 2) | **Risk (accepted)** | Cannot be checked from the PC. The screen ratio and band were measured in Phase 1 |
| Unraid host paths come from screenshots; only Movies was checked end to end | **Risk** | Phase 4 path-mapping step: `Test-Path` every library root over SMB (read-only) and record the result per library |
| The SMB share root equals the Unraid user-share root | **Risk** | Verified for Movies by seven films. Check the others in the same step |
| L1 to L10 are taken as given | **OK** | The design uses none of the broken paths; see section 5 |
| HDR10 and Dolby Vision output land on the same rows as SDR | **Risk** | HDR10 verified in Spike A (same geometry). Dolby Vision untested: T5 |
| The 316 ms lead holds for other files, rates and Kodi settings | **Risk** | One file at one refresh rate. T6 on real films re-measures; `lead_ms` is a setting |
| Wrong-zoom frames at the start of playback | **Risk** | Unmeasured; Phase 5 step 1 |
| A clip the user called 4:3 measures about 1.667 wide | **Risk** | Preliminary single-frame reading; the analyzer must resolve it (T2, T6) |
| ScopeNox skin may have an autozoom setting | **OK (closed)** | Checked: `enableautozoom` is false |
| Interlaced sources | **Risk** | No interlaced sample; stand-in is progressive. Ask for one or accept untested (G3) |
| Libraries not covered by screenshots (3D, YouTube) | **Risk** | 3D is skipped by design; YouTube is a Phase 4 decision |

## 3. Section 5 math, re-derived independently

`tools/audit_math.py` derives the scale a second way from `config/geometry.json` only, without using `tools/zoom_table.py`: (1) the largest rectangle of the active ratio that fits the band, divided by the width the player already draws; (2) a brute-force search over scales in steps of 0.0001 that keeps the scaled rectangle inside the band rows and the frame width. Run on 2026-10-28:

| Active ratio | s (target rectangle) | s (brute force) | Rows used | Inside the worst-case measured band (262 to 1898) |
|---|---|---|---|---|
| 1.333 | 0.7565 | 0.7564 | 263 to 1897 | yes |
| 1.778 | 0.7565 | 0.7564 | 263 to 1897 | yes |
| 1.850 | 0.7872 | 0.7872 | 263 to 1897 | yes |
| 1.900 | 0.8085 | 0.8084 | 263 to 1897 | yes |
| 2.000 | 0.8510 | 0.8510 | 263 to 1897 | yes |
| 2.200 | 0.9361 | 0.9361 | 263 to 1897 | yes |
| 2.350 | 1.0000 | 0.9999 | 263 to 1897 | yes |
| 2.390 | 1.0000 | 1.0000 | 276.7 to 1883.3 | yes |
| 2.400 | 1.0000 | 1.0000 | 280.0 to 1880.0 | yes |

- The two routes agree to within the 0.0001 step. They also agree with the spec's section 5.3 table to its rounding (2.00 is 0.85104 exactly: 0.8510 here, and the spec's table rounds it up to 0.8511).
- `scope_fill`: 2.39 gives s = 1.0170 and crops 32.6 px per side (0.84%); 2.40 gives 1.0212 and 40.8 px (1.04%). A 3840x1600 container gives s = 1.0 fit (rows 280 to 1880) or 1.0212 fill. A 720x480 file with 32:27 pixels has display ratio 1.7778 and gets 0.7565.
- Every scale is inside Kodi's accepted range of 0.5 to 2.0, and every fitted rectangle lies inside the worst case of the measured band, so a correct zoom cannot spill past the screen.
- **Finding (Gap):** the **units of Kodi's `verticalshift` are not measured.** Kodi's introspection says "1.0 means shift to bottom" and Spike A showed it accepts values, but no test relates a value to rows. All nine rows above need a shift of 0 because the band is centred and the mattes are symmetric. An **asymmetric matte** (spec section 10) needs a shift, so a one-time calibration (set a shift, read the displacement) is required in Phase 5 before that case can be claimed. Owner: C.
- The 2.39 and 2.40 cases leave thin bars of about 14 to 17 rows at the top and bottom unless `scope_fill` is on; the user decides this in Phase 4.

## 4. Section 10 failure modes

| Failure mode | Design coverage | Status | Note |
|---|---|---|---|
| Dark scenes, fades, credits on black | Dark-sample gate, persistence rule (Phase 4 steps 3 and 5); T2 fixtures | OK | |
| Ramps: ordinary, in dim shots, very long | Ramp refinement with widening window and fit (Phase 4 step 6); T2 has linear, eased, near-black and over-window ramps | OK | |
| Ramps where the sides also move | Spec step 6 measures left and right edges, but T2 lists only ramps that open top and bottom | **Gap** | Add a synthetic ramp that opens sideways (C) |
| Dim ramp verification on screen | The Spike A test pattern's code plate is unreadable in the near-black ramp, so playback of a dim ramp could not be measured | **Gap** | Redesign the boundary pattern with a bright marker outside the picture (DD11) (C) |
| Burned-in subtitles in a matte; studio logos at another ratio | Persistence and minimum-duration rules; T2 fixtures | OK | |
| Windowboxed, 4:3 pillarboxed, asymmetric mattes | Width detection (Phase 4 step 4); T2 covers 4:3 and windowbox, not asymmetric | **Gap** | Asymmetric-matte fixture, and the vertical-shift calibration in section 3 (C) |
| Non-square pixels | SAR applied to the ratio; T2 has a 720x480 fixture; math checked | OK | |
| Interlaced sources | Not designed beyond ffprobe flags | **Risk** | No sample; accept untested or supply one (G3) |
| Variable or high frame rate (*Way of Water*) | Timeline schema stores times and frames | **Risk** | Key the timeline on presentation time, not frame number, and ffprobe the real file for VFR before Phase 4 closes (DD12) (C) |
| Non-zero `start_time` | Normalize to the player's timebase (spec 5.4); T2 fixture | **Risk** | How Kodi's `getTime()` relates to the stream's start time is unmeasured; test the fixture on the Shield (C) |
| Very long, corrupt files; Unicode names; case-sensitive paths | ffprobe failure is logged and skipped; path mapping is case-sensitive | **Gap** | T1/T2 list no corrupt-file or Unicode-name test; add them (C) |
| Dolby Vision profile 5 | Spec asks for confirmation on fixtures | **Gap** | No sample (G3) |
| Dolby Vision profile 7, HDR10+, HLG, SDR | Per-type crop limits tuned on fixtures | **Risk** | Profile 7 and 8 clips exist; HDR10+ has no sample; HLG and SDR can be synthesized |
| File not analyzed yet | R6 fallback | OK | |
| File replaced by a new edition | `quick_hash` mismatch re-analyzes | OK | |
| Two versions of one movie | Part IDs differ per version | OK | Verified shape: one part per media on the test item |
| Extras and trailers | Phase 4 question | **Risk** | User decision in Phase 4 |
| NAS or API unreachable; Shield offline from the analyzer | Local cache then fallback (Phase 5, R6); T3 "API down" | OK | |
| Plex token rotated | Token from an env var; analyzer logs 401 | **Gap** | No test planned for a 401; add one to T1 (C) |
| Container updated, appdata kept, schema migrations | Versioned schema in the spec | **Gap** | No migration test planned; add one that opens an old-schema database (C) |
| GHCR authentication | Public repo; see R10 | **Gap** | Package visibility step and anonymous pull test (C) |
| Kodi per-file view-state saving | Measured: Kodi restores a per-file view. Add-on corrects it in `onPlayBackStarted` and resets at the end | **Risk** | Wrong-zoom frames at start unmeasured (R1) |
| The skin's own masking or autozoom | `enableautozoom` false; `scopemask` is true | **Risk** | `scopemask` is the skin's UI mask; confirm in T5 that it does not draw over the video edges (C) |

## 5. Lessons L1 to L10: none repeated

| ID | Check | Status |
|---|---|---|
| L1 | Design uses precomputed timelines (D4); no `RenderCapture` anywhere. Grep of the add-on: 0 matches | OK |
| L2 | No flag API use. Grep: 0 matches | OK |
| L3 | Zooms shrink non-scope content (0.7565 for 16:9); math re-derived | OK |
| L4 | `cihautonox` add-ons not installed (Phase 0, `Addons.GetAddons` and a folder search) | OK |
| L5 | CIHAutoNox code not reused; none in the repo | OK |
| L6 | Skin autozoom add-on disabled; skin `enableautozoom` is false | OK |
| L7 | `addon.xml` requires `xbmc.python` 3.0.1 | OK |
| L8 | Add-on sends zoom through JSON-RPC, not builtins; grep for a space before a parenthesis in builtins: 0 matches. Rule stays for later code | OK |
| L9 | Add-on installed by `adb push` (script exists and was used); Kodi found it as v0.1.0 | OK |
| L10 | Not applicable: route A uses no external player. PM4K is a Kodi add-on | OK |

## 6. Decisions D1 to D8

| ID | Status | Note |
|---|---|---|
| D1 Lens fixed, all scaling in the player | OK | Consistent with measured geometry |
| D2 16:9 and narrower shown in full, never cropped | OK | 16:9 gets 0.7565, 4:3 gets 0.7565 with side bars; math checked |
| D3 VAR films follow every change | OK | Designed; 25 of 25 cuts within a frame measured |
| D4 Offline detection, no live capture | OK | |
| D5 Analyzer on Unraid via GHCR | **Risk** | Package visibility (R10); Unraid hardware unknown (R7) |
| D6 Player chosen after spikes | OK | Chosen at the gate: route A with PM4K |
| D7 Subtitles low priority | **Risk** | Needs the user's agreement to defer (G2) |
| D8 Central timeline storage, read-only media | OK | The add-on also needs a small local cache (DD7) |

## 7. Design changes the audit adds to the spec

| ID | Change | Why |
|---|---|---|
| DD1 | Identify the title from the part ID in the playing URL first; then the video tag path (Plex-side file path); then the rating key from a transcode URL; then the mapped local or SMB path | PM4K can hand over any of these depending on direct play, path mapping and transcode |
| DD2 | The add-on never logs, stores or forwards the playing URL; it extracts IDs and paths and discards the rest | The URL contains the Plex token |
| DD3 | Treat resume as start-then-seek: apply the zoom for position 0 at start, then recompute on the seek callback | PM4K calls `seekTime` after `onAVStarted` |
| DD4 | Transcoded playback falls back to R6 (contain by display ratio) | No part ID and a re-encoded stream |
| DD5 | Keep the zoom reset at the end of playback, and set the starting zoom in `onPlayBackStarted` | Kodi stores the view per file |
| DD6 | Keep a local per-title override store with API sync | R11 must work offline |
| DD7 | Keep a small on-device timeline cache | R6 when the analyzer is down |
| DD8 | `edge_bias` as a one-frame lead offset by direction, verified in T6 | The two measured misses were both one frame late |
| DD9 | PM4K needs no view-mode handling (none found) | Source search |
| DD10 | Calibrate Kodi's vertical-shift units once before asymmetric mattes are claimed | Units unmeasured |
| DD11 | Rebuild the boundary pattern with a bright marker outside the picture so dim ramps can be read on camera | Spike A could not read the near-black ramp |
| DD12 | Key timelines on presentation time; do not rely on frame numbers for VFR files | Frame numbers are not stable across VFR |
| DD13 | Deferred: the Shield shortcut to PM4K via `autoexec.py` | User request, logged in `PROGRESS.md` |

## 8. Gaps and Risks for the gate

**Gaps to close before or in the phase noted (all mine unless marked):**
1. R8: no Dolby Vision profile 5, HDR10+ or interlaced sample (**U** can supply).
2. R10: GHCR package visibility step and anonymous pull test.
3. R11: remote button choice and PM4K dialog conflicts (**U** picks the button in Phase 5).
4. Tests missing from the plan: sideways ramp, asymmetric matte, corrupt file, Unicode name, 401 handling, schema migration.
5. Vertical-shift units and an off-centre matte.
6. The test pattern for dim ramps (DD11).

**Risks that need your decision or agreement (gate items):**
- **G1.** Accept R2 at "at most 1 frame" with the target of 0 not met (2 of 25 cuts one frame late). Your answer "PM4K" came with this in the same message but was not confirmed separately.
- **G2.** Defer R13 (subtitles inside the band) and document Kodi's subtitle setting instead.
- **G3.** Supply a Dolby Vision profile 5 file and, if you have them, HDR10+ and interlaced files, or accept those as untested.
- **G4.** Confirm that *Catching Fire* is one of the seven test films and give its path (needed for T8).
- **G5.** Accept that after a seek or at start-up the old zoom may show for about a third of a second unless Phase 5 measures better.

**Risks I will test (no decision needed):** wrong-zoom frames at start; HDR and Dolby Vision under zoom on the projector; lead value on other files; start-time behaviour in Kodi; VFR; dropped frames; what PM4K really hands to `getPlayingFile()`.

**Questions that arrive in Phase 4, listed so they are not forgotten:** `scope_fill` for 2.39 and 2.40; quiet hours; pause while Plex transcodes; analyze extras and trailers; the YouTube library and its host path.

## 9. Verdict

No requirement is missing from the design, and none of the ten lessons is repeated. Six Gaps need closing; five items need your decision (G1 to G5). **Phase 4 may start once you have answered the gate items.** The audit changes no measured result.

## 10. Gate answers and follow-up findings (2026-10-28)

| Item | Answer | Effect |
|---|---|---|
| G1 | **Accepted**: R2 at "at most 1 frame"; the target of 0 frames is not met by this route (2 of 25 cuts one frame late) | R2 stays a Risk with an accepted acceptance level; T6 and T8 still measure it |
| G2 | **Accepted**: R13 (subtitles inside the band) is deferred | R13 is deferred with the user's agreement, as the spec allows. The user guide will name Kodi's subtitle alignment setting |
| G3 | **Dolby Vision is not a concern for now** (user: the Shield does not support it; the Shield TV Pro hardware lists Dolby Vision, so the limit may be elsewhere in the chain, but the conclusion is the same). **HDR10+ sample supplied**: *Carrie* (2026) S01E06 | R3 narrows to HDR10, HDR10+ and SDR; Dolby Vision and profile 5 are out of scope until the user says otherwise. Interlaced remains untested and accepted as such |
| G4 | **Confirmed**: *Catching Fire* is one of the seven test films, path already in the private notes; ownership confirmed by the user | T8 can proceed in Phase 6 |
| G5 | **Accepted**: the old zoom may show for about 0.3 s after a seek or at the start | R5 stays a Risk with an accepted level; Phase 5 still measures it |

**The *Carrie* sample** (private path kept in `docs/LOCAL_FACTS.md`; a 90 s stream-copy clip is in the gitignored `samples/`): HEVC Main 10, **3840x1920 (2.00:1) native container**, 24 fps, PQ, BT.2020, **HDR10+ (SMPTE 2094-40) and Dolby Vision profile 8.1 (single layer, HDR10-compatible)**, MP4, 59 minutes, `start_time` 0. Checked read-only over SMB:

- **Crop detection runs on it and finds no mattes.** On the clip's keyframes, `cropdetect` at limits 0.02, 0.05 and 0.1 gives `crop=3840:1920:0:0` on all 44 samples. **Correction (2026-10-28, Phase 4 slice 2):** that result was weaker than I first wrote. Limited-range black is about 0.063 (16/255, or 64/1023), so limits of 0.02 and 0.05 are below black and detect nothing in any file; only the 0.1 result carries information. At limit 0.2 it produces false crops on dark frames (for example 2884x1392). Calibration on noisy letterboxed SDR and PQ fixtures (now a test) found the matte from 0.063 upward, so the analyzer default is 0.094. Brightness of the keyframes (10-bit YAVG, black = 64) ranged 205 to 375.
- **The zoom for this file is 0.8510** (a 2.00:1 active picture), and the R6 fallback (contain by the container's display ratio) gives the same answer, because the container itself is 2:1. It is therefore a native-ratio-container sample for the "native 2.39 encode" slot, and a test of the fallback.
- **Not tested on the Shield.** Whether Kodi plays profile 8.1 as HDR10 on this chain, and whether HDR10+ metadata passes, is untested; the clip is ready for a playback test.

**New design points from this sample (add to section 7):**

| ID | Change | Why |
|---|---|---|
| DD14 | The analyzer must choose the video stream by disposition and ignore attached pictures | This file has a JPEG cover-art stream (index 30) next to the real video (index 0); *Avatar*'s video is index 1 |
| DD15 | Keep `cropdetect` limits per stream, expressed as fractions of the luma scale, **above the black level** (about 0.063 for limited range in 8 and 10 bits); default 0.094. Corrected in slice 2: the first draft said 0.02 to 0.1 | Below 0.063 nothing counts as black; at 0.2 dark picture content produces false crops |
| DD16 | Take the display ratio from the container's display aspect ratio (SAR applied) for the fallback | The 2:1 container gives the right answer with no analysis |

**Added during Phase 4 slice 3 (2026-10-28), from building and testing the refinement pass:**

| ID | Change | Why |
|---|---|---|
| DD17 | `numpy` (pinned 2.5.4) is a runtime dependency | Full-resolution edge measurement needs array operations; doing it in ffmpeg filters cannot give both edge positions and a frame-to-frame scene-change score in one pass |
| DD18 | Picture edges are found against a threshold just above the **measured matte noise** (99th percentile of the matte plus 2, clamped to 18 to 32 on the 8-bit scale), as the largest contiguous block of rows and of columns; not against the coarse pass's fixed limit | A dim picture a few levels above black is still found; a subtitle a few rows from the picture falls outside the block. Segment checks use the lowest matte level across all segments, so a segment under suspicion cannot raise the bar it is judged by |
| DD19 | A segment's probes can **confirm** it, **refute** it (fold into the neighbour they match) or **correct** its rectangle (they agree with each other, not with the coarse crop). A decoded boundary window that does not end in steady picture (no drift over a second) is widened, up to 24 s a side | A permanent subtitle makes a wrong coarse crop; a slow ramp can look steady for a second inside a pixel tolerance, so steadiness also checks drift |
| DD20 | **Found on *Scott Pilgrim*, fixed the same day:** the edge threshold was calibrated on the matte outside the *coarse* rectangles, and a slow ramp's coarse rectangle holds real picture, so the threshold clamped high and the dark opening read as a bright doorway. The lowest matte level across the samples now wins, and the segment checks share one file-wide threshold. A window that opens on a picture, **cuts to a small inset, and eases into the final picture** is read as one ramp starting at the cut (`ramp_after_cut`, its first frame the first frame after the cut). Single flash or dark frames inside a ramp are set aside (a running median over 9 frames, 8 px) so one bad frame cannot bend the monotone fit. A low-confidence ramp is still flagged; the Phase 5 player must decide what to do with it (suggested: hold the neighbouring segment's zoom, never guess) | Real example in `samples/`; see `PROGRESS.md` slice 3 findings |
| DD21 | **Closed (2026-10-28).** The coarse pass identified a segment by ratio only, so a window that shrank and grew back to the same ratio, or a picture of one ratio at two sizes (a windowbox that grows to full frame), produced no boundary. A state is now the ratio **and** the picture rectangle (each edge within 1% of the frame, at least 8 px, of the run's first sample). A short run that opens the file can anchor a ramp chain. A chain is a ramp candidate if its labels lie between the two ratios (as before) or its rectangles follow a one-way change: either from the left run through the chain to the right run, or, with at least three states, from the first state on to the right run (which may equal the left). Refinement no longer merges equal neighbours across a ramp candidate | *Scott Pilgrim* escaped the gap only because its inset (1.78) and final picture (1.85) had different labels. Tests: constant-ratio growth, same-picture return, jitter under 1% is still one segment, random dark-scene crops are not a ramp, and a video test (was `xfail`) |

**Added during Phase 4 slice 4 (2026-10-28):**

| ID | Change | Why |
|---|---|---|
| DD22 | Each analysis runs in its **own process** (`python -m cih_analyzer --analyze-envelope`), started under `ionice -c3 nice -n 19` where those exist, with a timeout, killed when the service stops | A hung or crashing ffmpeg cannot take the API down; priority applies to the analysis only, not to serving timelines |
| DD23 | A changed file **loses its timeline immediately**; the API answers 404 with `status: stale`; the file is queued at on-demand priority when the player asks (`analyze=1`) | A timeline for the old contents would zoom the wrong picture; the player's R6 fallback is safe |
| DD24 | A timeline answer carries an extra `override` member (null, or `{mode, preset}`); raw ramp measurements are stored beside the timeline and served by `/api/v1/ramp` | R11 persisted per title; R14 raw-versus-fitted plot. The timeline schema itself is unchanged |
| DD25 | On-demand analysis only accepts a file that exists under a media root (no `..`); the API key is compared in constant time and never logged; the Plex token travels in a header, never in a URL | Closes the path-traversal and token-in-log risks for the new endpoints |

**Added during Phase 4 slice 5 (2026-10-29):**

| ID | Change | Why |
|---|---|---|
| DD26 | ffmpeg is Debian 13's apt package (7.1.5) in `python:3.12-slim-trixie`, not a downloaded static build; the container's default user is `99:100`, with no `PUID`/`PGID` variables; the image has a `test` stage and CI runs the whole suite in it | No third-party download at build time, apt verifies signatures, and the tests run against exactly the shipped ffmpeg; 99:100 is what Unraid uses for appdata and shares |
| DD27 | `ionice -c 3` and `nice -n 19` are each tried once and omitted (with a warning) if the runtime refuses them | A restrictive container must lose the priority, not fail every analysis |
| DD28 | `SAMPLE_INTERVAL` (global) and `SAMPLE_INTERVALS` (per folder, longest prefix wins) set how often the first pass samples keyframes | The real library has about 40,000 files (the spec assumed 2,000+); TV can be sampled sparsely and movies densely (R12) |

| DD29 | A cut or ramp between two verified pictures (zoom differing by at least 3%) that cannot be pinned to the frame is placed at the safe end of its uncertainty window instead of merging the neighbours; flags `placed_conservatively` (boundary) and `position_uncertain` (file) | Found on Interstellar: merging over the larger rectangle was safe but cascaded and wrote 31% of the film as 16:9. A doubt about *when* now costs at most the width of one coarse window of extra black |
| DD30 | Hybrid analysis: Movies batch plus on-demand for any unanalyzed title (movie or TV); TV and other libraries on demand only | The full library is about 40,000 files; measured 4K analysis takes 17 to 59 min per film at 3 workers. On-demand needs a playhead-first range mode (Phase 5) and accepts a 16:9 fallback until the timeline arrives (R6, G5) |

| DD31 | The number of files analysed at once is changeable at run time: `POST /api/v1/concurrency` and a field on the status page (1 to 16; a 0 count was tried and dropped as a duplicate of Pause that survives a restart silently). Pause starts nothing new and lets the running files finish, and says so on the page with the number still running; Stop now (`POST /api/v1/stop-now`, two clicks to confirm) pauses and kills the running files, which go back in the queue and start over after Resume. The page keeps a typed number until it is applied (an automatic refresh used to put the old count back as soon as the box lost focus, which on a phone is when Apply is tapped), checks it before sending, and keeps its messages up for 8 s. Raising it starts workers now; lowering it lets running files finish and takes no new ones. Saved in the database, it wins over `CONCURRENCY` until cleared (`DELETE /api/v1/concurrency`, "Use template setting" on the page). `START_PAUSED=1` starts the container paused so chosen files can be put first before anything runs | The user's request, and R12 (settings changeable without code edits or a container restart). Tested with a gated runner: three jobs held open, count lowered, none interrupted, later jobs ran on the one kept worker |

| DD32 | The status page shows one card per running file with a progress bar, the phase ("Reading the file" or "Checking changes"), the time so far and a rough time left; the page is laid out as cards that wrap, not table columns, so it reads on a phone. Progress travels from the worker process in a small file it replaces whole (`--progress-file`), is polled once a second, and is held in memory only | The user's request (R14). The bar is an estimate: the first pass is half of it and the checking of changes the other half, because the real split depends on the film (a film with many ratio changes spends most of its time checking). The first pass's share comes from ffmpeg's own `-progress` output, which tracked the clip's time evenly on a 4K test file |

| DD33 | A segment whose picture covers under half the frame's area and that was entered by a cut (not a ramp) is folded into its neighbour over the larger rectangle (`MIN_PICTURE_SHARE`) | Found on Grand Budapest Hotel: a 14 s title card measured as a 674x722 picture, and short credit segments of about 1.9. A real window that grows into the frame (a ramp beside it) is kept, and 4:3, windowboxed (80%) and 2.78:1 pictures all cover more than half the frame. Re-run on the first 4 minutes of the file: the card is gone, the real changes remain. A genuinely small picture entered by a cut now shows at the larger neighbour's size: smaller than ideal, never cut off |

| DD34 | A short run that fills the whole frame on bright samples, between smaller pictures, is kept as a candidate instead of being dropped as flicker (`is_full_frame_insert`: at least 98% of the frame both ways, mean luma 40 or more, a preset ratio, neighbours covering under 90% of its area); the refinement pass still verifies it at full resolution. A sub-second shot of a bigger picture inside a long scene is absorbed by the scene without widening it (`short_shot_dropped`) | Found by frame-sampling Oppenheimer (0.3.3 output): two of 120 sample frames were full-frame IMAX shots the timeline showed as 2.20, so the picture would have been cropped for those shots (the coarse pass saw one keyframe, and one sample cannot form a segment). On a 130 s clip from the film, 0.3.4 found none of the inserts at 26.9 s and 55.4 s; 0.3.5 finds both. A shot with no keyframe of its own (shorter than the keyframe spacing, about 1.6 s here) is still invisible to the first pass: a known limit, to be closed with denser sampling of films flagged variable-ratio if it matters |

| DD35 | The bigger-picture insert rule is widened from 98% to 90% of the frame in both directions (1.85 in 16:9 is 96%; 1.90 is 94%), and guarded by a new `skew` on each coarse sample (the larger margin minus the smaller before the crop was centred): a crop that grew on one side only is a subtitle or a bright object, not a bigger picture. A ramp measured with doubt (low confidence, many unreadable frames) between two verified pictures is placed at the safe end of its window instead of being merged | Found by frame-sampling Everything Everywhere All at Once (0.3.3 output): its fast 1.85 to 2.39 changes were merged over the larger rectangle for about 12 minutes of the film (four stretches of 100 to 220 s), and on a 200 s clip a quick change in dark frames read as a doubtful ramp widened 115 s of 2.39 to 1.85. On that clip 0.3.6 puts the changes at 70.4 s and 185.3 s, where the frames put them |

| DD36 | When a short run is merged into a neighbour, the neighbour whose picture is at least as big as the run's is chosen, however short it is (before: the longer one). A short run of at least two bright samples whose picture is clearly bigger than both neighbours, reaches the frame edge along the axis it grew, and opened the matte on both sides (even the crop's larger margin is smaller than the matte beside it) is kept for the refinement pass instead of being dropped (`is_bigger_shot`) | Found by frame-sampling Dunkirk (0.3.6 output, one spill in 1,990 frames): 3389.4 to 3391.1 s is a 1.7 s full-height shot that begins the full-frame section, and it was merged into the longer 2.20 stretch before it, so for 1.7 s the picture would have been 23% too big. On a 160 s clip of the film 0.3.7 starts the full-frame stretch at 3389.3 s, the frame the picture changes at, and the position is no longer flagged uncertain |

| DD37 | Two passes (the user's proposal): a **survey** first (`survey.py`): one seek every 8 s (`SURVEY_INTERVAL`, 0 = off), the keyframe there measured exactly as the full pass does it. If every usable sample shows one picture the file is finished from the survey (one whole-file segment, flag `surveyed`) after the usual full-resolution probes; otherwise the full keyframe pass runs unchanged. The two ways to be wrong are treated differently: any bright sample whose picture is clearly bigger than the usual one (and grew on both sides) sends the file to the full pass, because passing it as constant would leave that shot too big for the screen; a smaller picture counts only when two seeks in a row are bright and show it, because the worst a missed one costs is bars, and a dark scene's crop looks exactly like one. Samples covering under half the frame (credits) and dark samples are ignored. Too few readable samples is "unclear" and takes the full pass | Measured on real files (this PC, over the network, 3 to 4 films at a time, so absolute times mean little): of the films the full pass stored with more than one segment, the eight whose segments differ in zoom by more than a few percent (Dunkirk, Interstellar, Oppenheimer, Everything Everywhere, Grand Budapest, Buddy, American Pie 4, Minions & Monsters) were all sent to the full pass; Hercules, Poltergeist, Just Friends and Masters of the Universe differ by under 3% between segments (an end card, credits at a slightly different crop) and were taken as constant, as were Frankenstein and KPop Demon Hunters (one segment stored); Check Point has one 28 s card near the end that is wider than the film (a smaller picture, so playing it at the film's zoom shows bars, never overhang) and was taken as constant; of 7 random films 6 were constant and 1 was sent on and does change (a title card in a window). First version (any sample 3% off counted) sent three constant films to the full pass because of dark-scene crops; that is why a smaller picture now needs two bright seeks in a row. Cost: about 0.35 s per seek on a quiet network, about 800 seeks for a 6,400 s film. Known blind spot: a film that is constant apart from one or two bigger shots shorter than the interval can be missed (setting `SURVEY_INTERVAL=4` halves the gap). Speed on the NAS is not measured yet; a 1080p film the NAS reads in 165 s gains little |

| DD38 | Titles known to change ratio skip the survey and get the full pass (`known.py`, `data/known_variable.txt`, optional extra file `KNOWN_VARIABLE_FILE`). Matching is by title and year, from the file's name or its folder's. The bundled list is built from Wikipedia's "List of IMAX films" (films shot partly on IMAX film cameras) and well-documented variable-ratio releases; it is a hint and deliberately wide, because which ratio a *file* has depends on its release. The user then supplied two reference tables (compiled on videofileblog.com, whose notice reserves its content from AI copying; the analyzer did not fetch them): the list takes their **titles and years only**, no notes, from the whole non-IMAX table and from the IMAX table's films with at least one variable release (disc, 3D disc, 4K disc or streaming), with alternative ("a.k.a.") titles. Matching ignores accents, punctuation and a leading article | The survey's weak spot is exactly the IMAX-camera film (few, short bigger shots), and a title on the list costs at most the full pass, which is what every film cost before the survey existed. Of the 2,427 movie folders in Movies and Christmas Movies, 76 match the list (3%). In this library it covers Dunkirk, Interstellar, Oppenheimer, Everything Everywhere and Grand Budapest of the films the full pass found variable; American Pie 4, Buddy, Minions & Monsters and others are on no list and are found by the survey |

| DD39 | Hybrid analysis, first part (DD30): `ON_DEMAND_ROOTS` folders are catalogued as `idle` and never queued in the background; a request from a player (by Plex part id, rating key or path) queues the title at priority 0; `EXPRESS_WORKERS` (default 1) extra workers take only priority-0 titles, keep working while analysis is paused by hand, and still obey quiet hours and the Plex-transcode pause. Plex ids are attached to files by folder and file name when no `PATH_MAP` rule covers the Plex path (`NameIndex`, never guessing between two files). **Not built:** a playhead-first partial analysis; a title that changes ratio is analysed whole | Found when the user played *Crystal Lake* (TV, not mounted in the container): the analyzer could not know the file, so the add-on's fallback (the whole 3840x2160 frame contained in the screen) showed the episode's baked-in letterbox as black bars. The survey (DD37) is what makes on demand practical: an episode that keeps one ratio takes a few minutes. Without an express worker a request waits behind whatever is running, which on this library is films of one to three hours |

| DD40 | The add-on's service opens PM4K when Kodi starts (setting `open_at_start`, add-on id in `start_addon`), by `RunScript(script.plexmod)` once the home screen is up. Kodi's `autoexec.py` was tried first and never ran on this Kodi; `RunAddon` opened PM4K as a plugin folder and failed. Skip steps: Kodi's `videoplayer.seeksteps` set to -10,10 (was -600,-300,-180,-60,-30,-10,10,30,60,180,300,600) and PM4K's `kodi_skip_stepping` turned on, so PM4K skips 10 s each way (its own default is back 10 s, forward 30 s) | The user's requests. Verified on the Shield by screenshot: Kodi opens to PM4K's profile picker; the skip steps read back as -10,10 over JSON-RPC and in `guisettings.xml`; PM4K's own use of them is not yet tried in playback |
| DD41 | A player's request has its own priority (`PRIORITY_PLAYER`, -1; `analyze=1` on the timeline route, and a POST to `/analyze` with no source or `source: "player"`); a re-queue made by hand says `source: "manual"` (the status page's "Analyze again" does) and stays at 0. Express workers (default 2) take only player requests; background workers take everything in order. Hand re-queues therefore wait for Resume like the rest | Found by the user: Oppenheimer, re-queued by hand at 0 for review, started by itself on a paused container, because the express worker took it; it then held the only express worker for about an hour, so *Crystal Lake* S01E07 and E08 (asked for by the add-on, status queued) were never analyzed and still played with the fallback's black bars |
| DD42 | Hybrid analysis, second part: a title a player is waiting for gets a **provisional timeline** within seconds. The survey (DD37) now starts at the playhead (`at` in the request) and writes what it knows after its first 3 places and every 4 after that; the API answers 404 with a `provisional` member; the add-on uses it, keeps asking (2 s for two minutes) and swaps in the final timeline. A known-variable title is surveyed for the player too, then still gets the full pass. A request far from where a running survey began, and not covered by what it knows, moves the job (`REPOSITION_S` 90 s). Safety: the rectangle errs large, a stretch needs two agreeing places and ends at a bigger picture, dark samples claim nothing, everything unclaimed plays at the whole frame | The user: waiting minutes for the first correct zoom is not acceptable ("send the result for the first minute, then survey the rest"). Measured on *Crystal Lake* S01E08 from a PC over SMB: first stretch after 1.7 s; from the start of the file the stretches covered 8 to 2,398 s of 2,614 s after 90 s, with whole-frame gaps only at the changes of picture (a 1.67 card at 32 to 64 s) and the credits. The final timeline of that episode took 381 s (it has changes, so it takes the full pass). The 2.35 picture is baked into the 16:9 frame (3840x1636 inside 3840x2160), which is why the fallback zoom of 0.7565 left bars and the provisional zoom of 0.99 does not |
R10 status: the template, the install text with the GHCR visibility steps, and the anonymous-pull check are written; the check itself happens after the first publish.

**R8 after the gate:** the Gap narrows. An HDR10+ sample exists; profile 5 is out of scope; interlaced is accepted as untested. The remaining R8 work is the planned synthetic fixtures.

**Verdict after the gate:** all five gate items are answered. Six Gaps remain open (section 8, items 1 to 6, with item 1 now reduced to "interlaced and profile 5 are accepted as untested"). **Phase 4 may start.**
