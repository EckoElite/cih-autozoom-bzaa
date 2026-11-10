# Progress: Shield thread

Newest first. Shared history before 2026-11-09 is in PROGRESS.md.

## 2026-11-10: emoji above U+FFFF stripped from PM4K text

New `players/pm4k-osd/patch_emoji.py` patches `lib/windows/kodigui.py` in the add-on (original kept as `kodigui.py.cih-orig`): a `cihNoAstral()` helper drops characters above U+FFFF, the joiners and variation selectors that go with them and the extra space left, and is applied to list item labels (`ManagedListItem` constructor, `setLabel`, `setLabel2`, `setProperty`) and to both window and dialog `setProperty`. `reapply.py` pulls, patches and pushes the file with the rest. Helper checked offline on sample strings (flame, flame mid-title, ZWJ family sequence, multi-line, non-strings, a heart with a variation selector); patched file compiles.

Verified on the Shield, Movies library grid: "Trending Movies Now" shows "Trending Movies..." with no box; "Popular Movies" keeps its outline star; PM4K starts and browses normally. The flame in the collection's poster artwork is part of Plex's image and stays. Not checked: titles with emoji elsewhere (search results, details pages), because the library only has the two collection names above with emoji, as far as the Plex API shows.

## 2026-11-09 (later): emoji in PM4K titles, partly fixed with a merged font

Built `RobotoEmoji-Regular.ttf` and `RobotoEmoji-Medium.ttf` (Roboto from the Shield's `/system/fonts` plus Google's monochrome Noto Emoji, weights 400 and 500, 5250 glyphs, about 1.1 MiB each) with `players/pm4k-osd/build_emoji_font.py` (needs `pip install fonttools`; downloads Noto Emoji, SIL OFL 1.1, from github.com/google/fonts once). `apply_font.py` now points the four `plex*` fonts at those files (and migrates a Font.xml that still names the plain Roboto files); `reapply.py` builds and pushes the files when the skin's fonts folder lacks them. The two files were pushed to the Shield's skin fonts folder and Font.xml was updated; Kodi restarted.

Verified on the Shield (Movies grid, collection tiles): the star in "Popular Movies" (U+2B50, in the Basic Multilingual Plane) now draws as an outline star. The flame in "Trending Movies Now" (U+1F525) still draws as a box.

Cause, with one caveat: Kodi looks up glyphs by the low 16 bits of the character (from memory of its font code, not checked against the source here), so characters above U+FFFF (a flame, a clapper board, a popcorn box) cannot draw whatever the font holds, while BMP symbols (star, heart, sun, lightning, sparkles) can. Emoji are drawn flat, in the text colour.

Options for the astral ones, not done: strip them from titles in PM4K's Python (a patch like `patch_dropdown.py`), or map them into the font's private use area and translate the titles. The collections in the Movies and TV Shows libraries with emoji names are: a flame ("Trending ... Now") and a star ("Popular ..."), one pair per library.

Side note: once, closing the search panel with Back and pressing Down showed PM4K's "Sign in with Plex" (plex.tv/link) code screen. Pressing Back returned to the signed-in home screen, so nothing was lost; cause unknown. Nothing was entered on it.

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
