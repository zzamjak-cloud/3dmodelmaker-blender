# 생성 세션 상태머신: 프롬프트 → 코드 생성 → 실행 → 캡처 → 비평 → 반복 → 마무리
import logging
import os
import re
import shutil
import tempfile
import time

import bpy

_log = logging.getLogger(__name__)

from .. import preferences
from ..agents.claude_cli import ClaudeBackend
from ..agents.codex_cli import CodexBackend
from ..agents.parsing import parse_agent_reply
from . import capture, executor, library, loop, multiview, prompts, runner

_current = None  # 동시 세션은 1개만 허용


def is_active() -> bool:
    return _current is not None


def start_session(context, variation_of=None, variation_count=3, improve=False):
    """UI에서 호출하는 진입점. variation_of는 변형 생성, improve는 개선 세션."""
    global _current
    if _current is not None:
        return "이미 생성 세션이 진행 중입니다"
    props = context.scene.lp3d
    if props.is_running:
        # 세션 객체는 없는데 플래그만 남은 상태 (Dev Reload·파일 다시 열기 등)
        props.is_running = False
    if improve:
        if not props.last_code or not props.last_collection:
            return "개선할 결과가 없습니다 — 먼저 모델을 생성하세요"
        coll = bpy.data.collections.get(props.last_collection)
        if not coll or not any(o.type == 'MESH' for o in coll.objects):
            return "개선할 모델 컬렉션을 찾을 수 없습니다"
        request = props.last_prompt.strip() or props.prompt.strip()
    else:
        request = (props.last_prompt if variation_of else props.prompt).strip()
    if not request:
        return "프롬프트를 입력하세요"
    exe = preferences.resolve_cli_path(props.agent)
    if not exe:
        return f"{props.agent} CLI를 찾을 수 없습니다. 환경설정에서 경로를 지정하세요"
    ref_image = None
    if props.ref_image_path.strip():
        ref_image = bpy.path.abspath(props.ref_image_path.strip())
        if not os.path.isfile(ref_image):
            return f"참조 이미지를 찾을 수 없습니다: {props.ref_image_path}"
        if os.path.splitext(ref_image)[1].lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            return "참조 이미지는 PNG/JPG/WEBP만 지원합니다"
    _current = GenerationSession(
        scene_name=context.scene.name,
        request=request,
        agent=props.agent,
        exe=exe,
        max_iterations=1 if improve else loop.total_turns(props.auto_turns),
        variation_code=variation_of,
        variation_count=variation_count,
        improve_code=props.last_code if improve else None,
        improve_feedback=props.improve_feedback.strip() if improve else "",
        improve_collection=props.last_collection if improve else None,
        ref_image=ref_image,
    )
    _current.start()
    return None


def cancel_session():
    if _current:
        _current.cancel()


def _end_session():
    global _current
    _current = None
    runner.set_keepalive(False)


def _slug(text: str) -> str:
    ascii_part = re.sub(r"[^A-Za-z0-9]+", "_", text)[:24].strip("_")
    return ascii_part or "Model"


