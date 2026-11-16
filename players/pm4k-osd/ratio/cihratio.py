# coding=utf-8
"""CIH: the aspect ratio of a title, from the analyzer, as a tag on PM4K's movie page.

Installed as lib/windows/cihratio.py by players/pm4k-osd/patch_ratio.py. The page calls start(window, video) when it fills
in its details; the answer arrives a moment later (a background thread, so the page never waits for the network) as the
window property cih.ratio, which the chip added by ratio_chip.py shows. Only titles the analyzer has finished get a tag:
a title it has not seen, is still working on, or cannot reach gets none (no request for analysis is ever made from here).

Text: "2.39:1" for a title with one ratio; "VAR 1.85 / 2.39 / 1.33" for a variable-ratio title (every ratio that fills 3 % or
more of the running time, longest first). The analyzer's address and key are the Kodi add-on service.cih.zoom's settings.
"""
from __future__ import absolute_import

import json
import threading
import time

import six.moves.urllib.error
import six.moves.urllib.parse
import six.moves.urllib.request
import xbmcaddon

from lib import util

PROPERTY = 'cih.ratio'
TIMEOUT_S = 3
MIN_SHARE = 0.03  # a ratio that fills less of the film than this is a title card, a logo or a flash
OK_TTL_S = 3600
MISS_TTL_S = 120

_cache = {}  # (part id or rating key) -> (time, text)


def log(msg, *args):
    util.DEBUG_LOG(u'Ratio tag: ' + msg, *args)


def summarize(timeline):
    """the tag text for an analyzer timeline, or '' when it has no usable segments"""
    seconds = {}
    for segment in timeline.get('segments') or []:
        value = segment.get('preset') or segment.get('aspect')
        try:
            length = float(segment.get('end')) - float(segment.get('start'))
            ratio = round(float(value), 2)
        except (TypeError, ValueError):
            continue
        if length > 0 and ratio > 0:
            seconds[ratio] = seconds.get(ratio, 0.0) + length
    total = sum(seconds.values())
    if not total:
        return u''
    merged = {}  # 1.33 and 1.34 are one ratio measured twice: fold a value into a bigger one within 0.025 of it
    for ratio, secs in sorted(seconds.items(), key=lambda item: -item[1]):
        near = [r for r in merged if abs(r - ratio) <= 0.025]
        merged[near[0] if near else ratio] = merged.get(near[0] if near else ratio, 0.0) + secs
    seconds = merged
    ranked = sorted(seconds.items(), key=lambda item: -item[1])
    kept = [ratio for ratio, secs in ranked if secs / total >= MIN_SHARE] or [ranked[0][0]]
    if len(kept) == 1:
        return u'{0:.2f}:1'.format(kept[0])
    return u'VAR ' + u' / '.join(u'{0:.2f}'.format(ratio) for ratio in kept[:4])


def fetch(ident):
    """the tag text for a title the analyzer has finished; '' otherwise. `ident`: part_id / rating_key."""
    try:
        addon = xbmcaddon.Addon('service.cih.zoom')
        base = (addon.getSetting('analyzer_url') or u'').strip().rstrip('/')
        key = (addon.getSetting('api_key') or u'').strip()
    except Exception:
        return u''
    if not base:
        return u''
    query = six.moves.urllib.parse.urlencode(dict((k, v) for k, v in ident.items() if v))
    request = six.moves.urllib.request.Request(base + '/api/v1/timeline?' + query)  # no analyze=1: never queues work
    if key:
        request.add_header('X-Api-Key', key)
    try:
        response = six.moves.urllib.request.urlopen(request, timeout=TIMEOUT_S)
        timeline = json.loads(response.read().decode('utf-8'))
    except six.moves.urllib.error.HTTPError:
        return u''  # 404: not analysed (yet)
    except Exception as err:
        log('analyzer not reachable: {0}', type(err).__name__)
        return u''
    # a 200 is a finished, current timeline: the analyzer answers 404 for anything else
    return summarize(timeline) if isinstance(timeline, dict) else u''


def identify(video):
    """part ids of the title's media, then its rating key: what the analyzer can look a title up by"""
    ids = []
    try:
        for media in video.media():
            for part in media.parts():
                if part.id:
                    ids.append({'part_id': u'{0}'.format(part.id)})
    except Exception:
        pass
    if video.ratingKey:
        ids.append({'rating_key': u'{0}'.format(video.ratingKey)})
    return ids


def lookup(video):
    for ident in identify(video):
        key = tuple(sorted(ident.items()))
        cached = _cache.get(key)
        if cached and time.time() - cached[0] < (OK_TTL_S if cached[1] else MISS_TTL_S):
            if cached[1]:
                return cached[1]
            continue
        text = fetch(ident)
        _cache[key] = (time.time(), text)
        if text:
            return text
    return u''


def start(window, video):
    """show the tag for `video` on `window` (a cached answer at once, otherwise when the analyzer has answered)"""
    try:
        window.setProperty(PROPERTY, u'')
        ref = u'{0}'.format(video.ratingKey)

        def work():
            try:
                text = lookup(video)
                if text and u'{0}'.format(getattr(window.video, 'ratingKey', '')) == ref:
                    window.setProperty(PROPERTY, text)
            except Exception:
                util.ERROR()

        thread = threading.Thread(target=work)
        thread.daemon = True
        thread.start()
    except Exception:
        util.ERROR()
