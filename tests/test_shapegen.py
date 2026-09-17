# 이미지→3D 셰이프 클라이언트 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shapegen = _load("shapegen_mod", "core/shapegen.py")


def _png(path):
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    return path


class TestTurnaroundCells(unittest.TestCase):
    def test_cell_order_matches_sheet_layout(self):
        # 시트: 윗줄 정면|뒷면|좌측면, 아랫줄 우측면|상면|3/4 — multiview.SHEET_LAYOUT과 같아야 한다
        c = shapegen.TURNAROUND_CELLS
        self.assertEqual(c["front"], (0, 0))
        self.assertEqual(c["back"], (1, 0))
        self.assertEqual(c["left"], (2, 0))
        self.assertEqual(c["right"], (0, 1))

    def test_only_side_views_are_sent(self):
        # 멀티뷰 모델은 top·3/4을 받지 않는다
        self.assertEqual(set(shapegen.SEND_VIEWS), {"front", "back", "left", "right"})


class TestBuildBody(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.views = {n: _png(os.path.join(self.dir, n + ".png"))
                      for n in ("front", "back", "left", "top", "quarter")}

    def test_body_has_views_and_params(self):
        body = shapegen.build_body(self.views, octree=192, steps=20, face_count=30000, seed=3)
        for k in ("front", "back", "left"):
            self.assertIn(k, body)
        self.assertNotIn("top", body)
        self.assertNotIn("quarter", body)
        self.assertNotIn("right", body)  # 없는 뷰는 보내지 않는다
        self.assertEqual(body["octree_resolution"], 192)
        self.assertEqual(body["num_inference_steps"], 20)
        self.assertEqual(body["face_count"], 30000)
        self.assertEqual(body["seed"], 3)
        self.assertFalse(body["texture"])  # 텍스처는 애드온이 담당
        json.dumps(body)

    def test_front_is_required(self):
        with self.assertRaises(ValueError):
            shapegen.build_body({"back": self.views["back"]})

    def test_missing_files_are_skipped(self):
        body = shapegen.build_body({"front": self.views["front"], "back": "/nope.png"})
        self.assertIn("front", body)
        self.assertNotIn("back", body)


class TestRequestErrors(unittest.TestCase):
    def test_unreachable_server_returns_error_not_exception(self):
        import types
        fake_prefs = types.SimpleNamespace(shapegen_url="http://127.0.0.1:9", use_shapegen=True)
        pkg = types.ModuleType("shapegen_parent")
        pkg.preferences = types.SimpleNamespace(get_prefs=lambda: fake_prefs)
        shapegen.__package__ = "shapegen_parent.core"
        sys.modules["shapegen_parent"] = pkg
        sys.modules["shapegen_parent.core"] = types.ModuleType("shapegen_parent.core")
        d = tempfile.mkdtemp()
        views = {"front": _png(os.path.join(d, "f.png"))}
        path, err = shapegen.request_shape(views, os.path.join(d, "o.glb"), timeout=2)
        self.assertIsNone(path)
        self.assertIn("연결할 수 없습니다", err)
        self.assertFalse(shapegen.is_available(timeout=0.5))


if __name__ == "__main__":
    unittest.main()
