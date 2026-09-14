# 배경 세션의 키트 집계 산술 테스트 (Blender 없이 순수 파이썬으로 실행)
#
# 자식 에셋 집계·중단 임계·키트 명단 산출은 세션이 자식 수십 개를 거느린 상태에서만
# 드러나는 로직이라 수동으로 재현하기 어렵다. bpy 무의존 함수로 떼어낸 core/scene_kit.py를
# 여기서 고정한다.
import importlib.util
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "scene_kit_pkg"


def _load_pkg(module_name: str):
    """core/ 를 가짜 패키지로 등록해 모듈 간 상대 임포트를 살린 채 읽는다."""
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


def _load_persist():
    """persist는 모듈 최상위에서 bpy를 import한다 — 껍데기만 넣어 읽는다."""
    if "bpy" not in sys.modules:
        bpy = types.ModuleType("bpy")
        bpy.utils = types.SimpleNamespace(user_resource=lambda kind: "")
        sys.modules["bpy"] = bpy
    return _load_pkg("persist")


scene_kit = _load_pkg("scene_kit")
scene_plan = _load_pkg("scene_plan")
persist = _load_persist()

_PLAN = {
    "scene": {"size": "M", "palette": ["#6f7a5a", "#8a7a5c", "#b8ae95"]},
    "zones": [{"name": "yard", "center": [0, 0], "extent": [20, 20], "purpose": "연병장"}],
    "assets": [
        {"key": "watchtower", "prompt": "감시탑", "count": 2, "size_class": "L",
         "zone": "yard", "landmark": True},
        {"key": "barrel", "prompt": "드럼통", "count": 6, "size_class": "S",
         "zone": "yard", "landmark": False},
    ],
}


class TestKitVerdict(unittest.TestCase):
    """자식이 하나씩 끝날 때마다 부모가 내리는 판정."""

    def test_running_until_every_child_reports(self):
        verdict, _ = scene_kit.kit_verdict(4, [1], {1: True, 2: True})
        self.assertEqual(verdict, 'RUNNING')

    def test_done_when_all_children_reported(self):
        verdict, reason = scene_kit.kit_verdict(3, [1], {1: True, 2: True, 3: False})
        self.assertEqual(verdict, 'DONE')
        self.assertEqual(reason, "")

    def test_failure_over_half_aborts_immediately(self):
        # 4종 중 3종 실패 — 나머지 1종을 기다릴 이유가 없다
        verdict, reason = scene_kit.kit_verdict(4, [1], {1: True, 2: False, 3: False, 4: False})
        self.assertEqual(verdict, 'FAILED')
        self.assertIn("절반", reason)

    def test_exactly_half_failed_is_not_abort(self):
        verdict, _ = scene_kit.kit_verdict(4, [1], {1: True, 2: True, 3: False, 4: False})
        self.assertEqual(verdict, 'DONE')

    def test_all_landmarks_failed_aborts(self):
        verdict, reason = scene_kit.kit_verdict(6, [1, 2], {1: False, 2: False})
        self.assertEqual(verdict, 'FAILED')
        self.assertIn("랜드마크", reason)

    def test_one_surviving_landmark_keeps_going(self):
        verdict, _ = scene_kit.kit_verdict(6, [1, 2], {1: False, 2: True})
        self.assertEqual(verdict, 'RUNNING')

    def test_pending_landmark_is_not_counted_as_failed(self):
        verdict, _ = scene_kit.kit_verdict(6, [1, 2], {1: False})
        self.assertEqual(verdict, 'RUNNING')


class TestKitNaming(unittest.TestCase):
    def test_unique_name_passes_through(self):
        self.assertEqual(scene_kit.unique_name("barrel", set()), "barrel")

    def test_unique_name_suffixes_on_collision(self):
        taken = {"barrel", "barrel_2"}
        self.assertEqual(scene_kit.unique_name("barrel", taken), "barrel_3")

    def test_blank_name_falls_back(self):
        self.assertEqual(scene_kit.unique_name("", set()), "asset")

    def test_status_text_format(self):
        self.assertEqual(scene_kit.kit_status_text(3, 9), "에셋 키트 생성 3/9")


class TestManifest(unittest.TestCase):
    def test_bbox_size_from_corners(self):
        corners = [(-1.0, -0.5, 0.0), (1.0, 0.5, 2.0), (0.0, 0.0, 1.0)]
        self.assertEqual(scene_kit.bbox_size(corners), (2.0, 1.0, 2.0))

    def test_bbox_size_of_nothing_is_zero(self):
        self.assertEqual(scene_kit.bbox_size([]), (0.0, 0.0, 0.0))

    def test_manifest_entry_shape_matches_place_prompt(self):
        entry = scene_kit.manifest_entry("barrel", "barrel.001", (0.6, 0.6, 0.9), 220)
        self.assertEqual(entry["key"], "barrel")
        self.assertEqual(entry["obj_name"], "barrel.001")
        self.assertEqual(entry["tri"], 220)
        self.assertEqual(len(entry["size"]), 3)


