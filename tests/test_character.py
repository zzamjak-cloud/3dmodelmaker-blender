# 캐릭터 제작 모드 테스트 (Blender 없이 순수 파이썬으로 실행)
#
# 캐릭터는 프랍과 다른 세 가지가 있어야 한다: 리깅 자세 지침, 3x2 턴어라운드 시트,
# 유형(인간형/동물형/크리처형)별 비율 규칙. 셋 중 하나가 빠지면 "사람 모양 프랍"이 나온다.
import importlib.util
import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "character_pkg"


def _load_pkg(module_name: str):
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


prompts = _load_pkg("prompts")
multiview = _load_pkg("multiview")


class TestSystemPrompt(unittest.TestCase):
    def test_character_mode_uses_character_md(self):
        p = prompts.build_system_prompt("CHARACTER", "LOWPOLY")
        self.assertIn("리깅 자세로 만든다", p)
        self.assertIn("턴어라운드 시트 읽는 법", p)
        self.assertIn("STATUS: DONE", p)

    def test_character_mode_keeps_style_and_api(self):
        p = prompts.build_system_prompt("CHARACTER", "CUTE")
        self.assertIn("스타일 지침 — 귀여운 둥근", p)
        self.assertIn("lp 헬퍼 API 레퍼런스", p)

    def test_object_mode_is_not_affected(self):
        p = prompts.build_system_prompt("OBJECT", "LOWPOLY")
        self.assertNotIn("리깅 자세로 만든다", p)

    def test_character_md_pins_engine_conventions(self):
        with open(os.path.join(_ROOT, "prompts", "system_character.md"), encoding="utf-8") as f:
            md = f.read()
        for needle in ("-Y", "z=0", "mirror_x", "T-포즈", "관절"):
            self.assertIn(needle, md)


class TestInitialPrompt(unittest.TestCase):
    def test_character_head_and_type_note(self):
        p = prompts.build_initial_prompt("늑대 전사", mode="CHARACTER", character_type="ANIMAL")
        self.assertIn("게임 캐릭터를 만들어라", p)
        self.assertIn("동물형", p)
        self.assertIn("T-포즈", p)

    def test_auto_type_asks_model_to_decide(self):
        p = prompts.build_initial_prompt("정체불명", mode="CHARACTER", character_type="AUTO")
        self.assertIn("판단하라", p)

    def test_unknown_type_falls_back_to_auto(self):
        self.assertEqual(prompts.character_type_note("NOPE"), prompts.character_type_note("AUTO"))

    def test_character_multiview_note_describes_turnaround(self):
        p = prompts.build_initial_prompt("기사", multiview="multiview.png", mode="CHARACTER")
        self.assertIn("턴어라운드 시트(3x2)", p)
        self.assertIn("시트가 이긴다", p)

    def test_object_multiview_note_unchanged(self):
        p = prompts.build_initial_prompt("배럴", multiview="multiview.png")
        self.assertIn("멀티뷰 참조 시트", p)
        self.assertNotIn("턴어라운드", p)

    def test_object_head_no_longer_hardcodes_lowpoly(self):
        # 스타일이 데이터로 빠졌으니 첫 줄이 "로우폴리"를 못 박으면 안 된다
        self.assertNotIn("로우폴리 모델을 만들어라", prompts.build_initial_prompt("배럴"))


class TestTurnaroundSheet(unittest.TestCase):
    def test_turnaround_is_3x2_with_six_views(self):
        p = multiview.build_image_prompt("기사", sheet="TURNAROUND")
        self.assertIn("3x2", p)
        for view in ("FRONT", "BACK", "LEFT", "RIGHT", "TOP", "3/4"):
            self.assertIn(view, p)
        self.assertIn("T-포즈", p)

    def test_turnaround_aspect_is_landscape(self):
        self.assertEqual(multiview.sheet_aspect("TURNAROUND"), "3:2")
        self.assertEqual(multiview.sheet_aspect("MULTIVIEW"), "1:1")
        self.assertEqual(multiview.sheet_aspect("NOPE"), "1:1")

    def test_reference_art_must_be_preserved(self):
        p = multiview.build_image_prompt("기사", has_ref=True, sheet="TURNAROUND")
        self.assertIn("원화", p)
        self.assertIn("그대로", p)
        self.assertIn("추가하지 마라", p)

    def test_default_sheet_is_the_old_multiview(self):
        old = multiview.build_image_prompt("배럴")
        self.assertIn("2x2", old)
        self.assertNotIn("T-포즈", old)

    def test_codex_prompt_also_takes_sheet(self):
        p = multiview.build_prompt("기사", sheet="TURNAROUND")
        self.assertIn("턴어라운드", p)
        self.assertIn(multiview.MULTIVIEW_FILENAME, p)
        self.assertIn("SAVED", p)

    def test_style_note_applies_to_turnaround(self):
        p = multiview.build_image_prompt("기사", sheet="TURNAROUND", style_note="스타일: 복셀\n")
        self.assertIn("스타일: 복셀", p)


class TestComparePrompt(unittest.TestCase):
    def test_names_both_images_and_turn(self):
        p = prompts.build_character_compare_prompt("multiview.png", "render_1.png",
                                                   "x = 1", 1, 2)
        self.assertIn("multiview.png", p)
        self.assertIn("render_1.png", p)
        self.assertIn("대조 1/2", p)
        self.assertIn("x = 1", p)
        self.assertIn("STATUS: DONE", p)

    def test_asks_for_missing_parts_and_floating_parts(self):
        p = prompts.build_character_compare_prompt("s.png", "r.png", "", 1, 1)
        self.assertIn("모델에 없는 요소", p)
        self.assertIn("떠 있는 파트", p)
        self.assertIn("전체 코드", p)

    def test_explains_grid_order_difference(self):
        # 시트(FRONT|BACK|LEFT / RIGHT|TOP|3/4)와 렌더(FRONT|RIGHT|BACK / LEFT|TOP|BOTTOM)의
        # 칸 순서가 달라 모델이 칸 위치로 대조하면 틀린 짝을 비교한다
        p = prompts.build_character_compare_prompt("s.png", "r.png", "", 1, 1)
        self.assertIn("칸 순서가 시트와 다르니", p)


class TestCharacterRulesConnectParts(unittest.TestCase):
    def test_gap_rule_is_gone_and_penetration_rule_exists(self):
        with open(os.path.join(_ROOT, "prompts", "system_character.md"), encoding="utf-8") as f:
            md = f.read()
        # 첫 실행 결과: 파트 사이 틈 규칙 때문에 팔·다리가 몸에서 떨어져 떠 있었다
        self.assertNotIn("0.05m 이상 틈", md)
        self.assertIn("떠 있는 파트는 실패", md)
        self.assertIn("관통", md)
        self.assertIn("80,000", md)          # 폴리 제약 완화
        self.assertIn("시트 체크리스트", md)  # 원화 요소 열거


if __name__ == "__main__":
    unittest.main()
