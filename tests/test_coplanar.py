# 동일평면 겹침(Z-fighting) 절단의 순수 기하 계산
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


def _area3(points):
    n = cp.newell_normal(points)
    total = [0.0, 0.0, 0.0]
    for i in range(len(points)):
        a, b = points[i], points[(i + 1) % len(points)]
        total[0] += a[1] * b[2] - a[2] * b[1]
        total[1] += a[2] * b[0] - a[0] * b[2]
        total[2] += a[0] * b[1] - a[1] * b[0]
    return 0.5 * (total[0] * n[0] + total[1] * n[1] + total[2] * n[2])


class TestResolve(unittest.TestCase):
    def test_separate_boxes_untouched(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (2, 0, 0), (3, 1, 1))
        self.assertEqual(cp.resolve(faces), {})

    def test_penetrating_boxes_untouched(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (0.5, 0.2, 0.2), (1.5, 0.8, 0.8))
        self.assertEqual(cp.resolve(faces), {})

    def test_stacked_contact_removes_both_overlaps(self):
        # 큰 받침 위에 작은 상자 — 받침 윗면은 둘레만 남고 작은 상자 밑면은 사라진다
        faces = _box_faces("a", (0, 0, 0), (2, 2, 1)) + _box_faces("b", (0.5, 0.5, 1), (1.5, 1.5, 2))
        out = cp.resolve(faces)
        self.assertEqual(out[("b", "bottom")], [])
        ring = out[("a", "top")]
        self.assertAlmostEqual(sum(_area3(p) for p in ring), 4.0 - 1.0, places=6)
        for piece in ring:
            self.assertGreater(_area3(piece), 0)   # 노멀 방향 유지(윗면 = +Z)
            self.assertGreater(cp.newell_normal(piece)[2], 0.99)

    def test_identical_contact_deletes_both(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (1, 0, 0), (2, 1, 1))
        out = cp.resolve(faces)
        self.assertEqual(out[("a", "right")], [])
        self.assertEqual(out[("b", "left")], [])

    def test_flush_cuts_larger_face_only(self):
        # 벽 앞면에 붙인 패널 — 패널 앞면(좁은 쪽)이 남고 벽 앞면에 구멍이 난다
        wall = _box_faces("wall", (0, 0, 0), (4, 0.3, 3))
        panel = _box_faces("panel", (1, 0, 1), (2, 0.1, 2))
        out = cp.resolve(wall + panel)
        self.assertNotIn(("panel", "front"), out)
        self.assertAlmostEqual(sum(_area3(p) for p in out[("wall", "front")]), 12.0 - 1.0, places=6)

    def test_flush_identical_keeps_one(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (0, 0, 0.5), (1, 1, 1))
        out = cp.resolve(faces)
        tops = [k for k in (("a", "top"), ("b", "top")) if k in out]
        self.assertEqual(len(tops), 1)
        self.assertEqual(out[tops[0]], [])

    def test_immutable_side_is_never_cut(self):
        base = _box_faces("inst", (0, 0, 0), (1, 1, 1), mutable=False)
        slab = _box_faces("slab", (0, 0, -0.5), (1, 1, 1))
        out = cp.resolve(base + slab)
        self.assertFalse(any(k[0] == "inst" for k in out))
        self.assertEqual(out[("slab", "top")], [])

    def test_same_island_is_ignored(self):
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + [
            Face(key="dup", island="a", points=[(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)])]
        self.assertEqual(cp.resolve(faces), {})

    def test_partial_overlap_leaves_no_overlap(self):
        faces = _box_faces("a", (0, 0, 0), (2, 2, 1)) + _box_faces("b", (1, 1, 1), (3, 3, 2))
        out = cp.resolve(faces)
        self.assertAlmostEqual(sum(_area3(p) for p in out[("a", "top")]), 3.0, places=6)
        self.assertAlmostEqual(sum(_area3(p) for p in out[("b", "bottom")]), 3.0, places=6)

    def test_pieces_share_vertices_without_t_junctions(self):
        # 가운데 구멍을 낸 조각들은 서로의 꼭짓점을 변 안에 품지 않아야 한다
        faces = _box_faces("a", (0, 0, 0), (3, 3, 1)) + _box_faces("b", (1, 1, 1), (2, 2, 2))
        ring = cp.resolve(faces)[("a", "top")]
        verts = {tuple(round(c, 6) for c in p) for piece in ring for p in piece}
        for piece in ring:
            corners = {tuple(round(c, 6) for c in p) for p in piece}
            for v in verts - corners:
                for i in range(len(piece)):
                    a, b = piece[i], piece[(i + 1) % len(piece)]
                    d = [b[k] - a[k] for k in range(3)]
                    w = [v[k] - a[k] for k in range(3)]
                    t = sum(d[k] * w[k] for k in range(3)) / sum(c * c for c in d)
                    off = [w[k] - d[k] * t for k in range(3)]
                    self.assertFalse(0 < t < 1 and sum(c * c for c in off) < 1e-10,
                                     f"T자 이음새: {v} on {a}-{b}")

    def test_scale_tolerance_catches_tiny_gap(self):
        # 부동소수점 오차 수준으로 떨어진 면도 같은 평면으로 본다
        faces = _box_faces("a", (0, 0, 0), (1, 1, 1)) + _box_faces("b", (0, 0, 1 + 1e-7), (1, 1, 2))
        out = cp.resolve(faces)
        self.assertEqual(out[("a", "top")], [])

    def test_rounding_boundary_does_not_split_plane(self):
        # 고정 칸 반올림이면 경계 양쪽으로 갈라지던 값 — 틈 기준 묶음은 같은 평면으로 본다
        tol = 1e-4 * 4
        z = tol * 2.5
        faces = _box_faces("a", (0, 0, z - 1), (1, 1, z - 1e-7)) + _box_faces("b", (0, 0, z + 1e-7), (1, 1, z + 1))
        out = cp.resolve(faces)
        self.assertEqual(out[("a", "top")], [])

    def test_concave_face_is_cut_through_triangles(self):
        l_shape = [(0, 0, 1), (2, 0, 1), (2, 1, 1), (1, 1, 1), (1, 2, 1), (0, 2, 1)]
        faces = [Face(key="L", island="a", points=l_shape)] + _box_faces("b", (0, 0, 1), (1, 1, 2))
        out = cp.resolve(faces)
        self.assertAlmostEqual(sum(_area3(p) for p in out["L"]), 2.0, places=6)
        self.assertEqual(out[("b", "bottom")], [])

    def test_untouched_concave_face_is_not_triangulated(self):
        l_shape = [(0, 0, 1), (2, 0, 1), (2, 1, 1), (1, 1, 1), (1, 2, 1), (0, 2, 1)]
        faces = [Face(key="L", island="a", points=l_shape)] + _box_faces("b", (5, 5, 1), (6, 6, 2))
        self.assertEqual(cp.resolve(faces), {})


if __name__ == "__main__":
    unittest.main()
