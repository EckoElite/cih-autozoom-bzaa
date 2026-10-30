"""plan.py: the section 5 math and the analyzer-timeline-to-set-points conversion (no Kodi needed)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "service.cih.zoom"))
import plan  # noqa: E402
import zoomcore  # noqa: E402

GEO = plan.Geometry()                                   # design band 263..1897, 1634 rows
UHD = plan.Video(3840, 2160)


def seg(start, end, w, h, kind="cut", frame=0):
    return {"start": start, "end": end, "active": {"x": 0, "y": (2160 - h) // 2, "w": w, "h": h},
            "boundary": {"kind": kind, "frame": frame, "scene_cut": None}}


def timeline(segments, ramps=(), fps=23.976, video=(3840, 2160, "1:1")):
    return {"video": {"width": video[0], "height": video[1], "sar": video[2], "fps": fps},
            "segments": list(segments), "ramps": list(ramps)}


class ZoomMathTests(unittest.TestCase):
    """The nominal table of spec 5.3 (a 16:9 container, centred band), re-derived from the band rows."""

    def z(self, ratio):
        return plan.ratio_zoom(UHD, GEO, ratio)

    def test_nominal_table(self):
        for ratio, expected in ((4 / 3, 0.7565), (16 / 9, 0.7565), (1.85, 0.7872), (1.90, 0.8085), (2.00, 0.8511),
                                (2.20, 0.9362), (2.35, 1.0), (2.39, 1.0), (2.40, 1.0)):
            with self.subTest(ratio=ratio):
                self.assertAlmostEqual(self.z(ratio), expected, places=3)

    def test_scope_fill_fills_the_band_height_for_wider_pictures_only(self):
        fill = plan.Geometry(scope_fill=True)
        self.assertAlmostEqual(plan.ratio_zoom(UHD, fill, 2.39), 1.0170, places=3)
        self.assertAlmostEqual(plan.ratio_zoom(UHD, fill, 2.40), 1.0213, places=3)
        self.assertAlmostEqual(plan.ratio_zoom(UHD, fill, 16 / 9), 0.7565, places=3)     # narrower: still contained
        self.assertAlmostEqual(plan.ratio_zoom(UHD, fill, 2.20), 0.9362, places=3)

    def test_a_native_scope_encode_needs_no_zoom_and_a_wider_one_is_limited_by_the_width(self):
        native = plan.Video(3840, 1600)                       # a 2.40 encode: the frame fills the width
        self.assertAlmostEqual(plan.full_frame_zoom(native, GEO), 1.0, places=3)
        wide = plan.Video(3840, 1388)                         # 2.77:1: wider than the screen; the width limits it
        self.assertAlmostEqual(plan.full_frame_zoom(wide, GEO), 1.0, places=3)

    def test_the_whole_frame_is_the_fallback_whatever_the_container(self):
        self.assertAlmostEqual(plan.full_frame_zoom(UHD, GEO), 0.7565, places=3)
        self.assertAlmostEqual(plan.full_frame_zoom(plan.Video(1920, 1080), GEO), 0.7565, places=3)
        four_three = plan.Video(1440, 1080)                    # pillarboxed in the frame: height limits it
        self.assertAlmostEqual(plan.full_frame_zoom(four_three, GEO), 0.7565, places=3)

    def test_pixel_aspect_ratio_is_applied(self):
        dvd = plan.Video(720, 480, 32 / 27)                    # 16:9 anamorphic
        self.assertAlmostEqual(dvd.dar, 16 / 9, places=3)
        self.assertAlmostEqual(plan.full_frame_zoom(dvd, GEO), 0.7565, places=3)
        scope = plan.rect_zoom(dvd, GEO, 720, 480 * 0.745)     # a 2.39 picture inside it
        self.assertAlmostEqual(scope, 1.0, places=2)


class BuildPlanTests(unittest.TestCase):
    def test_constant_film_has_an_initial_zoom_and_no_events(self):
        p = plan.build_plan(timeline([seg(0, 100, 3840, 1606)]))
        self.assertAlmostEqual(p["initial"], 1.0, places=3)
        self.assertEqual((p["cuts"], p["ramps"]), ([], []))
        self.assertAlmostEqual(zoomcore.zoom_at_frame(p, 5000), 1.0, places=3)

    def test_cuts_land_on_their_boundary_frame_when_unbiased(self):
        tl = timeline([seg(0, 10, 3840, 1606), seg(10, 20, 3840, 2160, frame=240), seg(20, 30, 3840, 1606, frame=480)])
        p = plan.build_plan(tl, edge_bias="centered")
        self.assertEqual([c["frame"] for c in p["cuts"]], [240, 480])
        self.assertAlmostEqual(p["cuts"][0]["zoom_to"], 0.7565, places=3)
        self.assertAlmostEqual(p["cuts"][1]["zoom_to"], 1.0, places=3)

    def test_no_spill_shrinks_a_frame_early_and_grows_a_frame_late(self):
        tl = timeline([seg(0, 10, 3840, 1606), seg(10, 20, 3840, 2160, frame=240), seg(20, 30, 3840, 1606, frame=480)])
        p = plan.build_plan(tl, edge_bias="no_spill")
        self.assertEqual([c["frame"] for c in p["cuts"]], [239, 481])    # shrink (to 16:9) early, grow back late
        p = plan.build_plan(tl, edge_bias="no_bars")
        self.assertEqual([c["frame"] for c in p["cuts"]], [241, 479])

    def test_a_biased_cut_never_lands_on_or_before_the_previous_set_point(self):
        tl = timeline([seg(0, 1, 3840, 1606), seg(1, 2, 3840, 2160, frame=24), seg(2, 3, 3840, 1606, frame=26)])
        p = plan.build_plan(tl, edge_bias="no_spill")
        frames = [c["frame"] for c in p["cuts"]]
        self.assertEqual(frames[0], 23)
        self.assertGreater(frames[1], frames[0])

    def test_a_ramp_is_followed_frame_by_frame_and_not_biased(self):
        # the matte opens from 2.39 (1606 rows) to 16:9 (2160 rows) over 5 frames, frames 100..104
        frames = [{"f": 100 + i, "t": 4.0 + i / 24, "top": (2160 - h) / 2, "bottom": (2160 + h) / 2, "left": 0.0,
                   "right": 3840.0, "readable": True} for i, h in enumerate((1606, 1750, 1900, 2050, 2160))]
        tl = timeline([seg(0, 4, 3840, 1606), seg(4.2, 9, 3840, 2160, kind="ramp", frame=105)],
                      ramps=[{"start_frame": 100, "end_frame": 104, "frames": frames}], fps=24)
        p = plan.build_plan(tl, edge_bias="no_spill")
        self.assertEqual(p["ramps"][0]["start_frame"], 100)
        zs = p["ramps"][0]["zoom"]
        self.assertEqual(len(zs), 5)
        self.assertTrue(all(a >= b for a, b in zip(zs, zs[1:])), zs)       # the picture only gets smaller on screen
        self.assertAlmostEqual(zs[0], 1.0, places=3)
        self.assertAlmostEqual(zs[-1], 0.7565, places=3)
        self.assertEqual(p["cuts"][0]["frame"], 105)                          # the segment after a ramp starts as written
        self.assertAlmostEqual(zoomcore.zoom_at_frame(p, 102), zs[2], places=6)    # a seek into the ramp

    def test_overrides(self):
        tl = timeline([seg(0, 10, 3840, 1606), seg(10, 20, 3840, 2160, frame=240)])
        off = plan.build_plan(tl, override={"mode": "disable"})
        self.assertEqual((off["initial"], off["cuts"], off["ramps"]), (1.0, [], []))
        preset = plan.build_plan(tl, override={"mode": "preset", "preset": 1.85})
        self.assertAlmostEqual(preset["initial"], 0.7872, places=3)
        self.assertEqual(preset["cuts"], [])

    def test_frame_rates_and_the_plan_shape_zoomcore_expects(self):
        p = plan.build_plan(timeline([seg(0, 1, 3840, 2160), seg(1, 2, 3840, 1606, frame=24)], fps=23.976))
        events = zoomcore.build_events(p, 0.316)
        self.assertEqual(events[0]["frame"], 25)              # a grow, one frame late under no_spill
        self.assertAlmostEqual(events[0]["target"], 25 * 1001 / 24000 - 0.316, places=4)
        self.assertEqual(zoomcore.fps_pair(25.0), (25, 1))
        self.assertEqual(zoomcore.fps_pair(29.97), (30000, 1001))
        self.assertEqual(zoomcore.fps_pair("23.976"), (24000, 1001))


if __name__ == "__main__":
    unittest.main()
