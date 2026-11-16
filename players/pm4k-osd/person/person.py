# coding=utf-8
"""CIH: a person page for PM4K, like the one in the Plex apps.

Installed as lib/windows/person.py by players/pm4k-osd/patch_person.py (pushed by reapply.py). Opened from a cast, director
or writer entry (patched into roles.py). Rows: what you own with this person (all libraries, from the server's own
/library/people/<tagKey>/media), Known for, Upcoming, the filmography by credit group, and Appearances (hidden until
asked for). Titles you own open normally; the others open PM4K's Watchlist detail page. Data comes from Plex Discover
(discover.provider.plex.tv/library/people/<tagKey>), the same service the Plex apps use; any failure leaves the page empty
and the caller falls back to the old per-section grid.
"""
from __future__ import absolute_import

import datetime
import re

from kodi_six import xbmc
from kodi_six import xbmcgui
from plexnet import callback, plexapp, plexobjects, plexrequest, playqueue
from plexnet import util as pnUtil

from lib import util
from . import busy
from . import dropdown
from . import info
from . import kodigui
from . import opener
from . import search
from . import windowutils

POSTER_DIM = util.scaleResolution(244, 361)
PROFILE_DIM = util.scaleResolution(519, 519)

LIBRARY_LIST = 400
KNOWN_FOR_LIST = 401
UPCOMING_LIST = 402
CREDIT_LISTS = (403, 404, 405, 406, 407)
APPEARANCES_LIST = 408
ALL_LISTS = (LIBRARY_LIST, KNOWN_FOR_LIST, UPCOMING_LIST) + CREDIT_LISTS + (APPEARANCES_LIST,)

SORTS = (('newest', 'Newest first'), ('oldest', 'Oldest first'), ('title', 'Title'), ('rating', 'Rating'))
MAX_ROW_ITEMS = 120


def log(msg, *args):
    util.DEBUG_LOG(u'Person: ' + msg, *args)


def personKey(role):
    """Plex's cross-server key for a cast, director or writer entry ('' when the server has none)"""
    try:
        key = u'{0}'.format(role.get('tagKey') or '')
    except Exception:
        key = ''
    return key if len(key) >= 20 else ''


def discoverJson(path):
    """GET on Plex Discover as JSON with the account's token (never logged); None on any failure"""
    try:
        server = pnUtil.SERVERMANAGER.getDiscoverServer()
        url = server.buildUrl(path, includeToken=True)
        if not url:
            return None
        response = server.session.get(url, headers={'Accept': 'application/json'})
        if response.status_code != 200:
            log('Discover {0} answered {1}', path, response.status_code)
            return None
        return response.json().get('MediaContainer')
    except Exception:
        util.ERROR()
        return None


def ownedItems(key):
    """everything in the libraries of the selected server with this person, across all sections"""
    try:
        server = plexapp.SERVERMANAGER.selectedServer
        path = '/library/people/{0}/media'.format(key)
        data = server.query(path)
        if data is None:
            return []
        container = plexobjects.PlexContainer(data, initpath=path, server=server, address=path)
        items = []
        for elem in data:
            item = plexobjects.buildItem(server, elem, path, container=container)
            if item is not None and item.TYPE in ('movie', 'show'):
                items.append(item)
        return items
    except Exception:
        util.ERROR()
        return []


def guidOf(entry):
    return u'plex://{0}/{1}'.format(entry.get('type'), entry.get('ratingKey'))


def sortKey(mode):
    def year(entry):
        return entry.get('sortDate') or u''

    if mode == 'title':
        return lambda e: e['title'].lower()
    if mode == 'rating':
        return lambda e: -(e.get('rating') or 0)
    if mode == 'oldest':
        return year
    return year


def ownedEntry(item, role=None):
    try:
        rating = item.get('audienceRating') or item.get('rating')
        rating = float(u'{0}'.format(rating)) if rating else 0.0
    except Exception:
        rating = 0.0
    date = u'{0}'.format(item.get('originallyAvailableAt') or '') or u'{0}-01-01'.format(item.get('year') or '0000')
    return {'kind': 'owned', 'obj': item, 'title': item.title or '', 'year': u'{0}'.format(item.get('year') or ''),
            'sortDate': date, 'rating': rating, 'role': role or '', 'type': item.TYPE,
            'guid': u'{0}'.format(item.get('guid') or ''), 'watched': bool(item.isWatched)}


def discoverEntry(md, role, owned):
    guid = guidOf(md)
    date = md.get('originallyAvailableAt') or u'{0}-01-01'.format(md.get('year') or '0000')
    return {'kind': 'discover', 'md': md, 'obj': owned.get(guid), 'title': md.get('title') or '',
            'year': u'{0}'.format(md.get('year') or ''), 'sortDate': date, 'rating': md.get('rating') or 0,
            'role': role or '', 'type': md.get('type'), 'guid': guid, 'owned': guid in owned}


