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


if __name__ == "__main__":
    unittest.main()
