# 생성 세션 상태머신: 프롬프트 → 코드 생성 → 실행 → 마무리
import logging
import os
import re
import shutil
import tempfile
import time

import bpy

_log = logging.getLogger(__name__)

from .. import preferences
from ..agents.codex_cli import CodexBackend
from ..agents.parsing import parse_agent_reply
from . import (errors, executor, jobs, library, models, multiview,
               prompts, runner, scheduler, snapshots, texgen)

_sessions = {}  # uid -> GenerationSession. 여러 세션이 동시에 진행될 수 있다


class _bake_context:
    """타이머 콜백에서 렌더·depsgraph를 쓰기 위한 컨텍스트 — 창이 있으면 그 창과 씬으로 override."""

    def __init__(self, scene_name):
        self.scene = bpy.data.scenes.get(scene_name)
        self._override = None

    def __enter__(self):
        if self.scene is None:
            raise RuntimeError("세션 씬을 찾을 수 없습니다")  # 다른 씬에서 렌더하지 않는다
        wm = bpy.context.window_manager
        window = wm.windows[0] if wm and wm.windows else None
        if window is not None and self.scene is not None:
            self._override = bpy.context.temp_override(window=window, scene=self.scene)
            self._override.__enter__()
        return bpy.context

    def __exit__(self, *exc):
        if self._override is not None:
            self._override.__exit__(*exc)
        return False


def is_active(uid=None) -> bool:
    """세션 진행 여부. uid를 주면 그 잡만, 없으면 하나라도 돌고 있는지."""
    if uid is None:
        return bool(_sessions)
    return uid in _sessions


def active_count() -> int:
    return len(_sessions)


def start_job(scene_name, uid, variation_of=None, variation_count=3):
    """잡 항목 하나의 생성 세션을 시작한다. 오류 메시지 또는 None을 반환한다.

    프롬프트·참조 이미지는 씬이 아니라 잡 항목에서 읽는다 —
    여러 항목이 서로 다른 설정으로 동시에 돌 수 있어야 하기 때문이다."""
    if uid in _sessions:
        return "이미 진행 중인 항목입니다"
    scene = bpy.data.scenes.get(scene_name)
    if not scene or not getattr(scene, "lp3d", None):
        return "씬을 찾을 수 없습니다"
    props = scene.lp3d
    job = props.job_by_uid(uid)
    if job is None:
        return "항목을 찾을 수 없습니다"

    request = job.prompt.strip()
    if not request:
        return "프롬프트를 입력하세요"
    exe = preferences.resolve_cli_path('CODEX')
    if not exe:
        return "Codex CLI를 찾을 수 없습니다. 환경설정에서 경로를 지정하세요"
    ref_image = None
    if job.ref_image_path.strip():
        ref_image = bpy.path.abspath(job.ref_image_path.strip())
        if not os.path.isfile(ref_image):
            return f"참조 이미지를 찾을 수 없습니다: {job.ref_image_path}"
        if os.path.splitext(ref_image)[1].lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            return "참조 이미지는 PNG/JPG/WEBP만 지원합니다"

    # 환경설정 변경이 즉시 반영되도록 세션 시작 때마다 한도를 갱신한다
    scheduler.set_ai_limit(getattr(preferences.get_prefs(), "ai_concurrency", 3))
    session = GenerationSession(
        scene_name=scene_name,
        uid=uid,
        request=request,
        exe=exe,
        variation_code=variation_of,
        variation_count=variation_count,
        ref_image=ref_image,
        lane=job.lane,
    )
    _sessions[uid] = session
    session.start()
    return None


def cancel_session(uid=None):
    """세션을 취소한다. uid가 없으면 전부 취소한다."""
    targets = [_sessions[uid]] if uid in _sessions else (
        list(_sessions.values()) if uid is None else [])
    for session in targets:
        session.cancel()


def cancel_all():
    cancel_session(None)


