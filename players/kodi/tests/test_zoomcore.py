"""Offline tests for zoomcore (no Kodi needed). Run: python -m unittest discover -s players/kodi/tests -v"""
import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service.cih.zoom"))
import zoomcore  # noqa: E402

FRAME_S = 1001 / 24000.0

TIMELINE = {
    "fps": "23.976",
    "cuts": [
        {"frame": 48, "zoom_from": 1.0, "zoom_to": 0.8081},
        {"frame": 120, "zoom_from": 0.8081, "zoom_to": 1.0},
        {"frame": 192, "zoom_from": 1.0, "zoom_to": 0.7565},
    ],
    "ramps": [{"start_frame": 300, "zoom": [1.0, 0.99, 0.98, 0.97]}],
}


class World:
    """Virtual time. now() advances a little on every call (a spin loop must terminate); sleep() overshoots."""

    def __init__(self, seed=1):
        self.t = 100.0
        self.rng = random.Random(seed)

    def now(self):
        self.t += 5e-5
        return self.t

    def sleep(self, d):
        self.t += max(d, 0.0) + self.rng.uniform(0.0, 0.0008)

    def player_time(self, noise=0.015):
        # true player time is wall - 100; the readout is noisy and coarse (ms resolution)
        return round(self.t - 100.0 + self.rng.uniform(-noise, noise), 3)


class TimelineTests(unittest.TestCase):
    def test_frame_time_and_back(self):
        self.assertAlmostEqual(zoomcore.frame_time(48, "23.976"), 2.002, places=3)
        self.assertEqual(zoomcore.frame_at(2.003, "23.976"), 48)
        self.assertEqual(zoomcore.frame_at(2.001, "23.976"), 47)
        self.assertAlmostEqual(zoomcore.frame_time(48, "24"), 2.0)

    def test_zoom_at_frame(self):
        z = lambda f: zoomcore.zoom_at_frame(TIMELINE, f)
        self.assertEqual(z(0), 1.0)          # before the first cut: the first cut's zoom_from
        self.assertEqual(z(47), 1.0)
        self.assertEqual(z(48), 0.8081)      # the cut frame itself already has the new zoom
        self.assertEqual(z(119), 0.8081)
        self.assertEqual(z(150), 1.0)
        self.assertEqual(z(250), 0.7565)
        self.assertEqual(z(299), 0.7565)
        self.assertEqual(z(301), 0.99)       # inside a ramp
        self.assertEqual(z(303), 0.97)
        self.assertEqual(z(500), 0.97)       # after a ramp, the last ramp value holds

    def test_build_events_sorted_with_lead(self):
        ev = zoomcore.build_events(TIMELINE, 0.316)
        self.assertEqual([e["frame"] for e in ev], [48, 120, 192, 300, 301, 302, 303])
        self.assertAlmostEqual(ev[0]["target"], 48 * FRAME_S - 0.316)
        self.assertTrue(all(a["target"] <= b["target"] for a, b in zip(ev, ev[1:])))

    def test_build_events_from_frame(self):
        ev = zoomcore.build_events(TIMELINE, 0.3, from_frame=121)
        self.assertEqual([e["frame"] for e in ev][:2], [192, 300])

    def test_zoom_request_uses_floats(self):
        # Kodi ignores integer-valued JSON numbers in SetViewMode, so 1 and 0 must go out as 1.0 and 0.0
        body = zoomcore.zoom_request(1)
        self.assertIn('"zoom": 1.0', body)
        self.assertIn('"verticalshift": 0.0', body)
        parsed = json.loads(body)
        self.assertIsInstance(parsed["params"]["viewmode"]["zoom"], float)
        self.assertEqual(parsed["method"], "Player.SetViewMode")


