# 속 채우기(solid_fill)의 격자 계산 — 벽 찍기·바깥 흘려 넣기·닫힘·경계 사각형
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


sf = _load("solid_fill")


def box_shell(size=20, lo=4, hi=15, thickness=1):
    """lo~hi 칸 상자의 겉벽(두께 thickness)만 True 인 격자."""
    grid = np.zeros((size, size, size), bool)
    grid[lo:hi + 1, lo:hi + 1, lo:hi + 1] = True
    inner = slice(lo + thickness, hi + 1 - thickness)
    grid[inner, inner, inner] = False
    return grid


def signed_volume(verts, quads):
    tri = np.concatenate([quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]])
    a, b, c = verts[tri[:, 0]], verts[tri[:, 1]], verts[tri[:, 2]]
    return float(np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6.0)


class ExteriorTest(unittest.TestCase):
    def test_closed_shell_interior_is_not_exterior(self):
        wall = box_shell()
        outside = sf.exterior(~wall)
        self.assertFalse(outside[10, 10, 10])
        self.assertTrue(outside[0, 0, 0])

    def test_winding_corridor_is_reached(self):
        # 여러 번 꺾이는 통로 끝까지 번져야 한다 — 축 구간 단위 반복이 수렴하는지
        grid = np.ones((9, 9, 3), bool)
        grid[:, :, 1] = True
        empty = np.zeros_like(grid)
        path = [(1, y) for y in range(0, 8)] + [(x, 7) for x in range(1, 8)] + \
               [(7, y) for y in range(1, 8)] + [(x, 1) for x in range(3, 8)] + [(3, y) for y in range(1, 6)]
        for x, y in path:
            empty[x, y, 1] = True
        outside = sf.exterior(empty)
        self.assertTrue(outside[3, 5, 1])


class SolidifyTest(unittest.TestCase):
    def test_hollow_shell_is_filled(self):
        wall = box_shell()
        solid = sf.solidify(wall, close=0)
        self.assertTrue(solid[10, 10, 10])
        self.assertEqual(int(solid.sum()), 12 ** 3)

    def test_small_opening_is_closed(self):
        # 윗면에 2x2 칸 구멍 — 속 빈 껍질이 바깥과 이어진 셰이프 서버 메시의 찢김 입구
        wall = box_shell()
        wall[9:11, 9:11, 15] = False
        # 닫힘이 없어도 속 칸은 구멍 한 방향으로만 바깥이 보이므로 채워진다
        self.assertTrue(sf.solidify(wall, close=0)[10, 10, 10])
        solid = sf.solidify(wall, close=2)
        self.assertTrue(solid[10, 10, 10])
        self.assertGreaterEqual(int(solid.sum()), 12 ** 3 - 4)
        self.assertTrue(solid[9:11, 9:11, 14].all(), "입구 바로 밑은 막혀 있다")

    def test_wide_tear_does_not_leave_body_hollow(self):
        # 닫힘보다 훨씬 넓은 찢김(윗면 6x6) — 흘려 넣기는 속까지 바깥으로 보지만 속 칸은 위쪽으로만 뚫려 있다
        wall = box_shell()
        wall[7:13, 7:13, 15] = False
        solid = sf.solidify(wall, close=1)
        self.assertTrue(solid[10, 10, 10])
        self.assertTrue(solid[5, 5, 5], "찢김에서 먼 구석까지 찬다")

    def test_narrow_through_tunnel_in_body_is_filled(self):
        # 몸 앞뒤로 마주 난 찢김 사이 굴 — 굴 속 칸은 앞뒤 두 방향으로만 트여 몸속으로 본다
        wall = box_shell()
        wall[8:12, 4, 8:12] = False           # 앞 찢김
        wall[8:12, 15, 8:12] = False          # 마주 보는 뒤 찢김
        solid = sf.solidify(wall, close=0)
        self.assertTrue(solid[10, 10, 10])
        self.assertTrue(solid[5, 5, 5])

    def test_pocket_open_downward_wide_stays_open(self):
        # 다리 사이(반바지 자락 아래)처럼 앞뒤는 가려도 아래로 넓게 트인 공간은 대각 방향으로도 바깥이 보여 남는다
        wall = np.zeros((40, 30, 40), bool)
        wall[5:35, 5:25, 25:35] = True        # 엉덩이·반바지
        wall[5:13, 5:25, 5:25] = True         # 왼 다리
        wall[27:35, 5:25, 5:25] = True        # 오른 다리
        wall[13:27, 5, 12:25] = True          # 앞 자락
        wall[13:27, 24, 12:25] = True         # 뒤 자락
        solid = sf.solidify(wall, close=0)
        self.assertFalse(solid[20, 15, 15], "다리 사이는 채우지 않는다")

    def test_two_aligned_tears_leave_no_sealed_void(self):
        # 앞(-Y)·위(+Z) 찢김에 시선이 둘 다 걸친 칸 — 한 번만 판정하면 그 칸만 남아 몸속 밀폐 방울이 된다
        wall = box_shell()
        wall[8:12, 4, 8:12] = False
        wall[8:12, 8:12, 15] = False
        solid = sf.solidify(wall, close=0)
        sealed = ~solid & ~sf.exterior(~solid)
        self.assertEqual(int(sealed.sum()), 0)
        self.assertTrue(solid[10, 10, 10])

    def test_five_sided_pocket_is_filled(self):
        # 한 방향으로만 트인 오목부(벌린 입·턱 밑)는 채워진다 — 리토폴로지는 그 자리를 막힌 면으로 깐다
        wall = np.zeros((20, 20, 20), bool)
        wall[4:16, 4:16, 4:16] = True
        wall[8:12, 4:12, 8:12] = False     # -Y 쪽으로만 열린 주머니
        solid = sf.solidify(wall, close=0)
        self.assertTrue(solid[10, 8, 10])

    def test_escape_directions_counts_open_directions(self):
        wall = box_shell()
        counts = sf.escape_directions(wall)
        self.assertEqual(int(counts[10, 10, 10]), 0)
        self.assertGreaterEqual(int(counts[0, 0, 0]), 7)
        self.assertEqual(int(counts[4, 4, 4]), 0, "막힌 칸은 0")

