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
knife_plane = _geometry.knife_plane
knife_ring = _geometry.knife_ring
knife_bounds = _geometry.knife_bounds
fit_section = _geometry.fit_section
unfused_offset = _geometry.unfused_offset


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


    def test_knife_plane_contains_both_rays_in_perspective_and_ortho(self):
        # 원근: 같은 시점에서 나간 두 광선, 직교: 같은 방향의 평행 광선 — 둘 다 선과 보는 방향을 담는 평면이어야 한다
        eye = (0.0, -5.0, 0.0)
        a, b = _normalized((0.1, 1.0, -0.2)), _normalized((0.1, 1.0, 0.3))
        point, normal = knife_plane(eye, a, eye, b)
        self.assertAlmostEqual(sum(a[i] * normal[i] for i in range(3)), 0.0, places=9)
        self.assertAlmostEqual(sum(b[i] * normal[i] for i in range(3)), 0.0, places=9)
        point, normal = knife_plane((1.5, -5.0, -0.2), (0.0, 1.0, 0.0), (1.5, -5.0, 0.8), (0.0, 1.0, 0.0))
        self.assertAlmostEqual(abs(normal[0]), 1.0, places=9)
        self.assertIsNone(knife_plane(eye, a, eye, a))

    def test_knife_ring_keeps_the_user_tilt_and_picks_the_crossed_tube(self):
        # 나란한 두 다리를 비스듬한 평면이 함께 자를 때, 선이 가로지른 다리의 루프만 고르고 기울기는 그대로 둔다
        left = _tube(lambda z: 0.2, [z * 0.1 for z in range(21)])
        right = MeshData(tuple((x + 1.0, y, z) for x, y, z in left.vertices), left.faces)
        offset = len(left.vertices)
        legs = MeshData(left.vertices + right.vertices, left.faces + tuple(tuple(i + offset for i in f) for f in right.faces))
        tilt = math.radians(20.0)
        normal = (math.sin(tilt), 0.0, math.cos(tilt))
        point = (1.0, 0.0, 1.0)
        # 직교 뷰로 -y 에서 오른쪽 다리 앞면을 가로질러 그은 선의 히트
        hits = [(1.0 + x, -math.sqrt(max(0.0, 0.04 - x * x)), 1.0 - x * math.tan(tilt)) for x in (-0.15, -0.05, 0.05, 0.15)]
        loop, hit = knife_ring(legs, point, normal, hits)
        center = tuple(sum(p[i] for p in loop) / len(loop) for i in range(3))
        self.assertAlmostEqual(center[0], 1.0, delta=0.02)
        self.assertIn(hit, hits)
        for p in loop:  # 루프는 사용자가 정한 기울어진 평면 위에 있다
            self.assertAlmostEqual(sum((p[i] - point[i]) * normal[i] for i in range(3)), 0.0, places=6)

    def test_knife_ring_rejects_a_line_that_only_grazes_an_open_section(self):
        plane = MeshData(((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)), ((0, 1, 2), (0, 2, 3)))
        self.assertIsNone(knife_ring(plane, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), [(0.0, 0.0, 0.0)]))


    def test_knife_ring_keeps_only_the_dragged_span_of_a_fused_section(self):
        # 붙은 두 허벅지처럼 한 루프로 이어진 넓은 단면에서, 왼쪽 절반만 가로지른 선은 드래그 범위 밖을 잘라 낸다
        fused = _ellipse_tube(1.0, 0.2, [z * 0.1 for z in range(21)])
        ends = ((-1.2, -5.0, 1.0), (0.0, -5.0, 1.0))
        view = (0.0, 1.0, 0.0)
        point, normal = knife_plane(ends[0], view, ends[1], view)
        bounds = knife_bounds(ends[0], view, ends[1], view, normal)
        hits = [(x, -0.2 * math.sqrt(max(0.0, 1.0 - x * x)), 1.0) for x in (-0.9, -0.6, -0.3, -0.05)]
        loop, _hit = knife_ring(fused, point, normal, hits, bounds)
        xs = [p[0] for p in loop]
        self.assertAlmostEqual(max(xs), 0.0, places=6)
        self.assertAlmostEqual(min(xs), -1.0, delta=0.01)
        for p in loop:
            self.assertAlmostEqual(p[2], 1.0, places=6)
        whole, _hit = knife_ring(fused, point, normal, hits)
        self.assertAlmostEqual(max(p[0] for p in whole), 1.0, delta=0.01)
        # 옆면은 절단 평면 법선과 수직이라 축 오프셋 뒤에도 같은 범위를 자른다
        for origin, side in bounds:
            self.assertAlmostEqual(sum(side[i] * normal[i] for i in range(3)), 0.0, places=9)


    def test_unfused_offset_moves_a_clipped_knife_ring_to_where_the_section_fits_the_drag(self):
        # 위로 갈수록 굵어져 드래그 범위(|x| ≤ 0.3)를 넘는 관 — 붙은 두 허벅지처럼 범위로 잘린 자리는 아래로 옮긴다
        tube = _tube(lambda z: 0.2 + max(0.0, z - 1.0) * 0.5, [z * 0.05 for z in range(41)], around=48)
        view = (0.0, 1.0, 0.0)
        ends = ((-0.3, -5.0, 1.3), (0.3, -5.0, 1.3))
        _point, normal = knife_plane(ends[0], view, ends[1], view)
        bounds = knife_bounds(ends[0], view, ends[1], view, normal)
        axis = (0.0, 0.0, 1.0) if normal[2] > 0 else (0.0, 0.0, -1.0)
        shift = unfused_offset(tube, (0.0, 0.0, 1.3), axis, (0.0, -0.35, 1.3), 0.35, bounds)
        self.assertIsNotNone(shift)
        z = 1.3 + shift * axis[2]
        self.assertLess(z, 1.24)   # 반지름 0.315 까지는 범위 밖 여유(반지름 5%) 안이다
        self.assertGreater(z, 1.0)
        self.assertEqual(unfused_offset(tube, (0.0, 0.0, 0.5), axis, (0.0, -0.2, 0.5), 0.2, bounds), 0.0)

    def test_unfused_offset_keeps_moving_until_the_neighbour_is_clearance_away(self):
        # 두 다리가 z=1 에서 붙고 아래로 갈수록 벌어진다 — 막 떨어진 자리가 아니라 틈이 clearance 이상인 자리로 옮긴다
        def legs(sign):
            return _tube(lambda z: 0.2, [z * 0.02 for z in range(61)], around=32,
                         shape=lambda a: (math.cos(a), math.sin(a)))
        left, right = legs(-1), legs(1)
        def spread(v, sign):
            x, y, z = v
            gap = max(0.0, 1.0 - z) * 0.5          # z=1 에서 0, 아래로 벌어진다
            return (x + sign * (0.2 + gap * 0.5), y, z)
        verts = tuple(spread(v, -1) for v in left.vertices) + tuple(spread(v, 1) for v in right.vertices)
        offset = len(left.vertices)
        faces = left.faces + tuple(tuple(i + offset for i in f) for f in right.faces)
        mesh = MeshData(verts, faces)
        view = (0.0, 1.0, 0.0)
        # 선은 안쪽 허벅지 조금 앞(x=-0.05)에서 멈춰, 붙은 자리의 루프는 범위 밖으로 나간다
        ends = ((-0.8, -5.0, 1.1), (-0.05, -5.0, 1.1))
        _point, normal = knife_plane(ends[0], view, ends[1], view)
        bounds = knife_bounds(ends[0], view, ends[1], view, normal)
        axis = (0.0, 0.0, -1.0) if normal[2] < 0 else (0.0, 0.0, 1.0)
        center, hit = (-0.2, 0.0, 1.1), (-0.2, -0.2, 1.1)
        near = unfused_offset(mesh, center, axis, hit, 0.2, bounds)
        far = unfused_offset(mesh, center, axis, hit, 0.2, bounds, clearance=0.1)
        near_z, far_z = 1.1 + near * axis[2], 1.1 + far * axis[2]
        self.assertLess(far_z, near_z)
        self.assertLess(near_z, 0.86)   # 루프 오른쪽 끝 -0.25·(1-z) 가 범위(-0.05 + 여유 0.01) 안에 드는 높이
        self.assertLess(far_z, 0.76)    # 틈 0.5·(1-z) ≥ 0.1 이 앞뒤 ±0.05 평면에서도 성립하는 높이

    def test_fit_section_skips_a_plane_where_the_limb_merges_into_a_wider_part(self):
        # z ≥ 1 에서 단면이 반지름 0.5 로 합쳐지는 관: z=1.1 가이드(둘레 0.2 원)는 아래 떨어진 자리로 옮겨 자른다
        tube = _tube(lambda z: 0.2 if z < 0.99 else 0.5, [z * 0.05 for z in range(41)], around=48)
        guide_length = 2 * math.pi * 0.2
        shift, loop = fit_section(tube, (0.0, 0.0, 1.1), (0.0, 0.0, 1.0), 0.2, 0.02, guide_length)
        self.assertLess(shift, -0.1)
        self.assertGreaterEqual(shift, -0.2)
        self.assertAlmostEqual(perimeter(loop, True), guide_length, delta=0.02)
        shift, _loop = fit_section(tube, (0.0, 0.0, 0.5), (0.0, 0.0, 1.0), 0.2, 0.02, guide_length)
        self.assertEqual(shift, 0.0)


def _normalized(v):
    length = math.sqrt(sum(c * c for c in v))
    return tuple(c / length for c in v)


def _ellipse_tube(a: float, b: float, z_levels, around=32) -> MeshData:
    return _tube(lambda z: 1.0, z_levels, around=around, caps=True, shape=lambda angle: (a * math.cos(angle), b * math.sin(angle)))


if __name__ == "__main__":
    unittest.main()
