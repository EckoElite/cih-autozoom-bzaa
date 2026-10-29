"""The Plex client against a fake Plex server, the media scanner, and the Plex-to-file matching."""

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from cih_analyzer.pathmap import PathMap
from cih_analyzer.plex import PlexAuthError, PlexClient, PlexError, RedactingFilter, Section, redact
from cih_analyzer.scanner import scan_media, sync_plex
from cih_analyzer.store import PRIORITY_RECENT, Store

TOKEN = "SECRET-TOKEN-123"


def movie(rating_key, title, parts, added=1_700_000_000):
    return {
        "ratingKey": str(rating_key), "title": title, "type": "movie", "addedAt": added,
        "Media": [{"Part": [{"id": pid, "file": path, "size": 10} for pid, path in parts]}],
    }  # fmt: skip


class FakePlex:
    """A tiny Plex: routes map a path to a function of the query that returns JSON."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        self.reject = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                url = urlparse(self.path)
                owner.requests.append((self.path, self.headers.get("X-Plex-Token")))
                if owner.reject or self.headers.get("X-Plex-Token") != TOKEN:
                    self.send_response(401)
                    self.end_headers()
                    return
                route = owner.routes.get(url.path)
                if route is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(route({k: v[0] for k, v in parse_qs(url.query).items()})).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def plex():
    p = FakePlex()
    p.routes["/library/sections"] = lambda q: {
        "MediaContainer": {
            "Directory": [
                {"key": "1", "title": "Movies", "type": "movie"},
                {"key": "2", "title": "TV", "type": "show"},
                {"key": "3", "title": "Music", "type": "artist"},
                {"key": "4", "title": "YouTube", "type": "movie"},
            ]
        }
    }
    yield p
    p.close()


def client(plex):
    return PlexClient(plex.url, TOKEN, timeout=5)


def test_sections_and_the_token_travels_in_a_header_not_the_url(plex):
    sections = client(plex).sections()
    assert [s.title for s in sections] == ["Movies", "TV", "Music", "YouTube"]
    path, header = plex.requests[0]
    assert header == TOKEN and TOKEN not in path


def test_paging_and_two_versions_of_one_film(plex):
    items = [movie(i, f"Film {i}", [(100 + i, f"/movies/F{i}/f.mkv")]) for i in range(1, 6)]
    items[0]["Media"][0]["Part"].append({"id": 999, "file": "/movies/F1/f.1080p.mkv", "size": 5})
    plex.routes["/library/sections/1/all"] = lambda q: {
        "MediaContainer": {
            "totalSize": 5,
            "Metadata": items[int(q["X-Plex-Container-Start"]) : int(q["X-Plex-Container-Start"]) + 2],
        }
    }
    parts = list(client(plex).parts(Section("1", "Movies", "movie")))
    assert [p.part_id for p in parts] == [101, 999, 102, 103, 104, 105]
    assert parts[0].rating_key == 1 and parts[0].title == "Film 1" and parts[0].added_at == 1_700_000_000.0
    assert len(plex.requests) == 3  # two items a page: starts 0, 2 and 4, and it stops once all 5 are read


def test_episodes_are_titled_with_season_and_episode(plex):
    plex.routes["/library/sections/2/all"] = lambda q: {
        "MediaContainer": {
            "totalSize": 1,
            "Metadata": [
                {
                    "ratingKey": "77",
                    "type": "episode",
                    "title": "Pilot",
                    "grandparentTitle": "Show",
                    "parentIndex": 1,
                    "index": 2,
                    "Media": [{"Part": [{"id": 5, "file": "/tv/Show/S01E02.mkv", "size": 1}]}],
                }
            ],
        }
    }
    (part,) = list(client(plex).parts(Section("2", "TV", "show")))
    assert part.title == "Show S01E02 Pilot" and part.rating_key == 77
    assert (
        "/library/sections/2/all?X-Plex-Container-Start=0&X-Plex-Container-Size=200&type=4",
        TOKEN,
    ) in plex.requests


def test_other_library_types_are_not_read(plex):
    assert list(client(plex).parts(Section("3", "Music", "artist"))) == []
    assert plex.requests == []


def test_recently_added_and_transcode_detection(plex):
    plex.routes["/library/recentlyAdded"] = lambda q: {
        "MediaContainer": {"Metadata": [movie(9, "New", [(90, "/movies/New/n.mkv")])]}
    }
    plex.routes["/status/sessions"] = lambda q: {
        "MediaContainer": {
            "Metadata": [{"TranscodeSession": {"videoDecision": "copy"}}, {"title": "direct play"}]
        }
    }
    c = client(plex)
    assert [p.part_id for p in c.recently_added()] == [90]
    assert c.transcoding() is False  # a remux is cheap
    plex.routes["/status/sessions"] = lambda q: {
        "MediaContainer": {"Metadata": [{"TranscodeSession": {"videoDecision": "transcode"}}]}
    }
    assert c.transcoding() is True
    plex.routes["/status/sessions"] = lambda q: {"MediaContainer": {"size": 0}}
    assert c.transcoding() is False


def test_a_rotated_token_is_reported_without_leaking_it(plex):
    plex.reject = True
    with pytest.raises(PlexAuthError) as caught:
        client(plex).sections()
    assert TOKEN not in str(caught.value) and "rotated" in str(caught.value)


def test_unreachable_and_unusable_servers_raise_plex_error(plex):
    with pytest.raises(PlexError):
        PlexClient("http://127.0.0.1:9", TOKEN, timeout=1).sections()
    plex.routes["/library/sections"] = lambda q: "not a dict"
    with pytest.raises(AttributeError):  # a body that parses as JSON but has the wrong shape
        client(plex).sections()


def test_redaction_scrubs_tokens_from_text_and_log_records():
    assert redact("GET /x?X-Plex-Token=abc123&a=1") == "GET /x?X-Plex-Token=<redacted>&a=1"
    record = logging.LogRecord(
        "x", logging.INFO, "f", 1, "fetching %s", ("http://p/x?X-Plex-Token=zzz",), None
    )
    RedactingFilter().filter(record)
    assert "zzz" not in record.getMessage()


# ---- scanner -----------------------------------------------------------------------------------------------------


@pytest.fixture
def media(tmp_path):
    root = tmp_path / "movies"
    (root / "Film (2020)").mkdir(parents=True)
    (root / "Film (2020)" / "Film.mkv").write_bytes(b"x" * 10)
    (root / "Film (2020)" / "poster.jpg").write_bytes(b"x")
    (root / "Disc").mkdir()
    (root / "Disc" / "Disc.iso").write_bytes(b"x")
    (root / "3D").mkdir()
    (root / "3D" / "Thing 3D HSBS.mkv").write_bytes(b"x")
    (root / "Film (2020)" / "Extras").mkdir()
    (root / "Film (2020)" / "Extras" / "Making Of.mkv").write_bytes(b"x")
    (root / "Café – 日本語.mkv").write_bytes(b"x" * 3)
    return root


def test_scan_queues_videos_and_records_skips(tmp_path, media):
    store = Store(tmp_path / "s.sqlite")
    summary = scan_media([str(media)], store, "0.1.0")
    base = str(media).replace("\\", "/")
    assert (summary.seen, summary.new, summary.skipped) == (5, 2, 3)
    assert store.get(f"{base}/Film (2020)/Film.mkv")["status"] == "queued"
    assert store.get(f"{base}/Café – 日本語.mkv")["status"] == "queued"
    reasons = {
        r["path"].rsplit("/", 1)[1]: r["skip_reason"]
        for r in store._all("SELECT * FROM files WHERE status='skipped'")
    }
    assert reasons == {"Disc.iso": "iso", "Thing 3D HSBS.mkv": "3d-filename", "Making Of.mkv": "extra"}
    again = scan_media([str(media)], store, "0.1.0")
    assert (again.new, again.changed) == (0, 0)


def test_a_modified_file_is_noticed_and_a_removed_one_goes_missing(tmp_path, media):
    store = Store(tmp_path / "s.sqlite")
    scan_media([str(media)], store, "0.1.0")
    film = media / "Film (2020)" / "Film.mkv"
    film.write_bytes(b"y" * 20)
    gone = media / "Café – 日本語.mkv"
    gone.unlink()
    summary = scan_media([str(media)], store, "0.1.0")
    assert summary.changed == 1 and summary.missing == 1
    assert store.get(str(gone).replace("\\", "/"))["status"] == "missing"


def test_an_unmounted_root_is_reported_and_does_not_make_its_files_missing(tmp_path, media):
    store = Store(tmp_path / "s.sqlite")
    scan_media([str(media)], store, "0.1.0")
    summary = scan_media([str(tmp_path / "not-mounted")], store, "0.1.0")
    assert summary.errors and "not a directory" in summary.errors[0] and summary.missing == 0
    assert store.counts().get("missing") is None


def test_scan_can_be_stopped_and_then_marks_nothing_missing(tmp_path, media):
    store = Store(tmp_path / "s.sqlite")
    scan_media([str(media)], store, "0.1.0")
    summary = scan_media([str(media)], store, "0.1.0", should_stop=lambda: True)
    assert summary.missing == 0 and store.counts().get("missing") is None


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_symlinks_are_not_followed_out_of_the_root(tmp_path, media):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mkv").write_bytes(b"x")
    (media / "link").symlink_to(outside)
    store = Store(tmp_path / "s.sqlite")
    scan_media([str(media)], store, "0.1.0")
    assert store.find(path=str(outside / "secret.mkv")) is None


# ---- Plex matching -------------------------------------------------------------------------------------------------


def test_sync_attaches_plex_ids_to_scanned_files_and_boosts_recent_titles(tmp_path, media, plex):
    store = Store(tmp_path / "s.sqlite")
    base = str(media).replace("\\", "/")
    scan_media([str(media)], store, "0.1.0")
    pathmap = PathMap([[base, "/movies", "/mnt/user/media/Movies"]])
    items = [
        movie(1, "Film", [(11, "/movies/Film (2020)/Film.mkv")], added=2_000_000_000),
        movie(2, "Cafe", [(12, "/mnt/user/media/Movies/Café – 日本語.mkv")], added=100),
        movie(3, "Not here", [(13, "/movies/Nowhere/x.mkv")]),
    ]
    plex.routes["/library/sections/1/all"] = lambda q: {"MediaContainer": {"totalSize": 3, "Metadata": items}}
    plex.routes["/library/sections/4/all"] = lambda q: pytest.fail("the skipped library must not be read")
    # the TV library has no route: it answers 404, which must not hide the movies
    summary = sync_plex(
        client(plex), store, pathmap, skip_sections=("youtube",), recent_days=30, now=2_000_000_000.0
    )
    assert (summary.parts, summary.matched, summary.unknown) == (3, 2, 1) and summary.skipped_sections == [
        "YouTube"
    ]
    assert len(summary.errors) == 1 and summary.errors[0].startswith("TV:")
    assert store.find(part_id=11)["path"] == f"{base}/Film (2020)/Film.mkv"
    assert store.find(rating_key=2)["title"] == "Cafe"
    assert store.get(f"{base}/Film (2020)/Film.mkv")["priority"] == PRIORITY_RECENT
    assert store.get(f"{base}/Café – 日本語.mkv")["priority"] != PRIORITY_RECENT
