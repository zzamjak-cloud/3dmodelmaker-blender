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
        BPY.data.collections.clear()
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
        # 제거된 후속 턴 설정은 되살아나지 않는다 — 늘어나는 것은 오브젝트 시각 검토 턴뿐
        self.assertEqual(session.max_iterations, 1 + session.review_turns_total)

    def test_object_review_turn_defaults_to_zero(self):
        # 실측에서 검토 턴이 품질을 올리지 못했고 비용만 늘어 기본은 생성 1턴이다
        session = self._session()
        self.assertEqual(session.review_turns_total, 0)
        self.assertEqual(session.max_iterations, 1)

    def test_object_review_turn_can_be_enabled(self):
        self._session()
        PREFS.current.object_review_turns = 1
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate", exe="codex")
        self.assertEqual(session.review_turns_total, 1)
        self.assertEqual(session.max_iterations, 2)

    def test_object_review_turn_can_be_disabled(self):
        self._session()
        PREFS.current.object_review_turns = 0
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate", exe="codex")
        self.assertEqual(session.review_turns_total, 0)
        self.assertEqual(session.max_iterations, 1)

    def test_scene_asset_uses_its_own_review_setting(self):
        # 배경 에셋(부모 uid가 있는 자식 잡)은 오브젝트 설정이 아니라 배경 에셋 설정을 따른다
        self._session()
        self.job.parent_uid = "3"
        PREFS.current.object_review_turns = 2
        PREFS.current.scene_asset_review_turns = 0
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate", exe="codex")
        self.assertEqual(session.review_turns_total, 0)
        PREFS.current.scene_asset_review_turns = 1
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate", exe="codex")
        self.assertEqual(session.review_turns_total, 1)

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

    def test_resume_failure_on_default_model_turn_does_not_trigger_model_fallback(self):
        # 기본 모델로 라우팅된 후속 턴(검토 등)의 오류는 Astra 폴백 대상이 아니다
        session = self._session()
        session.session_id = "thread-1"
        session._was_resume = True
        session.backend.model = ""
        session._pending_prompt = "후속 요청"
        session._pending_images = []

        self.assertFalse(session._try_model_fallback(ASTRA_ERROR))

    def test_resume_turn_on_astra_falls_back_keeping_session(self):
        # 검토를 Astra로 설정했는데 계정에 Astra가 없으면 세션을 유지한 채 그 턴을 기본 모델로 다시 보낸다
        session = self._session()
        launched = []
        session._submit_ai = lambda fn: fn()
        session._launch = lambda cmd, prompt: launched.append((prompt, cmd))
        session.session_id = "thread-1"
        session.routing[SESSION.models.ROLE_REVIEW] = (SESSION.models.ASTRA_ID, "medium")
        session._dispatch("검토 요청", images=["r.png"], role=SESSION.models.ROLE_REVIEW)
        self.assertIn("-m", launched[-1][1])

        self.assertTrue(session._try_model_fallback(ASTRA_ERROR))
        self.assertEqual(session.session_id, "thread-1")
        self.assertTrue(session.model_fallback_used)
        self.assertEqual(launched[-1][0], "검토 요청")
        self.assertIn("resume", launched[-1][1])
        self.assertNotIn("-m", launched[-1][1])
        self.assertIn("r.png", launched[-1][1])
        self.assertFalse(session._try_model_fallback(ASTRA_ERROR))   # 1회만

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
        # 1차 자기수정은 저렴한 수정 모델로 간다
        dispatch.assert_called_once_with("오류 복구", role="fix")
        self.assertEqual(session.iteration, 1)


# 세션 테스트 그래프의 prompts는 빈 스텁이다 — 검토 프롬프트만 실제 모듈에서 빌려 쓴다
REAL_PROMPTS = _load(f"{PACKAGE}.core.prompts_real", ROOT / "core" / "prompts.py")