class BoundaryQuadsTest(unittest.TestCase):
    def test_single_cell_is_closed_outward_cube(self):
        solid = np.zeros((3, 3, 3), bool)
        solid[1, 1, 1] = True
        verts, quads = sf.boundary_quads(solid, (0.0, 0.0, 0.0), 0.5)
        self.assertEqual(len(quads), 6)
        self.assertEqual(len(verts), 8)
        self.assertAlmostEqual(signed_volume(verts, quads), 0.125)

    def test_every_edge_is_shared_by_two_quads(self):
        solid = np.zeros((6, 6, 6), bool)
        solid[1:4, 1:5, 2:4] = True
        _verts, quads = sf.boundary_quads(solid, (0.0, 0.0, 0.0), 1.0)
        edges = np.sort(np.stack([quads, np.roll(quads, -1, axis=1)], axis=2).reshape(-1, 2), axis=1)
        _unique, counts = np.unique(edges, axis=0, return_counts=True)
        self.assertTrue((counts == 2).all())
        self.assertAlmostEqual(signed_volume(_verts, quads), 3 * 4 * 2)


class RasterizeTest(unittest.TestCase):
    def test_large_triangle_leaves_no_gap(self):
        tri = np.array([[[0.05, 0.05, 1.5], [9.9, 0.05, 1.5], [0.05, 9.9, 1.5]]])
        wall = sf.rasterize(tri, (0.0, 0.0, 0.0), 1.0, (10, 10, 3))
        expected = {(x, y) for x in range(10) for y in range(10) if x + y <= 8}
        marked = {(x, y) for x, y in np.argwhere(wall[:, :, 1])}
        self.assertTrue(expected <= marked)
        self.assertFalse(wall[:, :, 0].any() or wall[:, :, 2].any())


