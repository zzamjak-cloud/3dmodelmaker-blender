# 클릭 링 가이드의 순수 기하 계산(단면·축 추정·둘레 비율) — 3DRemesher-Blender 테스트 이식
import importlib.util
import math
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

_geometry = _load("ring_geometry")
MeshData = _geometry.MeshData
SectionMesh = _geometry.SectionMesh
estimate_ring = _geometry.estimate_ring
perimeter = _geometry.perimeter
resample_loop = _geometry.resample_loop
rim_ratio = _geometry.rim_ratio
section_loops = _geometry.section_loops
slice_ring = _geometry.slice_ring


def _tube(radius_of, z_levels, around=24, caps=True, shape=None) -> MeshData:
    """z 높이마다 radius_of(z) 반지름을 갖는 삼각 관. caps 면 위·아래를 막는다. shape 는 단위 단면 (x, y) 생성기."""
    vertices = []
    for z in z_levels:
        r = radius_of(z)
        for k in range(around):
            a = 2 * math.pi * k / around
            x, y = shape(a) if shape is not None else (math.cos(a), math.sin(a))
            vertices.append((r * x, r * y, z))
    faces = []
    for level in range(len(z_levels) - 1):
        for k in range(around):
            a = level * around + k
            b = level * around + (k + 1) % around
            faces.append((a, b, b + around))
            faces.append((a, b + around, a + around))
    if not caps:
        return MeshData(tuple(vertices), tuple(faces))
    bottom = len(vertices); vertices.append((0.0, 0.0, z_levels[0]))
    top = len(vertices); vertices.append((0.0, 0.0, z_levels[-1]))
    last = (len(z_levels) - 1) * around
    for k in range(around):
        faces.append((bottom, (k + 1) % around, k))
        faces.append((top, last + k, last + (k + 1) % around))
    return MeshData(tuple(vertices), tuple(faces))


