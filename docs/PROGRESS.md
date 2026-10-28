# PROGRESS

**Current phase:** 0 (Environment and discovery) complete; Phase 1 (geometry calibration) next
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

## Next

Phase 1: build the calibration videos in `tools/testpatterns/` (SDR and HDR10), have the user play them on the Shield in Kodi (view mode Normal, zoom 1.0) and read the numbered rows at the screen's top and bottom edges.
