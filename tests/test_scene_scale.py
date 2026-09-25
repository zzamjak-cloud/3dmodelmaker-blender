# 씬 규모·밀도·실내 모드 테스트 (Blender 없이 순수 파이썬으로 실행)
#
# 규모가 바닥 크기만 바꾸고 밀도·종류는 그대로여서 대형 씬이 허전했다.
# 규모마다 "무엇을 만드는가"와 배치 총량이 함께 정해지는지 확인한다.
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scene_plan = _load("scene_plan_scale", "core/scene_plan.py")
prompts = _load("prompts_scale", "core/prompts.py")


class TestSizeProfile(unittest.TestCase):
    def test_outdoor_sizes_narrower_than_zone(self):
        # 구역(M)보다 좁은 옥외 규모가 있어야 가게 한 채·캠프 한 곳을 만들 수 있다
        for size in ("SPOT", "SITE"):
            self.assertFalse(scene_plan.is_interior(size))
            self.assertLess(scene_plan.SCENE_SIZE_M[size], scene_plan.SCENE_SIZE_M["M"])
        self.assertLess(scene_plan.SCENE_SIZE_M["SPOT"], scene_plan.SCENE_SIZE_M["SITE"])
        self.assertLess(scene_plan.target_instances("SPOT"), scene_plan.target_instances("SITE"))
        self.assertLess(scene_plan.target_instances("SITE"), scene_plan.target_instances("M"))

    def test_small_outdoor_plan_keeps_size_and_gets_note(self):
        p = prompts.build_scene_plan_prompt("주유소 한 곳", "SITE", 0, 12)
        self.assertIn("소구역", p)
        self.assertIn("24m", p)
        self.assertIn(scene_plan.SIZE_PROFILE["SITE"]["note"], p)
        self.assertNotIn("지형(terrain)을 만들지 마라", p)

    def test_plan_normalize_accepts_new_sizes(self):
        plan = {"scene": {"size": "spot"}, "zones": [{"name": "a", "center": [0, 0], "extent": [5, 5]}],
                "assets": [{"key": "fire", "prompt": "모닥불", "count": 1, "size_class": "S", "zone": "a"}]}
        normalized, _warnings = scene_plan.normalize(plan, 0, 8)
        self.assertEqual(normalized["scene"]["size"], "SPOT")

    def test_small_is_interior_others_are_not(self):
        self.assertTrue(scene_plan.is_interior("S"))
        self.assertFalse(scene_plan.is_interior("M"))
        self.assertFalse(scene_plan.is_interior("L"))

    def test_unknown_size_falls_back_to_medium(self):
        self.assertEqual(scene_plan.size_profile("NOPE"), scene_plan.SIZE_PROFILE["M"])
        self.assertFalse(scene_plan.is_interior("NOPE"))

    def test_asset_kinds_grow_with_size(self):
        kinds = [scene_plan.size_profile(s)["max_assets"] for s in ("S", "M", "L")]
        self.assertEqual(kinds, sorted(kinds))
        self.assertLess(kinds[0], kinds[-1])

    def test_landmarks_grow_with_size(self):
        marks = [scene_plan.size_profile(s)["landmarks"] for s in ("S", "M", "L")]
        self.assertEqual(marks, sorted(marks))

    def test_target_instances_grow_with_area(self):
        # 이게 핵심 회귀 방지선이다 — 예전에는 규모가 커져도 개수 기준이 없어
        # 대형 씬이 중형과 비슷한 밀도로 깔려 텅 비어 보였다
        small = scene_plan.target_instances("S")
        medium = scene_plan.target_instances("M")
        large = scene_plan.target_instances("L")
        self.assertLess(small, medium)
        self.assertLess(medium, large)
        self.assertGreater(large, medium * 2)

    def test_interior_is_denser_per_area(self):
        # 실내는 면적당 밀도가 옥외보다 높아야 한다 (가구가 촘촘하다)
        self.assertGreater(scene_plan.SIZE_PROFILE["S"]["density"],
                           scene_plan.SIZE_PROFILE["M"]["density"])


class TestStyleTriScaling(unittest.TestCase):
    def test_default_scale_matches_old_values(self):
        # 스타일 배수 1.0은 스타일 도입 전과 같은 값이어야 한다
        self.assertEqual(scene_plan.asset_tri_limit("L"), 6000)
        self.assertEqual(scene_plan.asset_tri_limit("M"), 2500)
        self.assertEqual(scene_plan.asset_tri_limit("S"), 800)

    def test_scale_multiplies_limit(self):
        self.assertEqual(scene_plan.asset_tri_limit("L", 6.0), 36000)

    def test_bad_scale_is_ignored(self):
        self.assertEqual(scene_plan.asset_tri_limit("L", None), 6000)
        self.assertEqual(scene_plan.asset_tri_limit("L", "x"), 6000)

    def test_scale_never_goes_to_zero(self):
        self.assertGreater(scene_plan.asset_tri_limit("S", 0.0), 0)