class RingGeometryTests(unittest.TestCase):
    def test_section_of_cylinder_is_one_closed_loop_with_expected_perimeter(self):
        mesh = _tube(lambda z: 0.3, [z * 0.1 for z in range(21)])
        loops = section_loops(mesh, (0.0, 0.0, 1.05), (0.0, 0.0, 1.0))
        closed = [loop for loop in loops if loop[1]]
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(perimeter(closed[0][0], True), 2 * math.pi * 0.3, delta=0.02)
        self.assertLess(closed[0][2], 0.05)  # 수직 단면이라 면 법선이 축과 수직
        oblique = [loop for loop in section_loops(mesh, (0.0, 0.0, 1.05), (0.0, 0.6, 0.8)) if loop[1]]
        self.assertGreater(oblique[0][2], 0.3)

    def test_estimate_ring_finds_axis_and_radius_from_side_hit(self):
        mesh = _tube(lambda z: 0.3, [z * 0.1 for z in range(21)])
        estimate = estimate_ring(mesh, (0.3, 0.0, 1.0), (1.0, 0.0, 0.0), scale=2.0)
        self.assertIsNotNone(estimate)
        self.assertGreater(abs(estimate.axis[2]), 0.98)
        self.assertAlmostEqual(estimate.radius, 0.3, delta=0.01)
        self.assertAlmostEqual(estimate.thickness, 0.6, delta=0.01)
        self.assertAlmostEqual(estimate.center[2], 1.0, delta=0.02)

    def test_estimate_ring_on_short_wide_neck_still_picks_the_tube_axis(self):
        # 반지름 0.4, 높이 0.3 인 짧은 목이 위(머리)·아래(어깨)로 반지름 0.9 까지 벌어지는 형상.
        # 길이가 폭보다 짧아 PCA 라면 폭 방향을 축으로 잡지만, 최소 둘레·안정 단면은 z 축을 고른다
        def radius_of(z):
            if 0.5 <= z <= 0.8:
                return 0.4
            return 0.4 + 0.5 * min(1.0, (0.5 - z) / 0.3 if z < 0.5 else (z - 0.8) / 0.3)
        mesh = _tube(radius_of, [z * 0.05 for z in range(27)], around=32)
        estimate = estimate_ring(mesh, (0.4, 0.0, 0.65), (1.0, 0.0, 0.0), scale=1.8)
        self.assertIsNotNone(estimate)
        self.assertGreater(abs(estimate.axis[2]), 0.95)
        self.assertAlmostEqual(estimate.radius, 0.4, delta=0.02)

    def test_tapered_tube_is_cut_perpendicular_not_tilted_toward_the_thin_end(self):
        # 반지름이 0.15 → 0.45 로 벌어지는 원뿔대(팔뚝·반바지 통). 최소 둘레만 보면 가는 쪽으로 기운다
        mesh = _tube(lambda z: 0.15 + 0.15 * z, [z * 0.1 for z in range(21)], around=32)
        estimate = estimate_ring(mesh, (0.3, 0.0, 1.0), (0.99, 0.0, -0.15), scale=2.0)
        self.assertIsNotNone(estimate)
        self.assertGreater(abs(estimate.axis[2]), 0.995, estimate.axis)

    def test_rim_ratio_is_one_on_uniform_tube_and_low_at_a_step(self):
        uniform = _tube(lambda z: 0.3, [z * 0.1 for z in range(21)])
        self.assertGreater(rim_ratio(uniform, (0.0, 0.0, 1.0), (0.0, 0.0, 1.0), (0.3, 0.0, 1.0), 0.3, 0.05), 0.97)
        # 발목→발등처럼 z=0.9~1.0 사이에서 반지름이 3배로 뛰는 관 (띠 반폭 0.15 로 경사 구간 바깥을 잰다)
        stepped = _tube(lambda z: 0.3 if z < 1.0 else 0.9, [z * 0.1 for z in range(21)])
        self.assertLess(rim_ratio(stepped, (0.0, 0.0, 0.95), (0.0, 0.0, 1.0), (0.3, 0.0, 0.95), 0.3, 0.15), 0.5)

    def test_slice_and_resample_keep_perimeter(self):
        mesh = _tube(lambda z: 0.3, [z * 0.1 for z in range(21)])
        loop = slice_ring(mesh, (0.0, 0.0, 0.55), (0.0, 0.0, 1.0), (0.3, 0.0, 0.55), 0.3)
        self.assertIsNotNone(loop)
        resampled = resample_loop(loop, 16)
        self.assertEqual(len(resampled), 16)
        self.assertAlmostEqual(perimeter(resampled, True), perimeter(loop, True), delta=0.03)

    def test_pure_python_fallback_matches_numpy_path(self):
        mesh = _tube(lambda z: 0.3, [z * 0.1 for z in range(21)])
        fast = SectionMesh(mesh)
        slow = SectionMesh(mesh)
        slow.np = None
        for axis in ((0.0, 0.0, 1.0), (0.0, 0.6, 0.8), (1.0, 0.0, 0.0)):
            a = section_loops(fast, (0.0, 0.0, 1.05), axis)
            b = section_loops(slow, (0.0, 0.0, 1.05), axis)
            self.assertEqual([(len(l[0]), l[1]) for l in a], [(len(l[0]), l[1]) for l in b])
            self.assertAlmostEqual(sum(l[2] for l in a), sum(l[2] for l in b), places=9)

    def test_nonmanifold_junction_is_not_reported_as_closed(self):
        # 정점 0-1 엣지를 면 세 장이 공유하는 부채 — 단면이 접합점을 지나면 닫힌 링으로 오판하면 안 된다
        vertices = ((0.0, 0.0, -1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (-0.5, 0.87, 0.0), (-0.5, -0.87, 0.0))
        faces = ((0, 1, 2), (0, 1, 3), (0, 1, 4))
        loops = section_loops(MeshData(vertices, faces), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        self.assertTrue(all(not loop[1] for loop in loops))

    def test_flat_elliptical_limb_keeps_its_axis(self):
        # 장단축 3:1 타원 관의 납작한 면을 클릭해도 축은 z, 반지름은 장축이어야 한다
        mesh = _ellipse_tube(0.3, 0.1, [z * 0.1 for z in range(21)])
        estimate = estimate_ring(mesh, (0.0, 0.1, 1.0), (0.0, 1.0, 0.0), scale=2.0)
        self.assertIsNotNone(estimate)
        self.assertGreater(abs(estimate.axis[2]), 0.97)
        self.assertAlmostEqual(estimate.radius, 0.3, delta=0.02)

    def test_estimate_returns_none_when_no_loop_encloses_the_hit(self):
        plane = MeshData(((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)), ((0, 1, 2), (0, 2, 3)))
        self.assertIsNone(estimate_ring(plane, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), scale=2.0))


def _ellipse_tube(a: float, b: float, z_levels, around=32) -> MeshData:
    return _tube(lambda z: 1.0, z_levels, around=around, caps=True, shape=lambda angle: (a * math.cos(angle), b * math.sin(angle)))


if __name__ == "__main__":
    unittest.main()
