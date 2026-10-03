# 엣지 선 가이드의 순수 계산(다시 찍기·자르기·중복 제거·틈 루프 판정·팁 분할)과 표면 경로
import importlib.util
import math
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, "lowpoly", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


edge_line = _load("edge_line")
geometry = _load("ring_geometry")


def _box(size=1.0, steps=8):
    """한 변 size, 면마다 steps×steps 격자로 나눈 정육면체 삼각 메시 (중심 원점)."""
    vertices, faces, index = [], [], {}

    def vid(p):
        key = tuple(round(c, 9) for c in p)
        if key not in index:
            index[key] = len(vertices)
            vertices.append(key)
        return index[key]

    h = size / 2.0
    for axis in range(3):
        for sign in (-1.0, 1.0):
            u, v = [a for a in range(3) if a != axis]
            for i in range(steps):
                for j in range(steps):
                    corners = []
                    for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        p = [0.0, 0.0, 0.0]
                        p[axis] = sign * h
                        p[u] = -h + size * (i + di) / steps
                        p[v] = -h + size * (j + dj) / steps
                        corners.append(vid(p))
                    if sign < 0:
                        corners.reverse()
                    faces.append((corners[0], corners[1], corners[2]))
                    faces.append((corners[0], corners[2], corners[3]))
    return geometry.MeshData(tuple(vertices), tuple(faces))


class ResampleTests(unittest.TestCase):
    def test_resample_keeps_ends_and_spacing(self):
        points = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0))
        sampled = edge_line.resample(points, 0.1)
        self.assertEqual(sampled[0], points[0])
        self.assertEqual(sampled[-1], points[-1])
        self.assertEqual(len(sampled), 21)
        steps = [math.dist(sampled[i], sampled[i + 1]) for i in range(len(sampled) - 1)]
        self.assertAlmostEqual(max(steps), 0.1, places=6)

    def test_closest_reports_arc_length(self):
        points = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0))
        distance, s, foot = edge_line.closest(points, (1.2, 0.5, 0.0))
        self.assertAlmostEqual(distance, 0.2, places=9)
        self.assertAlmostEqual(s, 1.5, places=9)
        self.assertAlmostEqual(foot[1], 0.5, places=9)

    def test_edge_lines_drop_short_lines(self):
        lines = edge_line.edge_lines([("짧음", [(0, 0, 0), (0.01, 0, 0)]), ("김", [(0, 0, 0), (1, 0, 0)])], 0.1)
        self.assertEqual([line.name for line in lines], ["김"])
        self.assertAlmostEqual(lines[0].reach, 0.1 * edge_line.REACH_RATIO)


