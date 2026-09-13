import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "task3_addon"
ASTRA_ERROR = "The model gpt-6-astra does not exist or you do not have access"
MCP_AUTH_NOISE = (
    "2026-09-04T07:55:26Z ERROR rmcp::transport::worker: worker quit with fatal: "
    "AuthRequired(AuthRequiredError { error=\"invalid_token\", "
    "error_description=\"Missing or invalid access token\" })"
)


class _Registry(dict):
    """Blender ID 컬렉션의 get 동작만 제공하는 테스트 대역."""


def _property(**kwargs):
    """Blender property 선언 인자를 관찰 가능한 값으로 보존한다."""
    return kwargs


def _install_bpy():
    """session과 properties import에 필요한 최소 bpy 모듈을 설치한다."""
    bpy = types.ModuleType("bpy")
    bpy.data = SimpleNamespace(scenes=_Registry(), collections=_Registry())
    bpy.context = SimpleNamespace(window_manager=None)
    bpy.types = SimpleNamespace(PropertyGroup=object, Scene=SimpleNamespace())
    bpy.utils = SimpleNamespace(register_class=lambda cls: None,
                                unregister_class=lambda cls: None)

    props = types.ModuleType("bpy.props")
    for name in ("BoolProperty", "CollectionProperty", "EnumProperty",
                 "FloatProperty", "IntProperty", "PointerProperty", "StringProperty"):
        setattr(props, name, _property)

    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda fn: fn
    app = types.ModuleType("bpy.app")
    app.handlers = handlers
    app.timers = SimpleNamespace(register=lambda *args, **kwargs: None)
    bpy.app = app

    sys.modules["bpy"] = bpy
    sys.modules["bpy.props"] = props
    sys.modules["bpy.app"] = app
    sys.modules["bpy.app.handlers"] = handlers
    return bpy


def _package(name: str, path: Path):
    """상대 import가 가능한 임시 package를 만든다."""
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module
    return module


