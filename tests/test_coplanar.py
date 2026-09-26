# 동일평면 겹침(Z-fighting) 띄우기의 순수 기하 계산
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


cp = _load("coplanar")
Face = cp.Face


def _box_faces(island, lo, hi, mutable=True):
    """축 정렬 박스의 바깥 노멀 면 6개 (key = (island, 이름))."""
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    quads = {
        "top": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        "bottom": [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
        "front": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
        "back": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
        "left": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
        "right": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
    }
    return [Face(key=(island, k), island=island, points=v, mutable=mutable) for k, v in quads.items()]


STEP = cp.nudge_distance(1.0)


class TestResolve(unittest.TestCase):
    def assertVec(self, got, want):
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, places=9)

    def test_separate_boxes_untouched(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (2, 0, 0), (3, 1, 1))
        self.assertEqual(cp.resolve(faces), {})

    def test_penetrating_boxes_untouched(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (0.5, 0.2, 0.2), (1.5, 0.8, 0.8))
        self.assertEqual(cp.resolve(faces), {})

    def test_contact_faces_untouched(self):
        # 받침 위 상자·나란히 맞댄 상자 — 맞댄 면은 두 솔리드 사이라 보이지 않는다
        stacked = _box_faces("a", (0, 0, 0), (2, 2, 1)) + _box_faces("b", (0.5, 0.5, 1), (1.5, 1.5, 2))
        side = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (1, 0, 0), (2, 1, 1))
        self.assertEqual(cp.resolve(stacked), {})
        self.assertEqual(cp.resolve(side), {})

    def test_flush_moves_smaller_part_outward(self):
        # 벽 앞면(-Y)에 붙인 패널 — 패널만 -Y로 한 단 나온다
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        panel = _box_faces("panel", (1, 0, 1), (2, 0.1, 2))
        out = cp.resolve(wall + panel)
        self.assertEqual(set(out), {"panel"})
        self.assertVec(out["panel"], (0, -STEP, 0))

    def test_stacked_details_get_separate_layers(self):
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        panel = _box_faces("panel", (1, 0, 1), (2, 0.1, 2))
        plate = _box_faces("plate", (1.2, 0, 1.2), (1.5, 0.05, 1.5))
        out = cp.resolve(wall + panel + plate)
        self.assertVec(out["panel"], (0, -STEP, 0))
        self.assertVec(out["plate"], (0, -2 * STEP, 0))

    def test_side_by_side_details_share_a_layer(self):
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        left = _box_faces("l", (0.5, 0, 1), (1.5, 0.1, 2))
        right = _box_faces("r", (2, 0, 1), (3, 0.1, 2))
        out = cp.resolve(wall + left + right)
        self.assertVec(out["l"], (0, -STEP, 0))
        self.assertVec(out["r"], (0, -STEP, 0))

    def test_multiple_directions_add_up(self):
        # 벽 모서리에 앞면·윗면이 모두 맞춰진 띠 — 두 방향으로 한 단씩
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        trim = _box_faces("trim", (0, 0, 2.8), (4, 0.05, 3))
        out = cp.resolve(wall + trim)
        self.assertEqual(set(out), {"trim"})
        self.assertAlmostEqual(out["trim"][1], -STEP)
        self.assertAlmostEqual(out["trim"][2], STEP)

    def test_opposite_flush_on_one_axis_moves_once(self):
        # 벽을 관통해 앞뒷면이 모두 맞춰진 판 — 한 방향으로만 밀면 반대쪽 면은 벽 안으로 숨는다
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        board = _box_faces("board", (1, 0, 1), (2, 0.3, 2))
        out = cp.resolve(wall + board)
        self.assertAlmostEqual(abs(out["board"][1]), STEP)

    def test_immutable_part_stays_and_partner_moves(self):
        base = _box_faces("inst", (0, 0, 0), (4, 0.3, 3), mutable=False)
        panel = _box_faces("panel", (1, 0, 1), (2, 0.1, 2))
        self.assertVec(cp.resolve(base + panel)["panel"], (0, -STEP, 0))
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        inst = _box_faces("inst", (1, 0, 1), (2, 0.1, 2), mutable=False)
        out = cp.resolve(wall + inst)
        self.assertNotIn("inst", out)
        self.assertVec(out["wall"], (0, STEP, 0))

    def test_same_island_is_ignored(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + [
            Face(key="dup", island="a", points=[(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)])]
        self.assertEqual(cp.resolve(faces), {})

    def test_scale_tolerance_catches_tiny_gap(self):
        # 부동소수점 오차 수준으로 떨어진 면도 같은 평면으로 본다
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (0.2, 0.2, 0.5), (0.8, 0.8, 1 + 1e-7))
        self.assertIn("b", cp.resolve(faces))

    def test_rounding_boundary_does_not_split_plane(self):
        # 고정 칸 반올림이면 경계 양쪽으로 갈라지던 값 — 틈 기준 묶음은 같은 평면으로 본다
        z = 1e-4 * 4 * 2.5
        faces = _box_faces("a", (0, 0, z - 1), (1, 1, z - 1e-7)) + _box_faces("b", (0.2, 0.2, z - 0.5), (0.8, 0.8, z + 1e-7))
        self.assertIn("b", cp.resolve(faces))

    def test_concave_face_overlap_through_triangles(self):
        l_shape = [(0, 0, 1), (2, 0, 1), (2, 1, 1), (1, 1, 1), (1, 2, 1), (0, 2, 1)]
        faces = [Face(key="L", island="a", points=l_shape)] + _box_faces("b", (0.2, 0.2, 0.5), (0.8, 0.8, 1))
        self.assertVec(cp.resolve(faces)["b"], (0, 0, STEP))
        away = [Face(key="L", island="a", points=l_shape)] + _box_faces("b", (1.2, 1.2, 0.5), (1.8, 1.8, 1))
        self.assertEqual(cp.resolve(away), {})

    def test_nudge_distance_is_clamped(self):
        self.assertEqual(cp.nudge_distance(0.01), cp.NUDGE_MIN)
        self.assertEqual(cp.nudge_distance(100.0), cp.NUDGE_MAX)

    def test_nudged_result_is_stable(self):
        # 띄운 뒤 다시 돌리면 더 옮길 것이 없다
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        panel = _box_faces("panel", (1, -STEP, 1), (2, 0.1 - STEP, 2))
        self.assertEqual(cp.resolve(wall + panel), {})


if __name__ == "__main__":
    unittest.main()