class SolidFromTrianglesTest(unittest.TestCase):
    @staticmethod
    def _box(lo, hi, flip=False):
        """노멀이 바깥(flip 이면 안)을 향하는 상자 삼각형 12개."""
        faces = []
        corners = np.array([[x, y, z] for x in (lo, hi) for y in (lo, hi) for z in (lo, hi)], float)
        quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        for quad in quads:
            tri = [corners[list(quad[:3])], corners[[quad[0], quad[2], quad[3]]]]
            faces += [t[::-1] if flip else t for t in tri]
        return faces

    def test_double_shell_becomes_one_solid(self):
        # 바깥 상자 + 4% 안쪽 상자 — 속 빈 이중 껍질인 셰이프 서버 메시의 축소판
        tris = np.array(self._box(0.0, 1.0) + self._box(0.04, 0.96, flip=True))
        normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        centers = tris.mean(axis=1)
        self.assertTrue((np.einsum('ij,ij->i', normals[:12], centers[:12] - 0.5) > 0).all(), "상자 노멀 방향 확인")
        verts, quads, pitch, close = sf.solid_from_triangles(tris, 0.02, 0.03)
        self.assertGreaterEqual(close, 1)
        volume = signed_volume(verts, quads)
        self.assertGreater(volume, 0.9, "속이 차야 한다 (껍질만이면 0.11 남짓)")
        self.assertLess(volume, 1.2)

    def test_inner_shell_faces_into_the_solid(self):
        # 바깥 상자 면은 바깥을, 4% 안쪽 상자(노멀이 몸속을 향함) 면은 채워진 속을 향한다
        tris = np.array(self._box(0.0, 1.0) + self._box(0.04, 0.96, flip=True))
        solid, origin, pitch, _close = sf.build_solid(tris, 0.02, 0.03)
        a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
        normals = np.cross(b - a, c - a)
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        outer = sf.facing_outside(solid, origin, pitch, (a + b + c) / 3, normals)
        self.assertTrue(outer[:12].all(), "바깥 상자 면")
        self.assertFalse(outer[12:].any(), "안쪽 껍질 면")

    def test_thin_flap_both_sides_face_outside(self):
        # 찢어진 천 자락 — 한 장짜리 판의 앞뒤 면 모두 바깥이다
        flap = np.array([[[0.2, 0.2, 0.5], [0.8, 0.2, 0.5], [0.8, 0.8, 0.5]],
                         [[0.2, 0.2, 0.5], [0.8, 0.8, 0.5], [0.8, 0.2, 0.5]]])
        solid, origin, pitch, _close = sf.build_solid(flap, 0.02, 0.03)
        centers = flap.mean(axis=1)
        outer = sf.facing_outside(solid, origin, pitch, centers, np.array([[0, 0, 1.0], [0, 0, -1.0]]))
        self.assertTrue(outer.all())

    def test_huge_triangle_is_split_without_gaps(self):
        # 한 변이 MAX_STEPS 칸을 훨씬 넘는 삼각형 — 쪼개서 찍어도 틈 없이 덮여야 한다
        size = sf.MAX_STEPS * 3
        tri = np.array([[[0.01, 0.01, 1.5], [size - 0.1, 0.01, 1.5], [0.01, size - 0.1, 1.5]]])
        wall = sf.rasterize(tri, (0.0, 0.0, 0.0), 1.0, (size, size, 3))
        diagonal = [(x, size - 2 - x) for x in range(0, size - 1, 7)]
        self.assertTrue(all(wall[x, y, 1] for x, y in diagonal))
        self.assertTrue(wall[:, :, 1][np.add.outer(np.arange(size), np.arange(size)) <= size - 3].all())

    def test_grid_is_capped(self):
        origin, shape, pitch, close = sf.plan_grid((0, 0, 0), (10, 10, 10), 0.001, 0.05, max_cells=100_000)
        self.assertLessEqual(int(np.prod(shape)), 100_000)
        self.assertGreater(pitch, 0.001)
        self.assertEqual(close, max(1, round(0.05 / pitch)))
        # 여백이 닫힘 칸 수보다 넓다
        self.assertLessEqual(origin[0], -pitch * (close + sf.PAD) + 1e-9)


if __name__ == "__main__":
    unittest.main()
