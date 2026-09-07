import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_models_module():
    """패키지 초기화 없이 모델 표시 도우미를 불러온다."""
    spec = importlib.util.spec_from_file_location("model_ui_models", ROOT / "core" / "models.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


models = load_models_module()


class TestJobModelLabel(unittest.TestCase):
    def test_scheduled_model_label(self):
        """실행 전 job은 요청 모델을 예정 모델로 표시한다."""
        self.assertEqual(
            models.job_model_label("GPT-6 Astra", "", False),
            "예정 모델: GPT-6 Astra",
        )

    def test_effective_model_label(self):
        """실행한 job은 실제 사용 모델을 표시한다."""
        self.assertEqual(
            models.job_model_label("GPT-6 Astra", "GPT-6 Astra", False),
            "사용 모델: GPT-6 Astra",
        )

    def test_fallback_model_label(self):
        """fallback job은 실제 모델과 fallback 사실을 함께 표시한다."""
        self.assertEqual(
            models.job_model_label(
                "GPT-6 Astra", "Codex CLI 기본 모델", True),
            "사용 모델: Codex CLI 기본 모델 (Astra 사용 불가)",
        )


class TestGenerationModelLabel(unittest.TestCase):
    def test_fallback_job_uses_effective_model(self):
        self.assertEqual(models.generation_model_label(
            "RUNNING", "GPT-6 Astra", "Codex CLI 기본 모델"),
            "Codex CLI 기본 모델")

    def test_pending_job_uses_astra(self):
        self.assertEqual(models.generation_model_label("PENDING", "", ""),
                         "GPT-6 Astra")

    def test_completed_legacy_job_does_not_claim_astra(self):
        self.assertEqual(models.generation_model_label("DONE", "", ""),
                         "모델 기록 없음")


if __name__ == "__main__":
    unittest.main()