def shutdown():
    """모든 세션을 동기적으로 끝낸다. 정리한 세션 수를 반환한다.

    cancel()은 정리를 Blender 큐에 넣기만 하고 실제 실행은 runner 펌프(0.25초 주기)가
    맡는다. Dev Reload처럼 곧바로 모듈을 갈아끼우는 경로에서는 큐가 비워지기 전에
    scheduler가 리로드되어 대기열째 사라지고, 반쯤 만들어진 컬렉션과 턴 스냅샷이
    씬에 남는다. 그래서 여기서는 정리를 큐에 넣지 않고 그 자리에서 실행한다.
    사용자의 일반 취소는 UI 응답성을 위해 기존 cancel_all()(큐 경로)을 그대로 쓴다."""
    targets = list(_sessions.values())
    for session in targets:
        runner.cancel(session.uid)  # 서브프로세스부터 끊는다 — 콜백이 정리 뒤에 끼어들지 않도록
        scheduler.cancel_job(session.uid)
        try:
            session._blender_cancel_cleanup()
        except Exception:
            _log.exception("LP3D 동기 종료 정리 실패")
        _end_session(session.uid)  # 정리가 어디서 끊겼든 세션 기록은 반드시 지운다
    scheduler.reset()  # 죽은 세션이 남긴 대기 항목까지 비워 큐를 실제 상태와 맞춘다
    return len(targets)


def _end_session(uid):
    _sessions.pop(uid, None)
    scheduler.cancel_job(uid)
    runner.remove_keepalive(uid)


def _slug(text: str) -> str:
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "_", text)[:24].strip("_")
    return ascii_part or "Model"


