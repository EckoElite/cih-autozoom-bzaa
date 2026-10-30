"""service.py against stand-in xbmc modules and a stand-in analyzer: start, cuts, seek, fallback, polling, override.

Real time, a few seconds in all. Catches wiring errors and checks the order and timing of what is sent to Kodi.
"""
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parents[1] / "service.cih.zoom"
sys.path.insert(0, str(ADDON_DIR))

PROFILE = tempfile.mkdtemp(prefix="cih_profile_")
RPC_CALLS = []
SETTINGS = {}
DEFAULT_SETTINGS = {"enabled": True, "lead_ms": 100, "edge_bias": "centered", "band_top": 263, "band_bottom": 1897,
                    "scope_fill": False, "request_analysis": True, "poll_s": 0, "analyzer_url": "http://analyzer",
                    "api_key": "", "open_at_start": True, "start_addon": "script.plexmod", "ramp_stride": 1}
INFO = {"Player.Process(videowidth)": "3,840", "Player.Process(videoheight)": "2,160", "Player.Process(videodar)": "1.78"}
CONDITIONS = {}
WINDOW = {"id": 10000}                                     # the window Kodi is showing (2999 while it starts up)
BUILTINS = []


def install_stubs():
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 1, 2, 3
    xbmc.log = lambda msg, level=1: None
    xbmc.getInfoLabel = lambda label: INFO.get(label, "")
    xbmc.getCondVisibility = lambda cond: CONDITIONS.get(cond, False)
    xbmc.executebuiltin = lambda command: BUILTINS.append(command)

    def execute(body):
        RPC_CALLS.append((time.perf_counter(), json.loads(body)))
        return '{"id":1,"jsonrpc":"2.0","result":"OK"}'

    xbmc.executeJSONRPC = execute

    class Player:
        t0 = None
        playing_file = "http://plex:32400/library/parts/42/1700000000/?X-Plex-Token=SECRET"

        def getTime(self):
            if Player.t0 is None:
                raise RuntimeError("Kodi is not playing any file")
            return round(time.perf_counter() - Player.t0, 3)

        def getPlayingFile(self):
            return Player.playing_file

        def isPlayingVideo(self):
            return Player.t0 is not None

    class Monitor:
        def abortRequested(self):
            return True

        def waitForAbort(self, t):
            return True

    xbmc.Player, xbmc.Monitor = Player, Monitor

    xbmcaddon = types.ModuleType("xbmcaddon")

    class Addon:
        def getSettingBool(self, k):
            return bool(SETTINGS[k])

        def getSettingInt(self, k):
            return int(SETTINGS[k])

        def getSetting(self, k):
            return str(SETTINGS[k])

        def getAddonInfo(self, k):
            return PROFILE

    xbmcaddon.Addon = Addon

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.getCurrentWindowId = lambda: WINDOW["id"]

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.mkdirs = lambda p: True
    sys.modules.update({"xbmc": xbmc, "xbmcaddon": xbmcaddon, "xbmcvfs": xbmcvfs, "xbmcgui": xbmcgui})
    return xbmc


XBMC = install_stubs()
import client as client_mod  # noqa: E402
import service  # noqa: E402


