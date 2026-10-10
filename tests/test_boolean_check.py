# cut/emboss 불리언 결과 불변식 판정 (순수 계산)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    # lowpoly/__init__ 은 bpy 를 임포트하므로 파일 단위로 불러온다
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, "lowpoly", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bc = _load("boolean_check")


class DifferenceOkTest(unittest.TestCase):
    def test_thin_groove_is_accepted(self):
        # 2.6m 박스에 0.012 x 0.36 x 1.0 홈: 면적이 조금 늘고 부피가 조금 준다
        self.assertTrue(bc.difference_ok(40.56, 40.58, 0.75, 17.576, 17.5759, 0.00432))

    def test_vanished_target_is_rejected(self):
        # 실측: 띠·문을 판 몸통(면적 ~51)이 면 6장짜리 조각으로 남았다
        self.assertFalse(bc.difference_ok(51.0, 0.002, 0.75, 22.82, 0.0001, 0.004))

    def test_empty_result_is_rejected(self):
        self.assertFalse(bc.difference_ok(10.0, 0.0, 1.0))

    def test_volume_growth_is_rejected(self):
        self.assertFalse(bc.difference_ok(10.0, 10.1, 1.0, 5.0, 6.0, 0.1))

    def test_volume_loss_beyond_cutter_is_rejected(self):
        self.assertFalse(bc.difference_ok(10.0, 9.5, 1.0, 5.0, 4.0, 0.1))

    def test_open_target_skips_volume_check(self):
        self.assertTrue(bc.difference_ok(10.0, 10.2, 1.0, None, None, 0.1))


class RegionOkTest(unittest.TestCase):
    def test_groove_floor_is_accepted(self):
        self.assertTrue(bc.region_ok(0.012, 0.75))

    def test_whole_wall_region_is_rejected(self):
        # 실측: 벽 전체(수 m²)가 얇은 홈 cutter 안쪽으로 분류돼 홈 색으로 칠해졌다
        self.assertFalse(bc.region_ok(6.8, 0.75))


if __name__ == "__main__":
    unittest.main()
