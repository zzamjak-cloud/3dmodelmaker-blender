# 시각 검토 자동 점검(짝 없는 파트·떠 있는 파트) 판정 테스트 — bpy 없이 실행
import importlib.util
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("review_diag", os.path.join(_ROOT, "core/review_diag.py"))
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)


def shell(center, size):
    return {"center": center, "size": size}


BODY = shell((0, 0, 0.8), (1.6, 3.6, 1.0))


class TestUnpaired(unittest.TestCase):
    def test_missing_headlamp_is_reported(self):
        # 실제 사고: 전조등을 미러하지 않는 목록에 넣어 +x 쪽 하나만 남았다
        lamp = shell((0.54, -1.85, 0.72), (0.34, 0.07, 0.22))
        found = diag.unpaired_shells([BODY, lamp])
        self.assertEqual(found, [lamp])

    def test_mirrored_pair_and_center_parts_are_fine(self):
        left = shell((-0.54, -1.85, 0.72), (0.34, 0.07, 0.22))
        right = shell((0.54, -1.85, 0.72), (0.34, 0.07, 0.22))
        badge = shell((0.0, -1.9, 0.7), (0.1, 0.03, 0.1))
        self.assertEqual(diag.unpaired_shells([BODY, left, right, badge]), [])

    def test_different_size_counterpart_does_not_count(self):
        right = shell((0.5, 0, 1), (0.3, 0.3, 0.3))
        tiny = shell((-0.5, 0, 1), (0.05, 0.05, 0.05))
        self.assertEqual(len(diag.unpaired_shells([right, tiny])), 2)


class TestFloating(unittest.TestCase):
    def test_detached_mirror_is_reported(self):
        mirror = shell((1.2, -0.7, 1.05), (0.2, 0.12, 0.14))   # 차체(x≤0.8)에서 떨어짐
        self.assertEqual(diag.floating_shells([BODY, mirror]), [mirror])

    def test_touching_and_grounded_parts_are_fine(self):
        handle = shell((0.82, 0.2, 0.9), (0.05, 0.18, 0.05))
        wheel = shell((0.8, -1.1, 0.34), (0.24, 0.7, 0.7))
        self.assertEqual(diag.floating_shells([BODY, handle, wheel]), [])


class TestDescribe(unittest.TestCase):
    def test_empty_when_clean(self):
        self.assertEqual(diag.describe([BODY]), [])

    def test_lists_coordinates(self):
        lines = diag.describe([BODY, shell((0.54, -1.85, 0.72), (0.34, 0.07, 0.22))])
        self.assertIn("좌우 짝이 없는 파트 1개", lines[0])
        self.assertIn("0.54, -1.85, 0.72", lines[1])


if __name__ == "__main__":
    unittest.main()


class TestTouchGraph(unittest.TestCase):
    def test_trim_inside_body_box_but_not_touching_is_floating(self):
        # 실제 사고: 해치 위 0.25m에 뜬 트림 — 상자로는 본체 상자 안이라 닿은 것으로 보였다
        trim = dict(shell((0, 1.42, 1.41), (1.28, 0.06, 0.06)), touches=[])
        body = dict(BODY, touches=[])
        self.assertEqual(diag.floating_shells([body, trim]), [trim])

    def test_chain_to_body_is_anchored(self):
        body = dict(BODY, touches=[1])
        stem = dict(shell((0.8, -0.6, 1.0), (0.16, 0.04, 0.04)), touches=[0, 2])
        head = dict(shell((0.95, -0.6, 1.0), (0.18, 0.12, 0.11)), touches=[1])
        self.assertEqual(diag.floating_shells([body, stem, head]), [])

    def test_degenerate_shells_are_ignored(self):
        body = dict(BODY, touches=[])
        dot = dict(shell((0.75, -1.3, 0.68), (0.0, 0.0, 0.0)), touches=[])
        self.assertEqual(diag.describe([body, dot]), [])
