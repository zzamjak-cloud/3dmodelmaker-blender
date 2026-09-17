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
               prompts, runner, scheduler, shapegen, snapshots, styles, texgen)

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


def start_job(scene_name, uid, variation_of=None, variation_count=3,
              multiview_override=None):
    """잡 항목 하나의 생성 세션을 시작한다. 오류 메시지 또는 None을 반환한다.

    프롬프트·참조 이미지는 씬이 아니라 잡 항목에서 읽는다 —
    여러 항목이 서로 다른 설정으로 동시에 돌 수 있어야 하기 때문이다.

    multiview_override를 주면 이 세션만 멀티뷰 사용 여부를 환경설정과 다르게 쓴다.
    배경 모드의 에셋 자식 잡은 랜드마크만 멀티뷰를 쓰기 때문이다(시간·호출 절감)."""
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
    kwargs = dict(
        scene_name=scene_name,
        uid=uid,
        request=request,
        exe=exe,
        variation_code=variation_of,
        variation_count=variation_count,
        ref_image=ref_image,
        lane=job.lane,
        multiview_override=multiview_override,
    )
    # 최상위 배경 잡만 배경 상태머신을 쓴다 — 자식 에셋 잡은 기존 오브젝트 경로다
    is_scene = (getattr(job, "creation_mode", 'OBJECT') == 'SCENE'
                and not getattr(job, "parent_uid", ""))
    if is_scene and not variation_of:
        from .scene_session import SceneSession
        session = SceneSession(scene_size=getattr(job, "scene_size", 'M'), **kwargs)
    else:
        session = GenerationSession(**kwargs)
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
    system_mode = 'OBJECT'  # prompts.build_system_prompt에 넘길 제작 모드

    def __init__(self, scene_name, uid, request, exe,
                 variation_code=None, variation_count=3,
                 ref_image=None, lane=0, multiview_override=None):
        self.scene_name = scene_name
        self.uid = uid
        self.lane = lane
        self.request = request
        self.multiview_override = multiview_override
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
        # 스타일도 같은 이유로 고정한다 — 실행 중 드롭다운을 바꿔도 이 세션은 영향받지 않는다
        self.style = getattr(job, "style", styles.DEFAULT_STYLE) if job else styles.DEFAULT_STYLE
        # 캐릭터 잡은 캐릭터 지침 + 턴어라운드(6면도) 시트를 쓴다. 배경은 SceneSession이
        # 클래스 속성으로 따로 정하므로 여기서는 CHARACTER만 본다. 변형(variation)은
        # 원본 코드를 따르므로 모드를 바꾸지 않는다.
        mode = str(getattr(job, "creation_mode", 'OBJECT') or 'OBJECT') if job else 'OBJECT'
        if mode == 'CHARACTER' and not variation_code:
            self.system_mode = 'CHARACTER'
        self.character_type = (str(getattr(job, "character_type", 'AUTO') or 'AUTO')
                               if job else 'AUTO')
        # 캐릭터는 실행 후 시트와 같은 6시점으로 렌더해 시트와 대조하는 턴을 돈다.
        # 첫 생성은 시트를 '보고' 만들지만 결과가 얼마나 다른지는 모른다 — 나란히
        # 놓고 비교시켜야 빠진 요소·비율 오차·떨어진 파트가 잡힌다.
        self.compare_turns_left = (int(getattr(self.prefs, "character_compare_turns", 1) or 0)
                                   if self.system_mode == 'CHARACTER' else 0)
        self.compare_turns_total = self.compare_turns_left
        if self.compare_turns_left:
            self.max_iterations = 1 + self.compare_turns_left
        # 배경 잡이 스폰한 에셋이면 부모 uid 문자열 — 완료 시 부모에게 알린다
        self.parent_uid = str(getattr(job, "parent_uid", "") or "") if job else ""
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
    def _begin(self):
        """잡 상태 초기화 + keepalive + 시스템 프롬프트 준비 — 모드별 start가 공유한다."""
        job = self._job()
        if job:
            job.state = 'RUNNING'
            job.log = ""
            job.started_at = time.time()
            job.phase = ""
            job.status = "대기 중 (순서 기다리는 중)"
        self._set_status("대기 중 (순서 기다리는 중)", self._model_log())
        runner.add_keepalive(self.uid)
        self.backend.prepare_workdir(prompts.build_system_prompt(self.system_mode, self.style))

    def _use_multiview(self) -> bool:
        """이 세션이 참조 시트를 만들지 여부 — 오버라이드가 있으면 환경설정보다 우선."""
        if self.multiview_override is not None:
            return bool(self.multiview_override)
        return bool(getattr(self.prefs, "use_multiview", True))

    def start(self):
        self._begin()
        if self.variation_code:
            first = prompts.build_variation_prompt(self.request, self.variation_code,
                                                   self.variation_count)
            # 변형은 기존 코드 스타일을 따르므로 참조 이미지·멀티뷰 불필요
            self._set_status(f"코드 생성 — {self._model_label()} 호출 대기중...",
                             f"세션 시작: {self.request} ({self.backend.name})", phase='GEN')
            self._dispatch(first)
            return
        # 신규 생성: 멀티뷰 참조 시트를 먼저 생성 (codex image_gen — 없으면 스킵)
        if self._use_multiview() and multiview.is_available():
            backend = multiview.backend_label()
            job = self._job()
            if job:
                job.image_backend = backend  # 패널에서 "무엇으로 만들었는지" 보여준다
            self._set_status(f"멀티뷰 참조 생성중 — {backend}",
                             f"멀티뷰 참조 시트 생성 시작 [{backend}]", phase='GEN')
            # 멀티뷰도 CLI 호출이므로 AI 슬롯을 점유한다
            self._submit_ai(self._run_multiview)
            return
        self._start_generation()

    def _sheet_kind(self) -> str:
        """참조 시트 종류 — 캐릭터는 3x2 턴어라운드, 그 외는 2x2 멀티뷰."""
        return 'TURNAROUND' if self.system_mode == 'CHARACTER' else 'MULTIVIEW'

    def _run_multiview(self):
        multiview.generate(self.request, self.workdir, self.prefs.timeout,
                           self._on_multiview, ref_image=self.ref_image,
                           style_note=styles.image_note(self.style),
                           job_key=self.uid, sheet=self._sheet_kind())

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
        if self.multiview and self.system_mode == 'CHARACTER' and shapegen.is_available():
            self._start_shapegen()
            return
        self._start_generation()

    # ---------- 캐릭터: 이미지→3D 셰이프 → 리토폴로지 ----------
    #
    # 프리미티브 코드로 조립한 캐릭터는 원화와 닮지 않는다. 턴어라운드 시트를 그대로
    # 이미지→3D 모델에 넣어 "찰흙" 셰이프를 받고 리토폴로지한다. Astra 모델링 턴은 건너뛰고
    # 마무리(은면 정리·게임레디·텍스처 6면도 베이크)는 기존 경로를 그대로 탄다.
    def _start_shapegen(self):
        try:
            views = shapegen.split_turnaround(self.multiview, os.path.join(self.workdir, "views"))
        except Exception as e:
            _log.exception("턴어라운드 분할 실패")
            self._set_status("시트 분할 실패 — 코드 모델링으로 진행", f"턴어라운드 분할 실패: {e}")
            self._start_generation()
            return
        self.shape_path = os.path.join(self.workdir, "shape.glb")
        self._set_status("이미지→3D 셰이프 생성중 (Hunyuan3D 로컬)...",
                         f"셰이프 생성 시작: 뷰 {', '.join(sorted(views))} → {shapegen.server_url()}",
                         phase='GEN')
        self._submit_ai(lambda: shapegen.generate(
            views, self.shape_path, max(self.prefs.timeout, 600), self._on_shape,
            job_key=self.uid, face_count=0))

    def _on_shape(self, path, error=None):
        if self._stale():
            return
        scheduler.release_ai(self.uid)
        if not path:
            # 셰이프 실패는 비치명 — 코드 모델링 경로로 돌아간다
            self._set_status("셰이프 생성 실패 — 코드 모델링으로 진행",
                             f"셰이프 생성 실패: {error}")
            self._start_generation()
            return
        self._set_status("셰이프 수신 — 리토폴로지 대기중...", "셰이프 GLB 수신", phase='EXEC')
        self._submit_blender(self._blender_retopo)

    def _blender_retopo(self):
        from ..lowpoly import retopo, set_session
        set_session(self.collection_name)
        try:
            coll = bpy.data.collections.get(self.collection_name)
            if coll is None:
                coll = bpy.data.collections.new(self.collection_name)
                bpy.context.scene.collection.children.link(coll)
            self._set_status("리토폴로지 중 (조각 제거·복셀 리메시·QuadriFlow)...", phase='EXEC')
            # 이름은 프롬프트에서 — _slug는 ASCII만 남겨 한국어 요청이 전부 "Model"이 된다
            info = retopo.process_glb(
                self.shape_path, multiview._slug(self.request, 24) or "Character", coll,
                height=float(getattr(self.prefs, "character_height", 1.8)),
                target_faces=int(getattr(self.prefs, "shapegen_faces", 12000)),
                method=str(getattr(self.prefs, "shapegen_method", 'QUADRIFLOW')),
                adaptive=bool(getattr(self.prefs, "shapegen_adaptive", False)),
                # 머리 영역은 곡률과 무관하게 촘촘하게 — 유형별 바운딩 박스 위쪽 비율
                head_frac={'HUMANOID': 0.24, 'CREATURE': 0.28, 'ANIMAL': 0.0}.get(
                    self.character_type, 0.22))
        except Exception as e:
            _log.exception("리토폴로지 실패")
            self._set_status("리토폴로지 실패 — 코드 모델링으로 진행", f"리토폴로지 실패: {e}")
            self._start_generation()
            return
        self.last_code = ""  # 코드 없이 만든 결과 — 라이브러리 few-shot에 섞이지 않는다
        self.compare_turns_left = 0
        self._set_status(
            "셰이프 리토폴로지 완료",
            f"셰이프: 원본 {info['raw_faces']}면, 파편 {info['floaters_removed']}개 제거, "
            f"리메시 {info['remeshed_faces']}면 → {info['method']} → {info['faces']}면 "
            f"(쿼드 {info['quads']}, 밀도 축소 {info['reduced_faces']}면) = {info['tris']} tris")
        self._finalize()

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
                                             multiview=self._mv_name(), fewshot=fewshot,
                                             mode=self.system_mode,
                                             character_type=self.character_type)
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
        self._notify_parent(ok)

    def _parent_session(self):
        """이 세션을 스폰한 배경 세션. 부모가 아니거나 이미 끝났으면 None."""
        if not self.parent_uid:
            return None
        for session in _sessions.values():
            if str(session.uid) == self.parent_uid:
                return session
        return None

    def _notify_parent(self, ok: bool):
        """에셋 자식이 끝났음을 부모 배경 세션에 알린다 (집계는 부모가 한다)."""
        parent = self._parent_session()
        report = getattr(parent, "on_child_done", None)
        if report is None:
            return
        try:
            report(self.uid, bool(ok))
        except Exception:
            # 여기서 터지면 자식 마감이 부모 세션을 통째로 잠근다 — 로그만 남긴다
            _log.exception("LP3D 부모 배경 세션 통지 실패")

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
            self._handle_error(error)
            return

        try:
            reply = self.backend.parse_response(stdout)
        except Exception as e:
            self._finish(f"응답 파싱 실패: {e}", ok=False)
            return
        if reply.session_id:
            self.session_id = reply.session_id
        self._handle_reply(reply.text)

    def _handle_error(self, error):
        """CLI 실패 처리 — 폴백·재시도 규칙은 제작 모드와 무관하게 같다."""
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
            self._resume_fallback_dispatch()
            return
        self._finish(f"실패: {reason}", ok=False, detail=errors.detail_lines(error),
                     hint=errors.action(error, self.backend.name))

    def _resume_fallback_dispatch(self):
        """resume이 깨져 대화 맥락을 잃었을 때 보낼 요청.

        배경 모드는 턴마다 요구하는 출력 형식이 달라(플랜은 JSON, 배치는 python)
        이 문구를 그대로 쓸 수 없다 — SceneSession이 단계별로 갈아끼운다."""
        self._dispatch("직전 지시를 계속 수행하라. 전체 코드를 다시 작성하라.")

    def _handle_reply(self, text: str):
        """성공 응답 처리 — 배경 모드는 플랜 턴에서 이 단계를 갈아끼운다."""
        status, code = parse_agent_reply(text)
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
        if self.compare_turns_left > 0 and self.multiview:
            if self._dispatch_compare(code):
                return
        self._finalize()

    def _dispatch_compare(self, code: str) -> bool:
        """현재 모델을 6시점 렌더해 턴어라운드 시트와 대조하는 턴을 보낸다. 실패하면 False.

        렌더는 개별 매핑용 캡처(texturing.capture)를 그대로 쓴다 — 시트와 칸 순서는
        다르지만 라벨이 붙어 있어 모델이 대조할 수 있다."""
        from ..texturing import capture as tex_capture
        try:
            coll = bpy.data.collections.get(self.collection_name)
            mesh_objs = [o for o in coll.objects if o.type == 'MESH'] if coll else []
            if not mesh_objs:
                return False
            out_dir = os.path.join(self.workdir, f"compare_{self.iteration}")
            with _bake_context(self.scene_name) as ctx:
                views = tex_capture.render_views(ctx, mesh_objs, out_dir)
            render = tex_capture.join_sheet(views, os.path.join(self.workdir,
                                                                f"render_{self.iteration}.png"))
        except Exception as e:
            _log.exception("6면도 대조 렌더 실패")
            self._set_status("6면도 대조 렌더 실패 — 대조 없이 마무리", f"대조 렌더 실패: {e}")
            return False
        turn = self.compare_turns_total - self.compare_turns_left + 1
        self.compare_turns_left -= 1
        self.iteration += 1
        job = self._job()
        if job:
            job.iteration = self.iteration
        self._set_status(f"6면도 대조 {turn}/{self.compare_turns_total} — {self._model_label()} 호출중...",
                         f"6면도 대조 턴 {turn}/{self.compare_turns_total}: 시트 vs 렌더 비교 요청",
                         phase='GEN')
        self._dispatch(prompts.build_character_compare_prompt(
            self._mv_name(), os.path.basename(render), code, turn, self.compare_turns_total),
            images=[self.multiview, render])
        return True

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
            self._apply_lane()
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

    def _apply_lane(self):
        """레인 오프셋 적용 (표식으로 이중 적용을 막는다).

        에셋 자식 잡은 부모 씬 컬렉션 안에서 조립되므로 옮기지 않는다 —
        부모가 마무리 단계에서 씬 전체를 한 번에 민다."""
        if self.parent_uid:
            return
        jobs.apply_lane_offset(self.collection_name, self.lane)

    def _finish_placed(self, extra: str = ""):
        """성공 마감. extra는 텍스처 단계 결과 문구."""
        self._apply_lane()
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
            backend = multiview.backend_label()
            job = self._job()
            if job:
                job.image_backend = backend
            self._set_status(f"텍스처 6면도 생성중 — {backend}",
                             f"텍스처 가이드 시트 생성 완료 → AI 채색 [{backend}]", phase='TEX')
            # 캐릭터는 턴어라운드를 색 원본으로 함께 넘기고 시점별 고해상(1:1 x 6)으로 받는다 —
            # 회색 가이드 한 장(칸당 512px)만 주면 색을 지어내고 얼굴이 비며 텍스처가 뿌옇다
            is_character = self.system_mode == 'CHARACTER'
            per_view = bool(getattr(self.prefs, "texture_per_view", False)) or is_character
            reference = self.multiview if is_character else None
            self._submit_ai_texture(lambda: texgen.generate(
                self.request, guide, self.workdir, self.prefs.timeout,
                self._on_texture_sheet, job_key=self.uid,
                reference=reference, per_view=per_view))
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
            got = f"시점 {len(path)}개 수신" if isinstance(path, dict) else "텍스처 시트 수신"
            self._set_status("텍스처 베이크 대기중...", got, phase='TEX')
            self._submit_blender(lambda: self._blender_bake_texture(path))
        except Exception as e:
            _log.exception("LP3D 텍스처 콜백 처리 실패")
            # except 블록을 벗어나면 e가 사라지므로 문구를 먼저 만들어 람다에 넘긴다
            detail = f"콜백 오류 {type(e).__name__}: {e}"
            self._submit_blender(lambda: self._texture_fallback(detail))

    def _blender_bake_texture(self, sheet_path: str):
        def _run():
            from ..texturing import apply as tex_apply, bake as tex_bake, capture as tex_capture
            mesh_objs = self._mesh_objs()
            if not mesh_objs:
                raise RuntimeError("생성된 오브젝트 없음")
            resolution = int(getattr(self.prefs, "texture_resolution", "1024"))
            if self.system_mode == 'CHARACTER':
                resolution = max(resolution, 2048)  # 얼굴·의상 디테일 — 시점별 1024px 소스를 살린다
            name = tex_apply.texture_name(self.collection_name, mesh_objs)
            self._set_status(f"텍스처 베이크중 ({resolution}px, CPU)...", phase='TEX')
            # 시점별 생성 결과는 이미 {시점: 경로}다 — 시트 한 장이면 잘라서 같은 형태로
            views = sheet_path if isinstance(sheet_path, dict) else tex_capture.split_sheet(sheet_path)
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
