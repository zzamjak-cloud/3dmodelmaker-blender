# LOOP 절단 링의 순수 계산(평면 피팅·미러·브리지 DP·경계 체인) — 3DRemesher-Blender 테스트 이식
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

_cut = _load("ring_cut")
GAP_CLOSE_RATIO = _cut.GAP_CLOSE_RATIO
RingCut = _cut.RingCut
_chains = _cut._chains
bridge_steps = _cut.bridge_steps
crowded_pairs = _cut.crowded_pairs
mirror_cuts = _cut.mirror_cuts
ring_cuts = _cut.ring_cuts


def _ring(count: int, radius: float, z: float, phase: float = 0.0):
    return [(radius * math.cos(2 * math.pi * k / count + phase), radius * math.sin(2 * math.pi * k / count + phase), z) for k in range(count)]


class RingCutTests(unittest.TestCase):
    def test_loop_guide_becomes_plane_with_center_normal_and_radius(self):
        points = tuple((1.3, 0.3 * math.cos(t), 0.3 * math.sin(t)) for t in [2 * math.pi * k / 16 for k in range(16)])

        cuts = ring_cuts([("LP3D_Ring_arm", points)], 0.1)

        self.assertEqual(len(cuts), 1)
        cut = cuts[0]
        self.assertEqual(cut.name, "LP3D_Ring_arm")
        self.assertAlmostEqual(cut.center[0], 1.3)
        self.assertAlmostEqual(abs(cut.normal[0]), 1.0, places=6)
        self.assertAlmostEqual(cut.radius, 0.3, places=6)
        self.assertAlmostEqual(cut.half_width, 0.05)
        self.assertAlmostEqual(cut.signed_distance((1.4, 0.0, 0.0)) * cut.normal[0], 0.1)
        self.assertTrue(cut.within((1.3, 0.42, 0.0), 1.5))
        self.assertFalse(cut.within((1.3, 0.5, 0.0), 1.5))

    def test_degenerate_loops_are_skipped(self):
        line = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))
        self.assertEqual(ring_cuts([("flat", line), ("short", line[:2])], 0.1), ())

    def test_crowded_rings_on_the_same_axis_are_reported(self):
        low = RingCut("low", (0.0, 0.0, 1.19), (0.0, 0.0, 1.0), 0.2, 0.02, ())
        near = RingCut("near", (0.0, 0.0, 1.25), (0.0, 0.0, 1.0), 0.2, 0.02, ())
        far = RingCut("far", (0.0, 0.0, 1.60), (0.0, 0.0, 1.0), 0.2, 0.02, ())
        self.assertEqual([(a, b) for a, b, _gap in crowded_pairs((low, near, far))], [("low", "near")])

    def test_bridge_uses_exactly_the_unavoidable_triangles_for_closed_rings(self):
        a = _ring(19, 1.0, 0.0)
        b = _ring(18, 1.0, 0.1)
        cost, steps = bridge_steps(a, b, True, penalty=4.0 * (2 * math.pi / 18))

        kinds = [kind for _i, _j, kind in steps]
        self.assertEqual(kinds.count("quad"), 18)
        self.assertEqual(len(kinds) - kinds.count("quad"), 1)
        self.assertTrue(math.isfinite(cost))

    def test_bridge_of_equal_rings_is_all_quads(self):
        a = _ring(12, 1.0, 0.0)
        b = _ring(12, 1.0, 0.1, phase=0.1)
        _cost, steps = bridge_steps(a, b, True, penalty=1.0)

        self.assertTrue(all(kind == "quad" for _i, _j, kind in steps))
        self.assertEqual(len(steps), 12)

    def test_bridge_of_open_chains_consumes_every_vertex(self):
        a = [(x, 0.0, 0.0) for x in range(6)]
        b = [(x * 5.0 / 3.0, 1.0, 0.0) for x in range(4)]
        _cost, steps = bridge_steps(a, b, False, penalty=1.0)

        kinds = [kind for _i, _j, kind in steps]
        self.assertEqual(kinds.count("quad"), 3)
        self.assertEqual(kinds.count("a"), 2)
        self.assertEqual(kinds.count("b"), 0)

    def test_negative_side_loop_is_mirrored_to_positive_side_and_duplicates_merge(self):
        left = RingCut("left", (-1.3, 0.0, 0.0), (-1.0, 0.0, 0.0), 0.3, 0.05, tuple(_ring(8, 0.3, 0.0)))
        left = RingCut(left.name, left.center, left.normal, left.radius, left.half_width, tuple((-1.3, y, z) for _x, y, z in _ring(8, 0.3, 0.0)))
        right = RingCut("right", (1.3, 0.0, 0.0), (1.0, 0.0, 0.0), 0.3, 0.05, tuple((1.3, y, z) for _x, y, z in _ring(8, 0.3, 0.0)))
        straddling = RingCut("neck", (0.0, 0.0, 1.2), (0.0, 0.0, 1.0), 0.2, 0.05, tuple((x, y, 1.2) for x, y, _z in _ring(8, 0.2, 0.0)))

        mirrored = mirror_cuts((left,), ("X",))
        self.assertEqual(len(mirrored), 1)
        self.assertAlmostEqual(mirrored[0].center[0], 1.3)
        self.assertTrue(all(p[0] > 0.0 for p in mirrored[0].points))
        # 양의 쪽에 이미 같은 루프가 있으면 하나만 남고, 대칭면을 가로지르는 루프는 그대로 둔다
        merged = mirror_cuts((left, right, straddling), ("X",))
        self.assertEqual([cut.name for cut in merged], ["left", "neck"])
        self.assertEqual(mirror_cuts((left,), ()), (left,))

    def test_two_rings_on_the_same_axis_are_kept_apart(self):
        # 목 아래·위처럼 같은 축 위에 0.07 떨어진 링 둘은 중복이 아니다 (반지름 0.2, 띠 반폭 0.02)
        low = RingCut("low", (0.0, 0.0, 1.19), (0.0, 0.0, 1.0), 0.2, 0.02, tuple((x, y, 1.19) for x, y, _z in _ring(8, 0.2, 0.0)))
        high = RingCut("high", (0.0, 0.0, 1.26), (0.0, 0.0, 1.0), 0.22, 0.02, tuple((x, y, 1.26) for x, y, _z in _ring(8, 0.22, 0.0)))
        same = RingCut("same", (0.01, 0.0, 1.20), (0.0, 0.0, 1.0), 0.2, 0.02, low.points)
        self.assertEqual([c.name for c in mirror_cuts((low, high), ("X",))], ["low", "high"])
        self.assertEqual([c.name for c in mirror_cuts((low, same), ("X",))], ["low"])

    def test_left_and_right_clicks_merge_after_mirroring_even_if_slightly_apart(self):
        # 좌우 허벅지를 따로 클릭하면 축 방향으로 0.028 어긋난다 (반지름 0.14, 띠 반폭 0.009)
        right = RingCut("R", (0.11, 0.0, 0.65), (0.0, 0.0, 1.0), 0.14, 0.009, tuple((0.11 + x, y, 0.65) for x, y, _z in _ring(8, 0.14, 0.0)))
        left = RingCut("L", (-0.115, 0.0, 0.678), (0.0, 0.0, 1.0), 0.14, 0.009, tuple((-0.115 - x, y, 0.678) for x, y, _z in _ring(8, 0.14, 0.0)))
        self.assertEqual([c.name for c in mirror_cuts((right, left), ("X",))], ["R"])

    def test_chain_with_one_small_gap_is_treated_as_closed_ring(self):
        ring = _ring(12, 1.0, 0.0)
        verts = [_Vert(p) for p in ring]
        edges = [_Edge(verts[i], verts[(i + 1) % 12]) for i in range(12)]
        closed = _chains(edges)
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0][1])
        # 엣지 하나가 빠진 링(작은 구멍)은 끝점이 엣지 하나 거리라 닫힘으로 승격된다
        gapped = _chains(edges[1:])
        self.assertEqual(len(gapped), 1)
        self.assertTrue(gapped[0][1])
        self.assertEqual(len(gapped[0][0]), 12)
        # 끝점이 평균 엣지의 GAP_CLOSE_RATIO 배보다 멀면 열린 호로 남는다
        arc = _chains(edges[3:])
        self.assertEqual(len(arc), 1)
        self.assertFalse(arc[0][1])
        self.assertGreater(math.dist(ring[3], ring[0]), GAP_CLOSE_RATIO * (2 * math.pi / 12) * 0.9)

    def test_small_hole_sharing_a_ring_vertex_is_detached(self):
        ring = _ring(12, 1.0, 0.0)
        verts = [_Vert(p) for p in ring]
        edges = [_Edge(verts[i], verts[(i + 1) % 12]) for i in range(12)]
        # 정점 0 에 붙은 4정점 구멍(정점 0 → h1 → h2 → h3 → 정점 0)
        hole = [_Vert((1.2, 0.1, 0.0)), _Vert((1.3, 0.0, 0.0)), _Vert((1.2, -0.1, 0.0))]
        edges += [_Edge(verts[0], hole[0]), _Edge(hole[0], hole[1]), _Edge(hole[1], hole[2]), _Edge(hole[2], verts[0])]
        chains = _chains(edges)
        self.assertEqual(len(chains), 1)
        self.assertTrue(chains[0][1])
        self.assertEqual(len(chains[0][0]), 12)


class _Vert:
    def __init__(self, co):
        self.co = co


class _Edge:
    def __init__(self, a, b):
        self.verts = (a, b)


if __name__ == "__main__":
    unittest.main()
