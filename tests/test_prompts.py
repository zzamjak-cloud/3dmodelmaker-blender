# 프롬프트 빌더의 참조 이미지 처리 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    """리포 내 파일을 패키지 임포트 없이 모듈로 읽어들인다 (core/__init__.py의 bpy 회피)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prompts = _load("prompts_mod", "core/prompts.py")


class TestRefImage(unittest.TestCase):
    def test_initial_without_ref(self):
        p = prompts.build_initial_prompt("배럴")
        self.assertNotIn("참조 이미지", p)

    def test_initial_with_ref(self):
        p = prompts.build_initial_prompt("배럴", ref_image="reference.png")
        self.assertIn("reference.png", p)
        self.assertIn("참조 이미지", p)

    def test_removed_followup_prompts_are_unavailable(self):
        self.assertFalse(hasattr(prompts, "build_critique_prompt"))
        self.assertFalse(hasattr(prompts, "build_improve_prompt"))

    def test_budget_is_10k(self):
        # 첫 생성의 기존 품질 기준은 유지한다.
        with open(os.path.join(_ROOT, "prompts", "system_lowpoly.md"), encoding="utf-8") as f:
            sys_md = f.read()
        self.assertIn("10000", sys_md)
        for old in ("≤ 1500", "≤ 5000", "프랍 1500"):
            self.assertNotIn(old, sys_md)

    def test_system_prompt_forbids_reading_external_skill_files(self):
        # codex가 모델링 전에 SKILL.md를 읽으려다 샌드박스 접근 거부로 턴을 낭비했다
        # build_system_prompt()는 bpy에 의존하므로 소스 md를 직접 읽는다
        with open(os.path.join(_ROOT, "prompts", "system_lowpoly.md"), encoding="utf-8") as f:
            sys_md = f.read()
        self.assertIn("작업 방식 (도구 사용)", sys_md)
        for needle in ("SKILL.md", "셸 명령을 실행하지 마라"):
            self.assertIn(needle, sys_md)


class TestSystemPromptModes(unittest.TestCase):
    """배경 모드는 씬 헬퍼만, 오브젝트 모드는 기존 어휘만 노출해야 한다."""

    def test_scene_mode_uses_scene_system_md(self):
        p = prompts.build_system_prompt("SCENE")
        self.assertIn("배경 공간(씬)", p)
        self.assertIn("STATUS: PLAN", p)
        self.assertIn("lp 헬퍼 API 레퍼런스", p)

    def test_scene_api_names_exposed_only_in_scene_mode(self):
        scene = prompts.build_system_prompt("SCENE")
        obj = prompts.build_system_prompt()
        for name in ("terrain", "instance", "place_grid", "place_scatter", "wall_run",
                     "path_strip", "ground_snap", "kit"):
            self.assertIn("lp." + name, scene)
            self.assertNotIn("lp." + name, obj)

    def test_object_mode_is_default_and_unchanged(self):
        self.assertEqual(prompts.build_system_prompt(), prompts.build_system_prompt("OBJECT"))
        self.assertIn("로우폴리 모델링 규칙", prompts.build_system_prompt())

    def test_scene_api_falls_back_when_lowpoly_unavailable(self):
        # bpy가 없는 환경에서도 씬 어휘 목록은 비어서는 안 된다
        names = prompts._scene_api_names()
        self.assertIn("terrain", names)
        self.assertIn("set_color", names)


class TestScenePromptBuilders(unittest.TestCase):
    PLAN = {
        "scene": {"size": "M", "palette": ["#6f7a5a", "#8a7a5c", "#b8ae95"], "mood": "삭막"},
        "terrain": {"relief": 0.25, "style": "흙바닥"},
        "zones": [{"name": "yard", "center": [0, 0], "extent": [26, 18], "purpose": "연병장"}],
        "assets": [{"key": "watchtower", "prompt": "감시탑", "count": 2, "size_class": "L",
                    "zone": "yard", "landmark": True}],
        "rules": ["중앙은 비운다"],
    }

    def test_plan_prompt_has_budget_and_status(self):
        p = prompts.build_scene_plan_prompt("포로 수용소", "M", 80000, 12)
        self.assertIn("80000", p)
        self.assertIn("12종", p)
        self.assertIn("40m", p)
        self.assertIn("STATUS: PLAN", p)
        self.assertIn("6000", p)          # size_class별 트라이 상한
        self.assertNotIn("컨셉 시트", p)

    def test_plan_prompt_notes_sceneview_and_reference(self):
        p = prompts.build_scene_plan_prompt("고대 성", "L", 80000, 12,
                                            ref_image="ref.png", sceneview="sceneview.png")
        self.assertIn("80m", p)
        self.assertIn("sceneview.png", p)
        self.assertIn("탑다운", p)
        self.assertIn("ref.png", p)

    def test_asset_prompt_carries_palette_and_limit(self):
        p = prompts.build_scene_asset_prompt(
            {"key": "watchtower", "prompt": "나무 감시탑"}, ["#6f7a5a", "#8a7a5c"], 6000)
        self.assertIn("나무 감시탑", p)
        self.assertIn("#6f7a5a", p)
        self.assertIn("6000", p)
        self.assertIn("원점", p)
        self.assertIn("lp.join", p)

    def test_place_prompt_lists_kit_and_order(self):
        manifest = [{"key": "watchtower", "obj_name": "Watchtower", "size": (3.0, 3.0, 7.5),
                     "tri": 1840}]
        p = prompts.build_scene_place_prompt(self.PLAN, manifest, 80000)
        self.assertIn("watchtower", p)
        self.assertIn("Watchtower", p)
        self.assertIn("1840", p)
        self.assertIn("3.00 x 3.00 x 7.50", p)
        self.assertIn("lp.terrain", p)
        self.assertIn("lp.ground_snap", p)
        self.assertIn("STATUS: DONE", p)
        self.assertIn("200줄", p)
        self.assertIn("80000", p)
        self.assertIn("\"landmark\": true", p)   # 플랜 JSON이 그대로 들어간다

    def test_budget_prompt_states_both_numbers(self):
        p = prompts.build_scene_budget_prompt(93000, 80000)
        self.assertIn("93000", p)
        self.assertIn("80000", p)
        self.assertIn("STATUS: DONE", p)

    def test_plan_retry_prompt_includes_error(self):
        p = prompts.build_scene_plan_retry_prompt("필수 키 'zones'가 없다")
        self.assertIn("필수 키 'zones'가 없다", p)
        self.assertIn("STATUS: PLAN", p)


if __name__ == "__main__":
    unittest.main()