class GenerationSession:
    def __init__(self, scene_name, uid, request, exe,
                 variation_code=None, variation_count=3,
                 ref_image=None, lane=0):
        self.scene_name = scene_name
        self.uid = uid
        self.lane = lane
        self.request = request
        self.max_iterations = 1
        self.variation_code = variation_code
        self.variation_count = variation_count
        self.workdir = tempfile.mkdtemp(prefix="lp3d_")
        # 참조 이미지는 workdir로 복사 — 에이전트가 상대경로(Read/-i)로 접근한다
        self.ref_image = None
        if ref_image:
            dest = os.path.join(self.workdir, "reference" + os.path.splitext(ref_image)[1].lower())
            shutil.copy(ref_image, dest)
            self.ref_image = dest
        self.multiview = None  # codex image_gen으로 생성한 멀티뷰 참조 시트 경로
        self.last_images = []  # 마지막 캡처 (라이브러리 썸네일용)
        self.backend = CodexBackend(exe, self.workdir)
        self.prefs = preferences.get_prefs()
        self.backend.model = models.ASTRA_ID
        # 실행 중 환경설정이 바뀌어도 이 세션의 요청 모델은 바뀌지 않는다
        self.requested_model_id = self.backend.model
        self.requested_model_label = models.model_label('CODEX', self.requested_model_id)
        self.effective_model_label = self.requested_model_label
        self.model_fallback_used = False
        self._pending_prompt = ""
        self._pending_images = []
        self._initial_dispatch_started = False
        self._model_fallback_eligible = False
        job = self._job()
        # 항목은 실행 중 삭제될 수 있으므로 타입은 시작 시점에 고정한다
        self.modeling_type = getattr(job, "modeling_type", 'PALETTE') if job else 'PALETTE'
        self.texture_path = None    # 개별 매핑 결과 PNG (보관 폴더)
        self._final_note = ""       # 마무리 통계 문구 (텍스처 단계 뒤에 붙인다)
        if job:
            job.requested_model = self.requested_model_label
            job.effective_model = self.effective_model_label
            job.model_fallback = False
            job.texture_path = ""
        # 런타임 상태
        self.session_id = None
        self.iteration = 1
        self.exec_retries = 0
        self.format_retries = 0
        self.stateless = False       # resume 실패 시 폴백 모드
        self.fallback_used = False
        self.last_code = None
        base = f"LP3D_{_slug(request)}"
        name, n = base, 1
        # 컬렉션 생성 전인 다른 세션의 이름도 점유로 본다.
        while (bpy.data.collections.get(name)
               or any(s.collection_name == name for s in _sessions.values())):
            n += 1
            name = f"{base}.{n:03d}"
        self.collection_name = name

    def _ref_name(self):
        return os.path.basename(self.ref_image) if self.ref_image else None

    def _mv_name(self):
        return os.path.basename(self.multiview) if self.multiview else None

    # ---------- 상태/로그 ----------
    def _job(self):
        """이 세션에 대응하는 잡 항목. 사용자가 삭제했으면 None."""
        scene = bpy.data.scenes.get(self.scene_name)
        if not scene or not getattr(scene, "lp3d", None):
            return None
        return scene.lp3d.job_by_uid(self.uid)

    def _set_status(self, status: str, log: str = None, phase: str = None, hint: str = ""):
        job = self._job()
        if job:
            job.status = status
            job.status_hint = hint
            job.iteration = self.iteration
            job.total_turns = self.max_iterations
            if phase is not None:
                job.phase = phase
            if log:
                lines = (job.log + "\n" + log).strip().splitlines()
                job.log = "\n".join(lines[-30:])
        if log:
            _log.info(log)
        self._redraw()

    def _model_label(self):
        return models.model_label('CODEX', self.backend.model)

    def _model_log(self, include_requested=True):
        """credential 없이 요청 및 실제 provider/model snapshot을 로그로 만든다."""
        lines = []
        if include_requested:
            lines.append(
                f"요청 모델: provider={self.backend.name}, "
                f"generation={self.requested_model_label}"
            )
        lines.append(
            f"실제 모델: provider={self.backend.name}, "
            f"generation={self.effective_model_label}"
        )
        return "\n".join(lines)

    @staticmethod
    def _redraw():
        wm = bpy.context.window_manager
        if not wm:
            return
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    # ---------- 큐 제출 ----------
    #
    # 스케줄러는 작업 하나가 터져도 큐가 멈추지 않도록 예외를 로그만 남기고 삼킨다.
    # 그래서 여기서 잡지 않으면 세션이 _sessions에 영원히 남아 is_active가 계속 참이
    # 되고(항목 재시작 불가), keepalive가 풀리지 않아 펌프도 계속 돈다.
    def _stale(self) -> bool:
        """이미 끝났거나 교체된 세션의 지연 콜백인지."""
        return _sessions.get(self.uid) is not self

    def _discard(self):
        """잡 항목이 사라진 세션의 미완성 컬렉션을 정리한다."""
        try:
            snapshots.clear_all(self.collection_name)
            executor.clear_collection(self.collection_name)
            coll = bpy.data.collections.get(self.collection_name)
            if coll and not coll.objects:
                bpy.data.collections.remove(coll)
        except Exception:
            _log.exception("LP3D 삭제된 항목 정리 실패")
        try:
            self._finish("항목 삭제됨", ok=False, state='CANCELLED')
        except Exception:
            _log.exception("LP3D 세션 마감 실패")
            _end_session(self.uid)

    def _fail(self, e):
        """단계 예외를 세션 실패로 마감한다. _finish 자체가 터져도 세션은 반드시 끝낸다."""
        _log.exception("LP3D 세션 단계 오류")
        try:
            self._finish(f"실패: 내부 오류 {type(e).__name__}: {e}", ok=False)
        except Exception:
            _log.exception("LP3D 세션 마감 실패")
            _end_session(self.uid)

    def _submit_blender(self, fn):
        """bpy 단계를 Blender 큐에 제출한다 (실행 시점의 유효성·예외를 함께 책임진다)."""
        def _step():
            if self._stale():
                return
            if self._job() is None:
                self._discard()  # 사용자가 리스트에서 항목을 지웠다
                return
            try:
                fn()
            except Exception as e:
                self._fail(e)

        scheduler.submit_blender(self.uid, _step)

    def _submit_ai(self, fn):
        """CLI 호출을 AI 슬롯 대기열에 제출한다. 호출 자체가 터지면 슬롯을 되돌린다."""
        def _step():
            if self._stale():
                scheduler.release_ai(self.uid)
                return
            try:
                fn()
            except Exception as e:
                scheduler.release_ai(self.uid)  # 콜백이 안 오므로 여기서 반환한다
                self._fail(e)

        scheduler.submit_ai(self.uid, _step)

    # ---------- 라이프사이클 ----------
    def start(self):
        job = self._job()
        if job:
            job.state = 'RUNNING'
            job.log = ""
            job.started_at = time.time()
            job.phase = ""
            job.status = "대기 중 (순서 기다리는 중)"
        self._set_status("대기 중 (순서 기다리는 중)", self._model_log())
        runner.add_keepalive(self.uid)
        self.backend.prepare_workdir(prompts.build_system_prompt())
        if self.variation_code:
            first = prompts.build_variation_prompt(self.request, self.variation_code,
                                                   self.variation_count)
            # 변형은 기존 코드 스타일을 따르므로 참조 이미지·멀티뷰 불필요
            self._set_status(f"코드 생성 — {self._model_label()} 호출 대기중...",
                             f"세션 시작: {self.request} ({self.backend.name})", phase='GEN')
            self._dispatch(first)
            return
        # 신규 생성: 멀티뷰 참조 시트를 먼저 생성 (codex image_gen — 없으면 스킵)
        if getattr(self.prefs, "use_multiview", True) and multiview.is_available():
            self._set_status("멀티뷰 참조 생성중 (codex image_gen)...",
                             "멀티뷰 참조 시트 생성 시작", phase='GEN')
            # 멀티뷰도 CLI 호출이므로 AI 슬롯을 점유한다
            self._submit_ai(self._run_multiview)
            return
        self._start_generation()

    def _run_multiview(self):
        multiview.generate(self.request, self.workdir, self.prefs.timeout,
                           self._on_multiview, ref_image=self.ref_image,
                           job_key=self.uid)

    def _on_multiview(self, path, error=None):
        if self._stale():
            return  # 이미 끝난 세션의 지연 콜백 — 슬롯은 _end_session이 이미 반환했다
        scheduler.release_ai(self.uid)
        if path:
            self.multiview = path
            # .blend 옆에 남겨 나중에 참조 이미지로 다시 쓸 수 있게 한다
            saved = multiview.archive(path, self.request)
            job = self._job()
            if job:
                job.multiview_path = saved or path
            if saved:
                self._set_status("멀티뷰 참조 생성 완료",
                                 f"멀티뷰 시트 저장: {saved}")
            else:
                self._set_status("멀티뷰 참조 생성 완료",
                                 "멀티뷰 시트 생성 완료 (파일 보관 실패 — 세션 중에만 사용)")
        else:
            # 시트가 없어도 모델링은 계속한다. 다만 로그인 만료처럼 조치 가능한
            # 원인은 상태줄에 그대로 드러내야 사용자가 고칠 수 있다.
            reason = errors.describe(error, 'codex') if error else "원인 불명"
            # 곧바로 코드 생성 단계가 상태줄을 덮어쓰므로, 조치까지 로그에 남긴다
            lines = ["멀티뷰 생성 실패 — 참조 시트 없이 계속 진행합니다"]
            todo = errors.action(error, 'codex') if error else ""
            if todo:
                lines.append(f"  → {todo}")
            lines += [f"  · {l}" for l in errors.detail_lines(error)]
            # 상태줄에 "— 참조 없이 진행"까지 붙이면 가운데가 잘려 정작 원인이 사라진다
            self._set_status(f"멀티뷰 실패: {reason}", "\n".join(lines))
        self._start_generation()

    def _start_generation(self):
        fewshot = []
        if getattr(self.prefs, "use_library", True):
            try:
                fewshot = library.fewshot_examples(self.request, limit=1)
            except Exception:
                _log.exception("few-shot 예시 조회 실패")  # 라이브러리 문제로 생성을 막지 않는다
            if fewshot:
                self._set_status("과거 합격 예시 참고", f"라이브러리 예시 {len(fewshot)}개 주입")
        first = prompts.build_initial_prompt(self.request, ref_image=self._ref_name(),
                                             multiview=self._mv_name(), fewshot=fewshot)
        init_images = [p for p in (self.ref_image, self.multiview) if p] or None
        self._set_status(f"코드 생성 — {self._model_label()} 호출중...",
                         f"세션 시작: {self.request} ({self.backend.name})", phase='GEN')
        self._dispatch(first, images=init_images)

    def cancel(self):
        runner.cancel(self.uid)
        scheduler.cancel_job(self.uid)
        # 정리도 bpy 조작이므로 큐를 통해 순서대로 실행한다
        scheduler.submit_blender(self.uid, self._blender_cancel_cleanup)

    def _blender_cancel_cleanup(self):
        # 취소 정리는 잡 항목이 지워진 뒤에도 돌아야 하므로 _submit_blender를 쓰지 않는다
        if self._stale():
            return
        try:
            snapshots.clear_all(self.collection_name)  # 취소된 세션의 중간 단계는 남기지 않는다
            executor.clear_collection(self.collection_name)
            coll = bpy.data.collections.get(self.collection_name)
            if coll and not coll.objects:
                bpy.data.collections.remove(coll)
            self._finish("취소됨", ok=False, state='CANCELLED')
        except Exception:
            # _finish 자체가 터졌을 수도 있다 — 세션은 반드시 여기서 끝낸다
            _log.exception("LP3D 취소 정리 실패")
            _end_session(self.uid)

    def _finish(self, status: str, ok: bool, detail=None, hint: str = "", state=None):
        job = self._job()
        if job:
            job.state = state or ('DONE' if ok else 'FAILED')
            if ok:
                job.collection_name = self.collection_name
                job.code = self.last_code or ""
                job.entry_id = self._archive(job.code)
        # 상태줄은 한 줄뿐이라 원인을 다 담을 수 없다 — 상세는 로그 패널에 남긴다
        log_text = self._model_log(include_requested=False) + f"\n세션 종료: {status}"
        if detail:
            log_text += "\n" + "\n".join(f"  · {line}" for line in detail)
        self._set_status(status, log_text, phase="", hint=hint)
        _end_session(self.uid)

    def _archive(self, code: str) -> str:
        """성공 결과를 라이브러리에 축적한다 — 실패해도 세션 결과에는 영향을 주지 않는다."""
        if self.variation_code or not getattr(self.prefs, "use_library", True):
            return ""
        try:
            from ..lowpoly.cleanup import collection_tri_count
            coll = bpy.data.collections.get(self.collection_name)
            tris = collection_tri_count(coll) if coll else 0
            thumb = self.last_images[0] if self.last_images else None
            return library.save_entry(self.request, code, stats={"tris": tris},
                                      thumbnail=thumb, multiview=self.multiview,
                                      agent=self.backend.name)
        except Exception:
            _log.exception("라이브러리 저장 실패")
            return ""

    # ---------- 에이전트 왕복 ----------
    def _dispatch(self, prompt: str, images=None):
        # fallback은 image hint가 붙기 전 원본 요청을 그대로 다시 보내야 한다
        self._pending_prompt = prompt
        self._pending_images = list(images or [])
        if images:
            prompt += self.backend.image_prompt_hint(images)
        use_resume = self.session_id and not self.stateless
        self._model_fallback_eligible = not self._initial_dispatch_started and not use_resume
        self._initial_dispatch_started = True
        if self.stateless and self.last_code:
            # 폴백: 세션 기억이 없으므로 맥락을 프롬프트에 인라인
            prompt = (
                f"(세션 요약) 원 요청: {self.request}\n"
                f"현재 코드:\n```python\n{self.last_code}\n```\n\n" + prompt
            )
        if use_resume:
            cmd = self.backend.build_resume_command(self.session_id, prompt, images or [])
        else:
            cmd = self.backend.build_initial_command(prompt, images or [])
        self._was_resume = use_resume
        # AI 슬롯이 빌 때까지 대기했다가 실행된다 (동시 실행 수는 환경설정)
        self._submit_ai(lambda: self._launch(cmd, prompt))

    def _launch(self, cmd, prompt):
        # 프롬프트는 stdin으로 — 명령줄 인자는 Windows .cmd 셸림에서 첫 줄만 전달된다
        runner.run_cli_async(cmd, self.workdir, self.prefs.timeout, self._on_response,
                             stdin_text=prompt, job_key=self.uid)

    def _on_response(self, stdout, error):
        # 콜백에서 예외가 나면 runner가 로그만 남기고 삼켜서 세션이 영구히 진행 중으로
        # 남는다(취소/생성 모두 잠김). 여기서 반드시 세션을 종료시킨다.
        if self._stale():
            return  # 이미 끝난 세션의 지연 콜백 — 슬롯은 _end_session이 이미 반환했다
        scheduler.release_ai(self.uid)
        if self._job() is None:
            # 사용자가 리스트에서 항목을 지웠다 — 조용히 정리하고 끝낸다
            _end_session(self.uid)
            return
        try:
            self._handle_response(stdout, error)
        except Exception as e:
            _log.exception("LP3D 세션 처리 오류")
            self._finish(f"실패: 내부 오류 {type(e).__name__}: {e}", ok=False)

    def _handle_response(self, stdout, error):
        if error:
            if "사용자 취소" in error:
                return  # cancel()이 이미 정리함
            if self._try_model_fallback(error):
                return
            kind = errors.classify(error)
            reason = errors.describe(error, self.backend.name)
            # 인증·사용량 문제는 재시도해도 똑같이 실패한다 — 폴백을 건너뛰고
            # 바로 조치 문구를 보여준다 (예전에는 "CLI 종료 코드 1"만 보였다)
            retryable = kind not in (errors.AUTH, errors.QUOTA, errors.MISSING)
            # resume이 깨졌으면 stateless 폴백으로 1회 재시도
            if retryable and getattr(self, "_was_resume", False) and not self.fallback_used:
                self.fallback_used = True
                self.stateless = True
                self._set_status("세션 이어가기 실패, 폴백 재시도...",
                                 f"resume 실패: {reason}", phase='GEN')
                self._dispatch("직전 지시를 계속 수행하라. 전체 코드를 다시 작성하라.")
                return
            self._finish(f"실패: {reason}", ok=False, detail=errors.detail_lines(error),
                         hint=errors.action(error, self.backend.name))
            return

        try:
            reply = self.backend.parse_response(stdout)
        except Exception as e:
            self._finish(f"응답 파싱 실패: {e}", ok=False)
            return
        if reply.session_id:
            self.session_id = reply.session_id

        status, code = parse_agent_reply(reply.text)
        if code is None:
            if self.format_retries < 1:
                self.format_retries += 1
                self._set_status("형식 위반, 재요청...", "응답에 코드 블록 없음 — 형식 재요청", phase='GEN')
                self._dispatch(
                    "출력 형식 위반이다. 첫 줄 `STATUS: REVISE` 또는 `STATUS: DONE`, "
                    "이어서 모델 전체의 python 코드 블록 1개로 다시 답하라."
                )
                return
            self._finish("실패: 에이전트 응답 형식 위반", ok=False)
            return

        self._execute(code, status)

    def _try_model_fallback(self, error: str) -> bool:
        """초기 Astra 가용성 오류를 Codex CLI 기본 모델로 한 번 재시도한다."""
        terminal_kinds = (errors.AUTH, errors.QUOTA, errors.NETWORK,
                          errors.TIMEOUT, errors.MISSING)
        if (self.backend.name != "codex"
                or self.model_fallback_used
                or self.backend.model != models.ASTRA_ID
                or not self._model_fallback_eligible
                or self.session_id is not None
                or getattr(self, "_was_resume", False)
                or errors.classify(error) in terminal_kinds
                or not models.is_model_unavailable(error, models.ASTRA_ID)):
            return False

        # 재호출이 pending 값을 갱신하기 전에 원본 요청을 지역 변수에 고정한다
        prompt = self._pending_prompt
        images = list(self._pending_images)
        self.model_fallback_used = True
        self.backend.model = ""
        self.session_id = None
        self.effective_model_label = models.CODEX_DEFAULT_LABEL
        job = self._job()
        if job:
            job.effective_model = self.effective_model_label
            job.model_fallback = True
        reason = errors.describe(error, self.backend.name)
        log_lines = [
            "모델 fallback: GPT-6 Astra -> Codex CLI 기본 모델",
            f"fallback 판정: {reason}",
            self._model_log(include_requested=False),
        ]
        for detail in errors.detail_lines(error, limit=2):
            detail = detail[:200]
            if detail and detail not in reason and reason not in detail:
                log_lines.append(f"  · {detail}")
        self._set_status(
            "GPT-6 Astra 사용 불가 - Codex CLI 기본 모델로 재시도중...",
            "\n".join(log_lines),
        )
        self._dispatch(prompt, images=images)
        return True

    # ---------- 실행/마무리 ----------
    def _execute(self, code: str, status):
        self._set_status(f"Blender 실행 대기중 (턴 {self.iteration}/{self.max_iterations})...",
                         phase='EXEC')
        self._submit_blender(lambda: self._blender_execute(code, status))

    def _blender_execute(self, code: str, status):
        self._set_status(f"Blender 코드 실행중 (턴 {self.iteration}/{self.max_iterations})...",
                         phase='EXEC')
        ok, error = executor.execute(code, self.collection_name,
                                     seed=self.iteration, workdir=self.workdir)
        if not ok:
            if self.exec_retries < 2:
                self.exec_retries += 1
                self._set_status(f"실행 오류 — {self._model_label()} 자기수정 호출중...",
                                 f"실행 오류(재시도 {self.exec_retries}/2): {error.splitlines()[-1]}",
                                 phase='GEN')
                self._dispatch(prompts.build_error_prompt(error))
                return
            self._finish("실패: 코드 실행 오류 반복", ok=False)
            return

        self.exec_retries = 0
        self.last_code = code
        self._set_status(f"턴 {self.iteration} 생성 완료", f"턴 {self.iteration} 실행 성공")
        self._finalize()

    def _finalize(self):
        self._set_status("마무리 대기중...", phase='FINAL')
        self._submit_blender(self._blender_finalize)

    def _blender_finalize(self):
        from ..lowpoly.cleanup import collection_tri_count, cull_hidden_faces, game_ready
        self._set_status("마무리 정리중 (은면 제거·게임레디)...", phase='FINAL')
        coll = bpy.data.collections.get(self.collection_name)
        if coll:
            mesh_objs = [o for o in coll.objects if o.type == 'MESH']
            removed = cull_hidden_faces(mesh_objs)  # join(union)을 안 거친 잔여 은면 제거
            for obj in mesh_objs:
                game_ready(obj)
            tris = collection_tri_count(coll)
            # 배치 실행 결과가 원점에 겹치지 않도록 레인만큼 옆으로 민다 (텍스처 대기 중에도)
            jobs.apply_lane_offset(self.collection_name, self.lane)
            note = f", 은면 {removed}개 제거" if removed else ""
            self._final_note = f"{tris} tris{note}"
            if self.modeling_type == 'TEXTURE' and mesh_objs:
                if not texgen.is_available():
                    self._set_status("텍스처 생략: codex CLI 없음",
                                     "개별 매핑 생략 — codex CLI를 찾을 수 없어 팔레트로 마감")
                else:
                    # 언랩·렌더·베이크는 각각 한 틱을 통째로 막으므로 스텝을 나눠
                    # 상태줄이 실제로 갱신되고 다른 잡의 큐도 사이사이 진행되게 한다
                    self._set_status("UV 언랩 대기중...", phase='TEX')
                    self._submit_blender(self._blender_unwrap)
                    return
            self._finish_placed()
        else:
            self._finish("실패: 생성된 오브젝트 없음", ok=False)

    def _finish_placed(self, extra: str = ""):
        """성공 마감. extra는 텍스처 단계 결과 문구."""
        jobs.apply_lane_offset(self.collection_name, self.lane)  # 표식으로 이중 적용을 막는다
        self._finish(f"완료 — {self.collection_name} ({self._final_note}{extra})", ok=True)

    # ---------- 개별 매핑 (언랩 → 6면도 가이드 → AI 텍스처 → 베이크) ----------
    #
    # 텍스처 단계 실패는 비치명이다 — 기하는 이미 완성됐으므로 팔레트 재질을 그대로
    # 두고 원인만 상태·로그에 남긴다.
    def _texture_fallback(self, reason: str, error: str = None):
        from ..texturing import apply as tex_apply
        coll = bpy.data.collections.get(self.collection_name)
        if coll:
            tex_apply.discard([o for o in coll.objects if o.type == 'MESH'])
        lines = [f"개별 매핑 실패 — 팔레트 재질을 유지합니다: {reason}"]
        if error:
            todo = errors.action(error, 'codex')
            if todo:
                lines.append(f"  → {todo}")
            lines += [f"  · {l}" for l in errors.detail_lines(error)]
        self._set_status(f"텍스처 실패: {reason}", "\n".join(lines))
        self._finish_placed(extra=", 텍스처 실패 — 팔레트 유지")

    def _mesh_objs(self):
        coll = bpy.data.collections.get(self.collection_name)
        return [o for o in coll.objects if o.type == 'MESH'] if coll else []

    def _texture_step(self, fn, stage: str):
        """텍스처 단계 하나를 실행한다 — 예외는 세션 실패가 아니라 팔레트 폴백으로 흡수한다."""
        try:
            fn()
        except Exception as e:
            _log.exception("LP3D 텍스처 %s 실패", stage)
            self._texture_fallback(f"{stage} 오류 {type(e).__name__}: {e}")

    def _submit_ai_texture(self, fn):
        """_submit_ai와 같지만 제출 자체가 터져도 잡을 FAILED로 만들지 않는다."""
        def _step():
            if self._stale():
                scheduler.release_ai(self.uid)
                return
            try:
                fn()
            except Exception as e:
                scheduler.release_ai(self.uid)
                _log.exception("LP3D 텍스처 제출 실패")
                self._texture_fallback(f"제출 오류 {type(e).__name__}: {e}")

        scheduler.submit_ai(self.uid, _step)

    def _blender_unwrap(self):
        def _run():
            from ..texturing import unwrap as tex_unwrap
            self._set_status("UV 언랩중 (박스 투영)...", phase='TEX')
            info = tex_unwrap.unwrap_objects(self._mesh_objs())
            self._set_status("6면도 가이드 렌더 대기중...",
                             f"언랩 완료: 아일랜드 {info['islands']}개, 면 {info['faces']}개", phase='TEX')
            self._submit_blender(self._blender_guide)
        self._texture_step(_run, "언랩")

    def _blender_guide(self):
        def _run():
            from ..texturing import capture as tex_capture
            self._set_status("6면도 가이드 렌더중...", phase='TEX')
            guide_dir = os.path.join(self.workdir, "texture")
            with _bake_context(self.scene_name) as ctx:
                views = tex_capture.render_views(ctx, self._mesh_objs(), guide_dir)
            guide = tex_capture.join_sheet(views, os.path.join(guide_dir, "guide_sheet.png"))
            self._set_status("텍스처 6면도 생성중 (codex image_gen)...",
                             "텍스처 가이드 시트 생성 완료", phase='TEX')
            self._submit_ai_texture(lambda: texgen.generate(
                self.request, guide, self.workdir, self.prefs.timeout,
                self._on_texture_sheet, job_key=self.uid))
        self._texture_step(_run, "가이드 렌더")

    def _on_texture_sheet(self, path, error=None):
        if self._stale():
            return
        scheduler.release_ai(self.uid)
        # 펌프는 콜백 예외를 삼키므로 여기서 잡지 않으면 세션이 영원히 끝나지 않는다
        try:
            if not path:
                reason = errors.describe(error, 'codex') if error else "원인 불명"
                self._submit_blender(lambda: self._texture_fallback(reason, error))
                return
            self._set_status("텍스처 베이크 대기중...", "텍스처 시트 수신", phase='TEX')
            self._submit_blender(lambda: self._blender_bake_texture(path))
        except Exception as e:
            _log.exception("LP3D 텍스처 콜백 처리 실패")
            self._submit_blender(lambda: self._texture_fallback(f"콜백 오류 {type(e).__name__}: {e}"))

    def _blender_bake_texture(self, sheet_path: str):
        def _run():
            from ..texturing import apply as tex_apply, bake as tex_bake, capture as tex_capture
            mesh_objs = self._mesh_objs()
            if not mesh_objs:
                raise RuntimeError("생성된 오브젝트 없음")
            resolution = int(getattr(self.prefs, "texture_resolution", "1024"))
            name = tex_apply.texture_name(self.collection_name, mesh_objs)
            self._set_status(f"텍스처 베이크중 ({resolution}px, CPU)...", phase='TEX')
            views = tex_capture.split_sheet(sheet_path)
            png = os.path.join(self.workdir, "texture", f"{name}.png")
            with _bake_context(self.scene_name) as ctx:
                stats = tex_bake.rasterize_to_png(
                    ctx, mesh_objs, views, png, resolution, padding=8,
                    uv_layer_names=[tex_apply.TEXTURE_UV] * len(mesh_objs))
            self._set_status("텍스처 적용 대기중...",
                             f"베이크 완료: 채움 {stats.get('filled_pixels', 0)}px", phase='TEX')
            self._submit_blender(lambda: self._blender_apply_texture(png, name, resolution))
        self._texture_step(_run, "베이크")

    def _blender_apply_texture(self, png: str, name: str, resolution: int):
        def _run():
            from ..texturing import apply as tex_apply
            mesh_objs = self._mesh_objs()
            # 세션 임시 폴더는 사라지므로 보관 폴더에 복사한 파일을 이미지 원본으로 삼는다
            saved = multiview.unique_path(multiview.archive_dir(), name)
            shutil.copy(png, saved)
            result = tex_apply.finalize(mesh_objs, saved, name)
            self.texture_path = saved
            job = self._job()
            if job:
                job.texture_path = saved
            self._set_status("텍스처 적용 완료", f"텍스처 저장: {saved} (머티리얼 {result['material']})")
            self._finish_placed(extra=f", 텍스처 {result['image']} {resolution}px")
        self._texture_step(_run, "적용")
