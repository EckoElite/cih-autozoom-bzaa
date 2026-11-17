"""Tests for trailer/cihtrailer.py: picking one rendition from a Plex trailer's master playlist.

Run: python -m unittest discover -s players/pm4k-osd/tests
cihtrailer imports PM4K's own modules (six, lib.util), which are stubbed here.
"""
import sys
import types
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def install_stubs():
    six = types.ModuleType("six")
    six.string_types = (str,)
    moves = types.ModuleType("six.moves")
    urllib_pkg = types.ModuleType("six.moves.urllib")
    for name in ("parse", "request"):
        module = getattr(urllib, name)
        setattr(urllib_pkg, name, module)
        sys.modules["six.moves.urllib." + name] = module
    moves.urllib = urllib_pkg
    six.moves = moves
    sys.modules.update({"six": six, "six.moves": moves, "six.moves.urllib": urllib_pkg})
    package = types.ModuleType("pm4k")
    package.__path__ = []
    util = types.ModuleType("pm4k.util")
    util.DEBUG_LOG = lambda *a, **k: None
    util.ERROR = lambda *a, **k: None
    sys.modules.update({"pm4k": package, "pm4k.util": util})


def load():
    install_stubs()
    source = (HERE.parent / "trailer" / "cihtrailer.py").read_text(encoding="utf-8")
    module = types.ModuleType("pm4k.cihtrailer")
    module.__package__ = "pm4k"
    exec(compile(source, "cihtrailer.py", "exec"), module.__dict__)
    return module


trailer = load()

PLEXVIDEOS = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=154000,CODECS="mp4a.40.2,avc1.4D401F",RESOLUTION=320x173
variant_0.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2896000,CODECS="mp4a.40.2,avc1.4D401F",RESOLUTION=1280x693
variant_5.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4647000,CODECS="mp4a.40.2,avc1.4D401F",RESOLUTION=1920x1040
variant_7.m3u8
"""


class ParseTests(unittest.TestCase):
    def test_real_master_with_audio_only_renditions(self):
        text = (HERE / "master_iva.m3u8").read_text(encoding="utf-8")
        renditions = trailer.parse_master(text)
        self.assertEqual(len(renditions), 12)
        pick = trailer.choose(renditions)
        self.assertEqual((pick[1], pick[2]), (1920, 1080))
        self.assertEqual(pick[0], 5539000)  # the best 1080p one; the audio-only ones have no size and are never chosen
        self.assertIn("video=4968000", pick[3])

    def test_picks_best_up_to_1080(self):
        pick = trailer.choose(trailer.parse_master(PLEXVIDEOS))
        self.assertEqual(pick[3], "variant_7.m3u8")

    def test_all_taller_than_limit_takes_the_smallest_picture(self):
        text = PLEXVIDEOS.replace("RESOLUTION=320x173", "RESOLUTION=3840x2160").replace("RESOLUTION=1280x693", "RESOLUTION=2560x1440").replace("RESOLUTION=1920x1040", "RESOLUTION=3000x2000")
        self.assertEqual(trailer.choose(trailer.parse_master(text))[3], "variant_5.m3u8")

    def test_separate_audio_streams_are_left_alone(self):
        text = '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",URI="audio.m3u8"\n' + PLEXVIDEOS.split("\n", 1)[1]
        self.assertIsNone(trailer.parse_master(text))

    def test_not_a_master_playlist(self):
        self.assertIsNone(trailer.parse_master("#EXTM3U\n#EXTINF:10,\nsegment.ts\n"))
        self.assertIsNone(trailer.parse_master("<html></html>"))

    def test_only_audio_renditions_gives_nothing_to_choose(self):
        self.assertIsNone(trailer.choose([(86000, 0, 0, "a.m3u8")]))


class SegmentTests(unittest.TestCase):
    def test_first_segments_resolved_against_the_playlist_address(self):
        playlist = "#EXTM3U\n#EXTINF:10,\na-1.ts?x=1\n#EXTINF:10,\nhttps://other.example/a-2.ts\n#EXTINF:10,\na-3.ts\n"
        found = trailer.segments(playlist, "https://h.example/p/q.m3u8?z=1", 2)
        self.assertEqual(found, ["https://h.example/p/a-1.ts?x=1", "https://other.example/a-2.ts"])


class WantedTests(unittest.TestCase):
    def test_discover_trailer_addresses_only(self):
        base = "https://discover.provider.plex.tv/library/metadata/abc/extras/def/parts/hls.m3u8?X-Plex-Token=t"
        self.assertTrue(trailer.wanted(base))
        self.assertFalse(trailer.wanted("https://10-0-0-2.abc.plex.direct:32400/library/parts/1/2/?X-Plex-Token=t"))
        self.assertFalse(trailer.wanted("https://discover.provider.plex.tv/library/metadata/abc"))
        self.assertFalse(trailer.wanted("https://other.example/library/metadata/abc/extras/def/parts/hls.m3u8"))
        self.assertFalse(trailer.wanted(None))
        self.assertFalse(trailer.wanted(["a", "playlist"]))

    def test_other_addresses_come_back_unchanged(self):
        url = "https://10-0-0-2.abc.plex.direct:32400/library/parts/1/2/?X-Plex-Token=t"
        self.assertIs(trailer.resolve(url), url)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        trailer._cache.clear()

    def run_with(self, opener):
        real = urllib.request.urlopen
        urllib.request.urlopen = opener
        try:
            return trailer.resolve("https://discover.provider.plex.tv/library/metadata/a/extras/b/parts/hls.m3u8?X-Plex-Token=t")
        finally:
            urllib.request.urlopen = real

    def test_resolves_against_the_redirect_target(self):
        class Response:
            def geturl(self):
                return "https://vod.example/trailers/x/main.m3u8"

            def read(self, n):
                return PLEXVIDEOS.encode()

        self.assertEqual(self.run_with(lambda *a, **k: Response()), "https://vod.example/trailers/x/variant_7.m3u8")

    def test_second_call_is_answered_from_the_cache(self):
        calls = []

        class Response:
            def geturl(self):
                return "https://vod.example/trailers/x/main.m3u8"

            def read(self, n):
                return PLEXVIDEOS.encode()

        def opener(*a, **k):
            calls.append(1)
            return Response()

        first = self.run_with(opener)
        second = self.run_with(opener)  # same address, other session ids in the query: still one request
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_a_failed_request_keeps_the_original_address(self):
        def boom(*a, **k):
            raise OSError("down")

        self.assertTrue(self.run_with(boom).startswith("https://discover.provider.plex.tv/"))


if __name__ == "__main__":
    unittest.main()
