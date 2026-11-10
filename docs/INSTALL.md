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
by an express worker (`EXPRESS_WORKERS`, three by default, which keep working even while analysis is paused), the
survey starts where you are watching and the add-on has a first zoom for that stretch within a few seconds (until then
it shows the whole frame contained in the screen). The survey runs about 25 times faster than playback, so it stays far
ahead of you; a film or episode that keeps one ratio gets its final timeline in a few minutes, and one that changes
takes as long as the full pass, with the stretches the survey found steady already played at the right zoom meanwhile. Plex ids are matched to files by folder and file name when no `PATH_MAP` rule
covers the Plex path. If files were already queued before `ON_DEMAND_ROOTS` was set (the first scan ran without it), the next scan puts the ones nobody asked for back to "on demand".

**Titles that change ratio often: the dense pass.** The first pass reads about one keyframe a second, which is
right for a film that changes ratio a few times and wrong for one that flips every second or two (a multiverse montage,
a flashback). The dense pass reads every reference frame (about every third frame), measures the picture in each, and
builds the film's states from those measurements, so a shot of a few frames is followed on its exact frames and a
stretch the first pass misjudged is corrected. Ramps (gradual matte changes) are kept as the first pass measured
them. It is slow: measured on a gaming PC reading a 4K remux over SMB, the full film (139 min) took 4 h 28 min, about 1.9 times its running time, on top of the first pass. The Unraid CPU has not been measured; expect it to be no faster. That is why it runs only for titles that need it, behind the first pass.

It runs, once per title, for:

| Title | When | Queue tier |
|---|---|---|
| on the known-variable list | in the same run as the first pass (a title already analysed is queued again once, starting from its stored result) | P3 Medium |
| any title whose first pass found `DENSE_TRIGGER` (20) or more changes | after the first pass: the title is queued again, starting from the result just stored, so players have the first-pass timeline meanwhile | P2 High |
| any title whose segment checks saw a bigger picture in a minority of their probes (flag `mixed_probes`: a short shot at another ratio) | the same | P2 High |
| any title, by hand | **Analyze again** with Pass set to *dense (follow every change)* | P1 Highest |

After an update, the next scan queues the titles already analysed that qualify (the known-variable list at P3, the
titles with 20 or more changes at P2). A result that came from the dense pass says so (flag `dense`; the title's
page shows "dense pass") and is not queued for it again.

What keeps it from following misreads: a sample whose crop grew on one side only (a subtitle, a bright object in the
matte) is ignored; a state shorter than a second must show a scene change at both of its edges and, unless it is a
bigger picture than both neighbours, must be bright (a dark frame's crop looks like a smaller picture); a state
shorter than `DENSE_MIN_FRAMES` frames is not followed. What fails those goes to the neighbour with the bigger
picture, so a miss costs a little extra black and never a picture too big for the screen.

**The queue and its priorities.** Titles a player is waiting for never queue: three express workers
(`EXPRESS_WORKERS`) take them at once, even while analysis is paused, and a fourth waits in an express line ahead of
the queue. Everything else is one queue in four tiers, taken in this order:

| Tier | What puts a title there |
|---|---|
| P1 Highest | you pressed Analyze (or Retry) on the status page |
| P2 High | a re-queue through the API (`source: "manual"`), and automatic re-analysis: the file was replaced, or the analyzer version changed |
| P3 Medium | the title is on the known-variable list |
| P4 Low | everything else; titles Plex added in the last `RECENT_DAYS` days go first, then the newest file first |

Inside P1 and P2 the first title in goes first. On the status page, **Up next** (at the bottom) shows the express slots and
the queue as two lines per title: the title, then its tier, its place in the tier and in the whole queue, and a
"variable list" tag. Change a title's priority with the menu on its row, tick several and move them together, or drag
a title: dropped under another title it takes that title's tier, dropped under a heading it goes to the top of that
tier, and dropped at the very top it becomes P1. A title you placed by hand keeps its place; titles queued later go
behind it in the same tier, and a title you placed is not moved up by the known-variable list. Searching for a title
shows the same tier and place while it waits, and the date, analyzer version, pass and time taken once it is done.

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
| `EXPRESS_WORKERS` | `3` | extra workers that take only titles a player is waiting for; they run even while analysis is paused by hand (a re-queue made by hand does not count: it waits for Resume) |
| `SURVEY_INTERVAL` | `8` | seconds between the places the quick first survey looks at (at least 2; `0` turns it off). A file that shows one picture throughout is finished by the survey; any other file gets the full pass. A short change that falls between two places, in a film that never changes otherwise, is missed |
| `DENSE_TRIGGER` | `20` | how many ratio changes the first pass must find before a title also gets the dense pass (see below); `0` turns the automatic trigger off, and titles on the known-variable list always get it |
| `DENSE_MIN_FRAMES` | `4` | the shortest change the dense pass follows, in frames (4 is about 0.17 s; at least 2) |
| `KNOWN_VARIABLE_FILE` | | optional text file (for example `/config/known_variable.txt`), one `Title (Year)` per line, of extra films that change ratio; they skip the survey and get the full pass, like the titles bundled with the analyzer, and are queued ahead of recently added titles and the backlog (behind only titles asked for by hand or by a player) |
| `SAMPLE_INTERVAL` | `1` | seconds between sampled keyframes in the full pass (at least 1) |
| `SAMPLE_INTERVALS` | | per-folder overrides, `prefix=seconds;prefix=seconds` |
| `SCAN_INTERVAL` | `3600` | seconds between scans of the media folders |
| `PLEX_INTERVAL` | `900` | seconds between reads of Plex |
| `RECENT_DAYS` | `14` | Plex titles added this recently are analysed first |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `TZ` | | time zone for quiet hours and the page |

### Memory limit (recommended)

Set **Extra Parameters** (Edit the container, switch on *Advanced View*) to `--memory=8g --memory-swap=8g`; the template
now has this by default, but an already-installed container keeps its old settings until you add it. With a limit, a
runaway analysis is stopped and restarted by Docker on its own, instead of using all of the server's RAM until
the whole Unraid box hangs (this happened twice on 2026-11-09/10, see PROGRESS-analyzer.md). Normal use is well under
2 GB with `CONCURRENCY` 1 and a few GB at 6 workers plus 3 express workers; raise the limit if the log shows
"Killed" with nothing wrong in the analysis.

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
