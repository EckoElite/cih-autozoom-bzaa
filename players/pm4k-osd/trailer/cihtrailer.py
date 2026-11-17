# coding=utf-8
"""CIH: faster, more reliable start of Plex trailers (the "Trailer" button and the extras row of a title's page).

Installed as lib/cihtrailer.py by players/pm4k-osd/patch_trailer.py; lib/player.py's Player.play() passes every address
through resolve() before it goes to Kodi.

Why: the trailer of a title that is not in your library is a Plex Discover stream (discover.provider.plex.tv/.../extras/
<id>/parts/hls.m3u8) that redirects to an HLS "master" playlist with 8 to 12 renditions (several resolutions, some of them
audio only). Kodi's HLS reader opens EVERY rendition and reads the start of each one before it shows a frame. Measured on
the PC with ffprobe, the same trailer opens in 16 s through the master playlist and in 0.6 s through one rendition. On the
Shield that is the 25 s wait and then "Playback Failed" (Kodi gives up after 30 s), and the 5-10 s delay on the trailers
that do start.

What: for such an address, read the master playlist here (one request, a second or less), pick one rendition, the best one
up to 1080p, and hand Kodi that rendition's playlist. Anything unexpected (another host, a playlist with separate audio
streams, a failed request) returns the address unchanged, so playback is never worse than before.
"""
from __future__ import absolute_import

import re
import threading
import time

import six
import six.moves.urllib.parse
import six.moves.urllib.request

from . import util

HOST = 'discover.provider.plex.tv'
TIMEOUT_S = 20  # the master playlist of a trailer nobody asked for lately takes about 10 s to appear
MAX_HEIGHT = 1080
MAX_BYTES = 200000
CACHE_TTL_S = 900
WARM_SEGMENTS = 2  # the player opens the first two before it shows a picture
WARM_SEGMENT_BYTES = 16000000

_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_cache = {}  # address without its query -> (time, rendition address)
_lock = threading.Lock()


def log(msg, *args):
    util.DEBUG_LOG(u'Trailer: ' + msg, *args)


def parse_master(text):
    """[(bandwidth, width, height, uri)] of the renditions in a master playlist; None when it has separate audio streams
    (an EXT-X-MEDIA entry with a URI: choosing one rendition would drop the sound) or is not a master playlist"""
    renditions = []
    lines = [line.strip() for line in text.splitlines()]
    if not lines or not lines[0].startswith('#EXTM3U'):
        return None
    for i, line in enumerate(lines):
        if line.startswith('#EXT-X-MEDIA:') and 'URI=' in line:
            return None
        if not line.startswith('#EXT-X-STREAM-INF:'):
            continue
        attrs = dict((k, v.strip('"')) for k, v in _ATTR.findall(line[len('#EXT-X-STREAM-INF:'):]))
        uri = next((l for l in lines[i + 1:] if l and not l.startswith('#')), None)
        if not uri:
            continue
        try:
            bandwidth = int(attrs.get('BANDWIDTH', 0))
        except ValueError:
            bandwidth = 0
        width = height = 0
        if 'x' in attrs.get('RESOLUTION', ''):
            try:
                width, height = [int(part) for part in attrs['RESOLUTION'].split('x', 1)]
            except ValueError:
                pass
        renditions.append((bandwidth, width, height, uri))
    return renditions or None


def choose(renditions, max_height=MAX_HEIGHT):
    """the highest-bandwidth rendition that has a picture no taller than max_height; the smallest picture if all are taller;
    None when none of them says what size it is (audio only entries have no RESOLUTION and are never picked)"""
    video = [r for r in renditions if r[2] > 0]
    if not video:
        return None
    fits = [r for r in video if r[2] <= max_height]
    return max(fits, key=lambda r: r[0]) if fits else min(video, key=lambda r: r[2])


def wanted(url):
    if not isinstance(url, six.string_types):
        return False
    parts = six.moves.urllib.parse.urlparse(url)
    return parts.hostname == HOST and '/extras/' in parts.path and parts.path.endswith('.m3u8')


