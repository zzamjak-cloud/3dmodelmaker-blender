# 정면 디테일 투영(front_project)의 numpy 계산 — UV·정면 래스터, 깊이 판정, 정면도, 실루엣 가중치, 정렬
import importlib.util
import os
import sys
import types
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_package():
    # lowpoly/__init__ 은 bpy 를 임포트하므로 빈 패키지를 세우고 두 모듈만 파일 단위로 얹는다
    pkg = types.ModuleType("lp_fp_pkg")
    pkg.__path__ = [os.path.join(_ROOT, "lowpoly")]
    sys.modules["lp_fp_pkg"] = pkg
    for name in ("color_match", "front_project"):
        spec = importlib.util.spec_from_file_location(f"lp_fp_pkg.{name}", os.path.join(_ROOT, "lowpoly", f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"lp_fp_pkg.{name}"] = module
        spec.loader.exec_module(module)
        setattr(pkg, name, module)
    return pkg.front_project


fp = _load_package()


class TestRasterize(unittest.TestCase):
    def test_uv_square_covered_once(self):
        # UV 정사각형을 삼각형 둘로 — 모든 텍셀이 정확히 한 삼각형에 들어가야 한다
        uvs = np.array([[[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 1], [0, 1]]], float)
        tri_id, bary = fp.rasterize_uv(uvs, np.array([[0, 1, 2], [0, 2, 3]]), 32)
        self.assertTrue((tri_id >= 0).all())
        self.assertTrue(np.allclose(bary.sum(axis=-1), 1.0, atol=1e-5))

    def test_front_keeps_nearest(self):
        # 같은 자리 두 삼각형 — 깊이가 작은(앞) 쪽이 남는다
        pts = np.array([[0, 0, 5], [16, 0, 5], [0, 16, 5], [0, 0, 1], [16, 0, 1], [0, 16, 1]], float)
        tri_id, _bary, depth = fp.rasterize_front(pts, np.array([[0, 1, 2], [3, 4, 5]]), 16, 16)
        self.assertEqual(tri_id[2, 2], 1)
        self.assertAlmostEqual(depth[2, 2], 1.0)
        self.assertEqual(tri_id[15, 15], -1)


class TestWeights(unittest.TestCase):
    def test_facing_ignores_winding(self):
        # 서버 메시는 감김이 섞여 있다 — 앞을 보든 뒤를 보든 축에 나란하면 1, 옆면이면 0
        w = fp.facing_weight(np.array([-1.0, 1.0, 0.0, -0.55]))
        self.assertEqual(w[0], 1.0)
        self.assertEqual(w[1], 1.0)
        self.assertEqual(w[2], 0.0)
        self.assertTrue(0.0 < w[3] < 1.0)

    def test_gate_fades_at_silhouette(self):
        inside = np.zeros((60, 60), bool)
        inside[10:50, 10:50] = True
        gate = fp.gate_map(inside, feather=4)
        self.assertEqual(gate[30, 30], 1.0)
        self.assertEqual(gate[5, 5], 0.0)
        self.assertLess(gate[10, 30], 0.6)

    def test_alignment_recovers_shift(self):
        mesh = np.zeros((100, 100), bool)
        mesh[20:80, 30:70] = True
        figure = np.zeros_like(mesh)
        figure[22:82, 30:70] = True            # 원화 실루엣이 2% 아래
        scale, dx, dy, iou = fp.best_alignment(mesh, figure)
        self.assertAlmostEqual(dy, 0.02)
        self.assertAlmostEqual(dx, 0.0)
        self.assertGreater(iou, 0.95)

    def test_bilinear_center(self):
        img = np.zeros((2, 2, 1))
        img[0, 1] = img[1, 0] = 1.0
        self.assertAlmostEqual(float(fp.sample_bilinear(img, np.array([1.0]), np.array([1.0]))[0, 0]), 0.5)


if __name__ == "__main__":
    unittest.main()