class GenerationSession:
    def __init__(self, scene_name, request, agent, exe, max_iterations,
                 variation_code=None, variation_count=3,
                 improve_code=None, improve_feedback="", improve_collection=None,
                 ref_image=None):
        self.scene_name = scene_name
        self.request = request
        self.max_iterations = max_iterations
        self.variation_code = variation_code
        self.variation_count = variation_count
        self.improve_code = improve_code
        self.improve_feedback = improve_feedback
        self.improve_collection = improve_collection
        self.workdir = tempfile.mkdtemp(prefix="lp3d_")
        # 참조 이미지는 workdir로 복사 — 에이전트가 상대경로(Read/-i)로 접근한다
        self.ref_image = None
        if ref_image:
            dest = os.path.join(self.workdir, "reference" + os.path.splitext(ref_image)[1].lower())
            shutil.copy(ref_image, dest)
            self.ref_image = dest
        self.multiview = None  # codex image_gen으로 생성한 멀티뷰 참조 시트 경로
        self.last_images = []  # 마지막 캡처 (라이브러리 썸네일용)
        backend_cls = ClaudeBackend if agent == 'CLAUDE' else CodexBackend
        self.backend = backend_cls(exe, self.workdir)
        self.prefs = preferences.get_prefs()
        # 모델 드롭다운은 Claude 별칭 기준 — Codex는 CLI 기본 설정을 따른다
        if agent == 'CLAUDE':
            self.backend.model = "" if self.prefs.gen_model == 'DEFAULT' else self.prefs.gen_model
            self.backend.critique_model = ("" if self.prefs.critique_model == 'DEFAULT'
                                           else self.prefs.critique_model)
        # 런타임 상태
        self.session_id = None
        self.iteration = 1
        self.exec_retries = 0
        self.format_retries = 0
        self.stateless = False       # resume 실패 시 폴백 모드
        self.fallback_used = False
        self.last_code = None
        if improve_collection:
            self.collection_name = improve_collection  # 기존 결과를 제자리에서 개선
        else:
            base = f"LP3D_{_slug(request)}"
            name, n = base, 1
            while bpy.data.collections.get(name):
                n += 1
                name = f"{base}.{n:03d}"
            self.collection_name = name

    def _ref_name(self):
        return os.path.basename(self.ref_image) if self.ref_image else None

    def _mv_name(self):
        return os.path.basename(self.multiview) if self.multiview else None

    # ---------- 상태/로그 ----------
    def _props(self):
        scene = bpy.data.scenes.get(self.scene_name)
        return scene.lp3d if scene else None

    def _set_status(self, status: str, log: str = None, phase: str = None):
        props = self._props()
        if props:
            props.status = status
            props.iteration = self.iteration
            props.total_turns = self.max_iterations
            if phase is not None:
                props.phase = phase
            if log:
                lines = (props.log + "\n" + log).strip().splitlines()
                props.log = "\n".join(lines[-30:])
        if log:
            _log.info(log)
        self._redraw()

    def _model_label(self, critique=False):
        m = (self.backend.critique_model if critique else "") or self.backend.model
        return m.capitalize() if m else "기본 모델"

    @staticmethod
    def _redraw():
        wm = bpy.context.window_manager
        if not wm:
            return
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    # ---------- 라이프사이클 ----------
    def start(self):
        props = self._props()
        if props:
            props.is_running = True
            props.log = ""
            props.started_at = time.time()
            props.phase = ""
        runner.set_keepalive(True)
        self.backend.prepare_workdir(prompts.build_system_prompt())
        if self.improve_code:
            # 개선 세션: 현재 모델을 캡처해 첫 턴부터 이미지+코드+피드백으로 개선 요청
            self._set_status("현재 모델 캡처중...", phase='CAPTURE')
            images, stats = capture.capture_collection(
                self.collection_name, self.workdir,
                count=self.prefs.capture_count, resolution=self.prefs.capture_resolution,
                silhouettes=1,
            )
            if not images:
                self._finish("실패: 개선할 모델 캡처 불가", ok=False)
                return
            first = prompts.build_improve_prompt(
                self.request, self.improve_code, self.improve_feedback,
                [os.path.basename(p) for p in images], stats,
                ref_image=self._ref_name(),
            )
            if self.ref_image:
                images = images + [self.ref_image]
            self._set_status(f"스크린샷 분석·개선 — {self._model_label(critique=True)} 호출중...",
                             f"개선 세션 시작: {self.request}"
                             + (f" / 피드백: {self.improve_feedback}" if self.improve_feedback else ""),
                             phase='CRITIQUE')
            self._dispatch(first, images=images)
            return
        if self.variation_code:
            first = prompts.build_variation_prompt(self.request, self.variation_code,
                                                   self.variation_count)
            # 변형은 기존 코드 스타일을 따르므로 참조 이미지·멀티뷰 불필요
            self._set_status(f"코드 생성 — {self._model_label()} 호출중...",
                             f"세션 시작: {self.request} ({self.backend.name})", phase='GEN')
            self._dispatch(first)
            return
        # 신규 생성: 멀티뷰 참조 시트를 먼저 생성 (codex image_gen — 없으면 스킵)
        if getattr(self.prefs, "use_multiview", True) and multiview.is_available():
            self._set_status("멀티뷰 참조 생성중 (codex image_gen)...",
                             "멀티뷰 참조 시트 생성 시작", phase='GEN')
            multiview.generate(self.request, self.workdir, self.prefs.timeout,
                               self._on_multiview, ref_image=self.ref_image)
            return
        self._start_generation()

    def _on_multiview(self, path):
        if path:
            self.multiview = path
            self._set_status("멀티뷰 참조 생성 완료", "멀티뷰 참조 시트 생성 완료")
        else:
            self._set_status("멀티뷰 생성 실패 — 참조 없이 진행", "멀티뷰 생성 실패/불가 — 스킵")
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
        runner.cancel()
        executor.clear_collection(self.collection_name)
        coll = bpy.data.collections.get(self.collection_name)
        if coll and not coll.objects:
            bpy.data.collections.remove(coll)
        self._finish("취소됨", ok=False)

    def _finish(self, status: str, ok: bool):
        # 개선 실패로 기존 모델까지 사라졌으면 이전 코드로 복원한다
        if not ok and self.improve_code:
            coll = bpy.data.collections.get(self.collection_name)
            if not coll or not any(o.type == 'MESH' for o in coll.objects):
                restored, _ = executor.execute(self.improve_code, self.collection_name,
                                               seed=1, workdir=self.workdir)
                if restored:
                    status += " — 이전 결과 복원됨"
        props = self._props()
        if props:
            props.is_running = False
            if ok:
                props.last_collection = self.collection_name
                # 개선 세션이 코드 없이 DONE으로 끝나면 이전 코드를 유지
                props.last_code = self.last_code or self.improve_code or ""
                if not self.variation_code:
                    props.last_prompt = self.request
                props.improve_open = True  # 개선 UI 노출
                if self.improve_code:
                    props.improve_feedback = ""  # 반영된 피드백은 비움
                props.last_entry_id = self._archive(props.last_code)
        self._set_status(status, f"세션 종료: {status}", phase="")
        _end_session()

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
        if images:
            prompt += self.backend.image_prompt_hint(images)
        use_resume = self.session_id and not self.stateless
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
        # 프롬프트는 stdin으로 — 명령줄 인자는 Windows .cmd 셸림에서 첫 줄만 전달된다
        runner.run_cli_async(cmd, self.workdir, self.prefs.timeout, self._on_response,
                             stdin_text=prompt)

    def _on_response(self, stdout, error):
        # 콜백에서 예외가 나면 runner가 로그만 남기고 삼켜서 세션이 영구히 진행 중으로
        # 남는다(취소/생성 모두 잠김). 여기서 반드시 세션을 종료시킨다.
        try:
            self._handle_response(stdout, error)
        except Exception as e:
            _log.exception("LP3D 세션 처리 오류")
            self._finish(f"실패: 내부 오류 {type(e).__name__}: {e}", ok=False)

    def _handle_response(self, stdout, error):
        if error:
            if "사용자 취소" in error:
                return  # cancel()이 이미 정리함
            # resume이 깨졌으면 stateless 폴백으로 1회 재시도
            if getattr(self, "_was_resume", False) and not self.fallback_used:
                self.fallback_used = True
                self.stateless = True
                self._set_status("세션 이어가기 실패, 폴백 재시도...", f"resume 실패: {error.splitlines()[0]}", phase='GEN')
                self._dispatch("직전 지시를 계속 수행하라. 전체 코드를 다시 작성하라.")
                return
            self._finish(f"실패: {error.splitlines()[0]}", ok=False)
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
            if status == 'DONE':
                # 최소 턴 전의 DONE은 근거 없는 조기 종료 — 무시하고 계속 비평한다
                # (self.last_code가 없으면 비평할 모델 자체가 없으므로 그대로 마무리)
                if loop.allow_done(self.iteration, self.max_iterations) or not self.last_code:
                    self._finalize()
                else:
                    self._set_status("이른 DONE 무시 — 계속 개선", "최소 턴 전 DONE 선언 무시")
                    self._critique()
                return
            if self.format_retries < 1:
                self.format_retries += 1
                self._set_status("형식 위반, 재요청...", "응답에 코드 블록 없음 — 형식 재요청", phase='GEN')
                self._dispatch(
                    "출력 형식 위반이다. 첫 줄 `STATUS: REVISE` 또는 `STATUS: DONE`, "
                    "이어서 python 코드 블록 1개(수정 불필요 시 DONE만)로 다시 답하라."
                )
                return
            self._finish("실패: 에이전트 응답 형식 위반", ok=False)
            return

        self._execute(code, status)

    # ---------- 실행/비평 ----------
    def _execute(self, code: str, status):
        self._set_status(f"Blender 코드 실행중 (턴 {self.iteration}/{self.max_iterations})...", phase='EXEC')
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
        if loop.should_finalize(status, self.iteration, self.max_iterations):
            self._finalize()
        else:
            self._critique()

    def _critique(self):
        self._set_status("뷰포트 캡처중...", phase='CAPTURE')
        images, stats = capture.capture_collection(
            self.collection_name, self.workdir,
            count=self.prefs.capture_count, resolution=self.prefs.capture_resolution,
            silhouettes=1,  # 비평 속도를 위해 실루엣은 1장만 (iso)
        )
        if not images:
            self._finalize()  # 캡처할 게 없으면 그대로 마무리
            return
        self.last_images = list(images)
        self.iteration += 1
        prompt = prompts.build_critique_prompt(
            [os.path.basename(p) for p in images], stats,
            self.iteration, self.max_iterations,
            allow_done=loop.allow_done(self.iteration, self.max_iterations),
            ref_image=self._ref_name(),
            multiview=self._mv_name(),
        )
        # 비평 턴마다 참조·멀티뷰 시트와 비교하도록 함께 전달
        images = images + [p for p in (self.ref_image, self.multiview) if p]
        self._set_status(f"스크린샷 분석 — {self._model_label(critique=True)} 호출중 ({self.iteration}/{self.max_iterations})...", phase='CRITIQUE')
        self._dispatch(prompt, images=images)

    def _finalize(self):
        from ..lowpoly.cleanup import collection_tri_count, cull_hidden_faces, game_ready
        self._set_status("마무리 정리중 (은면 제거·게임레디)...", phase='FINAL')
        coll = bpy.data.collections.get(self.collection_name)
        if coll:
            mesh_objs = [o for o in coll.objects if o.type == 'MESH']
            removed = cull_hidden_faces(mesh_objs)  # join(union)을 안 거친 잔여 은면 제거
            for obj in mesh_objs:
                game_ready(obj)
            tris = collection_tri_count(coll)
            note = f", 은면 {removed}개 제거" if removed else ""
            self._finish(f"완료 — {self.collection_name} ({tris} tris{note})", ok=True)
        else:
            self._finish("실패: 생성된 오브젝트 없음", ok=False)
