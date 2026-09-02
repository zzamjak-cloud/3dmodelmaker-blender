# CLI 오류 분류·요약 테스트 (Blender 없이 순수 파이썬으로 실행)
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


errors = _load("errors_mod", "core/errors.py")

# 실제로 발생했던 codex 토큰 만료 stderr (요약본)
CODEX_AUTH = """CLI 종료 코드 1
2026-09-02T01:31:04.484875Z ERROR codex_login::auth::manager: Failed to refresh token: 401 Unauthorized: {
  "error": {
    "message": "Your session has ended. Please log in again.",
    "code": "refresh_token_invalidated"
  }
}
2026-09-02T01:31:05.866137Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed
"""


class TestClassify(unittest.TestCase):
    def test_auth_from_real_codex_stderr(self):
        self.assertEqual(errors.classify(CODEX_AUTH), errors.AUTH)

    def test_quota_and_network_and_timeout(self):
        self.assertEqual(errors.classify("HTTP 429 rate_limit"), errors.QUOTA)
        self.assertEqual(errors.classify("error: connection refused"), errors.NETWORK)
        self.assertEqual(errors.classify("CLI 시간 초과 (600초)"), errors.TIMEOUT)
        self.assertEqual(errors.classify("실행 파일 없음: codex"), errors.MISSING)

    def test_unknown_stays_unknown(self):
        self.assertEqual(errors.classify("ValueError: bad mesh"), errors.UNKNOWN)
        self.assertEqual(errors.classify(""), errors.UNKNOWN)


class TestDescribe(unittest.TestCase):
    def test_auth_reason_is_short_and_specific(self):
        msg = errors.describe(CODEX_AUTH, "codex")
        self.assertIn("로그인 만료", msg)
        # 예전처럼 "CLI 종료 코드 1"만 보여서는 안 된다
        self.assertNotIn("종료 코드", msg)
        # 좁은 사이드바에서 가운데가 잘리지 않을 길이여야 한다
        self.assertLessEqual(len(msg), 30)

    def test_action_carries_the_login_command(self):
        self.assertIn("codex login", errors.action(CODEX_AUTH, "codex"))

    def test_claude_gets_its_own_login_command(self):
        self.assertIn("claude login", errors.action("401 Unauthorized", "claude"))

    def test_action_empty_when_cause_unknown(self):
        self.assertEqual(errors.action("ValueError: bad mesh", "codex"), "")

    def test_unknown_error_appends_real_cause(self):
        raw = ("CLI 종료 코드 2\nTraceback (most recent call last):\n"
               "  File x, line 3\nValueError: bad mesh input")
        msg = errors.describe(raw, "codex")
        self.assertIn("CLI 종료 코드 2", msg)
        self.assertIn("ValueError: bad mesh input", msg)  # 트레이스백의 마지막 줄이 진짜 원인

    def test_empty_error(self):
        self.assertEqual(errors.describe("", "codex"), "알 수 없는 오류")


class TestDetailLines(unittest.TestCase):
    def test_strips_log_prefix_and_json_fragments(self):
        lines = errors.detail_lines(CODEX_AUTH)
        self.assertIn("Your session has ended. Please log in again.", lines)
        for line in lines:
            self.assertNotIn("2026-09-02T", line)   # 타임스탬프 접두어 제거
            self.assertNotEqual(line.strip(), "}")  # 중괄호만 남은 줄 제거

    def test_drops_mcp_noise(self):
        joined = " ".join(errors.detail_lines(CODEX_AUTH, limit=10))
        self.assertNotIn("rmcp::transport", joined)

    def test_respects_limit(self):
        self.assertLessEqual(len(errors.detail_lines(CODEX_AUTH, limit=2)), 2)


class TestTail(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(errors.tail("abc\ndef", 100), "abc\ndef")

    def test_drops_partial_first_line(self):
        # 잘린 조각("...horized")이 첫 줄로 남으면 안 된다
        text = "aaaaaaaaaa\nbbbb\ncccc"
        out = errors.tail(text, 12)
        self.assertNotIn("aaaa", out)
        self.assertTrue(out.endswith("cccc"))


if __name__ == "__main__":
    unittest.main()
