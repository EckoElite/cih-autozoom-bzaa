"""Smoke test of service.py with stand-in xbmc modules (real time, about 1.5 s). Catches typos and wiring errors."""
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
SETTINGS = {"enabled": True, "lead_ms": 100}


def install_stubs():
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR = 1, 2, 3
    xbmc.log = lambda msg, level=1: None

    def execute(body):
        RPC_CALLS.append((time.perf_counter(), json.loads(body)))
        return '{"id":1,"jsonrpc":"2.0","result":"OK"}'

    xbmc.executeJSONRPC = execute

    class Player:
        t0 = None
        playing_file = "http://pc:8765/boundary_x.mkv"

        def __init__(self):
            pass

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

        def getAddonInfo(self, k):
            return PROFILE

    xbmcaddon.Addon = Addon

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p
    xbmcvfs.mkdirs = lambda p: True
    sys.modules.update({"xbmc": xbmc, "xbmcaddon": xbmcaddon, "xbmcvfs": xbmcvfs})
    return xbmc


XBMC = install_stubs()
import service  # noqa: E402


class GlueTests(unittest.TestCase):
    def setUp(self):
        RPC_CALLS.clear()
        events = Path(PROFILE) / "events.jsonl"
        if events.exists():
            events.unlink()

    def test_play_through_applies_start_zoom_events_and_resets(self):
        timeline = {"fps": "24", "cuts": [{"frame": 24, "zoom_from": 1.0, "zoom_to": 0.8},
                                          {"frame": 36, "zoom_from": 0.8, "zoom_to": 1.0}], "ramps": []}
        player = service.ZoomPlayer()
        player.load_timeline = lambda: timeline
        t0 = time.perf_counter() - 0.05                  # playback is already 50 ms in when the callback fires
        XBMC.Player.t0 = t0
        player.onAVStarted()
        time.sleep(1.6)
        player.onPlayBackEnded()
        XBMC.Player.t0 = None

        zooms = [(c[0], c[1]["params"]["viewmode"]["zoom"]) for c in RPC_CALLS]
        self.assertTrue(all(isinstance(z, float) for _, z in zooms), zooms)
        self.assertTrue(all(c[1]["method"] == "Player.SetViewMode" for c in RPC_CALLS))
        values = [z for _, z in zooms]
        self.assertEqual(values, [1.0, 0.8, 1.0, 1.0])    # start, cut 1, cut 2, reset at the end
        # cuts at 1.0 s and 1.5 s with a 100 ms lead: sent at player time 0.9 s and 1.4 s
        self.assertAlmostEqual(zooms[1][0] - t0, 0.9, delta=0.04)
        self.assertAlmostEqual(zooms[2][0] - t0, 1.4, delta=0.04)
        log = [json.loads(line) for line in (Path(PROFILE) / "events.jsonl").read_text().splitlines()]
        kinds = [r["kind"] for r in log]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds.count("event"), 2)
        self.assertEqual(kinds[-1], "finished")
        self.assertEqual(log[-1]["sent"], log[-1]["total"])

    def test_early_start_zoom_on_playback_started(self):
        timeline = {"fps": "24", "cuts": [{"frame": 0, "zoom_from": 0.8, "zoom_to": 0.8},
                                          {"frame": 240, "zoom_from": 0.8, "zoom_to": 1.0}], "ramps": []}
        player = service.ZoomPlayer()
        player.load_timeline = lambda: timeline
        before = len(RPC_CALLS)
        player.onPlayBackStarted()
        new = RPC_CALLS[before:]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0][1]["params"]["viewmode"]["zoom"], 0.8)
        self.assertIsInstance(new[0][1]["params"]["viewmode"]["zoom"], float)


if __name__ == "__main__":
    unittest.main()