class TrimTests(unittest.TestCase):
    def _line(self, a, b, edge=0.1):
        return edge_line.edge_lines([("선", [a, b])], edge)

    def test_trim_to_positive_half(self):
        line = self._line((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        pieces = edge_line.trim_lines(line, edge_line.plane_keep(0.2))
        self.assertEqual(len(pieces), 1)
        self.assertGreaterEqual(min(p[0] for p in pieces[0].points), 0.2 - 1e-9)
        self.assertAlmostEqual(pieces[0].points[-1][0], 1.0)

    def test_trim_splits_around_a_band_and_names_pieces(self):
        line = self._line((0.0, 0.0, 0.0), (0.0, 0.0, 2.0))
        pieces = edge_line.trim_lines(line, lambda p: not (0.9 < p[2] < 1.1))
        self.assertEqual([piece.name for piece in pieces], ["선#1", "선#2"])

    def test_trim_drops_tiny_leftovers(self):
        line = self._line((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        pieces = edge_line.trim_lines(line, lambda p: p[0] > 0.9)
        self.assertEqual(pieces, ())

    def test_reflect_and_dedupe_merge_symmetric_pair(self):
        right = self._line((0.3, 0.0, 0.0), (0.3, 0.0, 1.0))
        left = edge_line.reflect_lines(right)
        self.assertAlmostEqual(left[0].points[0][0], -0.3)
        both = right + edge_line.reflect_lines(left)
        self.assertEqual(len(edge_line.dedupe_lines(both)), 1)
        apart = right + self._line((0.6, 0.0, 0.0), (0.6, 0.0, 1.0))
        self.assertEqual(len(edge_line.dedupe_lines(apart)), 2)


class LoopTests(unittest.TestCase):
    def test_slit_loop_detection(self):
        lines = edge_line.edge_lines([("선", [(0, 0, 0), (1, 0, 0)])], 0.1)
        slit = [(x / 10.0, 0.03, 0.0) for x in range(11)] + [(x / 10.0, -0.03, 0.0) for x in range(11)]
        self.assertTrue(edge_line.is_slit_loop(slit, lines))
        hole = [(0.5 + 0.2 * math.cos(t), 0.2 * math.sin(t), 0.0) for t in (k * math.pi / 6 for k in range(12))]
        self.assertFalse(edge_line.is_slit_loop(hole, lines))

    def test_split_at_tips(self):
        # 틈 루프: 위쪽 사슬 0→4, 아래쪽 사슬 4→0 (정점 이름은 문자)
        ordered = ["t0", "u1", "u2", "u3", "t4", "d3", "d2", "d1"]
        params = [0.0, 1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 1.0]
        forward, backward = edge_line.split_at_tips(ordered, params)
        self.assertEqual(forward, ["t0", "u1", "u2", "u3", "t4"])
        self.assertEqual(backward, ["t0", "d1", "d2", "d3", "t4"])
        self.assertIsNone(edge_line.split_at_tips(["a", "b"], [1.0, 1.0]))


class SurfacePathTests(unittest.TestCase):
    def test_path_across_a_flat_face_is_straight(self):
        box = _box()
        a, b = (-0.3, -0.5, -0.2), (0.35, -0.5, 0.3)
        path = geometry.surface_path(box, a, b, (0.0, -1.0, 0.0), 0.05)
        self.assertIsNotNone(path)
        self.assertEqual(path[0], a)
        self.assertEqual(path[-1], b)
        for p in path:   # 앞면 위, a-b 직선 위
            self.assertAlmostEqual(p[1], -0.5, places=6)
        self.assertAlmostEqual(geometry.perimeter(path, False), math.dist(a, b), places=6)

    def test_path_wraps_over_a_box_edge(self):
        box = _box()
        a, b = (0.0, -0.5, 0.2), (0.3, -0.2, 0.5)   # 앞면 → 윗면
        normal = (0.0, -0.7071, 0.7071)
        path = geometry.surface_path(box, a, b, normal, 0.05)
        self.assertIsNotNone(path)
        for p in path:   # 모든 점이 표면(앞면 또는 윗면) 위
            self.assertTrue(abs(p[1] + 0.5) < 1e-6 or abs(p[2] - 0.5) < 1e-6, p)
        self.assertLess(geometry.perimeter(path, False), 1.0)   # 짧은 호 (반대편으로 돌지 않음)

    def test_path_none_when_points_are_off_the_surface(self):
        box = _box()
        self.assertIsNone(geometry.surface_path(box, (0.0, -2.0, 0.0), (0.2, -2.0, 0.0), (0, -1, 0), 0.05))


if __name__ == "__main__":
    unittest.main()


class ChainTests(unittest.TestCase):
    def test_merge_corner_lines_into_a_closed_square(self):
        # 사각형 외곽을 변 넷으로 나눠 그린 경우(모서리에서 끝점이 살짝 어긋남) — 한 닫힌 고리가 된다
        sides = [("a", [(0, 0, 0), (1, 0, 0)]), ("b", [(1.01, 0, 0), (1, 1, 0)]),
                 ("c", [(1, 1, 0), (0, 1, 0)]), ("d", [(0, 0.99, 0), (0, 0.01, 0)])]
        chains = edge_line.merge_chains(sides, 0.05)
        self.assertEqual(len(chains), 1)
        name, points, closed = chains[0]
        self.assertTrue(closed)
        self.assertEqual(sorted(name.split("+")), ["a", "b", "c", "d"])
        self.assertAlmostEqual(edge_line.length(points + [points[0]]), 4.0, delta=0.06)

    def test_merge_open_chain_and_reversed_piece(self):
        chains = edge_line.merge_chains([("a", [(0, 0, 0), (1, 0, 0)]), ("b", [(2, 0, 0), (1, 0, 0)])], 0.05)
        self.assertEqual(len(chains), 1)
        _name, points, closed = chains[0]
        self.assertFalse(closed)
        self.assertEqual({points[0], points[-1]}, {(0, 0, 0), (2, 0, 0)})

    def test_t_junction_is_not_merged(self):
        lines = [("a", [(0, 0, 0), (1, 0, 0)]), ("b", [(1, 0, 0), (2, 0, 0)]), ("c", [(1, 0, 0), (1, 1, 0)])]
        self.assertEqual(len(edge_line.merge_chains(lines, 0.05)), 3)

    def test_single_drawn_loop_is_closed(self):
        loop = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0.01, 0)]
        (_n, _p, closed), = edge_line.merge_chains([("loop", loop)], 0.05)
        self.assertTrue(closed)

    def test_edge_lines_marks_closed_and_repeats_first_point(self):
        square = [("sq", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0.02, 0)])]
        (line,) = edge_line.edge_lines(square, 0.2)
        self.assertTrue(line.closed)
        self.assertEqual(line.points[0], line.points[-1])

    def test_trim_closed_loop_rejoins_across_the_seam(self):
        square = edge_line.edge_lines([("sq", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0.02, 0)])], 0.1)
        # 오른쪽 변 가운데만 잘라 낸다 — 이음매(원점)를 지나는 한 조각이 남아야 한다
        pieces = edge_line.trim_lines(square, lambda p: not (p[0] > 0.9 and 0.4 < p[1] < 0.6))
        self.assertEqual(len(pieces), 1)
        self.assertFalse(pieces[0].closed)
        self.assertGreater(edge_line.length(pieces[0].points), 3.5)
        untouched = edge_line.trim_lines(square, lambda p: True)
        self.assertTrue(untouched[0].closed)
