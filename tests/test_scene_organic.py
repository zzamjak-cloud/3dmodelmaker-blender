# 비정형 부지·유기적 배치 플랜 스키마 테스트 (Blender 없이 순수 파이썬으로 실행)
#
# "대도시가 항상 정사각형이고 배치가 너무 규칙적"이라는 지적에서 나왔다.
# 플랜에 extent/outline/rotation이 살아서 배치 턴까지 전달되는지 확인한다.
import importlib.util
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "scene_organic_pkg"


def _load_pkg(module_name: str):
    """core/ 를 가짜 패키지로 등록해 모듈 간 상대 임포트(from . import ...)를 살린 채 읽는다."""
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [os.path.join(_ROOT, "core")]
        sys.modules[_PKG] = pkg
    full = _PKG + "." + module_name
    spec = importlib.util.spec_from_file_location(
        full, os.path.join(_ROOT, "core", module_name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


scene_plan = _load_pkg("scene_plan")
prompts = _load_pkg("prompts")
sceneview = _load_pkg("sceneview")


def _plan(**scene_extra):
    scene = {"size": "L", "palette": ["#111111", "#222222", "#333333"]}
    scene.update(scene_extra)
    return {
        "scene": scene,
        "terrain": {"relief": 0.3},
        "zones": [{"name": "town", "center": [0, 0], "extent": [40, 30], "rotation": 17,
                   "purpose": "마을"}],
        "assets": [{"key": "house", "prompt": "집", "count": 10, "size_class": "M",
                    "zone": "town", "landmark": True}],
    }


class TestExtent(unittest.TestCase):
    def test_missing_extent_defaults_to_size_square(self):
        out, _ = scene_plan.normalize(_plan(), 0, 24)
        self.assertEqual(out["scene"]["extent"], [100.0, 100.0])

    def test_non_square_extent_is_kept(self):
        out, _ = scene_plan.normalize(_plan(extent=[120, 60]), 0, 24)
        self.assertEqual(out["scene"]["extent"], [120.0, 60.0])

    def test_oversized_extent_is_scaled_down_keeping_ratio(self):
        # 규모는 참고 크기라 넓은 부지도 받되, 터무니없는 값(참고의 4배 초과)만 막는다
        out, warnings = scene_plan.normalize(_plan(extent=[1000, 500]), 0, 24)
        w, h = out["scene"]["extent"]
        self.assertLessEqual(max(w, h), 100 * 4 + 1e-6)
        self.assertAlmostEqual(w / h, 2.0, places=1)
        self.assertTrue(any("extent" in x for x in warnings))

    def test_small_extent_is_kept_for_real_size(self):
        # 규모 한 변은 참고 크기다 — 작은 부지를 규모 크기로 부풀리면 가구가 넓은 바닥에 흩어진다
        out, _ = scene_plan.normalize(_plan(extent=[5, 5]), 0, 24)
        self.assertEqual(out["scene"]["extent"], [5.0, 5.0])

    def test_degenerate_extent_is_lifted(self):
        out, _ = scene_plan.normalize(_plan(extent=[0.3, 0.3]), 0, 24)
        self.assertGreaterEqual(min(out["scene"]["extent"]), 2.4)


class TestOutline(unittest.TestCase):
    def test_outline_points_are_kept_and_rounded(self):
        pts = [[-30.04, -18], [-8, -26], [24, -20], [34, 4], [18, 22], [-12, 26]]
        out, _ = scene_plan.normalize(_plan(outline=pts), 0, 24)
        self.assertEqual(out["scene"]["outline"][0], [-30.0, -18.0])
        self.assertEqual(len(out["scene"]["outline"]), 6)

    def test_missing_outline_is_empty_list(self):
        out, _ = scene_plan.normalize(_plan(), 0, 24)
        self.assertEqual(out["scene"]["outline"], [])

    def test_bad_points_are_dropped(self):
        out, _ = scene_plan.normalize(_plan(outline=[[0, 0], "x", [10, 0], [None, 3], [0, 10]]),
                                      0, 24)
        self.assertEqual(out["scene"]["outline"], [[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])

    def test_too_few_points_are_discarded_with_warning(self):
        out, warnings = scene_plan.normalize(_plan(outline=[[0, 0], [1, 1]]), 0, 24)
        self.assertEqual(out["scene"]["outline"], [])
        self.assertTrue(any("outline" in x for x in warnings))

    def test_too_many_points_are_truncated(self):
        pts = [[i, i] for i in range(30)]
        out, warnings = scene_plan.normalize(_plan(outline=pts), 0, 24)
        self.assertEqual(len(out["scene"]["outline"]), 16)
        self.assertTrue(any("16점" in x for x in warnings))


class TestZoneRotation(unittest.TestCase):
    def test_rotation_is_kept(self):
        out, _ = scene_plan.normalize(_plan(), 0, 24)
        self.assertEqual(out["zones"][0]["rotation"], 17.0)

    def test_missing_rotation_defaults_to_zero(self):
        plan = _plan()
        del plan["zones"][0]["rotation"]
        out, _ = scene_plan.normalize(plan, 0, 24)
        self.assertEqual(out["zones"][0]["rotation"], 0.0)

    def test_rotation_is_wrapped_to_half_turn(self):
        plan = _plan()
        plan["zones"][0]["rotation"] = 370
        out, _ = scene_plan.normalize(plan, 0, 24)
        self.assertEqual(out["zones"][0]["rotation"], 10.0)


class TestPromptsDiscourageRegularity(unittest.TestCase):
    def test_plan_prompt_forbids_square_outdoors(self):
        p = prompts.build_scene_plan_prompt("대도시", "L", 0, 24)
        self.assertIn("정사각형으로 잡지 마라", p)
        self.assertIn("scene.outline", p)
        self.assertIn("rotation", p)

    def test_plan_prompt_does_not_push_outline_indoors(self):
        p = prompts.build_scene_plan_prompt("상점 내부", "S", 0, 10)
        self.assertNotIn("scene.outline", p)

    def test_place_prompt_requires_meander_and_cluster_outdoors(self):
        plan = {"scene": {"size": "L"}, "assets": [], "zones": [], "rules": []}
        p = prompts.build_scene_place_prompt(plan, [], 0, scene_size="L")
        for needle in ("lp.meander", "lp.place_cluster", "terrain(outline=", "규칙적으로 보이면 실패"):
            self.assertIn(needle, p)

    def test_place_prompt_skips_organic_block_indoors(self):
        plan = {"scene": {"size": "S"}, "assets": [], "zones": [], "rules": []}
        p = prompts.build_scene_place_prompt(plan, [], 0, scene_size="S")
        self.assertNotIn("규칙적으로 보이면 실패", p)

    def test_sceneview_prompt_forbids_square(self):
        p = sceneview.build_prompt("대도시", "L")
        self.assertIn("정사각형으로 그리지 마라", p)
        self.assertNotIn("정사각형 부지", p)

    def test_scene_api_exposes_new_helpers(self):
        names = prompts._scene_api_names()
        for name in ("meander", "place_cluster"):
            self.assertIn(name, names)

    def test_system_prompt_documents_regularity_rule(self):
        with open(os.path.join(_ROOT, "prompts", "system_scene.md"), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("규칙성은 결함이다", md)
        self.assertIn('"extent"', md)
        self.assertIn('"outline"', md)
        self.assertIn("lp.meander", md)
        self.assertIn("lp.place_cluster", md)
        # 예시 코드가 더는 정사각 둘레·격자를 가르치지 않는다
        self.assertNotIn("place_grid(lp.kit(\"house\")", md)


if __name__ == "__main__":
    unittest.main()
