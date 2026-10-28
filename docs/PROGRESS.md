# PROGRESS

**Current phase:** 0 (Environment and discovery), nearly complete
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
| Stand-in samples generated and read back correctly | `tools/testpatterns/make_stand_in_samples.py`; ffprobe and cropdetect on the outputs |

## Open findings

| Finding | State |
|---|---|
| One TV clip the user described as 4:3 measures about 1.667 wide in the section cut (single-frame cropdetect, threshold not tuned for PQ). Another clip expected to be scope measures 1.85 | Preliminary. For the analyzer to resolve; do not treat either as a ground truth |
| The user plays from Plezy and is considering PM4K (`script.plexmod`, already installed in Kodi) | If PM4K is adopted, playback is in Kodi's own player, which makes Spike A the main route. Decided at the Phase 2 gate |
| The ScopeNox skin may have its own autozoom setting | Not checked yet |
| Interlaced handling | The stand-in DVD-style sample is progressive, so interlaced sources are untested |

## Assumed (not verified)

- Everything in spec section 2 about the screen, projector, Plex and Unraid comes from the user and has not been checked from this PC.
- The "path as Plex sees it" for each library is assumed to be the Plex container path in the user's Unraid screenshots. The list was cut off, so it may be incomplete.
- The SMB share root is assumed to correspond to the Unraid user-share root.
- Spec section 4 lessons (L1 to L10) are taken as given.

## Decisions taken in chat

| Item | Value |
|---|---|
| Repo | `EckoElite/cih-autozoom-bzaa`, public. The name carries a suffix to be less guessable |
| Project folder | `Documents\Claude\cih-autozoom` |
| Public repo hygiene | Site-specific facts stay in the gitignored `docs/LOCAL_FACTS.md` |

## Still to collect

- [ ] Plex library list completeness (any library path not in the screenshots), and whether the Plex token works (not tested)
- [ ] Real native 2.39 and non-square-pixel samples (user will look; stand-ins generated for now)
- [ ] Variable-ratio clips, to be chosen from analyzer output rather than guessed timestamps

## Next

1. Commit and push the scaffold to the new repo.
2. Phase 1: build the calibration videos in `tools/testpatterns/` and have the user read the rows at the screen edges.
