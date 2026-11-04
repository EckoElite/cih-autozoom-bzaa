# Whole-system 2.35:1 on the SHIELD: feasibility report

Written 2026-11-04 in answer to `cih-whole-system-scope-handoff.md`. Nothing on the SHIELD was changed: only read-only
`wm size`, `wm density`, `getprop` and `dumpsys` calls were made. No code was changed. Kodi source quoted is the
`21.2-Omega` tag (fetched from GitHub); log evidence is from this project's own test-Kodi logs.

## 1. Verdict

**Feasible with changes.** The Android side should work. The Kodi GUI is the blocker, and the handoff's premise about
the starting state is wrong in a way that changes the command to run.

| Part | Verdict | Why |
|---|---|---|
| Android launcher, Plezy, SmartTube, Settings | Feasible, test needed | The display manager letterboxes a forced logical size into the physical display (AOSP behaviour, **not yet seen on this SHIELD**) |
| Kodi video path (HDR, refresh switching, my zoom patches) | Feasible | Video placement is computed in the same window space; physical modes are untouched (§3.1, §3.4) |
| Kodi GUI (ScopeNox, PM4K windows) | **Blocker without a change** | Kodi's GUI resolution follows the window, so a 16:9 skin is squashed to 2.35 (§3.2). Fixable with a small Kodi patch (§4) |
| CIH zoom code and analyzer data | Feasible, small change | Same inputs; zoom values change (§3.3) |

## 2. The handoff's starting state is not what this SHIELD reports

Read-only output from the SHIELD (SHIELD Android TV, Android 11, SDK 30):

```
Physical size: 3840x2160
Override size: 1920x1080
Physical density: 320
dumpsys window displays:  init=3840x2160 320dpi  base=1920x1080 320dpi  cur=1920x1080
dumpsys display:          mCurrentLayerStackRect=Rect(0, 0 - 1920, 1080)   mCurrentDisplayRect=Rect(0, 0 - 3840, 2160)
```

The SHIELD already runs Android at a **1920×1080 logical display upscaled 2× to the 3840×2160 HDMI signal**, and Kodi's
own log agrees (`window resolution: 1920x1080`, `Current resolution: 1920x1080 3840x2160 @ 23.976025`: GUI size, then
physical mode). So:

- `wm size 3840x1632` would not "keep Android at 4K and add bars". It would raise the logical size from 1920 to 3840
  wide: 4× the pixels for every app and for Kodi's GUI buffer, and density would have to be doubled just to keep the
  UI the same size. There is no benefit; the UI is 1080p today by design.
- The equivalent that keeps today's pipeline is **`wm size 1920x816`** (816 = 1920 ÷ 2.353). The display manager's mapping
  then gives 3840×1632 with 264 black rows top and bottom, 2× scaling exactly as now. For density, the handoff's
  "start here" (480 at 3840 wide, i.e. 1280×544 dp) is **`wm density 240`** at 1920 wide.
- **Revert with explicit values, not `wm size reset`.** `base=1920x1080` is a forced size; `reset` removes it and
  would leave logical 3840×2160. Revert with `wm size 1920x1080` and `wm density reset` (no density override exists
  now). Whether the SHIELD's firmware re-forces 1080p at boot is unknown; check after a reboot.

The 816-row band (264–1896) sits inside the measured visible band (258–1901, `config/geometry.json`), so no spill; the
screen shows about 5 px of black more at each edge than the 1643 measured rows.

## 3. Conflicts, ranked

### 3.1 Kodi derives resolution from the window, and video placement stays consistent: OK (evidence)
`CAndroidUtils` (`xbmc/windowing/android/AndroidUtils.cpp`): with `videoscreen.limitgui` = Auto (0) it sets
`m_width/m_height = 0`, and `GetNativeResolution` then takes the **native window size** as the GUI resolution
(`iWidth/iHeight`), while `iScreenWidth/iScreenHeight` stay the physical display mode (3840×2160). `SetNativeResolution`
calls `SetBuffersGeometry(iWidth, iHeight)`, so the GUI buffer is window-sized. `ProbeResolutions` clamps every mode to
`m_width × m_height` (cached on first call: **Kodi must be restarted after `wm size`**). The MediaCodec renderer sets
`m_surfDestRect = m_destRect`, which is in GUI/window coordinates, and my SurfaceControl patch (0002) works in the view
size, so the video rectangle follows the 1920×816 window. Refresh switching calls `SetDisplayMode(modeId)` on the
**physical** mode list, which `wm size` does not touch (the logs already show `3840x2160@23.976025` modes selected under
a 1920×1080 GUI).
- **Set `videoscreen.limitgui` to Auto.** "Unlimited" (9999) would make Kodi use the largest physical mode as its GUI
  size (3840×2160) in a 1920×816 window, which is a non-uniform squash of everything.
