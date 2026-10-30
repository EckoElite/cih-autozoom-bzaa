"""identify.py and client.py: which title is playing, and the analyzer conversation (stand-in HTTP, no network)."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service.cih.zoom"))
import client  # noqa: E402
import identify  # noqa: E402


class IdentifyTests(unittest.TestCase):
    def test_direct_play_url_gives_the_part_id_and_never_the_token(self):
        url = "http://10.0.0.5:32400/library/parts/48213/1729000000/file.mkv?X-Plex-Token=SECRET&download=0"
        ident = identify.identify(url)
        self.assertEqual((ident["kind"], ident["part_id"]), ("plex-part", 48213))
        self.assertNotIn("SECRET", json.dumps(ident))

    def test_transcode_url_gives_the_rating_key(self):
        url = ("http://10.0.0.5:32400/video/:/transcode/universal/start.m3u8?"
               "path=%2Flibrary%2Fmetadata%2F9021&protocol=hls&X-Plex-Token=SECRET")
        ident = identify.identify(url)
        self.assertEqual((ident["kind"], ident["rating_key"], ident["part_id"]), ("plex-transcode", 9021, None))

    def test_share_paths_and_local_files_are_paths(self):
        for path in ("smb://NAS/media/Movies/4K Movies/Dunkirk (2017)/Dunkirk (2017) 4k HDR.mkv",
                     "/storage/emulated/0/Movies/x.mkv", "nfs://nas/export/movie.mkv", r"D:\media\film.mkv"):
            with self.subTest(path=path):
                ident = identify.identify(path)
                self.assertEqual(ident["kind"], "path")
                self.assertEqual(ident["path"].replace("%20", " "), path)

    def test_the_url_pm4k_hands_kodi_on_the_shield(self):
        # as read from the Shield (2026-10-30): the played URL has no file name and a trailing slash; the item's own
        # path is the Plex-side path
        played = ("https://10-29-20-200.5033aa7ded574c4699153e0e29c4ff77.plex.direct:32400/library/parts/551450/"
                  "1791975125/?X-Plex-Token=SECRET&X-Plex-Client-Profile-Name=Generic&X-Plex-Session-Id=abc")
        item = "/movies/Buddy (2026)/Buddy (2026) WEBDL-2160p.mkv"
        ident = identify.identify_all(played, item)
        self.assertEqual((ident["kind"], ident["part_id"], ident["path"]), ("plex-part", 551450, item))
        self.assertEqual(identify.identify_all(item, played), ident)             # whichever order Kodi gives them
        self.assertNotIn("SECRET", json.dumps(ident))
        only_path = identify.identify_all("", item)
        self.assertEqual((only_path["kind"], only_path["path"], only_path["part_id"]), ("path", item, None))
        self.assertEqual(identify.identify_all("", "")["kind"], "unknown")

    def test_the_rest_is_unknown(self):
        for odd in ("", "plugin://plugin.video.thing/?play=1", "pvr://channels/tv/1"):
            self.assertEqual(identify.identify(odd)["kind"], "unknown")

    def test_cache_keys_differ_by_title_and_hold_no_path_text(self):
        keys = {identify.cache_key(identify.identify(u)) for u in (
            "http://h/library/parts/1/2/f.mkv", "http://h/library/parts/2/2/f.mkv", "smb://a/b.mkv", "smb://a/c.mkv")}
        self.assertEqual(len(keys), 4)
        self.assertTrue(all("smb" not in k and "/" not in k for k in keys))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Server:
    """A stand-in for urlopen: records requests, answers from a script."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append((req.get_method(), req.full_url, dict(req.header_items()), req.data, timeout))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(json.dumps(answer).encode("utf-8"))


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b"{}"))


