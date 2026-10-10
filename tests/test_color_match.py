# PBR 색 보정(color_match)의 numpy 계산 — 경계 상자·평탄 마스크·아핀 맞춤·적용 판정
import importlib.util
import os
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    # lowpoly/__init__ 은 bpy 를 임포트하므로 파일 단위로 불러온다
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, "lowpoly", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cm = _load("color_match")


def blocks(h=96, w=64, seed=0):
    """평탄한 색 블록으로 채운 그림 — 블록 안쪽은 평탄 마스크를 통과한다."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3))
    for y in range(0, h, 16):
        for x in range(0, w, 16):
            img[y:y + 16, x:x + 16] = rng.uniform(0.1, 0.85, 3)
    return img


class TestColorMatch(unittest.TestCase):
    def test_figure_bbox_ignores_white(self):
        img = np.ones((50, 40, 3))
        img[10:30, 5:25] = 0.5
        self.assertEqual(cm.figure_bbox(img), (10, 30, 5, 25))
        self.assertIsNone(cm.figure_bbox(np.ones((5, 5, 3))))

    def test_figure_bbox_uses_alpha(self):
        # 투명 PNG 의 투명 영역은 RGB 가 검정이라 색만 보면 전부 피사체로 잡힌다
        img = np.zeros((50, 40, 4))
        img[10:30, 5:25] = (0.5, 0.5, 0.5, 1.0)
        self.assertEqual(cm.figure_bbox(img), (10, 30, 5, 25))

    def test_resample_maps_bbox_to_grid(self):
        img = np.zeros((10, 10, 3))
        img[2:6, 4:8] = 1.0
        out = cm.resample(img, (2, 6, 4, 8), 3, 5)
        self.assertEqual(out.shape, (5, 3, 3))
        self.assertTrue(np.all(out == 1.0))

    def test_flat_mask_drops_edges(self):
        img = np.zeros((20, 20, 3))
        img[:, 10:] = 1.0
        mask = cm.flat_mask(img)
        self.assertFalse(mask[5, 10])
        self.assertTrue(mask[5, 2])

    def test_fit_recovers_affine(self):
        ref = blocks()
        m_true = np.array([[0.8, 0.1, 0.0], [0.05, 0.7, 0.0], [0.0, 0.1, 0.9], [0.05, 0.0, 0.02]])
        # 렌더 = 원화를 틀어 놓은 것, 보정 = 되돌리는 변환
        rendered = cm.apply_affine(ref, m_true)
        m, stats = cm.solve(rendered, np.ones(ref.shape[:2], bool), ref)
        self.assertIsNotNone(m)
        self.assertLess(stats["after"], stats["before"] * 0.5)

    def test_identity_is_not_applied(self):
        ref = blocks()
        m, stats = cm.solve(ref.copy(), np.ones(ref.shape[:2], bool), ref)
        self.assertIsNone(m)
        self.assertEqual(stats["reason"], "개선 미미")

    def test_too_few_samples(self):
        ref = blocks(16, 16)
        m, stats = cm.solve(ref * 0.5, np.ones(ref.shape[:2], bool), ref)
        self.assertIsNone(m)
        self.assertEqual(stats["reason"], "샘플 부족")

    def test_ridge_keeps_near_identity_on_narrow_colors(self):
        # 색이 한 가지뿐이면 행렬이 정해지지 않는다 — 정칙화로 항등 근처에 머물러야 한다
        src = np.tile([0.6, 0.5, 0.2], (3000, 1)) + np.random.default_rng(1).normal(0, 0.005, (3000, 3))
        m = cm.fit_affine(src, src * 0.9)
        self.assertLess(np.abs(m[:3] - np.eye(3)).max(), 0.5)


if __name__ == "__main__":
    unittest.main()