- Whitelist: no change needed for the logical size. Keep 2160p modes (the physical 1080p modes would only matter if the
  HDMI mode itself switched down).

### 3.2 Skins stretch: confirmed, but the direction is vertical squash, and the unit is the skin canvas (**blocker**)
`CGraphicContext::GetGUIScaling` (`xbmc/windowing/GraphicContext.cpp`) maps the skin's declared canvas to the whole
window with `scaleX = fromWidth/toWidth`, `scaleY = fromHeight/toHeight` (independent), and `CSkinInfo` picks the
`<res>` whose aspect is closest but then scales it the same way. Both ScopeNox (`<res width=1920 height=1080
aspect=16:9 folder=scopeformat>`) and PM4K (only a `1080i` folder) are 1920×1080 canvases, so in a 1920×816 window every
layout is **squashed to 75.6% vertically** (the handoff said stretched horizontally by 1.32: same distortion, opposite
axis). ScopeNox does not ship a 2.35 `<res>`; its scope mode is a `Skin.HasSetting(scopeFormat235)` reflow inside the 16:9
canvas, so with the setting on, the band would also be squashed (double letterbox). The video control and the video
rectangle are not affected (§3.1).
- `guiInsets` exists in `GetGUIScaling` but only fits the canvas **into** a smaller rectangle (uniform loss of size),
  it cannot crop.
- Pixel-ratio calibration is no use: it changes video and GUI together.

### 3.3 Zoom values change, the plan format does not (small change)
Today: Kodi "Normal" fits the frame into 3840×2160 and the add-on shrinks content into the 1634-row band (16:9 → 0.7565).
In the whole-system mode Normal fits into 3840×1632, so for the same picture: `zoom_new = zoom_old × f_old / f_new`
with `f = min(3840/W_frame, H_view/H_frame)`. For a 16:9 frame that is ×1.3235; for a native-ratio frame (3840×1608)
it is ×1.0 (width-limited both times). Worked values for a 16:9 frame, contain rule, `min(1920/1451, 816·a/1451)`:

| Active ratio | now (projector band) | whole-system |
|---|---|---|
| 1.78 | 0.757 | 1.000 |
| 1.85 | 0.787 | 1.040 |
| 1.90 | 0.809 | 1.071 |
| 2.00 | 0.851 | 1.126 |
| 2.39 | 1.000 | 1.323 |

The handoff's formula is right. Two things follow:
- **Zoom now goes above 1.0** for everything matted inside a 16:9 frame, which does not happen today. Kodi's JSON-RPC
  zoom accepts up to 2.0. My renderer patch (0003) rescales the destination rectangle about the view centre, so the
  rectangle will extend past the 1920×816 view by up to ±130 px; whether SurfaceFlinger/HWC clips that cleanly on the
  SHIELD (and keeps the hardware overlay) is **unverified** and is the first thing to test.
- **The frame-skip problem is already fixed** in the patched Kodi (patches 0001 to 0003: no dropped frame per change,
  25 of 25 cuts within half a content frame, ramps one step per frame). The handoff's "known issue" no longer holds for
  the `.cih` test build, and variable-ratio titles would use the same table either way.
- The **analyzer and its timelines are unchanged** (active rectangle in frame pixels, times on the player clock). The
  mapping to zoom lives in the add-on's `zoomcore`/`plan`, using `config/geometry.json`.

### 3.4 Capture/detection code: none is in use
No screen-capture detection exists (decision D4; L1). `script.cihautonox.tools` / `service.cihautonox.autozoom` are not
installed (Phase 0 inventory), and the ScopeNox autozoom add-on was disabled. Nothing breaks. ScopeNox's own
`scopeFormat235` and masking settings must be **off** in the whole-system mode.

