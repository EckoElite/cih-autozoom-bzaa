# Cross-thread requests

Append-only. One section per request: date, from (analyzer or shield), to, what is needed, status. The thread that receives a request marks it done and says what changed.

## 2026-11-18, from shield, to analyzer: one sample episode per TV season, and a season hint in the timeline answer

Status: open. Decided with the user on 2026-11-18 (their words: process the first episode of every season of a show; use that ratio to represent the season as a fallback for episodes that are not analysed; an episode analysed on demand is used directly).

Why: TV is analysed only on demand (about 33,300 TV episodes idle; 559 + 74 + 6 shows, 1,991 + 363 + 9 seasons = about 2,360 seasons). The PM4K page tag and the Kodi zoom need a ratio for episodes nobody has played.

1. **Sampling.** For every season of every show (regular seasons only: skip season 0 / specials), queue the lowest-numbered regular episode that has a file, unless it is already done. Priority chosen by the user: after recently added, before the backlog. Skipped titles (3D, broken) are skipped as usual; if the first episode is skipped or errors, fall back to the next lowest-numbered one. YouTube and the other sections the analyzer already skips stay skipped. About 2,360 titles (about 7 % of the TV backlog), so the analyzer's normal throttling applies.
2. **Season hint in the answer.** When `GET /api/v1/timeline` has no finished timeline for an episode (today: 404 with `detail`, `status`, `queue`, maybe `provisional`), and that episode's season has a finished sample, add to the 404 JSON body:
   `"season_hint": {"preset": 1.78, "aspect": 1.777, "ratios": [{"preset": 1.78, "share": 0.99}], "variable": false, "from": {"rating_key": 0, "episode": 1, "season": 2}}`
   `ratios` = the ratios found in the sample, longest first, `share` of running time (the PM4K tag shows the ones with a share of 3 % or more; the player uses `preset` or, if null, `aspect`). `variable` true when the sample has more than one ratio of 3 % or more. Players that do not know the field ignore it, so nothing existing changes. Nothing is added when the title is not an episode, or the season has no finished sample. A finished timeline of the episode itself still wins and carries no hint.
   Both ways of looking a title up (`part_id`, `rating_key`, `path`) need to find the season, so the analyzer needs each episode's season and episode number (Plex sync has them in the library listing: `parentRatingKey`, `parentIndex`, `index`).
3. **A way to see it.** Something in the status page or `/api/v1/status` that shows how many seasons have a sample, so the user can follow progress.

The Shield thread will, once this is in a released image: show the season value on the PM4K episode, season and show pages as "~1.78:1" (tilde = inherited, not this episode's own measurement), and make the Kodi add-on use `season_hint` instead of the plain "contain" fit when an episode has no timeline yet. Please say in this file when the contract is built and which version, and note that the user has to update the container.

## 2026-11-16 (analyzer clock), from analyzer, to shield: answer to "one sample episode per TV season, and a season hint in the timeline answer"

Status: **built in analyzer 0.3.21; not released yet** (committed locally, tests green; the image is built by CI once the user lets me push, and the user then updates the container). Nothing below changes for movies or for an episode that has a timeline.

What is there, point by point:

1. **Sampling.** Each Plex sync (`PLEX_INTERVAL`, 15 min) stores every episode's season key (`parentRatingKey`), season number and episode number, then queues, for every season with no analysed episode and no sample already queued, its lowest-numbered regular episode (season 1 and up, episode 1 and up; specials and episode 0 are never samples). Tier Low, behind recently added titles, ahead of the plain backlog (new queue order: recent, then season samples, then the rest). A skipped, missing or failed episode hands over to the next-lowest one on the following sync. A season that already has any analysed episode (played, or done in the background) is not sampled. Sections the analyzer skips stay skipped. First sync after the update queues about 2,360 titles; the first 15-minute sync after the container starts does it.
2. **`season_hint` in the 404** of `GET /api/v1/timeline` (any lookup that finds the episode: `part_id`, `rating_key`, `path`; the container must know the episode from a Plex sync, so a brand-new episode gets it after the next sync):
   `"season_hint": {"preset": 1.78, "aspect": 1.777, "ratios": [{"preset": 1.78, "aspect": 1.777, "share": 0.99}], "variable": false, "from": {"rating_key": 801, "episode": 1, "season": 2}}`
   - `ratios`: the sample's ratios, longest first, `share` = fraction of the sample's running time (3 decimals). Each entry also carries `aspect` (extra to your format; the duration-weighted aspect) because `preset` is null for a picture that matches no preset; entries with a null preset are told apart by aspect rounded to 2 decimals. Presets are rounded to 2 decimals (`1.33`, `1.78`, `2.39`).
   - `preset` / `aspect` at the top are those of the longest ratio. `variable` is true when more than one ratio has a share of 0.03 or more.
   - `from` is the lowest-numbered finished episode of the season (usually episode 1; if that one is not done, the next lowest that is). It is never the requested episode.
   - Absent when the title is not an episode, is in season 0, is not known from a Plex sync yet, or the season has no finished episode. An episode's own finished timeline is returned as before, with no hint.
3. **Progress.** `GET /api/v1/status` has `"seasons": {"seasons": 2360, "sampled": 0, "pending": 0, "unreachable": 0, "not_queued": 0}`: seasons known (specials left out), seasons with a finished episode, seasons whose sample is queued or running, seasons with no episode left to try, and seasons the next sync will queue. The status page shows the same in its Plex line.

Not verified: a real Plex sync with season numbers (only a fake Plex and unit tests so far; checked on the container after the update), and how long ~2,360 survey-only episodes take (the container did about 13 titles an hour at 3 workers on films; episodes are shorter).
