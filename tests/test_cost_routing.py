# 비용 최적화 — 턴 역할별 모델 라우팅 · 2단계 검토 · 입력 다이어트 · 사용량 집계 테스트
#
# Astra는 토큰 소모가 빨라 모든 턴에 쓰면 비용이 급증한다. 생성 턴만 Astra·high로 돌리고
# 검토·수정·플랜은 하위 모델로 돌리는 라우팅, 결함이 있을 때만 코드를 다시 쓰는 2단계 검토,
# resume 기록에 있는 코드·이미지를 다시 보내지 않는 다이어트를 Blender 없이 고정한다.
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


models = _load("cost_models", ROOT / "core" / "models.py")
parsing = _load("cost_parsing", ROOT / "agents" / "parsing.py")

# codex_cli는 상대 import라 임시 패키지로 읽는다
_agents = types.ModuleType("cost_agents")
_agents.__path__ = [str(ROOT / "agents")]
sys.modules["cost_agents"] = _agents
_load("cost_agents.base", ROOT / "agents" / "base.py")
CodexBackend = _load("cost_agents.codex_cli", ROOT / "agents" / "codex_cli.py").CodexBackend

# prompts는 core 패키지 상대 import — core를 가짜 패키지로 등록
_core = types.ModuleType("cost_core")
_core.__path__ = [str(ROOT / "core")]
sys.modules["cost_core"] = _core
prompts = _load("cost_core.prompts", ROOT / "core" / "prompts.py")


class TestRouting(unittest.TestCase):
    def test_default_routing_uses_astra_only_for_generation(self):
        routing = models.routing_from_prefs(SimpleNamespace())
        self.assertEqual(routing[models.ROLE_GENERATE], (models.ASTRA_ID, "high"))
        for role in (models.ROLE_REVIEW, models.ROLE_FIX, models.ROLE_PLAN):
            self.assertEqual(routing[role][0], "", role)
            self.assertEqual(routing[role][1], "medium", role)

    def test_prefs_override_each_role(self):
        prefs = SimpleNamespace(review_model='ASTRA', review_effort='low',
                                generate_model='DEFAULT', generate_effort='medium')
        routing = models.routing_from_prefs(prefs)
        self.assertEqual(routing[models.ROLE_REVIEW], (models.ASTRA_ID, "low"))
        self.assertEqual(routing[models.ROLE_GENERATE], ("", "medium"))

    def test_summary_names_every_role(self):
        text = models.routing_summary(models.routing_from_prefs(SimpleNamespace()))
        self.assertIn("생성 GPT-6 Astra·high", text)
        self.assertIn("검토 Codex CLI 기본 모델·medium", text)
        self.assertIn("플랜", text)

    def test_usage_accumulates_and_formats(self):
        total = {}
        models.add_usage(total, {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10})
        models.add_usage(total, {"input_tokens": 50, "output_tokens": 5})
        self.assertEqual(total["turns"], 2)
        self.assertEqual(models.format_usage(total), "입력 150 (캐시 40) / 출력 15")
        self.assertEqual(models.format_usage({}), "")


class TestBackendEffort(unittest.TestCase):
    def test_effort_follows_session_choice(self):
        backend = CodexBackend("codex", str(ROOT))
        backend.model = ""
        backend.reasoning_effort = "medium"
        command = backend.build_resume_command("t", "x", [])
        self.assertNotIn("-m", command)
        self.assertIn('model_reasoning_effort="medium"', command)

    def test_astra_without_effort_keeps_high_default(self):
        backend = CodexBackend("codex", str(ROOT))
        backend.model = models.ASTRA_ID
        self.assertIn('model_reasoning_effort="high"', backend.build_initial_command("x"))

    def test_parse_usage_from_turn_completed(self):
        backend = CodexBackend("codex", str(ROOT))
        stdout = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            '{"type":"turn.completed","usage":{"input_tokens":1200,"cached_input_tokens":800,'
            '"output_tokens":300}}\n'
        )
        self.assertEqual(backend.parse_usage(stdout),
                         {"input_tokens": 1200, "cached_input_tokens": 800, "output_tokens": 300})
        self.assertIsNone(backend.parse_usage('{"type":"item.completed"}'))


class TestVerdictParsing(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(parsing.parse_verdict("VERDICT: OK\n설명"), (True, []))

    def test_fix_collects_defects(self):
        ok, defects = parsing.parse_verdict(
            "VERDICT: FIX\n- FRONT 칸: 백미러가 떠 있음 → 지지대 끝에 놓기\n2. 창틀 어긋남\n\n잡담")
        self.assertFalse(ok)
        self.assertEqual(defects, ["FRONT 칸: 백미러가 떠 있음 → 지지대 끝에 놓기", "창틀 어긋남"])

    def test_missing_header(self):
        self.assertEqual(parsing.parse_verdict("결함 없음"), (None, []))

    def test_markdown_wrapped_headers_and_inline_defect(self):
        self.assertEqual(parsing.parse_verdict("**VERDICT: OK**"), (True, []))
        self.assertEqual(parsing.parse_verdict("`VERDICT: FIX`\n- 귀 없음"), (False, ["귀 없음"]))
        self.assertEqual(parsing.parse_verdict("VERDICT: **FIX**\n1) 꼬리"), (False, ["꼬리"]))
        self.assertEqual(parsing.parse_verdict("VERDICT: FIX — 바퀴 떠 있음"), (False, ["바퀴 떠 있음"]))
        self.assertEqual(parsing.parse_verdict("VERDICT: OKAY? no"), (None, []))

    def test_numeric_defect_bodies_are_kept(self):
        ok, defects = parsing.parse_verdict("VERDICT: FIX\n- 0.2m 내려라\n2. 3/4뷰 귀 없음")
        self.assertEqual(defects, ["0.2m 내려라", "3/4뷰 귀 없음"])


class TestReviewPrompts(unittest.TestCase):
    def test_verdict_prompt_asks_for_no_code(self):
        p = prompts.build_object_review_verdict_prompt("자동차", "r.png", 1, 1,
                                                       multiview="mv.png", attached=False)
        self.assertIn("VERDICT: OK", p)
        self.assertIn("VERDICT: FIX", p)
        self.assertIn("코드를 쓰지 마라", p)
        self.assertIn("시트 대조", p)
        self.assertIn("첫 턴에 첨부", p)   # 시트를 다시 보내지 않았음을 알린다
        self.assertNotIn("STATUS: DONE", p)

    def test_fix_prompt_lists_defects_and_points_to_history(self):
        p = prompts.build_object_review_fix_prompt(["창틀 어긋남", "바퀴 떠 있음"], None)
        self.assertIn("- 창틀 어긋남", p)
        self.assertIn("대화 기록에 있다", p)
        self.assertNotIn("```python", p)
        self.assertIn("STATUS: DONE", p)

    def test_direct_review_omits_code_when_history_has_it(self):
        p = prompts.build_object_review_prompt("a", "r.png", None, 1, 1)
        self.assertNotIn("```python", p)
        self.assertIn("대화 기록에 있다", p)
        with_code = prompts.build_object_review_prompt("a", "r.png", "x = 1", 1, 1)
        self.assertIn("```python\nx = 1", with_code)

    def test_compare_prompt_marks_unattached_sheet(self):
        p = prompts.build_character_compare_prompt("sheet.png", "r.png", None, 1, 1,
                                                   sheet_attached=False)
        self.assertIn("첫 턴에 첨부", p)
        self.assertNotIn("```python", p)


if __name__ == "__main__":
    unittest.main()
