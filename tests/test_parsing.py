# 에이전트 응답 파싱 테스트 — STATUS 헤더와 언어별 펜스 블록 추출
# (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    """리포 내 파일을 패키지 임포트 없이 모듈로 읽어들인다."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parsing = _load("parsing_mod", "agents/parsing.py")

FENCE = "```"


def _block(lang: str, body: str) -> str:
    return f"{FENCE}{lang}\n{body}\n{FENCE}"


class TestStatus(unittest.TestCase):
    def test_known_statuses(self):
        for value in ("DONE", "REVISE", "PLAN"):
            status, _ = parsing.parse_agent_block(f"STATUS: {value}\n설명")
            self.assertEqual(status, value)

    def test_plan_status_is_accepted(self):
        """배경 모드 플랜 턴은 STATUS: PLAN으로 끝난다 — 모르면 조용히 None이 된다."""
        status, body = parsing.parse_agent_block(
            "STATUS: PLAN\n" + _block("json", '{"zones": []}'), langs=("json",))
        self.assertEqual(status, "PLAN")
        self.assertEqual(body, '{"zones": []}')

    def test_unknown_status_ignored(self):
        status, _ = parsing.parse_agent_block("STATUS: MAYBE\n설명")
        self.assertIsNone(status)

    def test_empty_text(self):
        self.assertEqual(parsing.parse_agent_block(""), (None, None))
        self.assertEqual(parsing.parse_agent_reply(None), (None, None))


class TestBlockSelection(unittest.TestCase):
    def test_last_matching_block_wins(self):
        text = _block("python", "첫번째") + "\n중간\n" + _block("py", "두번째")
        _, body = parsing.parse_agent_block(text)
        self.assertEqual(body, "두번째")

    def test_other_language_blocks_ignored(self):
        text = _block("python", "코드") + "\n" + _block("json", "{}")
        _, body = parsing.parse_agent_block(text)
        self.assertEqual(body, "코드")

    def test_langs_argument_selects_json(self):
        text = _block("python", "코드") + "\n" + _block("json", "{}")
        _, body = parsing.parse_agent_block(text, langs=("json",))
        self.assertEqual(body, "{}")

    def test_no_matching_block(self):
        _, body = parsing.parse_agent_block(_block("bash", "ls"))
        self.assertIsNone(body)

    def test_language_tag_is_case_insensitive(self):
        _, body = parsing.parse_agent_block(_block("Python", "코드"))
        self.assertEqual(body, "코드")


class TestReplyWrapper(unittest.TestCase):
    """parse_agent_reply는 기존 호출부의 동작이 바뀌면 안 된다."""

    def test_reply_matches_python_block(self):
        text = "STATUS: DONE\n" + _block("python", "import bpy")
        self.assertEqual(parsing.parse_agent_reply(text), ("DONE", "import bpy"))

    def test_reply_ignores_json_block(self):
        text = "STATUS: REVISE\n" + _block("json", "{}")
        self.assertEqual(parsing.parse_agent_reply(text), ("REVISE", None))


if __name__ == "__main__":
    unittest.main()
