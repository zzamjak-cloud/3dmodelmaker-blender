# 턴어라운드 분할: 격자선 감지·라벨 덧칠 (bpy 불필요, 합성 픽셀)
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType("lp3dcore_split"); pkg.__path__ = [str(ROOT / "core")]
sys.modules["lp3dcore_split"] = pkg
spec = importlib.util.spec_from_file_location("lp3dcore_split.shapegen", ROOT / "core/shapegen.py")
sg = importlib.util.module_from_spec(spec); sys.modules["lp3dcore_split.shapegen"] = sg; spec.loader.exec_module(sg)
spec2 = importlib.util.spec_from_file_location("cp_split", ROOT / "core/character_parts.py")
cp = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(cp)


def _sheet(w, h, xcuts, ycut, gray=0.82):
    px = [1.0] * (w * h * 4)
    def dark(x, y):
        i = (y * w + x) * 4; px[i:i + 3] = [gray] * 3
    for x in xcuts:
        for y in range(h): dark(x, y); dark(x + 1, y)
    for x in range(w): dark(x, ycut); dark(x, ycut + 1)
    return px


class GridCutTests(unittest.TestCase):
    def test_uneven_grid_lines_are_found_within_band(self):
        w, h = 300, 200
        px = _sheet(w, h, xcuts=(92, 205), ycut=88)      # 등분(100,200 / 100)에서 벗어난 격자
        rows, cols = sg._darkness(px, w, h)
        self.assertEqual(sg.grid_cuts(cols, 3), [92, 205])
        self.assertEqual(sg.grid_cuts(rows, 2), [88])

    def test_without_lines_falls_back_to_equal_parts(self):
        w, h = 300, 200
        px = [1.0] * (w * h * 4)
        rows, cols = sg._darkness(px, w, h)
        self.assertEqual(sg.grid_cuts(cols, 3), [100, 200])
        self.assertEqual(sg.grid_cuts(rows, 2), [100])

    def test_character_arm_row_is_not_mistaken_for_grid(self):
        # T-포즈 팔처럼 넓게 어두운 행이 등분 위치 근처에 있어도 비율이 낮으면(50% 미만) 격자로 안 본다
        w, h = 300, 200
        px = [1.0] * (w * h * 4)
        for x in range(60, 180):                              # 40% 폭
            i = (100 * w + x) * 4; px[i:i + 3] = [0.6, 0.5, 0.4]
        rows, _ = sg._darkness(px, w, h)
        self.assertEqual(sg.grid_cuts(rows, 2), [100])         # 등분 폴백(우연히 같은 위치) — 어둡다고 선택된 게 아님
        self.assertLess(rows[100], sg.GRID_MIN_FRAC)


class CleanCellTests(unittest.TestCase):
    def test_label_and_margin_are_painted_white_but_body_kept(self):
        w, h = 100, 120
        px = [1.0] * (w * h * 4)
        def fill(x0, y0, x1, y1, rgb):
            for y in range(y0, y1):
                for x in range(x0, x1):
                    i = (y * w + x) * 4; px[i:i + 3] = rgb
        fill(0, 0, w, 3, [0.82] * 3)                  # 아래 가장자리 격자 잔재
        fill(40, 8, 60, 16, [0.0] * 3)                # 아래 라벨 글자
        fill(30, 30, 70, 100, [0.5, 0.3, 0.2])        # 몸체
        sg.clean_cell(px, w, h)
        def is_white(x, y):
            i = (y * w + x) * 4; return min(px[i:i + 3]) > 0.99
        self.assertTrue(is_white(50, 1)); self.assertTrue(is_white(50, 10))
        self.assertFalse(is_white(50, 60))
        self.assertTrue(is_white(2, 60))              # 좌측 여백


class SizesMatchTests(unittest.TestCase):
    def test_small_pixel_differences_are_tolerated(self):
        a = {'front': (512, 451), 'left': (508, 451)}
        b = {'front': (505, 460), 'left': (512, 449)}
        self.assertTrue(cp.sizes_match(a, b))
        self.assertFalse(cp.sizes_match(a, {'front': (400, 451), 'left': (508, 451)}))
        self.assertFalse(cp.sizes_match(a, {'front': (512, 451)}))


class ThinLineTests(unittest.TestCase):
    def test_wide_dark_body_column_is_not_a_grid_line(self):
        # 격자선 없는 시트: 2/3 부근에 폭 40px 의 어두운 몸통 열(55%)이 있어도 등분으로 떨어져야 한다 (실측 v7: 1207 오검출)
        cols = [0.0] * 300
        for i in range(190, 230):
            cols[i] = 0.55
        self.assertEqual(sg.grid_cuts(cols, 3), [100, 200])

    def test_thin_line_next_to_body_still_wins(self):
        cols = [0.0] * 300
        for i in range(150, 180):
            cols[i] = 0.55          # 몸통
        cols[205] = 0.98            # 얇은 격자선
        self.assertEqual(sg.grid_cuts(cols, 3), [100, 205])


if __name__ == "__main__":
    unittest.main()
