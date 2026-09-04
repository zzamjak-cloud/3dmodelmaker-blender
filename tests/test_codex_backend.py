import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS_PACKAGE = "task2_agents"


def load_agent_module(module_name: str):
    """상대 import를 지원하도록 agents 모듈을 임시 패키지로 불러온다."""
    package = sys.modules.setdefault(AGENTS_PACKAGE, types.ModuleType(AGENTS_PACKAGE))
    package.__path__ = [str(ROOT / "agents")]
    full_name = f"{AGENTS_PACKAGE}.{module_name}"
    spec = importlib.util.spec_from_file_location(full_name, ROOT / "agents" / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


CodexBackend = load_agent_module("codex_cli").CodexBackend


class TestCodexCommandModelContract(unittest.TestCase):
    def setUp(self):
        self.workdir = str(ROOT)

    def test_initial_command_includes_explicit_astra_model(self):
        backend = CodexBackend("codex", self.workdir)
        backend.model = "gpt-6-astra"

        command = backend.build_initial_command("ignored")

        self.assertEqual(command[command.index("-m") + 1], "gpt-6-astra")

    def test_default_model_omits_model_flag(self):
        backend = CodexBackend("codex", self.workdir)

        self.assertNotIn("-m", backend.build_initial_command("ignored"))

    def test_resume_does_not_override_session_model(self):
        backend = CodexBackend("codex", self.workdir)
        backend.model = "gpt-6-astra"

        command = backend.build_resume_command("thread-1", "ignored", [])

        self.assertNotIn("-m", command)
        self.assertIn("thread-1", command)


if __name__ == "__main__":
    unittest.main()
