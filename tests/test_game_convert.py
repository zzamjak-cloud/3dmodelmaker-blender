# 게임용 glTF 변환의 순수 계산 (셀 → 머티리얼 이름·선형색, 정규화 배율)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    # pipeline/__init__ 을 거치지 않고 파일 단위로 불러온다
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, "pipeline", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gc = _load("game_convert")


class MaterialTest(unittest.TestCase):
    def test_name_is_lowercase_hex(self):
        self.assertEqual(gc.material_name((236, 194, 127)), "C_ecc27f")

    def test_same_color_same_name(self):
        self.assertEqual(gc.material_name((10, 20, 30)), gc.material_name([10, 20, 30]))

    def test_linear_color_is_converted_once(self):
        # 팔레트 PNG 의 sRGB 236 → 선형 0.839. 변환을 빼면 0.925(엔진에서 바램), 두 번 하면 0.67(어두워짐)
        r, g, b, a = gc.linear_color((236, 194, 127))
        self.assertAlmostEqual(r, 0.8388, places=3)
        self.assertAlmostEqual(b, 0.2122, places=3)
        self.assertEqual(a, 1.0)

    def test_linear_extremes(self):
        self.assertEqual(gc.linear_color((0, 0, 0))[:3], (0.0, 0.0, 0.0))
        self.assertAlmostEqual(gc.linear_color((255, 255, 255))[0], 1.0)


class CellTest(unittest.TestCase):
    def test_cell_center(self):
        # 셀 13열·26행 중앙 (colorsnap.cell_uv 와 같은 규약)
        self.assertEqual(gc.uv_cell((13 + 0.5) / 64, (26 + 0.5) / 32, 64, 32), 26 * 64 + 13)

    def test_out_of_range_uv_clamps(self):
        self.assertEqual(gc.uv_cell(-0.2, 1.3, 64, 32), 31 * 64)
        self.assertEqual(gc.uv_cell(1.0, 0.0, 64, 32), 63)


class FitScaleTest(unittest.TestCase):
    def test_no_target_keeps_size(self):
        self.assertEqual(gc.fit_scale((2.0, 2.0, 3.0)), 1.0)

    def test_height_only(self):
        self.assertAlmostEqual(gc.fit_scale((2.0, 2.0, 4.0), height=0.85), 0.2125)

    def test_smaller_factor_wins(self):
        # 높이로는 0.85/1.4=0.607, 발판으로는 0.96/2.2=0.436 — 둘 다 넘지 않게 작은 쪽
        self.assertAlmostEqual(gc.fit_scale((2.2, 1.2, 1.4), height=0.85, footprint=0.96), 0.96 / 2.2)

    def test_footprint_uses_larger_side(self):
        self.assertAlmostEqual(gc.fit_scale((1.0, 3.0, 0.5), footprint=0.9), 0.3)


if __name__ == "__main__":
    unittest.main()
