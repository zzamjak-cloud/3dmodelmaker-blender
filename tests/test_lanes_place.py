# 완료 결과를 씬의 빈자리에 놓는 평면 배치 계산
import importlib.util
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("lanes_place", os.path.join(_ROOT, "core", "lanes.py"))
lanes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lanes)


class TestFreeOffset(unittest.TestCase):
    def test_empty_scene_stays_at_origin(self):
        self.assertEqual(lanes.free_offset((-1, -1, 1, 1), []), 0.0)

    def test_moves_right_of_blocker_with_margin(self):
        dx = lanes.free_offset((-1, -1, 1, 1), [(-2, -2, 2, 2)], margin=1.0)
        self.assertAlmostEqual(dx, 2 + 1 + 1)

    def test_large_result_clears_small_neighbors(self):
        # 7m 주유소 옆에 5m 가게 — 예전 4m 고정 간격이면 겹쳤다
        station = (-3.5, -3.0, 3.5, 3.0)
        shop = (-2.5, -2.5, 2.5, 2.5)
        dx = lanes.free_offset(shop, [station], margin=1.0)
        self.assertGreaterEqual(shop[0] + dx, station[2] + 1.0 - 1e-9)

    def test_fills_gap_between_results(self):
        # 앞 결과를 지워 생긴 빈자리를 다시 쓴다
        occupied = [(-1, -1, 1, 1), (10, -1, 12, 1)]
        dx = lanes.free_offset((-1, -1, 1, 1), occupied, margin=1.0)
        self.assertAlmostEqual(dx, 3.0)

    def test_skips_gap_too_narrow(self):
        occupied = [(-1, -1, 1, 1), (4, -1, 6, 1)]
        dx = lanes.free_offset((-2, -1, 2, 1), occupied, margin=1.0)
        self.assertAlmostEqual(dx, 6 + 1 + 2)

    def test_objects_in_other_rows_do_not_block(self):
        dx = lanes.free_offset((-1, -1, 1, 1), [(-1, 20, 1, 22)], margin=1.0)
        self.assertEqual(dx, 0.0)

    def test_result_never_overlaps(self):
        occupied = [(0, 0, 3, 3), (4, -2, 8, 1), (9.5, 0, 11, 5), (-5, -5, -1, 5)]
        box = (-1.5, -1.5, 1.5, 1.5)
        dx = lanes.free_offset(box, occupied, margin=0.5)
        moved = (box[0] + dx, box[1], box[2] + dx, box[3])
        self.assertFalse(any(lanes._overlaps(moved, o, 0.5) for o in occupied))


if __name__ == "__main__":
    unittest.main()