class SchedulerTests(unittest.TestCase):
    def run_schedule(self, events, noise=0.015, seed=1, start_samples=40):
        w = World(seed)
        clock = zoomcore.PlayerClock(lambda: w.player_time(noise), now=w.now)
        for _ in range(start_samples):
            clock.sample()
            w.sleep(0.01)
        sent = []

        def send(zoom):
            sent.append((w.t - 100.0, zoom))
            return True

        logs = []
        zoomcore.run_events(events, clock, send, lambda: False, sleep=w.sleep, log=logs.append)
        return sent, logs

    def test_events_sent_close_to_target_despite_noisy_clock(self):
        ev = zoomcore.build_events(TIMELINE, 0.316)
        for e in ev:
            e["target"] += 1.5          # the world starts at player time ~0.4 s after calibration
        sent, logs = self.run_schedule(ev)
        self.assertEqual(len(sent), len(ev))
        errors_ms = [(t - e["target"]) * 1000 for (t, _), e in zip(sent, ev)]
        self.assertLess(max(abs(x) for x in errors_ms), 6.0, errors_ms)
        self.assertEqual([z for _, z in sent], [e["zoom"] for e in ev])

    def test_per_frame_ramp_cadence(self):
        ev = zoomcore.build_events({"fps": "23.976", "cuts": [], "ramps": [{"start_frame": 60, "zoom": [1.0 - 0.003 * i for i in range(72)]}]}, 0.3)
        sent, _ = self.run_schedule(ev)
        gaps = [(b[0] - a[0]) * 1000 for a, b in zip(sent, sent[1:])]
        self.assertEqual(len(sent), 72)
        self.assertLess(max(abs(g - FRAME_S * 1000) for g in gaps), 8.0)

    def test_overdue_events_are_coalesced_not_replayed(self):
        w_events = [{"target": 0.5 + 0.01 * i, "frame": i, "zoom": 1.0 - 0.01 * i} for i in range(5)]
        # all five are far in the past once the clock reads 5 s; only the newest should go out
        w = World()
        w.t = 105.0
        clock = zoomcore.PlayerClock(lambda: w.player_time(0.0), now=w.now)
        for _ in range(20):
            clock.sample()
            w.sleep(0.01)
        sent, logs = [], []
        zoomcore.run_events(w_events, clock, lambda z: sent.append(z) or True, lambda: False, sleep=w.sleep, log=logs.append)
        self.assertEqual(len(sent), 1)
        self.assertAlmostEqual(sent[0], 1.0 - 0.04)
        self.assertTrue(any("dropped" in r for r in logs))

    def test_stop_flag_halts(self):
        ev = [{"target": 1000.0, "frame": 1, "zoom": 0.9}]
        w = World()
        clock = zoomcore.PlayerClock(lambda: w.player_time(0.0), now=w.now)
        for _ in range(20):
            clock.sample()
            w.sleep(0.01)
        calls = {"n": 0}

        def should_stop():
            calls["n"] += 1
            return calls["n"] > 5

        sent = []
        zoomcore.run_events(ev, clock, lambda z: sent.append(z), should_stop, sleep=w.sleep)
        self.assertEqual(sent, [])


class ClockTests(unittest.TestCase):
    def test_median_offset_rejects_outliers(self):
        w = World()
        outlier = {"n": 0}

        def get_time():
            outlier["n"] += 1
            base = w.t - 100.0
            return base + (5.0 if outlier["n"] % 9 == 0 else 0.0)   # every 9th reading wildly wrong

        clock = zoomcore.PlayerClock(get_time, now=w.now)
        for _ in range(60):
            clock.sample()
            w.sleep(0.01)
        self.assertAlmostEqual(clock.offset(), -100.0, delta=0.002)

    def test_unreadable_player_returns_false(self):
        def boom():
            raise RuntimeError("Kodi is not playing any file")

        clock = zoomcore.PlayerClock(boom)
        self.assertFalse(clock.sample())
        self.assertFalse(clock.ready())


if __name__ == "__main__":
    unittest.main()
