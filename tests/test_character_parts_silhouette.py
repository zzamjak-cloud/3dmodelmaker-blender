# 부품 시트 실루엣: 격자선·라벨 제외 bbox, 환각 부품 판정 (bpy 불필요, 합성 픽셀)
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('cp_sil', Path(__file__).resolve().parents[1] / 'core/character_parts.py')
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)

W, H = 120, 100


def _canvas():
    return [1.0] * (W * H * 4)   # 흰 배경, 알파 1


def _fill(px, x0, y0, x1, y1, rgb=(0.3, 0.2, 0.1)):
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * W + x) * 4
            px[i:i + 3] = rgb


def _grid_lines(px):
    _fill(px, 0, 0, W, 2, (0.4,) * 3); _fill(px, 0, H - 2, W, H, (0.4,) * 3)
    _fill(px, 0, 0, 2, H, (0.4,) * 3); _fill(px, W - 2, 0, W, H, (0.4,) * 3)


class BboxTests(unittest.TestCase):
    def test_grid_lines_and_bottom_label_are_ignored(self):
        px = _canvas(); _grid_lines(px)
        _fill(px, 40, 30, 80, 90)             # 몸체 (행 30~90)
        _fill(px, 50, 6, 70, 12, (0.0,) * 3)  # 하단 라벨 (bpy 는 행 0 이 아래)
        box = parts.silhouette_bbox(px, W, H)
        self.assertAlmostEqual(box[0], 40 / W); self.assertAlmostEqual(box[2], 80 / W)
        self.assertAlmostEqual(box[1], 30 / H); self.assertAlmostEqual(box[3], 90 / H)

    def test_top_label_is_ignored_too(self):
        px = _canvas(); _fill(px, 40, 20, 80, 70); _fill(px, 50, 88, 70, 94, (0.0,) * 3)
        box = parts.silhouette_bbox(px, W, H)
        self.assertAlmostEqual(box[3], 70 / H)

    def test_small_middle_pieces_are_kept(self):
        # 벨트 주머니처럼 본체에서 떨어진 작은 조각(가운데)은 라벨이 아니다
        px = _canvas(); _fill(px, 40, 30, 80, 90); _fill(px, 20, 55, 30, 62)
        box = parts.silhouette_bbox(px, W, H)
        self.assertAlmostEqual(box[0], 20 / W)

    def test_only_grid_and_label_is_empty(self):
        px = _canvas(); _grid_lines(px); _fill(px, 50, 6, 70, 12, (0.0,) * 3)
        self.assertIsNone(parts.silhouette_bbox(px, W, H))


class FaceBudgetTests(unittest.TestCase):
    BODY = (.2, .05, .8, .95)   # 정면 실루엣 (정규화)

    def test_body_gets_full_budget_and_parts_scale_by_area(self):
        self.assertEqual(parts.part_face_budget(12000, self.BODY, self.BODY), 12000)
        outfit = (.25, .3, .75, .9)                 # 면적비 ≈ 0.56 → 12000 * 0.56^0.75 ≈ 7,800
        belt = (.35, .45, .65, .55)                 # 면적비 ≈ 0.056 → 하한 12% = 1,440
        self.assertTrue(7000 < parts.part_face_budget(12000, self.BODY, outfit) < 8500)
        self.assertEqual(parts.part_face_budget(12000, self.BODY, belt), 1440)

    def test_missing_box_keeps_base(self):
        self.assertEqual(parts.part_face_budget(12000, None, (.1, .1, .2, .2)), 12000)

    def test_budget_never_exceeds_base(self):
        self.assertEqual(parts.part_face_budget(12000, self.BODY, (0, 0, 1, 1)), 12000)


class HallucinationTests(unittest.TestCase):
    def test_outfit_inside_body_has_low_outside_ratio(self):
        body = _canvas(); _fill(body, 40, 20, 80, 95)
        outfit = _canvas(); _fill(outfit, 44, 30, 76, 60)
        r = parts.outside_ratio(parts.silhouette_mask(outfit, W, H), parts.silhouette_mask(body, W, H))
        self.assertLess(r, 0.1)

    def test_sword_and_shield_beside_body_is_flagged(self):
        body = _canvas(); _fill(body, 50, 20, 70, 95)               # 좁은 몸체
        weapon = _canvas(); _fill(weapon, 5, 20, 20, 90); _fill(weapon, 95, 30, 115, 80)  # 몸 옆 검·방패
        r = parts.outside_ratio(parts.silhouette_mask(weapon, W, H), parts.silhouette_mask(body, W, H))
        self.assertGreaterEqual(r, parts.HALLUCINATION_RATIO)

    def test_held_weapon_overlapping_hand_passes(self):
        body = _canvas(); _fill(body, 20, 20, 100, 95)             # T-포즈처럼 넓은 몸체
        sword = _canvas(); _fill(sword, 90, 40, 96, 100)           # 손 위치에서 살짝 밖으로 나온 검
        r = parts.outside_ratio(parts.silhouette_mask(sword, W, H), parts.silhouette_mask(body, W, H))
        self.assertLess(r, parts.HALLUCINATION_RATIO)


if __name__ == '__main__':
    unittest.main()


class SizeToleranceTests(unittest.TestCase):
    def test_size_tolerances(self):
        body = {"front": (500, 500)}
        self.assertTrue(parts.sizes_match({"front": (510, 495)}, body))
        self.assertFalse(parts.sizes_match({"front": (540, 500)}, body))
        self.assertTrue(parts.sizes_match({"front": (540, 500)}, body, tolerance=parts.SIZE_SKIP_TOLERANCE))
        self.assertFalse(parts.sizes_match({"front": (600, 500)}, body, tolerance=parts.SIZE_SKIP_TOLERANCE))

