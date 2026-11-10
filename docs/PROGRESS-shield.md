# Progress: Shield thread

Newest first. Shared history before 2026-11-09 is in PROGRESS.md.

## 2026-11-09: PM4K screens checked after the Roboto font change; one layout fault fixed

Checked on the Shield by `adb screencap` (the Kodi GUI is captured; video is not), with the patches re-applied:

| Screen | Result |
|---|---|
| Home hub, library hubs (TV, Music, Extras) | OK |
| TV Shows grid (focus on first, second and third rows) | OK: titles and years sit under the posters, the focused poster clears the line above |
| Music grid, album page | OK |
| Artist page (long biography) | **Fault, fixed**: the biography ran over the play / shuffle buttons |
| Show page, movie page with resume bar | OK (resume bar level with the bottom of the poster) |
| Search panel, header user menu, Settings (Main, Video) | OK |
| Settings select dialogs, options dialogs, playback overlay | Not checked (select dialog did not open on a plain Enter; the overlay cannot be captured) |

Cause of the fault: `scale_ui.py` keeps a text box at its full height (its text keeps its size) while the button row under it moves up with the scaled layout. Fix: `fit_textboxes()` in `players/pm4k-osd/scale_ui.py` trims a text box that has the button row (control 300) under it so it ends 4 rows above the row. Effect on text box height (rows, window coordinates): artist 250 to 192, show page (seasons) 179 to 125, movie page (pre_play and watchlist variant) 102 to 87. A long text now shows about one line fewer, and its last visible line can be cut in half at the edge. Episodes and info pages are unchanged (the button row is beside the box, or there is none). Verified on the Shield for the artist page only; the movie page was seen with a short summary and the show page with a two-line one, so a long summary there is still unchecked.

Also: `reapply.py` now targets the Shield with `adb -s <ip>:<port>`. It failed with "more than one device/emulator" because an emulator (`emulator-5554`) is attached to adb on this PC. Use `adb -s` for every command here.

Open or unverified:
- Roboto is still unconfirmed as the typeface of the Plex apps. The only source found is a user remark in a Plex forum thread, [Plex font on Android TV](https://forums.plex.tv/t/plex-font-on-android-tv/741429); nothing found for Plex Web.
- Emoji in collection names (for example the flame in a "Trending" collection) render as a box: Roboto has no emoji glyphs and Kodi cannot draw colour emoji fonts. Options: strip emoji from titles in PM4K's Python, or accept the box. Not done.
- Season captions on the show page truncate ("Season 2...") because Roboto is wider than the old font at the same size.
- `adb shell input text` sends letters as Kodi hotkeys (it opened the PVR dialog once); use key events and the on-screen keyboard instead.
