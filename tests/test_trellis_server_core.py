# 셰이프 서버 계약·전처리 규칙 회귀 테스트 (GPU·Modal 불필요)
import base64
import importlib.util
import io
import unittest
import unittest.mock
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("server_core", ROOT / "scripts/trellis3d/server_core.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _cell_with_label(label_at_bottom: bool) -> Image.Image:
    """흰 셀 + 격자선 + 중앙 캐릭터 덩어리 + (상단 또는 하단) 라벨 텍스트 블록을 흉내낸다."""
    img = Image.new("RGB", (400, 360), (255, 255, 255))
    arr = np.asarray(img).copy()
    arr[:2, :] = arr[-2:, :] = 60          # 격자선(가로)
    arr[:, :2] = arr[:, -2:] = 60          # 격자선(세로)
    arr[80:280, 150:250] = (120, 80, 40)   # 몸통
    if label_at_bottom:
        arr[310:330, 170:230] = 0          # 하단 라벨(높이 20 = 5.6%)
    else:
        arr[20:40, 170:230] = 0            # 상단 라벨
    return Image.fromarray(arr, "RGB")


class PrepareViewTests(unittest.TestCase):
    def test_bottom_label_and_grid_removed_and_white_padded(self):
        out = sc.prepare_view(_cell_with_label(label_at_bottom=True))
        arr = np.asarray(out)
        self.assertEqual(out.width, out.height)                       # 정사각
        self.assertEqual(tuple(arr[0, 0]), (255, 255, 255))            # 여백은 흰색 (검정 여백 금지)
        dark = (255 - arr.min(axis=2)) > 12
        ys, xs = np.nonzero(dark)
        # 라벨(검정)이 사라졌으면 남은 어두운 픽셀은 몸통 색뿐이다
        self.assertFalse(np.any(arr[dark].sum(axis=1) == 0), "라벨 텍스트가 남아 있다")
        # 몸통 200x100이 캔버스에 중앙 배치 (1.15배 여유)
        self.assertAlmostEqual((ys.max() - ys.min() + 1) / out.height, 1 / sc.PAD_RATIO, delta=0.02)

    def test_top_label_also_removed(self):
        out = sc.prepare_view(_cell_with_label(label_at_bottom=False))
        arr = np.asarray(out)
        self.assertFalse(np.any(arr.sum(axis=2) == 0))

    def test_clean_input_is_unharmed(self):
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        a = np.asarray(img).copy(); a[30:70, 40:60] = (10, 200, 10)
        out = sc.prepare_view(Image.fromarray(a, "RGB"))
        arr = np.asarray(out)
        self.assertTrue(np.any((arr == (10, 200, 10)).all(axis=2)))


class DecodeAndCollectTests(unittest.TestCase):
    def test_transparent_padding_becomes_white_not_black(self):
        rgba = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        rgba.paste((200, 30, 30, 255), (20, 20, 44, 44))
        rgb = sc.decode_image(_png_b64(rgba))
        self.assertEqual(rgb.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(rgb.getpixel((30, 30)), (200, 30, 30))

    def test_rgb_to_rgba_white_makes_background_transparent_and_body_opaque(self):
        a = np.full((40, 40, 3), 255, dtype=np.uint8); a[10:30, 10:30] = (120, 80, 40)
        rgba = np.asarray(sc.rgb_to_rgba_white(Image.fromarray(a, "RGB")))
        self.assertEqual(rgba.shape[2], 4)
        self.assertEqual(int(rgba[0, 0, 3]), 0)          # 흰 배경 → 투명
        self.assertEqual(int(rgba[20, 20, 3]), 255)      # 몸통 → 불투명
        self.assertEqual(tuple(rgba[20, 20, :3]), (120, 80, 40))

    def test_collect_views_prefers_named_views_and_falls_back_to_image(self):
        cell = _png_b64(_cell_with_label(True))
        self.assertEqual(set(sc.collect_views({"front": cell, "left": cell})), {"front", "left"})
        self.assertEqual(set(sc.collect_views({"image": cell})), {"front"})
        with self.assertRaises(ValueError):
            sc.collect_views({})


class _FakePart:
    def __init__(self, lo, hi, faces=10):
        self.bounds = np.array([lo, hi], dtype=float); self.faces = [0] * faces


class PlaneRemoverTests(unittest.TestCase):
    def test_flat_wide_shell_is_a_plane_but_thin_sword_is_not(self):
        pr = sc.PlaneRemover()
        self.assertTrue(pr.is_plane(_FakePart([-.5, -.5, -.43], [.5, .5, -.43]), 1.0))       # 바닥판
        self.assertTrue(pr.is_plane(_FakePart([-.5, -.5, -.33], [.5, .5, -.31]), 1.0))       # 두께 2% 바닥판(실측)
        self.assertFalse(pr.is_plane(_FakePart([-.5, -.1, -.43], [.5, .1, .46]), 1.0))       # 몸체
        self.assertFalse(pr.is_plane(_FakePart([.4, -.01, -.2], [.42, .01, .5]), 1.0))       # 가느다란 검(좁아서 판 아님)

    def test_postprocess_always_runs_plane_removal_before_floaters(self):
        calls = []
        class M:  # 최소 메시 대역
            faces = [0] * 100
        with unittest.mock.patch.object(sc, "PlaneRemover") as plane, \
             unittest.mock.patch.object(sc, "FloaterRemover") as floater, \
             unittest.mock.patch.object(sc, "DegenerateFaceRemover") as degen:
            plane.return_value.side_effect = lambda m: (calls.append("plane"), m)[1]
            floater.return_value.side_effect = lambda m: (calls.append("floater"), m)[1]
            degen.return_value.side_effect = lambda m: (calls.append("degen"), m)[1]
            sc._postprocess_mesh(M(), {"preserve_parts": True})
            self.assertEqual(calls, ["plane", "degen"])
            calls.clear()
            sc._postprocess_mesh(M(), {})
            self.assertEqual(calls, ["plane", "floater", "degen"])


class BackgroundKeyTests(unittest.TestCase):
    def test_inner_white_survives_and_outer_white_is_cut(self):
        # 몸통(어두움) 안에 흰 이빨, 바깥은 흰 배경 — 배경만 투명해져야 한다
        a = np.full((40, 40, 3), 255, dtype=np.uint8)
        a[10:30, 10:30] = (60, 90, 40)
        a[18:22, 18:22] = 255                      # 실루엣 안쪽의 흰색
        rgba = np.asarray(sc.rgb_to_rgba_white(Image.fromarray(a, "RGB")))
        self.assertEqual(int(rgba[0, 0, 3]), 0)     # 배경
        self.assertEqual(int(rgba[20, 20, 3]), 255)  # 이빨
        self.assertEqual(int(rgba[12, 12, 3]), 255)  # 몸통

    def test_background_notch_reaching_the_edge_is_cut(self):
        a = np.full((40, 40, 3), 255, dtype=np.uint8)
        a[10:30, 10:30] = (60, 90, 40)
        a[10:30, 18:22] = 255                      # 가장자리까지 이어진 흰 틈
        rgba = np.asarray(sc.rgb_to_rgba_white(Image.fromarray(a, "RGB")))
        self.assertEqual(int(rgba[20, 20, 3]), 0)


class ParamAndFrameTests(unittest.TestCase):
    def test_map_params_defaults_and_cascade_threshold(self):
        base = sc.map_params({})
        self.assertEqual((base["seed"], base["steps"], base["cascade"], base["face_count"]), (1234, 12, False, 0))
        self.assertFalse(sc.map_params({"octree_resolution": 256})["cascade"])
        self.assertTrue(sc.map_params({"octree_resolution": 1024})["cascade"])
        self.assertEqual(sc.map_params({"num_inference_steps": 999})["steps"], 50)
        self.assertTrue(sc.map_params({"preserve_parts": True})["preserve_parts"])

    def test_to_gltf_frame_swaps_z_up_to_y_up(self):
        v = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        np.testing.assert_allclose(sc.to_gltf_frame(v), [[1.0, 3.0, -2.0]])

    def test_status_payload_advertises_preserve_parts(self):
        self.assertTrue(sc.status_payload()["capabilities"]["preserve_parts"])
        self.assertTrue(sc.status_payload()["ok"])


if __name__ == "__main__":
    unittest.main()