def analyzer_timeline(cut_frames=(24, 36), fps=24):
    """Scope (2.39), then 16:9 from the first cut frame, then scope again at the second."""
    def seg(start, end, h, frame):
        return {"start": start, "end": end, "active": {"x": 0, "y": (2160 - h) // 2, "w": 3840, "h": h},
                "boundary": {"kind": "cut", "frame": frame, "scene_cut": True}}

    return {"video": {"width": 3840, "height": 2160, "sar": "1:1", "fps": fps},
            "segments": [seg(0, 1, 1606, 0), seg(1, 1.5, 2160, cut_frames[0]), seg(1.5, 9, 1606, cut_frames[1])],
            "ramps": []}


class FakeClient:
    """Answers lookups from a script and remembers what it was asked."""

    def __init__(self, *lookups):
        self.lookups = list(lookups)
        self.calls = []
        self.requests = []  # (ident, at) of the explicit requests for analysis
        self.last_error = "HTTP 503"

    def timeline(self, ident, request_analysis=True, at=None):
        self.calls.append((ident, request_analysis))
        return self.lookups.pop(0) if len(self.lookups) > 1 else self.lookups[0]

    def request_analysis(self, ident, at=None):
        self.requests.append((ident, at))
        return True


class GlueTests(unittest.TestCase):
    def setUp(self):
        RPC_CALLS.clear()
        SETTINGS.clear()
        SETTINGS.update(DEFAULT_SETTINGS)
        CONDITIONS.clear()
        WINDOW["id"] = 10000
        BUILTINS.clear()
        self.players = []
        self.client = None
        self._make = service.make_client
        service.make_client = lambda: self.client
        service.POLL_FLOOR_S = 0.05
        service.FAST_POLL_S = 0.05

    def tearDown(self):
        for player in self.players:
            player.end()                                               # stops threads and closes events.jsonl
        service.make_client = self._make
        XBMC.Player.t0 = None
        (Path(PROFILE) / "events.jsonl").unlink(missing_ok=True)

    def new_player(self):
        player = service.ZoomPlayer()
        self.players.append(player)
        return player

    def zooms(self):
        return [round(c[1]["params"]["viewmode"]["zoom"], 4) for c in RPC_CALLS]

    @staticmethod
    def wait_for(condition, seconds=4.0):
        """The worker starts after about 0.12 s of clock sampling, more on a busy machine: wait, do not guess."""
        deadline = time.time() + seconds
        while time.time() < deadline and not condition():
            time.sleep(0.02)
        return condition()

    def test_play_through_applies_start_zoom_events_and_resets(self):
        self.client = FakeClient(client_mod.Lookup("ok", analyzer_timeline()))
        player = self.new_player()
        player.onPlayBackStarted()                       # early start zoom, before the first frame
        XBMC.Player.t0 = t0 = time.perf_counter() - 0.05 # playback is already 50 ms in when onAVStarted fires
        player.onAVStarted()
        time.sleep(1.6)
        player.onPlayBackEnded()
        XBMC.Player.t0 = None
        self.assertTrue(all(c[1]["method"] == "Player.SetViewMode" for c in RPC_CALLS))
        self.assertTrue(all(isinstance(c[1]["params"]["viewmode"]["zoom"], float) for c in RPC_CALLS))
        # not playing yet at onPlayBackStarted in this stub, so the early zoom comes from onAVStarted onward:
        self.assertEqual(self.zooms(), [1.0, 0.7565, 1.0, 1.0])     # start, cut 1 (16:9), cut 2 (scope), reset
        sent = [c[0] for c in RPC_CALLS]
        self.assertAlmostEqual(sent[1] - t0, 0.9, delta=0.04)       # 1.0 s cut, 100 ms lead
        self.assertAlmostEqual(sent[2] - t0, 1.4, delta=0.04)
        log = [json.loads(line) for line in (Path(PROFILE) / "events.jsonl").read_text().splitlines()]
        self.assertEqual([r["kind"] for r in log][0], "start")
        self.assertEqual(log[-1]["kind"], "finished")
        self.assertNotIn("SECRET", (Path(PROFILE) / "events.jsonl").read_text())

    def test_the_title_is_looked_up_by_part_id_and_analysis_is_requested(self):
        self.client = FakeClient(client_mod.Lookup("ok", analyzer_timeline()))
        XBMC.Player.t0 = time.perf_counter()
        player = self.new_player()
        player.onPlayBackStarted()
        ident, request = self.client.calls[0]
        self.assertEqual((ident["part_id"], request), (42, True))
        self.assertEqual(self.zooms(), [1.0])                          # the early start zoom
        player.onPlayBackStopped()
        SETTINGS["request_analysis"] = False
        player.onPlayBackStarted()
        self.assertEqual(self.client.calls[1][1], False)
        player.onPlayBackStopped()

    def test_seek_into_the_second_shot_gets_that_shots_zoom_at_once(self):
        self.client = FakeClient(client_mod.Lookup("ok", analyzer_timeline(cut_frames=(24, 240))))
        XBMC.Player.t0 = time.perf_counter() - 1.2                    # 1.2 s in: inside the 16:9 shot (1 to 10 s)
        player = self.new_player()
        player.onAVStarted()
        self.assertTrue(self.wait_for(lambda: self.zooms()))
        first = self.zooms()[0]
        player.onPlayBackStopped()
        self.assertEqual(first, 0.7565)

    def test_no_timeline_contains_the_whole_frame_and_keeps_asking_until_it_arrives(self):
        self.client = FakeClient(client_mod.Lookup("missing"), client_mod.Lookup("missing"),
                                 client_mod.Lookup("ok", analyzer_timeline(cut_frames=(240, 360))))  # fmt: skip
        XBMC.Player.t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        self.assertEqual(self.zooms(), [0.7565])                       # R6: contain 3840x2160 by its display ratio
        deadline = time.time() + 3
        while len(self.zooms()) < 2 and time.time() < deadline:
            time.sleep(0.05)
        player.onPlayBackStopped()
        self.assertEqual(self.zooms()[:2], [0.7565, 1.0])              # the timeline arrived: scope at this position
        self.assertTrue(all(request is False for _, request in self.client.calls[1:]))   # polling asks, never queues

    def test_a_provisional_timeline_is_used_at_once_and_the_final_one_replaces_it(self):
        def segment(start, end, h, frame):
            return {"start": start, "end": end, "active": {"x": 0, "y": (2160 - h) // 2, "w": 3840, "h": h},
                    "boundary": {"kind": "cut", "frame": frame, "scene_cut": None}}  # fmt: skip

        provisional = {"provisional": True, "coverage": [[0.0, 60.0]],
                       "video": {"width": 3840, "height": 2160, "sar": "1:1", "fps": 24},
                       "segments": [segment(0, 60, 1622, 0), segment(60, 100, 2160, 1440)], "ramps": []}  # fmt: skip
        longer = dict(provisional, coverage=[[0.0, 90.0]])
        final = {"video": provisional["video"], "segments": [segment(0, 100, 2078, 0)], "ramps": []}  # 1.85
        self.client = FakeClient(client_mod.Lookup("missing"), client_mod.Lookup("provisional", provisional),
                                 client_mod.Lookup("provisional", longer), client_mod.Lookup("ok", final))  # fmt: skip
        XBMC.Player.t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        self.assertEqual(self.zooms()[0], 0.7565)  # nothing known yet: the whole frame, contained
        found = self.wait_for(lambda: 0.7863 in self.zooms())  # ...then the final 1.85 picture
        self.assertTrue(found, (self.zooms(), len(self.client.calls)))
        player.onPlayBackStopped()
        values = [z for i, z in enumerate(self.zooms()) if i == 0 or z != self.zooms()[i - 1]]
        self.assertEqual(values[:3], [0.7565, 1.0, 0.7863])  # contained, then the survey's picture, then the final one
        self.assertGreaterEqual(len(self.client.calls), 4)  # it kept asking until the final timeline came
        self.assertFalse(player.provisional)

    def test_a_request_for_analysis_that_did_not_get_through_is_made_again(self):
        # the analyzer was busy or unreachable for the first request: nothing was queued, so nothing would ever arrive
        provisional = {"provisional": True, "coverage": [[0.0, 60.0]],
                       "video": {"width": 3840, "height": 2160, "sar": "1:1", "fps": 24},
                       "segments": [{"start": 0, "end": 60, "active": {"x": 0, "y": 269, "w": 3840, "h": 1622},
                                     "boundary": {"kind": "cut", "frame": 0, "scene_cut": None}}], "ramps": []}  # fmt: skip
        self.client = FakeClient(client_mod.Lookup("missing", None, False), client_mod.Lookup("provisional", provisional, True))
        XBMC.Player.t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        self.assertTrue(self.wait_for(lambda: self.client.requests))  # the poller asked again
        self.assertTrue(self.wait_for(lambda: 1.0 in self.zooms()))  # and the survey's picture arrived
        count = len(self.client.requests)
        time.sleep(0.3)
        self.assertEqual(len(self.client.requests), count)  # once the analyzer has the request it is not asked again
        player.onPlayBackStopped()
    def test_a_seek_while_the_timeline_is_only_provisional_tells_the_analyzer_where_the_player_is(self):
        self.client = FakeClient(client_mod.Lookup("missing"))
        XBMC.Player.t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        XBMC.Player.t0 = time.perf_counter() - 1500.0  # resumed 25 minutes in
        player.onPlayBackSeek(1500000, 1500000)
        self.assertTrue(self.wait_for(lambda: self.client.requests))
        ident, at = self.client.requests[0]
        self.assertEqual(ident["part_id"], 42)
        self.assertAlmostEqual(at, 1500.0, delta=2.0)
        player.onPlayBackStopped()
    def test_an_unreadable_timeline_falls_back_instead_of_crashing(self):
        self.client = FakeClient(client_mod.Lookup("ok", {"video": {"width": 3840}, "segments": "nonsense"}))
        XBMC.Player.t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        self.assertEqual(self.zooms(), [0.7565])
        player.onPlayBackStopped()

    def test_override_off_keeps_the_title_at_normal_and_a_preset_applies_one_zoom(self):
        tl = analyzer_timeline()
        tl["override"] = {"mode": "disable", "preset": None}
        self.client = FakeClient(client_mod.Lookup("ok", tl))
        XBMC.Player.t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        self.assertTrue(self.wait_for(lambda: self.zooms()))
        self.assertEqual(set(self.zooms()), {1.0})
        tl["override"] = {"mode": "preset", "preset": 1.85}
        self.client = FakeClient(client_mod.Lookup("ok", tl))
        player.reload()                                                # what override.py triggers
        found = self.wait_for(lambda: 0.7872 in self.zooms())
        player.onPlayBackStopped()
        self.assertTrue(found, self.zooms())

    def test_disabled_and_unsupported_playbacks_are_left_alone(self):
        self.client = FakeClient(client_mod.Lookup("ok", analyzer_timeline()))
        XBMC.Player.t0 = time.perf_counter()
        SETTINGS["enabled"] = False
        player = self.new_player()
        player.onPlayBackStarted()
        player.onAVStarted()
        SETTINGS["enabled"] = True
        CONDITIONS["VideoPlayer.IsStereoscopic"] = True               # 3D plays on another player
        player.onPlayBackStarted()
        player.onAVStarted()
        CONDITIONS.clear()
        CONDITIONS["Pvr.IsPlayingTv"] = True
        player.onAVStarted()
        self.assertEqual(RPC_CALLS, [])
        self.assertEqual(self.client.calls, [])

    def test_pause_stops_the_schedule_and_resume_picks_it_up_again(self):
        self.client = FakeClient(client_mod.Lookup("ok", analyzer_timeline(cut_frames=(24, 240))))
        XBMC.Player.t0 = t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        self.assertTrue(self.wait_for(lambda: self.zooms()))             # the start zoom is out; now pause
        player.onPlayBackPaused()
        sent_before = len(RPC_CALLS)
        time.sleep(1.0)                                                # the 0.9 s event must not fire while paused
        self.assertEqual(len(RPC_CALLS), sent_before)
        XBMC.Player.t0 = time.perf_counter() - 1.3                     # resumed later in the film: the 16:9 shot (1 to 10 s)
        player.onPlayBackResumed()
        found = self.wait_for(lambda: 0.7565 in self.zooms()[sent_before:])
        player.onPlayBackStopped()
        self.assertTrue(found, self.zooms())

    def test_kodi_opens_pm4k_once_its_first_screen_is_up(self):
        class Mon:
            def __init__(self, abort_after=None):
                self.waits = 0

            def abortRequested(self):
                return False

            def waitForAbort(self, seconds):
                self.waits += 1
                if self.waits == 3:                                   # the first screen appears after a second or so
                    WINDOW["id"] = 10001                              # (any window from 10000 up, not only Home)
                return False

        WINDOW["id"] = 2999                                           # still the start-up animation
        service.open_start_addon(Mon())
        self.assertEqual(BUILTINS, ["RunScript(script.plexmod)"])
        BUILTINS.clear()
        CONDITIONS["Player.HasMedia"] = True                          # something is already playing: leave it
        service.open_start_addon(Mon())
        SETTINGS["open_at_start"] = False                             # switched off
        CONDITIONS.clear()
        service.open_start_addon(Mon())
        SETTINGS["open_at_start"], SETTINGS["start_addon"] = True, ""  # nothing named
        service.open_start_addon(Mon())
        self.assertEqual(BUILTINS, [])

    def test_video_size_labels_with_thousands_separators_are_read(self):
        video = service.playing_video_size()
        self.assertEqual((video.width, video.height), (3840, 2160))
        INFO["Player.Process(videowidth)"] = ""
        self.assertIsNone(service.playing_video_size())
        INFO["Player.Process(videowidth)"] = "3,840"


if __name__ == "__main__":
    unittest.main()
