# 자동 반복 루프 정책 테스트 (Blender 없이 순수 파이썬으로 실행)
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


loop = _load("loop", "core/loop.py")
prompts = _load("prompts", "core/prompts.py")


class TestLoopPolicy(unittest.TestCase):
    # 자동 반복 값 = 턴 수 1:1 (최소 1)
    def test_total_turns_identity(self):
        self.assertEqual(loop.total_turns(1), 1)
        self.assertEqual(loop.total_turns(3), 3)
        self.assertEqual(loop.total_turns(9), 9)
        self.assertEqual(loop.total_turns(0), 1)

    # 최소 3턴 전에는 DONE을 인정하지 않는다
    def test_done_ignored_before_min_turns(self):
        self.assertFalse(loop.should_finalize('DONE', 1, 3))
        self.assertFalse(loop.should_finalize('DONE', 2, 3))
        self.assertTrue(loop.should_finalize('DONE', 3, 3))

    # 2사이클(6턴)이어도 3턴부터는 DONE으로 조기 종료 가능
    def test_done_honored_from_min_turns_in_longer_run(self):
        self.assertFalse(loop.should_finalize('DONE', 2, 6))
        self.assertTrue(loop.should_finalize('DONE', 3, 6))
        self.assertTrue(loop.should_finalize('DONE', 4, 6))

    # REVISE는 최대 턴에 도달해야만 종료된다
    def test_revise_runs_to_max(self):
        self.assertFalse(loop.should_finalize('REVISE', 2, 3))
        self.assertTrue(loop.should_finalize('REVISE', 3, 3))
        self.assertFalse(loop.should_finalize('REVISE', 5, 6))
        self.assertTrue(loop.should_finalize('REVISE', 6, 6))

    # 개선하기 세션(총 1턴)은 1턴의 DONE을 그대로 인정한다
    def test_improve_session_single_turn(self):
        self.assertTrue(loop.allow_done(1, 1))
        self.assertTrue(loop.should_finalize('DONE', 1, 1))

    def test_allow_done_boundary(self):
        self.assertFalse(loop.allow_done(1, 3))
        self.assertFalse(loop.allow_done(2, 3))
        self.assertTrue(loop.allow_done(3, 3))


class TestCritiquePromptDoneRule(unittest.TestCase):
    _IMAGES = ["capture_iso_front.png"]
    _STATS = {"트라이앵글 수": 500}

    # DONE 허용 턴: 기존처럼 DONE 선언 지침이 들어간다
    def test_allow_done_true_offers_done(self):
        text = prompts.build_critique_prompt(self._IMAGES, self._STATS, 3, 3, allow_done=True)
        self.assertIn("STATUS: DONE", text)
        self.assertIn("STATUS: REVISE", text)

    # DONE 금지 턴: DONE 선언 금지를 명시하고 REVISE를 요구한다
    def test_allow_done_false_forbids_done(self):
        text = prompts.build_critique_prompt(self._IMAGES, self._STATS, 2, 3, allow_done=False)
        self.assertIn("선언하지 마라", text)
        self.assertIn("STATUS: REVISE", text)
        self.assertNotIn("DONE`만 반환", text)
        self.assertNotIn("반복은 비용이다", text)


if __name__ == "__main__":
    unittest.main()