### 3.5 Other items from the handoff
| Item | Assessment |
|---|---|
| Configuration-change restart (6) | Unverified. Close Kodi before `wm size` and start it after: its cached `m_width/m_height` (above) would otherwise be stale |
| Video path quality (7) | Not measurable now (nothing playing). Check with `dumpsys SurfaceFlinger` during playback before and after (test plan). Expect the same hardware overlay scaled 2× as today, plus a vertical offset of 264 px |
| SHIELD AI upscaling | Not checked; it only acts on sources up to 1080p and is independent of the logical size |
| Subtitles/OSD (R13) | Improve for free: the logical display is the visible band, so subtitles and the OSD land on screen (R13 is deferred in the projector-band mode) |
| Plezy's mpv / SmartTube | Pillarbox 16:9 and fill native scope files with no work; **mattes inside a 16:9 frame still need a zoom**, which only Kodi has (and Plezy has none) |

## 4. Proposed changes (nothing done yet)

| # | Change | Effort |
|---|---|---|
| 1 | Add-on setting `display_mode` = `projector_band` (default, today's behaviour) / `system_scope`; `zoomcore` takes the view size and band from the mode (formula in §3.3). Auto-detect option from `System.ScreenWidth/Height` (aspect near 2.35 means system mode). Unit tests for both | 0.5 to 1 day |
| 2 | Kodi patch 0004 "GUI canvas crop": a setting `gui_canvas_crop` that makes `GetGUIScaling` map the skin canvas 1:1 and centred (translate by (816−1080)/2 rows) instead of squashing. A 16:9 skin whose content already sits in the central 2.35 band (ScopeNox scope mode, PM4K with the margin patch) then works **unchanged**, cropped to the screen exactly as the projector crops it today. Default off | 0.5 to 1 day plus a CI build |
| 3 | Renderer: confirm or make the destination rectangle clip to the view for zoom above 1.0 (SurfaceControl crop in patch 0002 if SurfaceFlinger does not) | 0.5 day if needed |
| 4 | Optional: a skin with a real 2.35 canvas (`<res height=816 aspect=2.35:1>`) so the GUI is not cropped from 16:9. ScopeNox is a reflowing 16:9 skin, so this is a fork of it; PM4K has dozens of 1080i windows | Large (weeks); not recommended unless 2 is unacceptable |
| 5 | Docs: both modes, the revert recipe, the warning about `wm size reset` | 0.25 day |

Hybrid toggle (Kodi at 16:9 while other apps use 2.35) is not practical: `wm size` is global and needs shell rights; a
network ADB script would restart activities each time. Not recommended.

## 5. Test plan (run on the SHIELD; steps marked **ask** change the display and need you at the screen)

Before anything, save a baseline: `wm size`, `wm density`, `dumpsys display` (the lines above), `dumpsys window displays`,
and during a playback `dumpsys SurfaceFlinger` (composition type of the video layer, HDR state) plus my
`watch_kodi_sf.py` frame-pacing capture.

1. **Baseline playback** in the `.cih` Kodi: a 16:9 title, a 2.39-in-16:9 title, a native 3840×1608 title, the boundary
   test video (frame-exact cuts), EEAAO ramp 1731 to 1737 s; note HDR on the projector info.
2. **ask** Close Kodi. `adb shell wm size 1920x816`, `adb shell wm density 240`. Reboot is optional; try without first.
3. Launcher, Settings, Plezy, SmartTube: nothing clipped, remote navigation OK. Check `dumpsys display` shows
   `mCurrentDisplayRect=Rect(0, 264 - 3840, 1896)` and the layer-stack rect 1920×816. If not, the mapping assumption is wrong
   and the plan stops.
4. Pixel check: play `cal_sdr_2160p.mkv` (4 px row chart) and the ratio frames; the 2.35 ratio frame border should meet all four
   screen edges, and the rows chart should be unchanged.
5. Kodi GUI (limit GUI = Auto): expect a squashed skin (this confirms §3.2). Then, once patch 0004 exists, the cropped
   skin.
6. Kodi playback with `display_mode = system_scope` (needs change 1): the same titles as step 1. Pass: 16:9 pillarboxed at zoom 1.0,
   2.39-in-16:9 fills the width, native 2.39 needs no zoom, cuts and ramps as frame-exact as today (re-run the
   240 fps clip and my analysis), HDR10 still reported, no dropped frames in the capture, `SurfaceFlinger` shows the video
   layer on the hardware overlay and the zoom above 1.0 is clipped, not spilled.
7. A 23.976 title: refresh-rate switch happens, the logical size does not change, GUI returns correctly.
8. Subtitles and OSD sit on the screen.
9. **ask** Revert: `adb shell wm size 1920x1080`, `adb shell wm density reset`, reboot, and compare `dumpsys` with the baseline.

## 6. Open questions for you

1. What do you want from the whole-system mode: scope-fit streaming apps (SmartTube, Plezy's own playback), the on-screen OSD and
   subtitles in Kodi, or both? Kodi's CIH already works in the current mode; this decides whether the cost is worth it.
2. Is a cropped 16:9 Kodi GUI (patch 0004) acceptable, or do you want a true 2.35 GUI (change 4)?
3. The handoff mentions a JVC DLA-X750R besides the XGIMI. Is it in the same room and fed from the same SHIELD? Its lens
   memory or anamorphic path would change the geometry.
4. Are you happy to let me do the display change over ADB while you watch (the **ask** steps), and to restart the `.cih` Kodi
   afterwards?

## 7. Result of the on-device test (2026-11-04): the SHIELD stretches instead of letterboxing

Run with the user at the screen (they ran the `wm` commands; I read the state). `wm size 3840x1632` + `wm density 480`:
`dumpsys display` reported the expected `mCurrentLayerStackRect=Rect(0, 0 - 3840, 1632)` and `mCurrentDisplayRect=Rect(0, 264 - 3840, 1896)`,
but SurfaceFlinger's composition state was `viewport=[0 0 3840 1632] ... frame=[0 0 3840 2160]` and the user saw every app
**stretched vertically (x1.32) to fill the 16:9 signal**. With `wm scaling off` applied (`display_scaling_force=1`) SurfaceFlinger's frame
was unchanged. So this firmware's compositor ignores the letterbox rectangle that Android's display manager computes, and `wm size` to a
different aspect cannot give a real 2.35 letterbox here. `wm overscan` does not exist on Android 11 (not in `wm help`). The assumption in
section 1 ("feasible, test needed") was wrong for this device. Kodi's own CIH remains the working path; the whole-system idea needs a
different mechanism (per-app settings, or an external video processor or projector aspect feature). Revert recipe: `wm scaling auto`,
`wm size 1920x1080`, `wm density reset`.

## 8. Per-app options and the SHIELD's own overscan (2026-11-04, read-only inspection)

Settings strings read from the installed APKs (not exercised on the screen):

| App | Relevant options |
|---|---|
| Projectivy Launcher 4.72 | Launcher "Top margin (in screen height %)", "Start margin", "End margin"; "Display Zoom" (window-manager size/density tool); no bottom margin seen |
| SmartTube 32.68 | Video zoom (default / fit width / fit height / fit either / stretch), Aspect ratio, Pixel ratio, UI scale, Video grid scale; no margin or overscan option |
| Hopwatch 1.3.15 | Its own "Calibrate overscan" screen (visible-region calibration) and zoom in/out controls |
| Plezy 2.25.0 | Video zoom 50 to 200 % with presets (README); a fixed `video-zoom` through its mpv config (Spike B); UI cannot be moved |

The SHIELD has NVIDIA's `com.nvidia.overscancomp` ("Adjust for overscan", `com.nvidia.overscancomp.action.START_OVERSCANCOMP`). It stores a
**single** value, `persist.vendor.tegra.hdmi.underscan` (read as 0.24 after the user fitted the picture to the screen height, which is
1 - 1643/2160 within rounding), and it scales the whole 16:9 output uniformly, so the picture stays 16:9 with black sides. It cannot
shrink vertically only, so it cannot be combined with `wm size 3840x1632` to cancel the stretch. `persist.sys.displayinset.top` also
exists (value 0) but a persist.sys property cannot be set from the ADB shell without root. Consequences: underscan fits every app's whole UI
inside the visible band at about 76 % size; it must be off for Kodi's CIH (CIH needs the full 3840 px frame width).