TIMELINE = {"video": {"fps": 24}, "segments": [], "override": {"mode": "disable", "preset": None}}
PART = {"kind": "plex-part", "part_id": 7, "rating_key": None, "path": None}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix="cih_cache_")

    def make(self, server, key="K"):
        return client.AnalyzerClient("http://analyzer:8765/", key, self.cache, timeout=1.0, opener=server)

    def test_a_lookup_asks_by_part_id_with_the_key_and_a_short_timeout(self):
        server = Server(TIMELINE)
        found = self.make(server).timeline(PART)
        self.assertEqual(found.status, "ok")
        method, url, headers, _, timeout = server.requests[0]
        self.assertEqual((method, url), ("GET", "http://analyzer:8765/api/v1/timeline?part_id=7"))
        self.assertEqual(headers.get("X-api-key"), "K")
        self.assertEqual(timeout, 1.0)
        self.assertEqual(found.override, {"mode": "disable", "preset": None})

    def test_every_key_we_have_goes_in_one_request(self):
        server = Server(TIMELINE)
        ident = {"kind": "plex-part", "part_id": 7, "rating_key": None, "path": "/movies/A (2000)/A.mkv"}
        self.make(server).timeline(ident)
        self.assertEqual(server.requests[0][1],
                         "http://analyzer:8765/api/v1/timeline?part_id=7&path=%2Fmovies%2FA+%282000%29%2FA.mkv")

    def test_a_missing_title_is_queued_and_reported_missing(self):
        server = Server(http_error(404), {"status": "queued"})
        found = self.make(server).timeline(PART)
        self.assertEqual(found.status, "missing")
        self.assertEqual([r[0] for r in server.requests], ["GET", "POST"])
        self.assertEqual(json.loads(server.requests[1][3]), {"part_id": 7})
        self.assertTrue(server.requests[1][1].endswith("/api/v1/analyze"))

    def test_polling_does_not_queue_again(self):
        server = Server(http_error(404))
        self.assertEqual(self.make(server).timeline(PART, request_analysis=False).status, "missing")
        self.assertEqual(len(server.requests), 1)

    def test_an_unreachable_analyzer_uses_the_copy_on_disk_else_nothing(self):
        self.make(Server(TIMELINE)).timeline(PART)                       # writes the copy
        down = Server(urllib.error.URLError("no route"))
        found = self.make(down).timeline(PART)
        self.assertEqual((found.status, found.timeline["video"]["fps"]), ("cached", 24))
        self.assertEqual(self.make(down).timeline({"kind": "plex-part", "part_id": 99}).status, "unreachable")

    def test_a_server_error_is_treated_like_an_unreachable_analyzer(self):
        self.make(Server(TIMELINE)).timeline(PART)
        self.assertEqual(self.make(Server(http_error(500))).timeline(PART).status, "cached")

    def test_a_404_with_an_older_copy_uses_the_copy_while_the_new_one_is_made(self):
        self.make(Server(TIMELINE)).timeline(PART)
        found = self.make(Server(http_error(404), {"status": "queued"})).timeline(PART)
        self.assertEqual(found.status, "cached")

    def test_nothing_to_ask_about_and_no_url(self):
        self.assertEqual(self.make(Server(TIMELINE)).timeline({"kind": "unknown"}).status, "unknown")
        self.assertEqual(client.AnalyzerClient("", "", self.cache).timeline(PART).status, "unreachable")

    def test_set_override_posts_the_mode_and_preset(self):
        server = Server({"override": {}})
        self.assertTrue(self.make(server).set_override(PART, "preset", 1.85))
        self.assertEqual(json.loads(server.requests[0][3]), {"part_id": 7, "mode": "preset", "preset": 1.85})
        self.assertFalse(self.make(Server(urllib.error.URLError("down"))).set_override(PART, "clear"))

    def test_the_copy_is_written_atomically_and_the_key_is_not_in_it(self):
        self.make(Server(TIMELINE), key="TOPSECRET").timeline(PART)
        names = os.listdir(self.cache)
        self.assertEqual(names, ["part7.json"])
        self.assertNotIn("TOPSECRET", (Path(self.cache) / names[0]).read_text())


if __name__ == "__main__":
    unittest.main()