def thumbUrl(server, url, dim):
    if not url:
        return ''
    try:
        return server.getImageTranscodeURL(url, dim[0], dim[1]) or url
    except Exception:
        return url


NO_GLYPH = re.compile(u'[⺀-鿿가-힯豈-﫿＀-￯]+')


def cleanSummary(text):
    """the biography without the source line, and without the Chinese, Japanese or Korean spellings that the font
    cannot draw (they would show as boxes)"""
    text = (text or u'').replace(u'\xa0', u' ').split(u'\n\nDescription above')[0]
    text = NO_GLYPH.sub(u'', text)
    text = re.sub(u'\\((?:[A-Za-z ]+:\\s*)?[;,]\\s*', u'(', text)
    text = re.sub(u'\\(\\s*\\)', u'', text)
    return re.sub(u' {2,}', u' ', text)


class PersonWindow(kodigui.ControlledWindow, windowutils.UtilMixin):
    xmlFile = 'script-cih-person.xml'
    path = util.ADDON.getAddonInfo('path')
    theme = 'Main'
    res = '1080i'
    width = 1920
    height = 1080

    HOME_BUTTON_ID = 201
    SEARCH_BUTTON_ID = 202
    INFO_BUTTON_ID = 301
    PLAY_BUTTON_ID = 302
    SHUFFLE_BUTTON_ID = 303
    MORE_BUTTON_ID = 304

    def __init__(self, *args, **kwargs):
        kodigui.ControlledWindow.__init__(self, *args, **kwargs)
        windowutils.UtilMixin.__init__(self)
        self.role = kwargs.get('role')
        self.key = kwargs.get('key') or personKey(self.role)
        self.name = u'{0}'.format(self.role.tag if self.role else '')
        self.exitCommand = None
        self.lastFocusID = None
        self.sortMode = 'newest'
        self.unwatchedOnly = False
        self.showAppearances = False
        self.owned = []
        self.credits = {}
        self.knownFor = []
        self.profile = {}
        self.groups = []
        self.lists = {}
        self.loaded = False

    # ------------------------------------------------------------------ data

    def fetch(self):
        self.profile = {}
        profile = discoverJson('/library/people/{0}'.format(self.key)) or {}
        for md in profile.get('Metadata') or []:
            self.profile = md
            break
        credits_ = discoverJson('/library/people/{0}/credits'.format(self.key)) or {}
        self.groups = credits_.get('CreditGroup') or []
        known = discoverJson('/library/people/{0}/person_known_for'.format(self.key)) or {}
        self.knownFor = known.get('Metadata') or []
        self.owned = ownedItems(self.key)
        return bool(self.profile or self.groups or self.owned)

    def ownedByGuid(self):
        return dict((u'{0}'.format(i.get('guid') or ''), i) for i in self.owned if i.get('guid'))

    def rolesByGuid(self):
        roles = {}
        for group in self.groups:
            for credit in group.get('Credit') or []:
                md = credit.get('Metadata')
                if md and credit.get('role') and guidOf(md) not in roles:
                    roles[guidOf(md)] = credit.get('role')
        return roles

    # ------------------------------------------------------------------ rows

    def makeItem(self, entry, dim=POSTER_DIM):
        thumb = ''
        server = plexapp.SERVERMANAGER.selectedServer
        if entry['kind'] == 'owned':
            obj = entry['obj']
            thumb = obj.defaultThumb.asTranscodedImageURL(*dim)
        else:
            if entry.get('obj') is not None:
                thumb = entry['obj'].defaultThumb.asTranscodedImageURL(*dim)
            else:
                thumb = thumbUrl(server, entry['md'].get('thumb'), dim)
        sub = entry['role'] or entry['year']
        if entry['role'] and entry['year']:
            sub = u'{0}  •  {1}'.format(entry['year'], entry['role'])
        mli = kodigui.ManagedListItem(entry['title'], sub, thumbnailImage=thumb, data_source=entry)
        mli.setProperty('thumb.fallback', 'script.plex/thumb_fallbacks/{0}.png'.format(entry['type'] == 'show' and 'show' or 'movie'))
        obj = entry.get('obj')
        if obj is not None:
            if entry['kind'] == 'discover':
                mli.setBoolProperty('owned', True)  # the gold check: this title is in your library
            if obj.TYPE == 'show':
                if not obj.isWatched:
                    mli.setProperty('unwatched.count', u'{0}'.format(obj.unViewedLeafCount) or '')
                else:
                    mli.setBoolProperty('watched', True)
            else:
                mli.setProperty('unwatched', not obj.isWatched and '1' or '')
                mli.setBoolProperty('watched', obj.isFullyWatched)
                mli.setProperty('progress', util.getProgressImage(obj))
        return mli

    def fill(self, listId, title, entries):
        control = self.lists[listId]
        control.reset()
        self.setProperty('row.{0}.title'.format(listId), title if entries else '')
        if not entries:
            return
        control.addItems([self.makeItem(e) for e in entries[:MAX_ROW_ITEMS]])

    def sortedLibrary(self):
        roles = self.rolesByGuid()
        entries = [ownedEntry(i, roles.get(u'{0}'.format(i.get('guid') or ''))) for i in self.owned]
        if self.unwatchedOnly:
            entries = [e for e in entries if not e['watched']]
        entries.sort(key=sortKey(self.sortMode), reverse=self.sortMode in ('newest',))
        return entries

    def fillAll(self):
        owned = self.ownedByGuid()
        today = datetime.date.today().isoformat()
        thisYear = datetime.date.today().year

        self.fill(LIBRARY_LIST, u'In your library', self.sortedLibrary())

        known = [discoverEntry(md, '', owned) for md in self.knownFor]
        self.fill(KNOWN_FOR_LIST, u'Known for', known)

        upcoming, rows, appearances = [], [], []
        seenUpcoming = set()
        for group in self.groups:
            entries = []
            for credit in group.get('Credit') or []:
                md = credit.get('Metadata')
                if not md:
                    continue
                entry = discoverEntry(md, credit.get('role'), owned)
                future = (md.get('originallyAvailableAt') or '') > today if md.get('originallyAvailableAt') else (md.get('year') or 0) > thisYear
                if future and group.get('type') != 'appeared':
                    if entry['guid'] not in seenUpcoming:
                        seenUpcoming.add(entry['guid'])
                        upcoming.append(entry)
                    continue
                entries.append(entry)
            entries.sort(key=lambda e: e['sortDate'], reverse=True)
            if group.get('type') == 'appeared':
                appearances = entries
            else:
                rows.append((group.get('title') or u'Credits', entries))

        upcoming.sort(key=lambda e: e['sortDate'])
        self.fill(UPCOMING_LIST, u'Upcoming', upcoming)
        for index, listId in enumerate(CREDIT_LISTS):
            title, entries = rows[index] if index < len(rows) else (u'', [])
            self.fill(listId, title, entries)
        self.appearanceCount = len(appearances)
        self.appearances = appearances
        self.fill(APPEARANCES_LIST, u'Appearances', appearances if self.showAppearances else [])

    def refreshLibraryRow(self):
        self.fill(LIBRARY_LIST, u'In your library', self.sortedLibrary())

    # ---------------------------------------------------------------- window

    def onFirstInit(self):
        for listId in ALL_LISTS:
            self.lists[listId] = kodigui.ManagedControlList(self, listId, 5)

        self.setProperty('artist.title', self.name)
        if self.role is not None and self.role.thumb:
            self.setProperty('thumb', self.role.thumb.asTranscodedImageURL(*PROFILE_DIM))
        self.setProperty('initialized', '1')

        ok = busy.widthDialog(self.fetch, '')
        self.setHeader()
        if not ok:
            self.setProperty('summary', u'Nothing found for this person.')
        self.fillAll()
        if self.owned:
            self.updateBackgroundFrom(self.owned[0])
        self.loaded = True
        self.setFocusId(self.PLAY_BUTTON_ID if self.owned else self.INFO_BUTTON_ID)

    def setHeader(self):
        profile = self.profile or {}
        if profile.get('title'):
            self.setProperty('artist.title', profile['title'])
        if profile.get('thumb'):
            self.setProperty('thumb', thumbUrl(plexapp.SERVERMANAGER.selectedServer, profile['thumb'], PROFILE_DIM))
        bits = []
        if profile.get('bornAt'):
            bits.append(u'Born {0}'.format(profile['bornAt']))
        if profile.get('birthPlace'):
            bits.append(profile['birthPlace'])
        if profile.get('knownFor'):
            bits.append(profile['knownFor'])
        self.setProperty('artist.genre', u'  •  '.join(bits))
        self.setProperty('summary', cleanSummary(profile.get('summary')))

    def onAction(self, action):
        try:
            if action in (xbmcgui.ACTION_MOVE_LEFT, xbmcgui.ACTION_MOVE_RIGHT) and self.lastFocusID in ALL_LISTS:
                mli = self.lists[self.lastFocusID].getSelectedItem()
                entry = mli.dataSource if mli else None
                if entry and entry.get('obj') is not None:
                    self.updateBackgroundFrom(entry['obj'])
        except Exception:
            util.ERROR()
        kodigui.ControlledWindow.onAction(self, action)

    def onFocus(self, controlID):
        self.lastFocusID = controlID
        if 399 < controlID < 500:
            self.setProperty('hub.focus', str(controlID - 400))
        elif controlID in (self.PLAY_BUTTON_ID, self.SHUFFLE_BUTTON_ID, self.MORE_BUTTON_ID, self.INFO_BUTTON_ID):
            self.setProperty('hub.focus', '')

    def onClick(self, controlID):
        if controlID == self.HOME_BUTTON_ID:
            self.goHome()
        elif controlID in ALL_LISTS:
            self.rowClicked(controlID)
        elif controlID == self.PLAY_BUTTON_ID:
            self.playOwned(False)
        elif controlID == self.SHUFFLE_BUTTON_ID:
            self.playOwned(True)
        elif controlID == self.INFO_BUTTON_ID:
            self.showBio()
        elif controlID == self.SEARCH_BUTTON_ID:
            self.processCommand(search.dialog(self))
        elif controlID == self.MORE_BUTTON_ID:
            self.optionsClicked()

    # --------------------------------------------------------------- actions

    def rowClicked(self, listId):
        mli = self.lists[listId].getSelectedItem()
        if not mli or not mli.dataSource:
            return
        entry = mli.dataSource
        try:
            if entry.get('obj') is not None:
                self.openItem(entry['obj'])
                return
            server = pnUtil.SERVERMANAGER.getDiscoverServer()
            obj = server.getObject('/library/metadata/{0}'.format(entry['md'].get('ratingKey')))
            if obj is None:
                raise ValueError('no object')
            # not directly_from_watchlist: PM4K then takes the title for one that is on the watchlist and skips checking
            self.openItem(obj, from_watchlist=True, external_item=True)
        except util.NoDataException:
            raise
        except Exception:
            util.ERROR(u'Could not open {0}'.format(entry.get('title')), notify=True, time_ms=4000)

    def playOwned(self, shuffle):
        movies = [e['obj'] for e in self.sortedLibrary() if e['type'] == 'movie']
        if not movies:
            util.ERROR(u'No movies of this person in your library', notify=True, time_ms=3000)
            return
        if len(movies) == 1:
            shuffle = False
        server = plexapp.SERVERMANAGER.selectedServer
        uri = u'server://{0}/com.plexapp.plugins.library/library/metadata/{1}'.format(
            server.uuid, u','.join(u'{0}'.format(m.ratingKey) for m in movies))
        pq = playqueue.PlayQueue(server, 'video', playqueue.PlayOptions({}))
        request = plexrequest.PlexRequest(pq.server, '/playQueues')
        request.addParam('uri', uri)
        request.addParam('type', 'video')
        request.addParam('shuffle', '1' if shuffle else '0')
        pq.addRequestOptions(request)
        context = request.createRequestContext('create', callback.Callable(pq.onResponse))
        pnUtil.APP.startRequest(request, context, body='')
        self.processCommand(opener.open(pq))

    def showBio(self):
        opener.handleOpen(
            info.InfoWindow,
            title=self.getProperty('artist.title'),
            sub_title=self.getProperty('artist.genre'),
            thumb=self.getProperty('thumb'),
            thumb_fallback='script.plex/thumb_fallbacks/music.png',
            info=self.getProperty('summary'),
            background=self.getProperty('background'),
            is_16x9=False,
        )

    def optionsClicked(self):
        check = 'script.plex/indicators/check.png'
        options = []
        for key, label in SORTS:
            options.append({'key': 'sort:' + key, 'display': u'Sort: ' + label, 'indicator': self.sortMode == key and check or ''})
        options.append(dropdown.SEPARATOR)
        options.append({'key': 'unwatched', 'display': u'Unwatched only', 'indicator': self.unwatchedOnly and check or ''})
        if getattr(self, 'appearanceCount', 0):
            options.append({'key': 'appearances', 'display': self.showAppearances and u'Hide appearances' or
                            u'Show appearances ({0})'.format(self.appearanceCount)})
        choice = dropdown.showDropdown(options, (930, 640), pos_is_bottom=False)
        if not choice:
            return
        key = choice['key']
        if key.startswith('sort:'):
            self.sortMode = key.split(':', 1)[1]
            self.refreshLibraryRow()
        elif key == 'unwatched':
            self.unwatchedOnly = not self.unwatchedOnly
            self.refreshLibraryRow()
        elif key == 'appearances':
            self.showAppearances = not self.showAppearances
            self.fill(APPEARANCES_LIST, u'Appearances', self.appearances if self.showAppearances else [])
            if self.showAppearances:
                xbmc.sleep(250)  # the row has to be visible before the list scrolls to the focus
                self.setFocusId(APPEARANCES_LIST)

