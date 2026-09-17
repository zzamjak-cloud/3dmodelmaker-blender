# 배경 모드 플랜 파싱·정규화와 씬 컨셉 시트 프롬프트 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import json
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "scene_plan_pkg"


def _load_pkg(module_name: str):
    """core/ 를 가짜 패키지로 등록해 모듈 간 상대 임포트(from . import ...)를 살린 채 읽는다."""
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [os.path.join(_ROOT, "core")]
        sys.modules[_PKG] = pkg
    full = _PKG + "." + module_name
    spec = importlib.util.spec_from_file_location(full, os.path.join(_ROOT, "core", module_name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


scene_plan = _load_pkg("scene_plan")
sceneview = _load_pkg("sceneview")

_GOOD_PLAN = {
    "scene": {"size": "M", "palette": ["#6f7a5a", "#8a7a5c", "#b8ae95"], "mood": "삭막한 수용소"},
    "terrain": {"relief": 0.25, "style": "마른 흙바닥"},
    "zones": [
        {"name": "yard", "center": [0, 0], "extent": [26, 18], "purpose": "연병장"},
        {"name": "barracks", "center": [-12, 8], "extent": [14, 16], "purpose": "막사"},
    ],
    "assets": [
        {"key": "watchtower", "prompt": "감시탑", "count": 2, "size_class": "L",
         "zone": "yard", "landmark": True},
        {"key": "crate", "prompt": "나무 상자", "count": 8, "size_class": "S",
         "zone": "barracks", "landmark": False},
    ],
    "rules": ["중앙은 비운다"],
}


def _reply(plan, lang="json"):
    return "STATUS: PLAN\n\n```%s\n%s\n```\n" % (lang, json.dumps(plan, ensure_ascii=False))


class TestExtractAndParse(unittest.TestCase):
    def test_parse_tagged_block(self):
        plan = scene_plan.parse_plan(_reply(_GOOD_PLAN))
        self.assertEqual(plan["scene"]["size"], "M")
        self.assertEqual(len(plan["assets"]), 2)

    def test_parse_untagged_block(self):
        # 모델이 언어 태그를 자주 빠뜨린다 — 본문이 { 로 시작하면 받아준다
        plan = scene_plan.parse_plan(_reply(_GOOD_PLAN, lang=""))
        self.assertEqual(plan["zones"][0]["name"], "yard")

    def test_last_json_block_wins(self):
        text = _reply({"scene": {}, "zones": [], "assets": []}) + _reply(_GOOD_PLAN)
        self.assertEqual(len(scene_plan.parse_plan(text)["assets"]), 2)

    def test_no_block_raises(self):
        with self.assertRaises(scene_plan.PlanError):
            scene_plan.parse_plan("STATUS: PLAN\n설명만 있고 블록이 없다")

    def test_broken_json_raises(self):
        with self.assertRaises(scene_plan.PlanError):
            scene_plan.parse_plan("```json\n{\"scene\": }\n```")


class TestNormalizeRequired(unittest.TestCase):
    def test_missing_key_raises(self):
        for missing in ("scene", "zones", "assets"):
            plan = json.loads(json.dumps(_GOOD_PLAN))
            del plan[missing]
            with self.assertRaises(scene_plan.PlanError):
                scene_plan.normalize(plan, 80000, 12)

    def test_empty_assets_raises(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["assets"] = []
        with self.assertRaises(scene_plan.PlanError):
            scene_plan.normalize(plan, 80000, 12)

    def test_good_plan_passes_without_warnings(self):
        plan, warnings = scene_plan.normalize(json.loads(json.dumps(_GOOD_PLAN)), 80000, 12)
        self.assertEqual(warnings, [])
        self.assertEqual(plan["assets"][0]["key"], "watchtower")

    def test_input_is_not_mutated(self):
        source = json.loads(json.dumps(_GOOD_PLAN))
        source["assets"][1]["count"] = 400
        scene_plan.normalize(source, 20000, 12)
        self.assertEqual(source["assets"][1]["count"], 400)


class TestNormalizeFixes(unittest.TestCase):
    def test_palette_refilled_and_invalid_dropped(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["scene"]["palette"] = ["#6f7a5a", "빨강", "not-a-color"]
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        self.assertGreaterEqual(len(result["scene"]["palette"]), 3)
        for color in result["scene"]["palette"]:
            self.assertTrue(scene_plan._is_hex_color(color))
        self.assertTrue(any("팔레트" in w for w in warnings))

    def test_landmark_auto_assigned_to_first_large(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        for asset in plan["assets"]:
            asset["landmark"] = False
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        marked = [a["key"] for a in result["assets"] if a["landmark"]]
        self.assertEqual(marked, ["watchtower"])
        self.assertTrue(any("랜드마크" in w for w in warnings))

    def test_unknown_zone_moved_to_first_zone(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["assets"][1]["zone"] = "nowhere"
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        self.assertEqual(result["assets"][1]["zone"], "yard")
        self.assertTrue(any("구역" in w for w in warnings))

    def test_korean_key_becomes_ascii_slug(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["assets"][0]["key"] = "감시 탑"
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        key = result["assets"][0]["key"]
        self.assertTrue(key and key.isascii())
        self.assertTrue(any("슬러그" in w for w in warnings))

    def test_duplicate_keys_are_deduped(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["assets"][1]["key"] = "watchtower"
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        keys = [a["key"] for a in result["assets"]]
        self.assertEqual(len(set(keys)), 2)
        self.assertTrue(any("중복" in w for w in warnings))

    def test_bad_size_class_and_count_defaults(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["assets"][1]["size_class"] = "XL"
        plan["assets"][1]["count"] = 0
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        self.assertEqual(result["assets"][1]["size_class"], "M")
        self.assertEqual(result["assets"][1]["count"], 1)
        self.assertTrue(any("size_class" in w for w in warnings))

    def test_unknown_scene_size_becomes_m(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["scene"]["size"] = "XXL"
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        self.assertEqual(result["scene"]["size"], "M")
        self.assertTrue(any("규모" in w for w in warnings))

    def test_missing_terrain_gets_default(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        del plan["terrain"]
        result, _ = scene_plan.normalize(plan, 80000, 12)
        self.assertIn("relief", result["terrain"])


class TestBudgetAndLimits(unittest.TestCase):
    def test_estimated_tris_includes_terrain_reserve(self):
        # 2 x 6000 + 8 x 800 + 지형 여유 2000
        self.assertEqual(scene_plan.estimated_tris(_GOOD_PLAN),
                         2 * 6000 + 8 * 800 + scene_plan.TERRAIN_RESERVE_TRI)

    def test_asset_tri_limit(self):
        self.assertEqual(scene_plan.asset_tri_limit("L"), 6000)
        self.assertEqual(scene_plan.asset_tri_limit("s"), 800)
        self.assertEqual(scene_plan.asset_tri_limit(None), 2500)

    def test_over_budget_scales_down_non_landmark(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["assets"][1]["count"] = 60      # 60 x 800 = 48000
        result, warnings = scene_plan.normalize(plan, 20000, 12)
        landmark = next(a for a in result["assets"] if a["landmark"])
        prop = next(a for a in result["assets"] if not a["landmark"])
        self.assertEqual(landmark["count"], 2)          # 랜드마크는 건드리지 않는다
        self.assertLess(prop["count"], 60)
        self.assertGreaterEqual(prop["count"], 1)
        self.assertLessEqual(scene_plan.estimated_tris(result), 20000)
        self.assertTrue(any("예산" in w for w in warnings))

    def test_budget_too_small_keeps_at_least_one_and_warns(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        result, warnings = scene_plan.normalize(plan, 3000, 12)
        for asset in result["assets"]:
            self.assertGreaterEqual(asset["count"], 1)
        self.assertTrue(any("예산" in w for w in warnings))

    def test_max_assets_trims_keeping_landmark(self):
        plan = json.loads(json.dumps(_GOOD_PLAN))
        plan["assets"].append({"key": "barrel", "prompt": "드럼통", "count": 30,
                               "size_class": "S", "zone": "yard", "landmark": False})
        result, warnings = scene_plan.normalize(plan, 200000, 2)
        keys = [a["key"] for a in result["assets"]]
        self.assertEqual(len(keys), 2)
        self.assertIn("watchtower", keys)   # 랜드마크 우선
        self.assertIn("barrel", keys)       # 그다음은 count가 큰 순
        self.assertTrue(any("상한" in w for w in warnings))


class TestSceneviewPrompt(unittest.TestCase):
    def test_prompt_has_filename_and_layout(self):
        p = sceneview.build_prompt("포로 수용소", "M")
        self.assertIn(sceneview.SCENEVIEW_FILENAME, p)
        for needle in ("조감도", "탑다운", "40m", "SAVED"):
            self.assertIn(needle, p)
        self.assertNotIn("참조 이미지", p)

    def test_prompt_size_meters_follow_scene_size(self):
        self.assertIn("100m", sceneview.build_prompt("고대 성", "L"))
        self.assertIn("12m", sceneview.build_prompt("상점 내부", "S"))

    def test_interior_prompt_asks_for_cutaway_not_birdseye(self):
        # 실내는 조감도가 의미 없다 — 천장을 걷어낸 단면이라야 안이 보인다
        p = sceneview.build_prompt("상점 내부", "S")
        self.assertIn("컷어웨이", p)
        self.assertIn("평면도", p)
        self.assertNotIn("정사각형 부지", p)

    def test_ref_note_when_has_ref(self):
        self.assertIn("참조 이미지", sceneview.build_prompt("포로 수용소", "M", has_ref=True))

    def test_archive_prefix_differs_from_multiview(self):
        self.assertEqual(sceneview.ARCHIVE_PREFIX, "LP3D_sceneview_")


if __name__ == "__main__":
    unittest.main()
