"""framelock.py: the plan as a per-frame zoom table."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service.cih.zoom"))
import framelock  # noqa: E402

PLAN = {"fps": 24000 / 1001, "initial": 1.0,
        "cuts": [{"frame": 48, "zoom_from": 1.0, "zoom_to": 0.8081}, {"frame": 120, "zoom_from": 0.8081, "zoom_to": 1.0}],
        "ramps": [{"start_frame": 200, "zoom": [1.0, 0.99, 0.98, 0.98]}]}


class FrameLockTests(unittest.TestCase):
    def test_entries_are_the_initial_zoom_then_each_change_at_its_frame_time(self):
        e = framelock.entries(PLAN)
        self.assertEqual(e[0], (0.0, 1.0))
        self.assertAlmostEqual(e[1][0], 48 * 1001 / 24000, places=6)
        self.assertEqual(e[1][1], 0.8081)
        self.assertAlmostEqual(e[2][0], 120 * 1001 / 24000, places=6)
        self.assertEqual([z for _, z in e], [1.0, 0.8081, 1.0, 0.99, 0.98])   # repeated 0.98 and the 1.0 ramp start drop out

    def test_times_ascend_strictly(self):
        e = framelock.entries(PLAN, stride=1, shift_s=0.05)
        times = [t for t, _ in e]
        self.assertEqual(times, sorted(set(times)))
        self.assertEqual(times[0], 0.0)                                      # the initial zoom is not shifted

    def test_the_shift_moves_every_change_but_not_the_start(self):
        a = framelock.entries(PLAN)
        b = framelock.entries(PLAN, shift_s=0.042)
        self.assertEqual(b[0][0], 0.0)
        for (ta, _), (tb, _) in zip(a[1:], b[1:]):
            self.assertAlmostEqual(tb - ta, 0.042, places=5)

    def test_text_has_a_header_and_one_line_per_change(self):
        text = framelock.table_text([(0.0, 1.0), (2.002, 0.8081)], "7")
        self.assertEqual(text, "CIH1 7\n0.000000 1.000000\n2.002000 0.808100\n")


if __name__ == "__main__":
    unittest.main()