class TestPlanPruning(unittest.TestCase):
    """실패한 에셋은 플랜에서 빼야 배치 턴이 없는 이름을 부르지 않는다."""

    def _manifest(self):
        return [scene_kit.manifest_entry("watchtower", "watchtower", (3, 3, 8), 4100)]

    def test_dropped_assets_are_reported(self):
        self.assertEqual(scene_kit.dropped_assets(_PLAN, self._manifest()), ["barrel"])

    def test_prune_keeps_only_built_assets(self):
        pruned = scene_kit.prune_plan(_PLAN, self._manifest())
        self.assertEqual([a["key"] for a in pruned["assets"]], ["watchtower"])
        self.assertEqual(pruned["zones"], _PLAN["zones"])

    def test_prune_does_not_mutate_original(self):
        scene_kit.prune_plan(_PLAN, self._manifest())
        self.assertEqual(len(_PLAN["assets"]), 2)

    def test_plan_summary_counts_kinds_and_placements(self):
        summary = scene_kit.plan_summary(_PLAN)
        self.assertIn("구역 1개", summary)
        self.assertIn("에셋 2종(배치 8개)", summary)


class TestSceneSpacingAndTimeout(unittest.TestCase):
    def test_spacing_is_scene_extent_plus_margin(self):
        for size in ("S", "M", "L"):
            with self.subTest(size=size):
                self.assertEqual(scene_kit.scene_spacing(size),
                                 scene_plan.SCENE_SIZE_M[size] + scene_kit.LANE_MARGIN_M)

    def test_unknown_size_falls_back_to_medium(self):
        self.assertEqual(scene_kit.scene_spacing("XL"), scene_kit.scene_spacing("M"))

    def test_scene_spacing_exceeds_default_lane_gap(self):
        # 기본 4m 간격이면 배경끼리 겹친다 — 반드시 더 커야 한다
        self.assertGreater(scene_kit.scene_spacing("S"), 4.0)

    def test_timeout_is_scaled(self):
        self.assertEqual(scene_kit.scaled_timeout(300, 2.0), 600)

    def test_timeout_never_shrinks_below_base(self):
        self.assertEqual(scene_kit.scaled_timeout(300, 0.5), 300)
        self.assertEqual(scene_kit.scaled_timeout(300, None), 300)


class TestPlanNameDedup(unittest.TestCase):
    """중복 이름을 한 번만 고치면 세 번째 중복에서 또 겹친다."""

    def test_three_identical_zone_names_all_become_unique(self):
        plan = {
            "scene": {"size": "M", "palette": ["#6f7a5a", "#8a7a5c", "#b8ae95"]},
            "zones": [{"name": "yard", "center": [0, 0], "extent": [10, 10],
                       "purpose": "마당 %d" % i} for i in range(3)],
            "assets": [{"key": "barrel", "prompt": "드럼통", "count": 1,
                        "size_class": "S", "zone": "yard", "landmark": True}],
        }
        result, _ = scene_plan.normalize(plan, 80000, 12)
        names = [z["name"] for z in result["zones"]]
        self.assertEqual(len(set(names)), 3, names)

    def test_three_identical_asset_keys_all_become_unique(self):
        plan = {
            "scene": {"size": "M", "palette": ["#6f7a5a", "#8a7a5c", "#b8ae95"]},
            "zones": [{"name": "yard", "center": [0, 0], "extent": [10, 10],
                       "purpose": "마당"}],
            "assets": [{"key": "barrel", "prompt": "드럼통 %d" % i, "count": 1,
                        "size_class": "S", "zone": "yard", "landmark": i == 0}
                       for i in range(3)],
        }
        result, warnings = scene_plan.normalize(plan, 80000, 12)
        keys = [a["key"] for a in result["assets"]]
        self.assertEqual(len(set(keys)), 3, keys)
        self.assertEqual(sum("중복" in w for w in warnings), 2)


class TestScenePrefsPersisted(unittest.TestCase):
    """배경 설정도 애드온 업데이트 후 살아남아야 한다."""

    def test_scene_pref_keys_are_saved(self):
        for key in ("scene_tri_budget", "scene_max_assets", "scene_timeout_scale"):
            with self.subTest(key=key):
                self.assertIn(key, persist._PREF_KEYS)


if __name__ == "__main__":
    unittest.main()
