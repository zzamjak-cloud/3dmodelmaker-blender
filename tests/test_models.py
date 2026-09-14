import unittest
from pathlib import Path

from core import models


ROOT = Path(__file__).resolve().parents[1]


def read_source(relative_path: str) -> str:
    """테스트 대상 소스 파일을 UTF-8 텍스트로 읽는다."""
    return (ROOT / relative_path).read_text(encoding="utf-8")


class TestCodexModelSelection(unittest.TestCase):
    def test_astra_api_model_id(self):
        self.assertEqual(models.ASTRA_ID, "gpt-6-astra")

    def test_labels_are_stable(self):
        self.assertEqual(models.model_label("CODEX", "gpt-6-astra"), "GPT-6 Astra")
        self.assertEqual(models.model_label("CODEX", ""), "Codex CLI 기본 모델")


class TestModelUnavailable(unittest.TestCase):
    def test_requires_model_context_and_availability_phrase(self):
        self.assertTrue(models.is_model_unavailable(
            "The model gpt-6-astra does not exist or you do not have access",
            "gpt-6-astra"))
        self.assertFalse(models.is_model_unavailable("HTTP 401 Unauthorized", "gpt-6-astra"))
        self.assertFalse(models.is_model_unavailable("HTTP 429 rate_limit", "gpt-6-astra"))
        self.assertFalse(models.is_model_unavailable("connection refused", "gpt-6-astra"))

    def test_metadata_warning_is_not_a_failure(self):
        warning = "Model metadata for `gpt-6-astra` not found. Defaulting to fallback metadata"
        self.assertFalse(models.is_model_unavailable(warning, "gpt-6-astra"))

    def test_metadata_warning_does_not_hide_later_model_error(self):
        error = (
            "Model metadata for `gpt-6-astra` not found. "
            "Defaulting to fallback metadata\n"
            "The model gpt-6-astra does not exist"
        )

        self.assertTrue(models.is_model_unavailable(error, "gpt-6-astra"))

    def test_not_supported_word_order_is_model_unavailable(self):
        self.assertTrue(models.is_model_unavailable(
            "model gpt-6-astra is not supported",
            "gpt-6-astra",
        ))

    def test_capacity_error_without_model_name_is_unavailable(self):
        # 실제 관측된 stdout 이벤트 — 모델명이 실려 오지 않는다
        error = (
            "CLI 종료 코드 1\n"
            '{"type":"error","message":"Selected model is at capacity. '
            'Please try a different model."}'
        )

        self.assertTrue(models.is_model_unavailable(error, models.ASTRA_ID))

    def test_capacity_error_still_requires_a_selected_model(self):
        error = '{"type":"error","message":"Selected model is at capacity."}'

        self.assertFalse(models.is_model_unavailable(error, ""))

    def test_metadata_warning_does_not_pair_with_unrelated_access_error(self):
        error = (
            "Model metadata for `gpt-6-astra` not found. "
            "Defaulting to fallback metadata\n"
            "You do not have access to this workspace"
        )

        self.assertFalse(models.is_model_unavailable(error, "gpt-6-astra"))

    def test_terminal_failures_are_not_model_unavailable(self):
        failures = (
            "401: model gpt-6-astra does not exist",
            "429: model gpt-6-astra is not supported",
            "Network is unreachable: model gpt-6-astra not found",
            "Timeout: model gpt-6-astra does not exist",
        )

        for error in failures:
            with self.subTest(error=error):
                self.assertFalse(models.is_model_unavailable(error, "gpt-6-astra"))


class TestCodexPreferenceDefaults(unittest.TestCase):
    def test_preferences_and_scene_defaults_select_codex_astra(self):
        prefs = read_source("preferences.py")
        props = read_source("properties.py")
        persist = read_source("core/persist.py")

        self.assertNotIn("codex_model: EnumProperty", prefs)
        self.assertNotIn("claude_path", prefs)
        self.assertNotIn("auto_turns:", props)
        self.assertNotIn("agent: EnumProperty", props)
        self.assertNotIn('"codex_model"', persist)


if __name__ == "__main__":
    unittest.main()
