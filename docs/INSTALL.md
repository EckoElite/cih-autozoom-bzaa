# Installing the analyzer on Unraid

The analyzer is one container. It reads your media (read-only), works out where the picture sits in every file, and
serves the result over HTTP on your LAN. You do not need SSH: everything below is done in the Unraid web UI and a
browser. (Site-specific values, such as your own paths and Plex address, are in `docs/LOCAL_INSTALL.md`, which is
kept out of the public repository.)

## 0. What you need

- Unraid with Docker enabled and a share that holds your media.
- Optional: a Plex server address and token (the container works without Plex, but then it has no Plex ids, no
  "recently added first" and no pausing during transcodes).
- About 900 MB for the image and a few hundred MB of appdata per 10,000 analysed files (estimate, to be measured).

## 1. The image (one time)

GitHub Actions builds and publishes `ghcr.io/eckoelite/cih-analyzer` whenever `main` changes.

**Check that the package is public.** Unraid pulls without a login only if it is. The first publish from this public
repository came out public (verified 2026-10-29: an anonymous registry request returned the manifest, and an anonymous
`docker pull` on the PC succeeded and started a healthy container). To check again from any machine without logging
in: `docker logout ghcr.io` then `docker pull ghcr.io/eckoelite/cih-analyzer:latest`.

If it ever turns out to be private: on github.com open your profile, then **Packages**, then **cih-analyzer**, then
**Package settings**, **Danger Zone**, **Change package visibility**, **Public**.

If you prefer to keep it private, Unraid needs to log in first: create a GitHub personal access token with only
`read:packages`, and run `docker login ghcr.io` in the Unraid web terminal. The token then lives on the server; it is
never part of the template.

## 2. Add the container

**Option A: from the template (preferred).** Unraid 7.2.4 has no **Template repositories** setting under **Settings**,
**Docker** (reported by the user, 2026-10-29), so put the template file where Unraid keeps custom templates:

1. In the Unraid web UI open the terminal (the **`>_`** icon at the top right).
2. Paste, as one line, and press Enter:
   `wget -O /boot/config/plugins/dockerMan/templates-user/my-cih-analyzer.xml https://raw.githubusercontent.com/EckoElite/cih-autozoom-bzaa/main/unraid/cih-analyzer.xml`
   (it only downloads this repository's template file).
3. **Docker** tab, **Add Container**, open the **Template** drop-down and pick **my-cih-analyzer** under the user
   templates. The form fills in. This route has not yet been tried on a real Unraid server; if the template does not
   appear, use option B.

**Option B: by hand.** **Docker** tab, **Add Container**, and fill in the fields as below. Click **Add another Path,
Port, Variable, Label or Device** for each row of the tables.

| Field | Value |
|---|---|
| Name | `cih-analyzer` |
| Repository | `ghcr.io/eckoelite/cih-analyzer:latest` |
| Network type | `bridge` |
| Console shell | `sh` |

| Type | Name | Container side | Host side | Mode |
|---|---|---|---|---|
| Port | Web UI and API | `8765` (TCP) | `8765` | |
| Path | Appdata | `/config` | `/mnt/user/appdata/cih-analyzer` | Read/Write |
| Path | Library: one small library first | `/media/<name>` (for example `/media/musicvideos`) | the library's folder on the array | **Read Only** |

| Variable | Value |
|---|---|
| `PATH_MAP` | one rule per library, see below |
| `MEDIA_ROOTS` | `/media` |
| `PLEX_URL` | `http://<plex address>:32400` (optional) |
| `PLEX_TOKEN` | your Plex token (optional; tick **Mask**) |
| `API_KEY` | a long random string (optional; tick **Mask**) |
| `TZ` | your time zone, for example `America/New_York` |

Everything else has a sensible default (see **Settings** below).

**`PATH_MAP`** tells the analyzer that several names are the same folder, so a Plex path finds its timeline. One rule
per library, rules separated by `;`, names inside a rule separated by `|`, the first name being the path inside this
container:

```
/media/musicvideos|/musicvideos|/mnt/user/media/Music Videos;/media/movies|/movies|/mnt/user/media/Movies
```

Use the path Plex shows for the library (the container-side path of the library in Plex's own template) as the second
name. Paths are case-sensitive.

3. Click **Apply**. Unraid pulls the image and starts the container.

## 3. First run

1. Open the container's **WebUI** (right-click the icon, **WebUI**), or go to `http://<unraid address>:8765/`. The
   status page should show a scan finishing and the queue filling.
2. `http://<unraid address>:8765/health` should answer `{"ok":true,...}`.
3. Watch the first library: the page lists what is running and what failed. A title opens to a bar of its ratio
   segments and, for gradual matte changes, a per-frame plot of the measured edge against the stored curve.
4. Note the throughput (titles per hour) after the first library finishes; that is the number to plan the rest with.

**Order.** Start with one small library. Add the next by adding another Read Only path and its `PATH_MAP` rule, then
**Apply**. The queue keeps what is already done.

**Large libraries (TV): analysed when played.** Mount the library read-only (for example at `/media/tv`) and set
`ON_DEMAND_ROOTS` to `/media/tv`. The analyzer then catalogues its files (the status page counts them as "on demand")
but queues none of them. When you play an episode, the Kodi add-on asks for it by its Plex id; it is analysed at once
by an express worker (`EXPRESS_WORKERS`, one by default, which keeps working even while analysis is paused), the
add-on shows the whole frame contained in the screen meanwhile, and switches to the right zoom when the timeline
arrives. A film or episode that keeps one ratio takes a few minutes (the survey reads a few percent of the file); one that
changes takes as long as the full pass. Plex ids are matched to files by folder and file name when no `PATH_MAP` rule
covers the Plex path.

## 4. Settings

All settings are environment variables; change them by editing the container and clicking **Apply**.

| Variable | Default | Meaning |
|---|---|---|
| `CONFIG_DIR` | `/config` | appdata folder |
| `MEDIA_ROOTS` | `/media` | folders scanned, separated by `;` |
| `PATH_MAP` | | equivalent names of the same folder |
| `PLEX_URL`, `PLEX_TOKEN` | | Plex server (optional) |
| `PLEX_SKIP_SECTIONS` | | Plex library titles to ignore; only affects Plex ids, **what is analysed is what you mount** |
| `API_KEY` | | if set, the API needs it as the `X-Api-Key` header |
| `CONCURRENCY` | `1` | files analysed at once when the container first starts; each runs at the lowest CPU and disk priority. Change it later on the status page ("Files analysed at once"): raising it starts more now, lowering it lets running files finish and takes no new ones. A count saved there is kept across restarts and wins over this setting |
| `QUIET_HOURS` | | `HH:MM-HH:MM` local time when analysis pauses |
| `START_PAUSED` | `0` | `1` starts with analysis paused until Resume on the status page; the queue still fills, so chosen files can be put first (search the title, then "Analyze again") before anything runs |
| `PAUSE_ON_TRANSCODE` | `1` | pause while Plex transcodes video |
| `ON_DEMAND_ROOTS` | | folders inside `/media` (`;` separated) analysed only when a player asks, for example `/media/tv` |
| `EXPRESS_WORKERS` | `1` | extra workers that take only titles a player asked for; they run even while analysis is paused by hand |
| `SURVEY_INTERVAL` | `8` | seconds between the places the quick first survey looks at (at least 2; `0` turns it off). A file that shows one picture throughout is finished by the survey; any other file gets the full pass. A short change that falls between two places, in a film that never changes otherwise, is missed |
| `KNOWN_VARIABLE_FILE` | | optional text file (for example `/config/known_variable.txt`), one `Title (Year)` per line, of extra films that change ratio; they skip the survey and get the full pass, like the titles bundled with the analyzer |
| `SAMPLE_INTERVAL` | `1` | seconds between sampled keyframes in the full pass (at least 1) |
| `SAMPLE_INTERVALS` | | per-folder overrides, `prefix=seconds;prefix=seconds` |
| `SCAN_INTERVAL` | `3600` | seconds between scans of the media folders |
| `PLEX_INTERVAL` | `900` | seconds between reads of Plex |
| `RECENT_DAYS` | `14` | Plex titles added this recently are analysed first |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `TZ` | | time zone for quiet hours and the page |

## 5. Updating, backing up, removing

- **Update.** **Docker** tab, **Check for Updates**, then **apply update** on the row. Appdata is kept; a newer
  analyzer upgrades the database in place and refuses a database written by a newer analyzer than itself. A new
  analyzer version that changes how files are analysed queues the affected files again at low priority; their old
  timelines keep serving until replaced.
- **Back up** `/mnt/user/appdata/cih-analyzer` (the whole folder; the database is one file plus its journal).
- **Remove.** Delete the container; delete the appdata folder if you want the results gone. Media is never touched: the
  container only has read-only access to it.

## 6. What the container never does

It never writes to media (every library is mounted read-only), never changes Plex, never runs as root (it runs as
`99:100`, Unraid's `nobody:users`), and never writes your Plex token or API key to its log.
