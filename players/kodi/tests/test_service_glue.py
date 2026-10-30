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
                    "api_key": ""}
INFO = {"Player.Process(videowidth)": "3,840", "Player.Process(videoheight)": "2,160", "Player.Process(videodar)": "1.78"}
CONDITIONS = {}


def install_stubs():
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 1, 2, 3
    xbmc.log = lambda msg, level=1: None
    xbmc.getInfoLabel = lambda label: INFO.get(label, "")
    xbmc.getCondVisibility = lambda cond: CONDITIONS.get(cond, False)

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

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.mkdirs = lambda p: True
    sys.modules.update({"xbmc": xbmc, "xbmcaddon": xbmcaddon, "xbmcvfs": xbmcvfs})
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

    def timeline(self, ident, request_analysis=True):
        self.calls.append((ident, request_analysis))
        return self.lookups.pop(0) if len(self.lookups) > 1 else self.lookups[0]


class GlueTests(unittest.TestCase):
    def setUp(self):
        RPC_CALLS.clear()
        SETTINGS.clear()
        SETTINGS.update(DEFAULT_SETTINGS)
        CONDITIONS.clear()
        self.players = []
        self.client = None
        self._make = service.make_client
        service.make_client = lambda: self.client
        service.POLL_FLOOR_S = 0.05

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
        self.client = FakeClient(client_mod.Lookup("ok", analyzer_timeline()))
        XBMC.Player.t0 = time.perf_counter() - 1.2                    # 1.2 s in: inside the 16:9 shot (1.0 to 1.5 s)
        player = self.new_player()
        player.onAVStarted()
        time.sleep(0.4)
        player.onPlayBackStopped()
        self.assertEqual(self.zooms()[0], 0.7565)

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
        time.sleep(0.3)
        self.assertEqual(set(self.zooms()), {1.0})
        tl["override"] = {"mode": "preset", "preset": 1.85}
        self.client = FakeClient(client_mod.Lookup("ok", tl))
        player.reload()                                                # what override.py triggers
        time.sleep(0.3)
        player.onPlayBackStopped()
        self.assertIn(0.7872, self.zooms())

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
        self.client = FakeClient(client_mod.Lookup("ok", analyzer_timeline()))
        XBMC.Player.t0 = t0 = time.perf_counter()
        player = self.new_player()
        player.onAVStarted()
        time.sleep(0.2)
        player.onPlayBackPaused()
        sent_before = len(RPC_CALLS)
        time.sleep(1.0)                                                # the 0.9 s event must not fire while paused
        self.assertEqual(len(RPC_CALLS), sent_before)
        XBMC.Player.t0 = time.perf_counter() - 1.3                     # resumed later in the film: 16:9 shot
        player.onPlayBackResumed()
        time.sleep(0.4)
        player.onPlayBackStopped()
        self.assertIn(0.7565, self.zooms()[sent_before:])

    def test_video_size_labels_with_thousands_separators_are_read(self):
        video = service.playing_video_size()
        self.assertEqual((video.width, video.height), (3840, 2160))
        INFO["Player.Process(videowidth)"] = ""
        self.assertIsNone(service.playing_video_size())
        INFO["Player.Process(videowidth)"] = "3,840"


if __name__ == "__main__":
    unittest.main()