class TestUnlimitedBudget(unittest.TestCase):
    def test_zero_budget_states_no_cap(self):
        p = prompts.build_scene_plan_prompt("대도시", "L", 0, 24)
        self.assertIn("상한 없음", p)
        self.assertNotIn("예산을 넘지 않게", p)

    def test_positive_budget_still_caps(self):
        p = prompts.build_scene_plan_prompt("대도시", "L", 80000, 24)
        self.assertIn("80000", p)
        self.assertIn("예산을 넘지 않게", p)

    def test_plan_normalize_skips_clamp_without_budget(self):
        plan = {
            "scene": {"size": "L", "palette": ["#111111", "#222222", "#333333"]},
            "terrain": {"relief": 0.2},
            "zones": [{"name": "yard", "center": [0, 0], "extent": [40, 40], "purpose": "광장"}],
            "assets": [{"key": "house", "prompt": "집", "count": 40, "size_class": "L",
                        "zone": "yard", "landmark": True}],
        }
        out, _warnings = scene_plan.normalize(plan, 0, 24)
        self.assertEqual(out["assets"][0]["count"], 40)  # 예산 상한이 없으면 깎지 않는다

    def test_plan_normalize_still_clamps_with_budget(self):
        plan = {
            "scene": {"size": "L", "palette": ["#111111", "#222222", "#333333"]},
            "terrain": {"relief": 0.2},
            "zones": [{"name": "yard", "center": [0, 0], "extent": [40, 40], "purpose": "광장"}],
            "assets": [
                {"key": "keep", "prompt": "성채", "count": 1, "size_class": "L",
                 "zone": "yard", "landmark": True},
                {"key": "house", "prompt": "집", "count": 40, "size_class": "L",
                 "zone": "yard", "landmark": False},
            ],
        }
        out, warnings = scene_plan.normalize(plan, 30000, 24)
        house = next(a for a in out["assets"] if a["key"] == "house")
        self.assertLess(house["count"], 40)
        self.assertTrue(any("예산" in w for w in warnings))


class TestInteriorPrompts(unittest.TestCase):
    def test_plan_prompt_forbids_terrain_indoors(self):
        p = prompts.build_scene_plan_prompt("빵집 내부", "S", 0, 10)
        self.assertIn("실내", p)
        self.assertIn("lp.room", p)
        self.assertIn("지형(terrain)을 만들지 마라", p)

    def test_plan_prompt_keeps_terrain_outdoors(self):
        p = prompts.build_scene_plan_prompt("성채", "L", 0, 24)
        self.assertIn("옥외 부지", p)
        self.assertNotIn("지형(terrain)을 만들지 마라", p)

    def test_place_prompt_uses_room_and_skips_ground_snap_indoors(self):
        manifest = [{"key": "shelf", "obj_name": "Shelf", "size": (1.0, 0.4, 2.0), "tri": 400}]
        plan = {"scene": {"size": "S"}, "assets": [], "zones": [], "rules": []}
        p = prompts.build_scene_place_prompt(plan, manifest, 0, scene_size="S")
        self.assertIn("lp.room", p)
        self.assertIn("ground_snap`은 실내에서 쓰지 마라", p)

    def test_place_prompt_keeps_ground_snap_outdoors(self):
        manifest = [{"key": "tree", "obj_name": "Tree", "size": (2.0, 2.0, 5.0), "tri": 900}]
        plan = {"scene": {"size": "M"}, "assets": [], "zones": [], "rules": []}
        p = prompts.build_scene_place_prompt(plan, manifest, 0, scene_size="M")
        self.assertIn("lp.terrain", p)
        self.assertIn("lp.ground_snap(모든 인스턴스 리스트, 지형", p)

    def test_place_prompt_takes_size_from_plan_when_not_given(self):
        plan = {"scene": {"size": "S"}, "assets": [], "zones": [], "rules": []}
        self.assertIn("lp.room", prompts.build_scene_place_prompt(plan, [], 0))


class TestFenceGuidance(unittest.TestCase):
    def test_place_prompt_routes_fences_away_from_wall_run(self):
        # 울타리를 wall_run으로 만들면 속이 꽉 찬 판때기가 된다
        plan = {"scene": {"size": "M"}, "assets": [], "zones": [], "rules": []}
        p = prompts.build_scene_place_prompt(plan, [], 0, scene_size="M")
        self.assertIn("lp.fence_run", p)
        self.assertIn("판때기", p)

    def test_scene_system_prompt_documents_fence_rule(self):
        with open(os.path.join(_ROOT, "prompts", "system_scene.md"), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("fence_run", md)
        self.assertIn("울타리에 `wall_run`을 쓰지 마라", md)

    def test_scene_api_exposes_fence_and_room(self):
        for name in ("fence_run", "room"):
            self.assertIn(name, prompts._scene_api_names())


class TestDensityGuidance(unittest.TestCase):
    def test_plan_prompt_states_instance_target(self):
        for size in ("S", "M", "L"):
            p = prompts.build_scene_plan_prompt("테스트", size, 0, 12)
            self.assertIn(str(scene_plan.target_instances(size)), p)
            self.assertIn("배치 총량 기준", p)

    def test_place_prompt_repeats_instance_target(self):
        plan = {"scene": {"size": "L"}, "assets": [], "zones": [], "rules": []}
        p = prompts.build_scene_place_prompt(plan, [], 0, scene_size="L")
        self.assertIn(str(scene_plan.target_instances("L")), p)


if __name__ == "__main__":
    unittest.main()
