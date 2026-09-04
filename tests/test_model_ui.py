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


class TestStageModelLabels(unittest.TestCase):
    def test_started_claude_job_uses_saved_generation_and_critique_models(self):
        """실행 중 환경설정이 바뀌어도 시작 시 모델 snapshot을 표시한다."""
        labels = models.stage_model_labels(
            state="RUNNING",
            requested_model="Sonnet",
            effective_model="Sonnet",
            requested_critique_model="Haiku",
            effective_critique_model="Haiku",
            scheduled_model="Opus",
            scheduled_critique_model="Opus",
        )

        self.assertEqual(labels, ("Sonnet", "Haiku"))

    def test_pending_job_uses_current_scheduled_models(self):
        """아직 시작하지 않은 job만 현재 환경설정으로 예정 모델을 계산한다."""
        labels = models.stage_model_labels(
            state="PENDING",
            requested_model="",
            effective_model="",
            requested_critique_model="",
            effective_critique_model="",
            scheduled_model="Sonnet",
            scheduled_critique_model="Haiku",
        )

        self.assertEqual(labels, ("Sonnet", "Haiku"))

    def test_completed_legacy_job_does_not_claim_current_preferences(self):
        """모델 snapshot이 없는 완료 job은 현재 설정을 과거 기록처럼 쓰지 않는다."""
        labels = models.stage_model_labels(
            state="DONE",
            requested_model="",
            effective_model="",
            requested_critique_model="",
            effective_critique_model="",
            scheduled_model="Opus",
            scheduled_critique_model="Haiku",
        )

        self.assertEqual(labels, ("모델 기록 없음", "모델 기록 없음"))

    def test_critique_phase_uses_critique_model_as_current(self):
        """비평 단계의 현재 모델은 생성 모델이 아니라 비평 모델이다."""
        self.assertEqual(
            models.current_model_label("CRITIQUE", "Sonnet", "Haiku"),
            "Haiku",
        )


if __name__ == "__main__":
    unittest.main()
