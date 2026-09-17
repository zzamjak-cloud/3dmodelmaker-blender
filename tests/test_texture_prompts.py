# 텍스처 6면도 프롬프트 테스트 — 색 참조·시점별 고해상 (Blender 없이 순수 파이썬으로 실행)
#
# 첫 실측: 회색 가이드 한 장(칸당 512px)만 주면 이미지 모델이 색을 지어내고 얼굴을 비워 두며
# 결과가 뿌옇다. 원화를 색 원본으로 함께 주고, 시점마다 1:1로 따로 요청해야 한다.
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


layout = _load("tex_layout_mod", "texturing/layout.py")


class TestSheetPrompt(unittest.TestCase):
    def test_without_reference_has_no_color_contract(self):
        p = layout.build_image_prompt("배럴")
        self.assertNotIn("색 계약", p)

    def test_reference_adds_color_contract_and_face_rule(self):
        p = layout.build_image_prompt("늑대 전사", has_reference=True)
        self.assertIn("색 계약", p)
        self.assertIn("턴어라운드 원화", p)
        self.assertIn("얼굴은 반드시 완성하라", p)
        self.assertIn("색을 새로 정하지 마라", p)

    def test_codex_prompt_unchanged(self):
        p = layout.build_prompt("배럴", "x.png")
        self.assertIn("x.png", p)
        self.assertIn("SAVED", p)


class TestViewPrompt(unittest.TestCase):
    def test_names_the_view_in_korean_and_english(self):
        p = layout.build_view_prompt("늑대 전사", "FRONT")
        self.assertIn("정면", p)
        self.assertIn("FRONT", p)
        self.assertIn("1:1", p)

    def test_all_six_views_have_labels(self):
        for view in layout.VIEWS:
            p = layout.build_view_prompt("x", view)
            self.assertIn(view, p)
            self.assertNotIn("{", p)  # 포맷 누락 없음

    def test_view_prompt_keeps_shape_contract(self):
        p = layout.build_view_prompt("x", "TOP")
        self.assertIn("형상 계약", p)
        self.assertIn("실루엣 변경 금지", p)

    def test_view_prompt_with_reference_maps_direction(self):
        p = layout.build_view_prompt("x", "BACK", has_reference=True)
        self.assertIn("색 계약", p)
        self.assertIn("뒷면 시점이다", p)

    def test_unknown_view_does_not_crash(self):
        p = layout.build_view_prompt("x", "weird")
        self.assertIn("WEIRD", p)


if __name__ == "__main__":
    unittest.main()
