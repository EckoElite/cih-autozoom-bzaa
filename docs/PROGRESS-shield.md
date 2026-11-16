# Progress: Shield thread

Newest first. Shared history before 2026-11-09 is in PROGRESS.md.

## 2026-11-18: clicking a cast member sometimes did nothing

Cause: on titles you do not own (opened from the watchlist or from the person page's Known for / filmography) PM4K's movie, show and episode screens ignore a click on the cast row on purpose (`if self.fromWatchlist: return` in `preplay.py`, `episodes.py` twice, `subitems.py`), because its old per-section grid needs a library title. Clicks on titles in your library always worked (8 of 8 cast clicks on a library movie opened the page).
Fix: `patch_person.py` now also removes those guards, and `personKey()` accepts the id that Discover puts on a person (`key` = `/library/people/<id>`; Discover titles have no `tagKey`). `mixins/roles.py` gets one more guard so that a title without a person key from the watchlist still does nothing instead of failing. `reapply.py` pulls and pushes `preplay.py`, `subitems.py`, `episodes.py`. Checked: Memoirs of a Geisha (not in the library): clicking Zhang Ziyi opens her page.
Not fixed: the few library cast entries that have no `tagKey` (5 of 3505 in a sample: P90X, Tony Horton, Culture Club, a YouTube channel in Extras, Exercise and Music Videos) still use the old per-section grid, which can do nothing when the name search finds no section. Rare, left as it is.
Also: biographies now drop Armenian, Hebrew, Arabic, Persian, Indian and other scripts the font cannot draw (Persian spelling showed as boxes), as well as Chinese, Japanese and Korean.

## 2026-11-17 (evening): long-press menu with the watchlist entry, and a watchlist mark on posters (person page)

Scope as chosen by the user: actor (person) page rows only. Library grids, home rows and search are unchanged (the library grid already has PM4K's own long-press menu, without a watchlist entry).

- Long press on a title in any row: "Choose action for: <title> (<year>)" with Mark Played / Mark Unplayed (titles you own, same conditions as the library grid) and "Add to watchlist" or "Remove from watchlist" by the title's real state (read from Plex Discover `userState` when the menu opens). After the choice a pop-up says "Added to watchlist" / "Removed from watchlist", and the mark below is updated in every row that has the title.
- Mark on the poster: titles on the watchlist get a dark badge with a gold bookmark, below the gold check when the title is also in your library, or in the top-left corner when it is not. The watchlist is read once when the page opens (all pages of `/library/sections/watchlist/all`, 100 per request; 61 titles at the moment).

Checked on the Shield, with the context-menu key: menu on Sunshine (which is in the library), Add to watchlist: pop-up and both badges appeared; menu again showed Remove from watchlist; removing it took the badge away; the account's watchlist is back to its 61 titles. An unowned title on the watchlist in another row showed the bookmark in the corner. NOT checked: the Shield remote's own long press of OK. `adb input keyevent --longpress` only produced an ordinary click (the title's page opened), so I cannot tell from here whether the remote's long press reaches the page as the context-menu action, which is what the existing grid menu relies on; Mark Played / Unplayed were not clicked.

## 2026-11-17 (later): "Removed from watchlist" flashed before "Added to watchlist"

Cause: on a click, PM4K first flipped the bookmark state (hiding the "add" button and showing the "added" one) and only then did my patch set `wl.feedback`; for a moment the old feedback ("removed") was still set while the "add" button still had focus, so its label read "Removed from watchlist". Fix: `patch_watchlist.py` now sets `wl.feedback` before the state flips (it also migrates the first version of the patch on the Shield), and the labels in `button_hints.py` match the feedback value exactly (add button: empty or "removed"; added button: empty or "added"), so a half-updated moment shows no text instead of the wrong text. Checked on Sunshine: add, remove, add again, with captures about a second after the click showed "Added to watchlist" at once; a flash shorter than that cannot be seen with screenshots, so this part rests on the ordering change. The title was removed from the watchlist again afterwards.

## 2026-11-17: unowned titles opened from the person page showed "In watchlist"

Cause (mine): `person.py` opened titles you do not own with `directly_from_watchlist=True`, copied from the library screen's watchlist grid. PM4K reads that as "this title came from your watchlist": it marks it watchlisted and skips the check. Fix: pass only `from_watchlist` and `external_item` (as the home screen's watchlist rows do), so PM4K asks Plex for the real state. Checked: Sunshine (not on the watchlist according to Plex's `userState`) now opens with the outlined bookmark and "Add to watchlist". Owned titles were never affected.

## 2026-11-16 (evening): second lines scroll, watchlist feedback, button names

1. Person page: the line under each title (year and role) is now a copy of the title label, so it scrolls in the focused item exactly when the title does. Checked on a long item (Star Trek: Section 31: both lines scroll together).
2. Watchlist button (movie page and the watchlist variant): while it has focus it shows "Add to watchlist" or "In watchlist"; after a click, in gold, "Added to watchlist" or "Removed from watchlist". The feedback is the window property `wl.feedback`, set after a successful add or remove and cleared whenever PM4K checks the item's watchlist state (opening the page, coming back to it), so it falls back to the real state. `patch_watchlist.py` edits `lib/windows/mixins/watchlist.py` (two lines; original kept as `watchlist.py.cih-orig`). Checked on the Shield: add then remove on Everything Everywhere All at Once showed both messages; leaving and reopening after an add showed "In watchlist". The title is back off the watchlist as it was (I added and removed it to test).
3. Button names: `button_hints.py` appends one label per button to the end of the button row, visible only while that button has focus, so the name sits right after the last icon (Info, Play, Trailer, Versions, Audio and subtitles, More; on the watchlist variant Checking availability, Choose where to watch, Not in your library). The person page row has Biography, Play all, Shuffle, Sort and filter. Applied to the movie screen, its watchlist variant and the person page only; show, season, episode, artist and library screens are unchanged. The row is widened to 1500 so it never scrolls when the label appears.

Not checked: the Versions label (needs a title with several versions), "Checking availability" and "Choose where to watch" (transient states).

## 2026-11-16 (later): person page built and checked on the Shield

Clicking a cast, director or writer entry that has a Plex person key now opens a Plex-style person page (entries without a key keep the old per-section grid). Files: `players/pm4k-osd/person/person.py` (window and data, installed as `lib/windows/person.py`), `person/make_person_xml.py` (builds `script-cih-person.xml` from PM4K's own artist and movie screens, so it follows the theme and fonts), `patch_person.py` (installs the module, patches `lib/windows/mixins/roles.py`); `reapply.py`, `scale_ui.py` and `apply_font.py` handle the new file. The skin file is named `script-cih-*` on purpose: PM4K re-renders every screen (undoing the patches) when its count of `script-plex-*.xml` files differs from its templates.

Data: Plex Discover `/library/people/<tagKey>` (profile), `/credits` (groups), `/person_known_for`, plus the server's own `/library/people/<tagKey>/media` (everything you own with that person across sections, with watched state and progress). Owned titles in the filmography are matched by `plex://<type>/<ratingKey>`.

Page: photo, name, born / place / known for, biography (CJK spellings removed: the font cannot draw them), round buttons (info, Play all, Shuffle, options, home, search), rows In your library, Known for, Upcoming, one row per credit group, Appearances (hidden until chosen in the options menu). Gold check on owned titles in the other rows, year and role under each title. Owned titles open normally; titles you do not own open PM4K's Watchlist detail page. Options menu: sort the library row (newest, oldest, title, rating), unwatched only, show or hide Appearances. Play all and Shuffle build one server play queue from the owned movies (shows are left out).

Verified on the Shield (screenshots, 2026-11-16): Michelle Yeoh (from Everything Everywhere All at Once) and Daniel Kwan (director) open; all rows scroll into view with titles; owned title opens and Back returns; unowned Known-for title (Sunshine) opens the Watchlist page; options menu, Sort: Title reorders, Show appearances works; Shuffle started playback from the page (the log shows the streams opening) and stop returned to the page.

Not checked: Play (not Shuffle), Sort oldest / rating, Unwatched only, a show's cast, a managed account, a person with no Discover data, long-term memory use with the biggest filmographies (rows are capped at 120 items). The first-row bio can show half a line at its bottom edge, as on the artist page.

## 2026-11-16: actor page review (research only, nothing changed on the Shield)

Wrote `docs/ACTOR_PAGE_REVIEW.md`: how Plex's own person pages work (data from `discover.provider.plex.tv`, keyed by the cast entry's `tagKey`), read-only checks against this account (profile, credits, related hubs, known-for all answer; credits match library items by `plex://<type>/<ratingKey>`), what PM4K does today (cast click opens a per-section filtered grid, no photo or bio), and three options (merged grid, Plex-like page, full copy). The official support article returned 403, so its details come from search excerpts, a forum thread and a reverse-engineered Plezy pull request.

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
