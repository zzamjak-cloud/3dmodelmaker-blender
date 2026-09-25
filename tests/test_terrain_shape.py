# 지형 윤곽 다듬기·내부 정점 배치의 순수 기하
import importlib.util
import math
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("terrain_shape", os.path.join(_ROOT, "lowpoly", "terrain_shape.py"))
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

HEX = [(-30, -18), (-8, -26), (24, -20), (34, 4), (18, 22), (-12, 26), (-32, 8)]


class TestTerrainShape(unittest.TestCase):
    def test_ccw_orients_polygon(self):
        self.assertGreater(ts.signed_area(ts.ccw(HEX[::-1])), 0)

    def test_chaikin_rounds_corners_inside_hull(self):
        smooth = ts.chaikin(ts.ccw(HEX), 2)
        self.assertEqual(len(smooth), len(HEX) * 4)
        self.assertTrue(all(ts.point_in_polygon(x * 0.999, y * 0.999, ts.ccw(HEX)) for x, y in smooth))

    def test_resample_caps_segment_length(self):
        out = ts.resample(HEX, 3.0)
        for i in range(len(out)):
            (x1, y1), (x2, y2) = out[i], out[(i + 1) % len(out)]
            self.assertLessEqual(math.hypot(x2 - x1, y2 - y1), 3.0 + 1e-9)

    def test_interior_points_stay_inside_and_off_rim(self):
        poly = ts.ccw(HEX)
        pts = ts.interior_points(poly, 5.0, seed=1)
        self.assertGreater(len(pts), 10)
        for x, y in pts:
            self.assertTrue(ts.point_in_polygon(x, y, poly))
            self.assertGreater(ts.edge_distance(x, y, poly), 2.5)

    def test_offset_ring_moves_inward(self):
        square = ts.ccw([(0, 0), (10, 0), (10, 10), (0, 10)])
        ring = ts.offset_ring(square, 1.0)
        self.assertAlmostEqual(abs(ts.signed_area(ring)), 64.0, places=3)


if __name__ == "__main__":
    unittest.main()
