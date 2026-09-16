# 아트 스타일 카탈로그·프롬프트 조립 테스트 (Blender 없이 순수 파이썬으로 실행)
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


styles = _load("styles_mod", "core/styles.py")
prompts = _load("prompts_mod_styles", "core/prompts.py")


class TestCatalog(unittest.TestCase):
    def test_default_is_lowpoly(self):
        # 기본값이 바뀌면 기존 사용자의 결과 스타일이 통째로 달라진다
        self.assertEqual(styles.DEFAULT_STYLE, "LOWPOLY")

    def test_default_is_first_item(self):
        # 큐 항목의 style은 동적 enum이라 default를 줄 수 없다 — 첫 항목이 기본값이 된다
        self.assertEqual(styles.enum_items()[0][0], styles.DEFAULT_STYLE)

    def test_every_style_has_a_guide_file(self):
        for style in styles.STYLES:
            self.assertTrue(styles.guide(style["id"]).strip(),
                            "%s 스타일 지침 파일이 비었다" % style["id"])

    def test_every_guide_declares_a_tri_budget(self):
        # 폴리 버짓이 없으면 에이전트가 스타일과 무관하게 기본값으로 만든다
        for style in styles.STYLES:
            self.assertIn("트라이 기준", styles.guide(style["id"]),
                          "%s 스타일에 폴리 버짓이 없다" % style["id"])

    def test_every_style_has_an_image_note(self):
        for style in styles.STYLES:
            self.assertIn("스타일:", styles.image_note(style["id"]))

    def test_unknown_style_falls_back_to_default(self):
        # 구버전 .blend에 저장된 값이나 오타가 와도 생성이 죽으면 안 된다
        self.assertEqual(styles.style_def("NOPE")["id"], styles.DEFAULT_STYLE)
        self.assertEqual(styles.style_def("")["id"], styles.DEFAULT_STYLE)
        self.assertEqual(styles.style_def(None)["id"], styles.DEFAULT_STYLE)

    def test_style_id_is_case_insensitive(self):
        self.assertEqual(styles.style_def("voxel")["id"], "VOXEL")

    def test_tri_scale_grows_with_style_density(self):
        # 로우폴리를 1.0 기준으로 사실적 스타일이 가장 크다
        self.assertEqual(styles.tri_scale("LOWPOLY"), 1.0)
        self.assertGreater(styles.tri_scale("REALISTIC"), styles.tri_scale("STYLIZED"))
        self.assertGreater(styles.tri_scale("STYLIZED"), styles.tri_scale("CUTE"))

    def test_only_voxel_uses_voxel_api(self):
        self.assertTrue(styles.uses_voxel_api("VOXEL"))
        for other in ("LOWPOLY", "CUTE", "STYLIZED", "REALISTIC"):
            self.assertFalse(styles.uses_voxel_api(other))


class TestSystemPromptStyles(unittest.TestCase):
    def test_style_guide_is_included(self):
        for style in styles.STYLES:
            text = prompts.build_system_prompt("OBJECT", style["id"])
            self.assertIn("# 스타일 지침", text)

    def test_styles_differ(self):
        seen = {prompts.build_system_prompt("OBJECT", s["id"]) for s in styles.STYLES}
        self.assertEqual(len(seen), len(styles.STYLES))

    def test_common_rules_survive_in_every_style(self):
        # 스타일을 바꿔도 공중 부양 금지·시그니처 선언 같은 공통 기준은 남아야 한다
        for style in styles.STYLES:
            text = prompts.build_system_prompt("OBJECT", style["id"])
            self.assertIn("공중 부양은 금지", text)
            self.assertIn("시그니처 요소를 먼저 선언하라", text)
            self.assertIn("STATUS: DONE", text)

    def test_voxel_api_exposed_only_for_voxel_style(self):
        voxel = prompts.build_system_prompt("OBJECT", "VOXEL")
        self.assertIn("### lp.voxel", voxel)
        for other in ("LOWPOLY", "CUTE", "STYLIZED", "REALISTIC"):
            self.assertNotIn("### lp.voxel", prompts.build_system_prompt("OBJECT", other))

    def test_scene_mode_also_takes_style(self):
        text = prompts.build_system_prompt("SCENE", "CUTE")
        self.assertIn("배경 공간(씬)", text)       # 배경 골격은 그대로
        self.assertIn("귀여운 둥근", text)          # 스타일 지침도 붙는다

    def test_unknown_style_still_builds(self):
        self.assertIn("# 스타일 지침", prompts.build_system_prompt("OBJECT", "NOPE"))


if __name__ == "__main__":
    unittest.main()
