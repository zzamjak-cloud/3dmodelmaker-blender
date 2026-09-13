# 개별 매핑 타입의 순수 파이썬 부분 테스트 (Blender 없이 실행)
#   python -m unittest tests.test_texturing -v
import importlib.util
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


layout = _load("tex_layout", "texturing/layout.py")
unwrap = _load("tex_unwrap", "texturing/unwrap.py")
# texgen은 ..texturing.layout 상대 임포트를 쓰므로 가짜 패키지 트리 아래에 얹는다
_pkg = types.ModuleType("lp3d_t")
_pkg.__path__ = [_ROOT]
sys.modules["lp3d_t"] = _pkg
_tex = types.ModuleType("lp3d_t.texturing")
_tex.__path__ = [os.path.join(_ROOT, "texturing")]
_tex.layout = layout
sys.modules["lp3d_t.texturing"] = _tex
sys.modules["lp3d_t.texturing.layout"] = layout
_core = types.ModuleType("lp3d_t.core")
_core.__path__ = [os.path.join(_ROOT, "core")]
sys.modules["lp3d_t.core"] = _core
texgen = _load("lp3d_t.core.texgen", "core/texgen.py")


class TestLayout(unittest.TestCase):
    def test_six_cells_row_major_top_first(self):
        lay = layout.LAYOUT
        self.assertEqual(lay.cell_count, 6)
        # 첫 행(FRONT)은 캔버스 위쪽 → 좌하단 원점에서 bottom이 절반 이상
        left, bottom, right, top = lay.cell_bounds(1536, 1024, 0)
        self.assertEqual((left, bottom, right, top), (0, 512, 512, 1024))
        # 마지막 셀(BOTTOM)은 아랫줄 오른쪽
        self.assertEqual(lay.cell_bounds(1536, 1024, 5), (1024, 0, 1536, 512))

    def test_canvas_matches_sheet_aspect_without_padding(self):
        self.assertEqual(layout.LAYOUT.canvas_size(1024), (3072, 2048))
        self.assertEqual(layout.LAYOUT.cell_origin(3072, 2048, 4, 1024), (1024, 0))

    def test_prompt_mentions_views_size_and_filename(self):
        text = layout.build_prompt("나무 배럴", "texture_sheet.png")
        for view in ("FRONT", "RIGHT SIDE", "BACK", "LEFT SIDE", "TOP", "BOTTOM"):
            self.assertIn(view, text)
        self.assertIn("1536x1024", text)
        self.assertIn("texture_sheet.png", text)
        self.assertIn("나무 배럴", text)
        self.assertIn("paint-over", text)
        self.assertTrue(text.endswith("SAVED 한 단어만 답하라."))