def resolve(url):
    """the address to give Kodi for `url`: one rendition's playlist for a Plex Discover trailer, `url` itself otherwise"""
    if not wanted(url):
        return url
    key = url.split('?', 1)[0]
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        response = six.moves.urllib.request.urlopen(six.moves.urllib.request.Request(url), timeout=TIMEOUT_S)
        final = response.geturl()
        text = response.read(MAX_BYTES).decode('utf-8', 'replace')
        renditions = parse_master(text)
        pick = choose(renditions) if renditions else None
        if not pick:
            log('keeping the master playlist (no single rendition to choose)')
            return url
        log('{0} renditions, using {1}x{2} at {3} kbit/s', len(renditions), pick[1], pick[2], pick[0] // 1000)
        picked = six.moves.urllib.parse.urljoin(final, pick[3])
        with _lock:
            _cache[key] = (time.time(), picked)
        return picked
    except Exception as err:
        log('could not read the playlist ({0}); using the original address', type(err).__name__)
        return url


def _fetch(url, limit, gzip=False):
    """GET `url` and read up to `limit` bytes (the whole body for a small one); returns (bytes, seconds). gzip=True asks
    the way Kodi's own HTTP client does (Accept-Encoding: gzip)."""
    started = time.time()
    headers = {'Accept-Encoding': 'gzip, deflate'} if gzip else {}
    response = six.moves.urllib.request.urlopen(six.moves.urllib.request.Request(url, headers=headers), timeout=40)
    data = b''
    while len(data) < limit:
        chunk = response.read(65536)
        if not chunk:
            break
        data += chunk
    return data, time.time() - started


def segments(playlist, base, count):
    """the addresses of the first `count` media segments of a media playlist"""
    found = [line.strip() for line in playlist.splitlines() if line.strip() and not line.startswith('#')]
    return [six.moves.urllib.parse.urljoin(base, line) for line in found[:count]]


def warm(extra):
    """in the background: resolve a title's trailer, then request what the player will ask for first (the rendition's
    playlist, with and without gzip, and its first two segments), so that pressing Trailer finds it all ready. Plex's video
    host builds each file on its first request, about 10 s each, and serves it in a fraction of a second after that; the gzip
    and plain versions of a playlist count as two files. A trailer nobody has played lately is that slow all the way down. Never raises; does nothing for trailers on your own server (their address is not a
    Discover one)."""
    def work():
        try:
            part = extra.media()[0].parts[0]  # media() is a method, parts a list
            url = extra.getServer().buildUrl(part.getAbsolutePath('key'), True)
            if not wanted(url):
                return
            started = time.time()
            picked = resolve(url)
            if picked == url:
                return
            body, took = _fetch(picked, MAX_BYTES)  # what the player's HLS reader asks for ...
            log('pre-fetch: playlist after {0:.1f} s', took)
            _, took = _fetch(picked, MAX_BYTES, gzip=True)  # ... and what Kodi itself asks for first: a separate file at the host
            log('pre-fetch: playlist (gzip) after {0:.1f} s', took)
            pending = segments(body.decode('utf-8', 'replace'), picked, WARM_SEGMENTS)

            def one(address):
                try:
                    data, secs = _fetch(address, WARM_SEGMENT_BYTES)
                    log('pre-fetch: segment of {0} kB after {1:.1f} s', len(data) // 1000, secs)
                except Exception as err:
                    log('pre-fetch of a segment failed ({0}: {1})', type(err).__name__, err)

            threads = [threading.Thread(target=one, args=(address,)) for address in pending]
            for thread in threads:
                thread.daemon = True
                thread.start()
            for thread in threads:
                thread.join()
            log('pre-fetch: ready after {0:.1f} s', time.time() - started)
        except Exception as err:
            log('pre-fetch failed ({0}: {1})', type(err).__name__, err)

    try:
        thread = threading.Thread(target=work)
        thread.daemon = True
        thread.start()
    except Exception:
        pass
