# Actor page for PM4K: what Plex does and what is possible (2026-11-16)

## What the official Plex apps show

Sources: Plex's "Discover Credits" support article (could not be opened here: HTTP 403, so its content is known only from search excerpts),
the forum thread [Actor Page inconsistencies](https://forums.plex.tv/t/actor-page-inconsistencies/818427), and the Plezy pull request
[edde746/plezy#2956](https://github.com/edde746/plezy/pull/2956), a reverse-engineered client (not merged upstream).

- Reached from the cast row on a details page, or from People results in search.
- Shows a photo, biography and birth details, a **Known for** row, and a **filmography** grouped Actor / Producer / Appearances / Additional
  credits. Each title is marked when it is in a library you can reach, and titles on Plex's free streaming are listed too.
- Needs Plex's online metadata (the new Plex Movie / TV Series agents); embedded or NFO cast data has no Discover link. Managed accounts can open
  the page but not Known for or Filmography.
- The data comes from `https://discover.provider.plex.tv`, keyed by the cast entry's `tagKey` (not the per-server `id`). Plex does not publish
  this API.

## Checked against this server and account (read-only requests, 2026-11-16)

Test person: Michelle Yeoh, from the cast of Everything Everywhere All at Once.

| Request (host `discover.provider.plex.tv`) | Result |
|---|---|
| The server's own cast entry (`/library/metadata/<ratingKey>`, `Role`) | has `tagKey` (24 hex characters), `tag`, `role`, `thumb`, `id` |
| `GET /library/people/<tagKey>` | profile: bio (Wikipedia text), `bornAt`, `birthPlace`, `knownFor`, `thumb`, `slug`, `External` links |
| `GET /library/people/<tagKey>/credits` | 4 groups: Actor 80, Producer 8, Appearances 103, Additional 1; each credit has `role` and `Metadata` (`ratingKey`, `type`, `title`, `year`, `thumb`, `art`); 0.16 s |
| `GET /library/people/<tagKey>/related` | hubs: Movies & Shows on Plex, Known For, Featured Videos (empty placeholders; fetch each hub's `key`) |
| `GET /library/people/<tagKey>/person_known_for` | 30 titles |
| `.../media`, `.../knownfor`, `.../hubs` | 404 (guessed names; not endpoints) |

Matching to the local library: a library item's `guid` is `plex://<type>/<ratingKey>`, and a credit's `Metadata` carries the same `ratingKey` and
`type`, so `plex://{type}/{ratingKey}` finds the owned copy without a title search. For this actor: 19 Actor credits, 2 Producer and 1 Appearance
are in the library (22 rows, 20 distinct titles, movies and shows), against 17 titles that the server's own `actor=<id>` filter returns for the
Movies library alone.

## What PM4K does today

- Cast click: `lib/windows/mixins/roles.py` `roleClicked` calls `Role.sectionRoles` (`lib/_included_packages/plexnet/media.py`), which searches hubs
  by the actor's name, then shows a dropdown of library sections when more than one has the actor, then `opener.actorClicked` opens the normal library
  grid filtered by `actor=<id>` in that one section. No photo, no bio, no titles you do not own.
- PM4K already talks to Discover: `plexapp.SERVERMANAGER.getDiscoverServer()` (`plexnet/myplexserver.py`, `PlexDiscoverServer`) is used for the
  Watchlist, and a Watchlist detail window (`PrePlayWindowWL`) already opens titles you do not own.

## Options

| Option | What you get | Work | Risk |
|---|---|---|---|
| A. One merged grid | Click a cast member: one grid of everything in your library with them, across all sections, instead of a section dropdown | Small: Python only, reuse the library grid | Low |
| B. Plex-like person page | Photo, bio, born, Known for row, "In your library" row, filmography by group with owned marks; owned items open normally, others open the Watchlist detail page | Medium: a new window (XML + Python) scaled into the 2.35:1 band, plus `reapply.py` support | New files may be removed when PM4K re-renders its skin or updates; undocumented API; the page needs an online Plex account |
| C. Also "on Plex" streaming and Featured Videos | The rest of the official page | Larger | Not useful for a local-library player |

Fallbacks to keep: no `tagKey` (legacy agent), a failed Discover request, or a managed account all fall back to today's behaviour.