class TestTexgenCommand(unittest.TestCase):
    def test_guide_attached_before_stdin_marker(self):
        cmd = texgen.build_command("/usr/bin/codex", "/tmp/w", "/tmp/w/guide_sheet.png")
        self.assertEqual(cmd[-1], "-")
        self.assertEqual(cmd[cmd.index("-i") + 1], "/tmp/w/guide_sheet.png")
        self.assertIn("workspace-write", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertEqual(cmd[cmd.index("--cd") + 1], "/tmp/w")

    def test_prompt_uses_sheet_filename(self):
        self.assertIn(texgen.SHEET_FILENAME, texgen.build_prompt("돌담"))


def _cube_faces(size=1.0, offset=(0, 0, 0), tag="a"):
    """단위 큐브 6면 — (정점키, 월드 위치, 노멀)."""
    s = size / 2
    ox, oy, oz = offset
    verts = [(ox + x, oy + y, oz + z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
    # 정점 인덱스: x*4 + y*2 + z
    idx = lambda x, y, z: x * 4 + y * 2 + z
    faces_def = [
        ((0, 0, 0), [idx(0, 0, 0), idx(1, 0, 0), idx(1, 0, 1), idx(0, 0, 1)], (0, -1, 0)),  # FRONT
        ((0, 0, 0), [idx(1, 1, 0), idx(0, 1, 0), idx(0, 1, 1), idx(1, 1, 1)], (0, 1, 0)),   # BACK
        ((0, 0, 0), [idx(1, 0, 0), idx(1, 1, 0), idx(1, 1, 1), idx(1, 0, 1)], (1, 0, 0)),   # RIGHT
        ((0, 0, 0), [idx(0, 1, 0), idx(0, 0, 0), idx(0, 0, 1), idx(0, 1, 1)], (-1, 0, 0)),  # LEFT
        ((0, 0, 0), [idx(0, 0, 1), idx(1, 0, 1), idx(1, 1, 1), idx(0, 1, 1)], (0, 0, 1)),   # TOP
        ((0, 0, 0), [idx(0, 1, 0), idx(1, 1, 0), idx(1, 0, 0), idx(0, 0, 0)], (0, 0, -1)),  # BOTTOM
    ]
    return [(tuple((tag, i) for i in vi), [verts[i] for i in vi], n) for _o, vi, n in faces_def]


class TestUnwrap(unittest.TestCase):
    def test_pick_view_follows_dominant_axis(self):
        self.assertEqual(unwrap.pick_view((0, -0.9, 0.3)), "FRONT")
        self.assertEqual(unwrap.pick_view((0.7, 0.1, -0.6)), "RIGHT")
        self.assertEqual(unwrap.pick_view((0, 0, -1)), "BOTTOM")

    def test_cube_gives_six_islands_one_per_view(self):
        islands = unwrap.build_islands(_cube_faces())
        self.assertEqual(len(islands), 6)
        self.assertEqual(sorted(i["view"] for i in islands), sorted(unwrap.VIEW_AXES))

    def test_coplanar_adjacent_faces_share_island(self):
        # 정면을 두 조각으로 쪼갠 면 2개 — 변을 공유하고 같은 시점이면 한 아일랜드
        a = (("v", 0), ("v", 1), ("v", 2), ("v", 3))
        b = (("v", 1), ("v", 4), ("v", 5), ("v", 2))
        pa = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]
        pb = [(1, 0, 0), (2, 0, 0), (2, 0, 1), (1, 0, 1)]
        islands = unwrap.build_islands([(a, pa, (0, -1, 0)), (b, pb, (0, -1, 0))])
        self.assertEqual(len(islands), 1)
        self.assertEqual(islands[0]["bounds"], (0, 0, 2, 1))

    def test_front_projection_is_upright_and_not_mirrored(self):
        # 정면 시점: 화면 오른쪽=+X, 위=+Z
        self.assertEqual(unwrap.project((2, 5, 3), "FRONT"), (2, 3))
        # 뒷면은 좌우가 뒤집혀야 카메라에서 본 방향과 일치
        self.assertEqual(unwrap.project((2, 5, 3), "BACK"), (-2, 3))

    def test_pack_fits_unit_square_without_overlap(self):
        bounds = [(0, 0, 2, 1), (0, 0, 1, 1), (0, 0, 0.5, 3), (0, 0, 1.5, 1.5), (0, 0, 0.2, 0.2)]
        scale, offsets = unwrap.pack(bounds, margin=0.01)
        rects = []
        for (l, b, r, t), (ou, ov) in zip(bounds, offsets):
            w, h = (r - l) * scale, (t - b) * scale
            self.assertGreaterEqual(ou, 0.0)
            self.assertGreaterEqual(ov, 0.0)
            self.assertLessEqual(ou + w, 1.0 + 1e-9)
            self.assertLessEqual(ov + h, 1.0 + 1e-9)
            rects.append((ou, ov, ou + w, ov + h))
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                separated = a[2] <= b[0] + 1e-9 or b[2] <= a[0] + 1e-9 or a[3] <= b[1] + 1e-9 or b[3] <= a[1] + 1e-9
                self.assertTrue(separated, f"아일랜드 {i}와 {j}가 겹침: {a} {b}")

    def test_pack_is_deterministic(self):
        bounds = [(0, 0, 1, 2), (0, 0, 2, 1), (0, 0, 1, 1)]
        self.assertEqual(unwrap.pack(bounds), unwrap.pack(bounds))


if __name__ == "__main__":
    unittest.main()