def _load(name: str, path: Path):
    """지정한 package 이름으로 실제 소스 모듈을 불러온다."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_targets():
    """Blender 외부에서 session 통합 경계를 실행할 모듈 그래프를 구성한다."""
    bpy = _install_bpy()
    root = _package(PACKAGE, ROOT)
    core = _package(f"{PACKAGE}.core", ROOT / "core")
    agents = _package(f"{PACKAGE}.agents", ROOT / "agents")
    root.core = core
    root.agents = agents

    prefs = types.ModuleType(f"{PACKAGE}.preferences")
    prefs.current = None
    prefs.get_prefs = lambda: prefs.current
    sys.modules[prefs.__name__] = prefs
    root.preferences = prefs

    for name in ("capture", "executor", "lanes", "library", "loop",
                 "multiview", "prompts", "runner", "scheduler", "snapshots", "texgen"):
        module = types.ModuleType(f"{PACKAGE}.core.{name}")
        sys.modules[module.__name__] = module
        setattr(core, name, module)

    _load(f"{PACKAGE}.core.errors", ROOT / "core" / "errors.py")
    models = _load(f"{PACKAGE}.core.models", ROOT / "core" / "models.py")
    jobs = _load(f"{PACKAGE}.core.jobs", ROOT / "core" / "jobs.py")
    session = _load(f"{PACKAGE}.core.session", ROOT / "core" / "session.py")
    properties = _load(f"{PACKAGE}.properties", ROOT / "properties.py")
    return bpy, prefs, models, jobs, session, properties


BPY, PREFS, MODELS, JOBS, SESSION, PROPERTIES = _load_targets()


class TestJobModelFields(unittest.TestCase):
    def test_job_model_fields_have_runtime_defaults(self):
        annotations = PROPERTIES.LP3DJobItem.__annotations__

        self.assertEqual(annotations["requested_model"]["default"], "")
        self.assertEqual(annotations["effective_model"]["default"], "")
        self.assertFalse(annotations["model_fallback"]["default"])


class TestSessionModelRouting(unittest.TestCase):
    def setUp(self):
        SESSION._sessions.clear()
        self.workdir = Path(tempfile.mkdtemp(prefix="lp3d_task3_test_"))
        self.original_mkdtemp = SESSION.tempfile.mkdtemp
        SESSION.tempfile.mkdtemp = lambda **kwargs: str(self.workdir)
        self.job = SimpleNamespace(log="", state="PENDING", phase="")
        props = SimpleNamespace(job_by_uid=lambda uid: self.job)
        BPY.data.scenes["Scene"] = SimpleNamespace(lp3d=props)

    def tearDown(self):
        SESSION.tempfile.mkdtemp = self.original_mkdtemp
        SESSION._sessions.clear()
        BPY.data.scenes.clear()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _session(self, codex_model="ASTRA"):
        PREFS.current = SimpleNamespace(
            codex_model=codex_model,
            timeout=30,
            use_multiview=False,
            use_library=False,
        )
        return SESSION.GenerationSession(
            scene_name="Scene", uid=7, request="crate",
            exe="codex",
        )

    def test_astra_preference_reaches_initial_codex_command(self):
        session = self._session(codex_model="ASTRA")

        command = session.backend.build_initial_command("prompt")

        self.assertEqual(session.backend.model, "gpt-6-astra")
        self.assertEqual(command[command.index("-m") + 1], "gpt-6-astra")
        self.assertEqual(self.job.requested_model, "GPT-6 Astra")
        self.assertEqual(self.job.effective_model, "GPT-6 Astra")
        self.assertFalse(self.job.model_fallback)

    def test_old_default_preference_still_starts_astra(self):
        session = self._session(codex_model="DEFAULT")
        self.assertEqual(session.backend.model, "gpt-6-astra")
        self.assertEqual(session.max_iterations, 1)

    def test_start_log_records_requested_and_effective_provider_models(self):
        session = self._session()
        with (
            patch.object(SESSION.runner, "add_keepalive", create=True),
            patch.object(SESSION.prompts, "build_system_prompt",
                         return_value="system", create=True),
            patch.object(session.backend, "prepare_workdir"),
            patch.object(session, "_start_generation"),
        ):
            session.start()

        self.assertIn(
            "요청 모델: provider=codex, generation=GPT-6 Astra",
            self.job.log,
        )
        self.assertIn(
            "실제 모델: provider=codex, generation=GPT-6 Astra",
            self.job.log,
        )

    def test_success_finish_preserves_model(self):
        session = self._session()
        session.last_code = "print(1)"
        session._archive = lambda code: "entry-1"
        with (patch.object(SESSION.scheduler, "cancel_job", create=True),
              patch.object(SESSION.runner, "remove_keepalive", create=True)):
            session._finish("완료", ok=True)
        self.assertEqual(self.job.state, "DONE")
        self.assertEqual(self.job.effective_model, "GPT-6 Astra")

    def test_dispatch_caches_original_prompt_and_copies_images(self):
        session = self._session()
        session._submit_ai = lambda fn: None
        images = ["view.png"]

        session._dispatch("원본 요청", images=images)
        images.append("late.png")

        self.assertEqual(session._pending_prompt, "원본 요청")
        self.assertEqual(session._pending_images, ["view.png"])


class TestSessionModelFallback(unittest.TestCase):
    setUp = TestSessionModelRouting.setUp
    tearDown = TestSessionModelRouting.tearDown
    _session = TestSessionModelRouting._session

    def test_callback_releases_slot_and_resubmits_original_initial_request(self):
        session = self._session()
        submissions = []
        released = []
        launched = []
        session._submit_ai = submissions.append
        session._launch = lambda cmd, prompt: launched.append((cmd, prompt))
        SESSION._sessions[session.uid] = session

        session._dispatch("원본 요청", images=["view.png"])
        with patch.object(SESSION.scheduler, "release_ai",
                          side_effect=released.append, create=True):
            session._on_response("", ASTRA_ERROR)
        submissions[-1]()

        self.assertEqual(released, [session.uid])
        self.assertEqual(len(submissions), 2)
        self.assertNotIn("-m", launched[0][0])
        self.assertEqual(launched[0][1], "원본 요청")
        self.assertIn("view.png", launched[0][0])
        self.assertTrue(session.model_fallback_used)
        self.assertEqual(session.effective_model_label, "Codex CLI 기본 모델")
        self.assertTrue(self.job.model_fallback)
        self.assertIn("gpt-6-astra does not exist", self.job.log)
        self.assertIn(
            "실제 모델: provider=codex, generation=Codex CLI 기본 모델",
            self.job.log,
        )

    def test_model_fallback_is_single_use(self):
        session = self._session()
        session._submit_ai = lambda fn: None
        session._dispatch("원본 요청")

        self.assertTrue(session._try_model_fallback(ASTRA_ERROR))
        self.assertFalse(session._try_model_fallback(ASTRA_ERROR))

    def test_mcp_auth_noise_does_not_block_astra_fallback(self):
        session = self._session()
        session._submit_ai = lambda fn: None
        session._dispatch("원본 요청")
        composite_error = f"{MCP_AUTH_NOISE}\n{ASTRA_ERROR}"

        self.assertTrue(session._try_model_fallback(composite_error))
        self.assertTrue(self.job.model_fallback)

    def test_non_model_failures_do_not_fallback(self):
        errors = (
            "HTTP 401 Unauthorized",
            "HTTP 401: model gpt-6-astra does not exist or you do not have access",
            "HTTP 429 rate_limit",
            "connection refused",
            "CLI 시간 초과 (30초)",
        )
        for error in errors:
            with self.subTest(error=error):
                session = self._session()
                session._submit_ai = lambda fn: None
                session._dispatch("원본 요청")
                self.assertFalse(session._try_model_fallback(error))

    def test_resume_failure_does_not_trigger_model_fallback(self):
        session = self._session()
        session.session_id = "thread-1"
        session._was_resume = True
        session._pending_prompt = "후속 요청"
        session._pending_images = []

        self.assertFalse(session._try_model_fallback(ASTRA_ERROR))

    def test_later_initial_style_request_does_not_trigger_model_fallback(self):
        session = self._session()
        session._submit_ai = lambda fn: None

        session._dispatch("첫 요청")
        session._dispatch("형식 재요청")

        self.assertFalse(session._try_model_fallback(ASTRA_ERROR))


class TestResetStaleModelTracking(unittest.TestCase):
    def test_retry_clears_model_tracking_before_start_validation(self):
        job = SimpleNamespace(
            uid=42,
            state="FAILED",
            status="이전 실패",
            status_hint="이전 안내",
            log="이전 로그",
            iteration=3,
            phase="GEN",
            requested_model="GPT-6 Astra",
            effective_model="Codex CLI 기본 모델",
            model_fallback=True,
        )
        context = SimpleNamespace(
            scene=SimpleNamespace(lp3d=SimpleNamespace(jobs=[job])))

        def fail_validation(_context, current_job):
            self.assertEqual(current_job.phase, "")
            self.assertEqual(current_job.requested_model, "")
            self.assertEqual(current_job.effective_model, "")
            self.assertFalse(current_job.model_fallback)
            return "Codex CLI를 찾을 수 없습니다"

        with patch.object(JOBS, "start_one", side_effect=fail_validation):
            error = JOBS.retry_job(context, 0)

        self.assertEqual(error, "Codex CLI를 찾을 수 없습니다")
        self.assertEqual(job.state, "FAILED")
        self.assertEqual(job.requested_model, "")
        self.assertEqual(job.effective_model, "")

    def test_reset_stale_clears_all_model_tracking_fields(self):
        job = SimpleNamespace(
            uid=41,
            state="RUNNING",
            status="실행 중",
            phase="GEN",
            requested_model="GPT-6 Astra",
            effective_model="GPT-6 Astra",
            model_fallback=True,
        )
        props = SimpleNamespace(jobs=[job])

        with patch.object(SESSION, "is_active", return_value=False):
            self.assertEqual(JOBS.reset_stale(props), 1)

        self.assertEqual(job.state, "PENDING")
        self.assertEqual(job.requested_model, "")
        self.assertEqual(job.effective_model, "")
        self.assertFalse(job.model_fallback)


if __name__ == "__main__":
    unittest.main()

class TestSingleGeneration(unittest.TestCase):
    setUp = TestSessionModelRouting.setUp
    tearDown = TestSessionModelRouting.tearDown
    _session = TestSessionModelRouting._session

    def test_first_success_finalizes_without_another_ai_request(self):
        for status in ("REVISE", "DONE"):
            with self.subTest(status=status):
                session = self._session()
                with (patch.object(SESSION.executor, "execute",
                                   return_value=(True, None), create=True),
                      patch.object(session, "_finalize") as finalize,
                      patch.object(session, "_dispatch") as dispatch):
                    session._blender_execute("model_code", status)
                finalize.assert_called_once_with()
                dispatch.assert_not_called()
                self.assertEqual(session.iteration, 1)
                self.assertEqual(session.last_code, "model_code")

    def test_done_without_code_requires_code_and_never_finalizes(self):
        session = self._session()
        with (patch.object(session.backend, "parse_response",
                           return_value=SimpleNamespace(text="STATUS: DONE", session_id=None)),
              patch.object(session, "_dispatch") as dispatch,
              patch.object(session, "_finalize") as finalize):
            session._handle_response("", None)
        dispatch.assert_called_once()
        finalize.assert_not_called()

    def test_execution_failure_repair_stays_in_first_turn(self):
        session = self._session()
        with (patch.object(SESSION.executor, "execute",
                           return_value=(False, "ValueError: invalid"), create=True),
              patch.object(SESSION.prompts, "build_error_prompt",
                           return_value="오류 복구", create=True),
              patch.object(session, "_dispatch") as dispatch):
            session._blender_execute("bad_code", "DONE")
        dispatch.assert_called_once_with("오류 복구")
        self.assertEqual(session.iteration, 1)
