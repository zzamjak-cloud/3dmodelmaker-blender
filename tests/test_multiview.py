# 멀티뷰 참조 시트 생성 명령·프롬프트 구성 테스트 (Blender 없이 순수 파이썬으로 실행)
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


multiview = _load("multiview_mod", "core/multiview.py")
prompts = _load("prompts_mod2", "core/prompts.py")


class TestBuildCommand(unittest.TestCase):
    def test_basic_command(self):
        cmd = multiview.build_command("/usr/bin/codex", "/tmp/mv")
        self.assertEqual(cmd[0], "/usr/bin/codex")
        self.assertEqual(cmd[1], "exec")
        # 이미지 저장을 위해 쓰기 샌드박스여야 한다
        self.assertIn("workspace-write", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertEqual(cmd[-1], "-")  # 프롬프트는 stdin

    def test_ref_image_attached_before_stdin_marker(self):
        cmd = multiview.build_command("codex", "/tmp/mv", ref_image="/tmp/ref.png")
        self.assertIn("-i", cmd)
        self.assertLess(cmd.index("-i"), cmd.index("-"))
        self.assertEqual(cmd[cmd.index("-i") + 1], "/tmp/ref.png")


class TestBuildPrompt(unittest.TestCase):
    def test_contains_filename_and_views(self):
        p = multiview.build_prompt("빨간 승용차")
        self.assertIn(multiview.MULTIVIEW_FILENAME, p)
        for view in ("정면", "측면", "상면", "3/4"):
            self.assertIn(view, p)
        self.assertNotIn("참조 이미지", p)

    def test_ref_note_when_has_ref(self):
        p = multiview.build_prompt("빨간 승용차", has_ref=True)
        self.assertIn("참조 이미지", p)


class TestPromptIntegration(unittest.TestCase):
    def test_initial_prompt_with_multiview(self):
        p = prompts.build_initial_prompt("승용차", multiview="multiview.png")
        self.assertIn("multiview.png", p)
        self.assertIn("멀티뷰", p)

    def test_critique_prompt_with_multiview(self):
        p = prompts.build_critique_prompt(["cap.png"], {"tris": 10}, 2, 3,
                                          multiview="multiview.png")
        self.assertIn("multiview.png", p)

    def test_no_multiview_no_note(self):
        p = prompts.build_initial_prompt("승용차")
        self.assertNotIn("멀티뷰", p)


if __name__ == "__main__":
    unittest.main()