class TestCostRoutingInSession(unittest.TestCase):
    """턴 역할별 모델 라우팅 · 2단계 검토 · 검토 생략 · 사용량 로그가 세션 흐름에 실제로 걸리는지."""
    setUp = TestSessionModelRouting.setUp
    tearDown = TestSessionModelRouting.tearDown
    _session = TestSessionModelRouting._session

    def _with_mesh(self, session):
        """검토 분기에 들어가도록 세션 컬렉션에 메시 하나를 둔다."""
        BPY.data.collections[session.collection_name] = SimpleNamespace(
            objects=[SimpleNamespace(type='MESH')])

    def _dispatched(self, session):
        """_dispatch가 만든 명령을 모아 (프롬프트, 명령) 목록으로 돌려준다."""
        launched = []
        session._submit_ai = lambda fn: fn()
        session._launch = lambda cmd, prompt: launched.append((prompt, cmd))
        return launched

    def test_generation_turn_is_astra_high_and_review_turn_is_default_medium(self):
        session = self._session()
        launched = self._dispatched(session)
        session._dispatch("생성")
        session.session_id = "thread-1"
        session._dispatch("검토", role=SESSION.models.ROLE_REVIEW)

        gen_cmd, review_cmd = launched[0][1], launched[1][1]
        self.assertEqual(gen_cmd[gen_cmd.index("-m") + 1], "gpt-6-astra")
        self.assertIn('model_reasoning_effort="high"', gen_cmd)
        self.assertNotIn("-m", review_cmd)
        self.assertIn('model_reasoning_effort="medium"', review_cmd)

    def test_astra_fallback_applies_to_every_astra_role(self):
        session = self._session()
        PREFS.current.review_model = 'ASTRA'
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate", exe="codex")
        session._submit_ai = lambda fn: None
        session._dispatch("첫 요청")
        self.assertTrue(session._try_model_fallback(ASTRA_ERROR))
        self.assertEqual(session._route(SESSION.models.ROLE_REVIEW)[0], "")
        self.assertEqual(session._route(SESSION.models.ROLE_GENERATE)[0], "")

    def test_second_exec_retry_escalates_to_generate_model(self):
        session = self._session()
        session.exec_retries = 1
        with (patch.object(SESSION.executor, "execute",
                           return_value=(False, "ValueError: invalid"), create=True),
              patch.object(SESSION.prompts, "build_error_prompt",
                           return_value="오류 복구", create=True),
              patch.object(session, "_dispatch") as dispatch):
            session._blender_execute("bad_code", "DONE")
        dispatch.assert_called_once_with("오류 복구", role="generate")

    def test_clean_diagnostics_without_sheet_skip_review(self):
        session = self._session()
        session.session_id = "thread-1"
        self._with_mesh(session)
        with (patch.object(SESSION.review_diag, "shell_boxes", return_value=[], create=True),
              patch.object(SESSION.review_diag, "describe", return_value=[], create=True),
              patch.object(session, "_render_sheet") as render,
              patch.object(session, "_dispatch") as dispatch):
            self.assertFalse(session._dispatch_review("code"))
        render.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual(session.review_turns_left, 0)
        self.assertIn("검토 턴 생략", self.job.log)

    def test_two_stage_review_sends_verdict_then_fix_only_when_defective(self):
        self._session()
        PREFS.current.review_mode = 'TWO_STAGE'
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate", exe="codex")
        session.session_id = "thread-1"
        session.last_code = "x = 1"
        self._with_mesh(session)
        with (patch.object(SESSION.review_diag, "shell_boxes", return_value=[], create=True),
              patch.object(SESSION.review_diag, "describe",
                           return_value=["짝 없는 파트: bar"], create=True),
              patch.object(session, "_render_sheet", return_value="review_2.png"),
              patch.object(SESSION.prompts, "build_object_review_verdict_prompt",
                           side_effect=REAL_PROMPTS.build_object_review_verdict_prompt, create=True),
              patch.object(SESSION.prompts, "build_object_review_fix_prompt",
                           side_effect=REAL_PROMPTS.build_object_review_fix_prompt, create=True),
              patch.object(session, "_dispatch") as dispatch):
            self.assertTrue(session._dispatch_review("x = 1"))
        prompt = dispatch.call_args.args[0]
        self.assertEqual(dispatch.call_args.kwargs["role"], "review")
        self.assertEqual(dispatch.call_args.kwargs["images"], ["review_2.png"])  # 시트·참조 재첨부 없음
        self.assertIn("VERDICT", prompt)
        self.assertNotIn("```python", prompt)   # 코드는 기록에 있으니 싣지 않는다
        self.assertTrue(session._awaiting_verdict)

        # 결함 없음 → 수정 턴 없이 마무리
        with (patch.object(session, "_dispatch") as dispatch,
              patch.object(session, "_finalize") as finalize):
            session._handle_reply("VERDICT: OK")
        dispatch.assert_not_called()
        finalize.assert_called_once_with()

        # 결함 있음 → 수정 모델에 결함 목록만 넘긴다
        session._awaiting_verdict = True
        with (patch.object(session, "_dispatch") as dispatch,
              patch.object(SESSION.prompts, "build_object_review_fix_prompt",
                           side_effect=REAL_PROMPTS.build_object_review_fix_prompt, create=True),
              patch.object(session, "_finalize") as finalize):
            session._handle_reply("VERDICT: FIX\n- 창틀 어긋남")
        finalize.assert_not_called()
        self.assertEqual(dispatch.call_args.kwargs["role"], "fix")
        self.assertIn("- 창틀 어긋남", dispatch.call_args.args[0])
        self.assertFalse(session._awaiting_verdict)

    def test_direct_review_mode_sends_full_fix_prompt_to_review_model(self):
        session = self._session()   # 기본값이 즉시 수정이다
        session.session_id = None   # 기록 없음 → 코드·이미지를 싣는다
        session.ref_image = "ref.png"
        self._with_mesh(session)
        with (patch.object(SESSION.review_diag, "shell_boxes", return_value=[], create=True),
              patch.object(SESSION.review_diag, "describe", return_value=["떠 있음"], create=True),
              patch.object(session, "_render_sheet", return_value="review_2.png"),
              patch.object(SESSION.prompts, "build_object_review_prompt",
                           side_effect=REAL_PROMPTS.build_object_review_prompt, create=True),
              patch.object(session, "_dispatch") as dispatch):
            self.assertTrue(session._dispatch_review("x = 1"))
        self.assertEqual(dispatch.call_args.kwargs["role"], "review")
        self.assertEqual(dispatch.call_args.kwargs["images"], ["review_2.png", "ref.png"])
        self.assertIn("```python\nx = 1", dispatch.call_args.args[0])
        self.assertFalse(session._awaiting_verdict)

    def test_usage_is_logged_per_turn_and_summed_at_finish(self):
        session = self._session()
        session._turn_role = SESSION.models.ROLE_REVIEW
        session._log_usage({"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50})
        session._log_usage({"input_tokens": 200, "output_tokens": 20})
        self.assertIn("토큰 [검토 · GPT-6 Astra · high] 입력 1,000 (캐시 600) / 출력 50", self.job.log)
        session._archive = lambda code: "entry-1"
        with (patch.object(SESSION.scheduler, "cancel_job", create=True),
              patch.object(SESSION.runner, "remove_keepalive", create=True)):
            session._finish("완료", ok=True)
        self.assertIn("토큰 합계 (2턴): 입력 1,200 (캐시 600) / 출력 70", self.job.log)

    def test_review_override_disables_review_turns(self):
        self._session()
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate",
                                            exe="codex", review_override=False)
        self.assertEqual(session.review_turns_left, 0)

    def test_resume_failure_while_awaiting_verdict_finalizes_without_review(self):
        session = self._session()
        session.session_id = "thread-1"
        session.last_code = "x = 1"
        session._turn_role = SESSION.models.ROLE_REVIEW
        session._awaiting_verdict = True
        with (patch.object(session, "_dispatch") as dispatch,
              patch.object(session, "_finalize") as finalize):
            session._resume_fallback_dispatch()
        dispatch.assert_not_called()
        finalize.assert_called_once_with()
        self.assertFalse(session._awaiting_verdict)

    def test_resume_failure_on_fix_turn_resends_defect_list(self):
        session = self._session()
        session.session_id = "thread-1"
        session.last_code = "x = 1"
        session.stateless = True
        session._turn_role = SESSION.models.ROLE_FIX
        session._review_defects = ["창틀 어긋남"]
        with (patch.object(SESSION.prompts, "build_object_review_fix_prompt",
                           side_effect=REAL_PROMPTS.build_object_review_fix_prompt, create=True),
              patch.object(session, "_dispatch") as dispatch):
            session._resume_fallback_dispatch()
        self.assertEqual(dispatch.call_args.kwargs["role"], "fix")
        self.assertIn("- 창틀 어긋남", dispatch.call_args.args[0])
        self.assertNotIn("```python", dispatch.call_args.args[0])   # stateless 머리말이 코드를 붙인다

    def test_format_violation_on_fix_turn_keeps_previous_result(self):
        session = self._session()
        session.last_code = "x = 1"
        session.format_retries = 1
        session._turn_role = SESSION.models.ROLE_FIX
        with (patch.object(session, "_dispatch") as dispatch,
              patch.object(session, "_finalize") as finalize,
              patch.object(session, "_finish") as finish):
            session._handle_reply("코드 없음")
        dispatch.assert_not_called()
        finish.assert_not_called()
        finalize.assert_called_once_with()

    def test_failed_turn_usage_is_still_counted(self):
        session = self._session()
        stdout = '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}'
        with patch.object(session, "_handle_error"):
            session._handle_response(stdout, "CLI 종료 코드 1")
        self.assertEqual(session.usage_total.get("input_tokens"), 10)

    def test_reference_image_keeps_review_even_when_diagnostics_clean(self):
        session = self._session()
        session.session_id = "thread-1"
        session.ref_image = "ref.png"
        self._with_mesh(session)
        with (patch.object(SESSION.review_diag, "shell_boxes", return_value=[], create=True),
              patch.object(SESSION.review_diag, "describe", return_value=[], create=True),
              patch.object(session, "_render_sheet", return_value="review_2.png"),
              patch.object(SESSION.prompts, "build_object_review_prompt",
                           side_effect=REAL_PROMPTS.build_object_review_prompt, create=True),
              patch.object(session, "_dispatch") as dispatch):
            self.assertTrue(session._dispatch_review("x = 1"))
        dispatch.assert_called_once()

    def test_fix_turn_does_not_exceed_declared_turn_count(self):
        self._session()
        PREFS.current.object_review_turns = 1
        session = SESSION.GenerationSession(scene_name="Scene", uid=7, request="crate", exe="codex")
        session.session_id = "thread-1"
        session.last_code = "x = 1"
        session.iteration = 2   # 검토 턴에서 이미 올렸다
        session._awaiting_verdict = True
        with (patch.object(session, "_dispatch"),
              patch.object(SESSION.prompts, "build_object_review_fix_prompt",
                           side_effect=REAL_PROMPTS.build_object_review_fix_prompt, create=True)):
            session._handle_reply("VERDICT: FIX\n- 결함")
        self.assertEqual(session.iteration, 2)
        self.assertLessEqual(session.iteration, session.max_iterations)
