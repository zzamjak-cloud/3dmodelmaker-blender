# 배경 공간(SCENE) 세션 상태머신: 컨셉 시트 → 플랜 턴 → 에셋 키트 → 배치 턴 → 마무리
#
# 오브젝트 모드는 "1잡 = 1프롬프트 = 1코드"지만 배경은 그렇게 만들 수 없다.
# 에이전트에게 지형·구조물·프랍 수십 개를 한 번에 시키면 어느 것도 제대로 나오지 않는다.
# 그래서 에셋 품질은 이미 검증된 오브젝트 파이프라인(자식 잡)에서 확보하고,
# 에이전트는 플랜 턴에서 공간 설계를, 배치 턴에서 배치만 하도록 역할을 쪼갠다.
#
# 이 모듈은 GenerationSession의 왕복·큐·폴백 기계를 그대로 물려받고
# start / _start_generation / _handle_reply / _blender_execute / _blender_finalize
# 만 갈아끼운다. 취소·삭제·모델 폴백 같은 예외 경로는 부모 구현이 그대로 쓰인다.
import json
import logging
import os

import bpy

from . import (errors, jobs, library, multiview, prompts, runner, scene_kit, scene_plan,
               sceneview, scheduler, styles)
from .session import GenerationSession, cancel_session, is_active

_log = logging.getLogger(__name__)

KIT_SUFFIX = "_Kit"  # 키트 컬렉션 이름 접미어 (세션 컬렉션의 형제로 둔다)


def _unique_kit_name(base: str) -> str:
    """아직 쓰이지 않은 키트 컬렉션 이름을 고른다.

    앞선 세션이 크래시로 남긴 동명의 키트가 있으면 그것을 재사용해 남의 에셋을
    배치하게 된다. 사용자 데이터를 지우지 않고 새 이름을 잡는다 — 세션 컬렉션
    이름을 정하는 방식(session.GenerationSession.__init__)과 같은 규칙이다."""
    from .session import _sessions

    name, n = base, 1
    while (bpy.data.collections.get(name)
           or any(getattr(s, "kit_collection_name", None) == name
                  for s in _sessions.values())):
        n += 1
        name = "%s.%03d" % (base, n)
    return name


class SceneSession(GenerationSession):
    """배경 잡 하나의 전체 수명 — 자식 에셋 잡들의 오케스트레이터이기도 하다."""

    system_mode = 'SCENE'

    def __init__(self, scene_size='M', **kwargs):
        super().__init__(**kwargs)
        # 실행 중 환경설정·항목이 바뀌어도 이 세션의 기준은 시작 시점에 고정한다
        self.scene_size = str(scene_size or 'M').strip().upper()
        # 0이면 씬 전체 상한을 걸지 않는다. 스타일의 트라이 상한은 모델 1개 기준이라
        # 에셋이 여러 종 들어가는 배경에 그대로 씌우면 밀도를 만들 수 없다.
        self.tri_budget = max(0, int(getattr(self.prefs, "scene_tri_budget", 0) or 0))
        # 0이면 규모별 자동값 (실내 10 / 구역 16 / 대규모 24)
        self.max_assets = (int(getattr(self.prefs, "scene_max_assets", 0) or 0)
                           or scene_plan.size_profile(self.scene_size)["max_assets"])
        # 스타일이 요구하는 밀도에 맞춰 에셋 1개의 트라이 상한을 키운다
        self.style_scale = styles.tri_scale(self.style)
        self.interior = scene_plan.is_interior(self.scene_size)
        self.timeout = scene_kit.scaled_timeout(
            getattr(self.prefs, "timeout", 300),
            getattr(self.prefs, "scene_timeout_scale", 2.0))
        self.kit_collection_name = _unique_kit_name(self.collection_name + KIT_SUFFIX)

        self.stage = 'PLAN'         # PLAN → KIT → PLACE
        self.sceneview = None       # 씬 컨셉 시트 경로
        self.plan = None            # 정규화된 플랜 JSON
        self.place_plan = None      # 실패 에셋을 뺀 배치용 플랜
        self.kit_manifest = []
        self.scene_tris = 0
        self.plan_retried = False
        self.budget_retried = False
        self._aborting = False      # 부모가 자식을 일괄 취소하는 중 (집계 무시)

        self.children = {}          # 자식 uid -> 에셋 key
        self.landmark_uids = []
        self.child_results = {}     # 자식 uid -> 성공 여부

    # ---------- 공통 유틸 ----------
    def _sv_name(self):
        return os.path.basename(self.sceneview) if self.sceneview else None

    def _props(self):
        scene = bpy.data.scenes.get(self.scene_name)
        return getattr(scene, "lp3d", None) if scene else None

    def _launch(self, cmd, prompt):
        # 배경 턴은 오브젝트 한 개보다 훨씬 오래 걸린다 — 배수를 적용한 타임아웃을 쓴다
        runner.run_cli_async(cmd, self.workdir, self.timeout, self._on_response,
                             stdin_text=prompt, job_key=self.uid)

    def _save_collections(self):
        # 인스턴스는 키트 원본 메시를 공유한다 — 키트를 숨긴 채 함께 담아야 파일을 열어도 편집할 수 있다
        return [self.collection_name], [self.kit_collection_name]

    def _apply_lane(self):
        # 배경 결과는 수십 미터에 걸치므로 기본 4m 간격으로는 옆 레인과 겹친다
        jobs.apply_lane_offset(self.collection_name, self.lane,
                               spacing=scene_kit.scene_spacing(self.scene_size, self.plan))

    # ---------- ① 컨셉 시트 ----------
    def start(self):
        self._begin()
        if self._use_multiview() and sceneview.is_available():
            backend = multiview.backend_label()
            job = self._job()
            if job:
                job.image_backend = backend
            self._set_status(f"컨셉 시트 생성 중 — {backend}",
                             f"씬 컨셉 시트 생성 시작 [{backend}]", phase='VIEW')
            self._submit_ai(self._run_sceneview)
            return
        self._start_generation()

    def _run_sceneview(self):
        sceneview.generate(self.request, self.scene_size, self.workdir, self.timeout,
                           self._on_sceneview, ref_image=self.ref_image, job_key=self.uid,
                           style_note=styles.image_note(self.style))

    def _on_sceneview(self, path, error=None):
        if self._stale():
            return  # 이미 끝난 세션의 지연 콜백 — 슬롯은 _end_session이 이미 반환했다
        scheduler.release_ai(self.uid)
        if path:
            self.sceneview = path
            saved = sceneview.archive(path, self.request)
            if saved:
                self.archived.append(saved)
            job = self._job()
            if job:
                job.multiview_path = saved or path  # 패널 썸네일은 같은 칸을 쓴다
            self._set_status("컨셉 시트 생성 완료",
                             f"컨셉 시트 저장: {saved}" if saved
                             else "컨셉 시트 생성 완료 (파일 보관 실패 — 세션 중에만 사용)")
        else:
            # 시트가 없어도 플랜 턴은 진행한다. 다만 로그인 만료처럼 조치 가능한
            # 원인은 사용자가 고칠 수 있도록 상태·로그에 그대로 드러낸다.
            reason = errors.describe(error, 'codex') if error else "원인 불명"
            lines = ["컨셉 시트 생성 실패 — 시트 없이 플랜을 세웁니다"]
            todo = errors.action(error, 'codex') if error else ""
            if todo:
                lines.append(f"  → {todo}")
            lines += [f"  · {l}" for l in errors.detail_lines(error)]
            self._set_status(f"컨셉 시트 실패: {reason}", "\n".join(lines))
        self._start_generation()

    # ---------- ② 플랜 턴 ----------
    def _start_generation(self):
        self.stage = 'PLAN'
        prompt = prompts.build_scene_plan_prompt(
            self.request, self.scene_size, self.tri_budget, self.max_assets,
            ref_image=self._ref_name(), sceneview=self._sv_name(),
            style_scale=self.style_scale)
        images = [p for p in (self.ref_image, self.sceneview) if p] or None
        budget_text = f"예산 {self.tri_budget} tris" if self.tri_budget else "예산 상한 없음"
        self._set_status(f"씬 플랜 설계 중 — {self._model_label()} 호출중...",
                         f"배경 세션 시작: {self.request} (규모 {self.scene_size}, "
                         f"{budget_text}, 배치 총량 기준 "
                         f"{scene_plan.target_instances(self.scene_size)}개)", phase='PLAN')
        self._dispatch(prompt, images=images)

    def _resume_fallback_dispatch(self):
        """resume이 깨졌을 때 — 배경은 턴마다 요구 형식이 달라 그 턴을 통째로 다시 보낸다.

        기본 문구("전체 코드를 다시 작성하라")를 플랜 턴에 보내면 JSON 대신
        python 코드가 돌아와 형식 위반으로 잡이 죽는다."""
        if self.stage == 'PLAN':
            self._start_generation()
            return
        if self.stage == 'PLACE' and self.kit_manifest:
            self._start_place()
            return
        # KIT 단계에는 AI 왕복이 없다 — 여기까지 왔다면 상태가 어긋난 것이다
        self._finish("실패: 세션 이어가기 실패 (배경 단계 불명)", ok=False)

    def _handle_reply(self, text: str):
        if self.stage != 'PLAN':
            super()._handle_reply(text)  # 배치 턴은 오브젝트 모드와 같은 python 블록이다
            return
        try:
            plan, warnings = scene_plan.normalize(
                scene_plan.parse_plan(text), self.tri_budget, self.max_assets)
        except scene_plan.PlanError as e:
            self._retry_plan(str(e))
            return
        self.plan = plan
        self._save_plan(plan)
        lines = [scene_kit.plan_summary(plan)]
        lines += [f"  · 플랜 보정: {w}" for w in warnings]
        self._set_status("씬 플랜 확정", "\n".join(lines), phase='PLAN')
        self._submit_blender(self._blender_start_kit)

    def _retry_plan(self, reason: str):
        if self.plan_retried:
            self._finish(f"실패: 씬 플랜을 쓸 수 없습니다 — {reason}", ok=False)
            return
        self.plan_retried = True
        self._set_status("플랜 형식 오류, 재요청...", f"플랜 검증 실패: {reason}", phase='PLAN')
        self._dispatch(prompts.build_scene_plan_retry_prompt(reason))

    def _save_plan(self, plan: dict):
        """확정 플랜을 세션 작업 폴더에 남긴다 — 실패해도 진행을 막지 않는다."""
        try:
            with open(os.path.join(self.workdir, "scene_plan.json"), "w",
                      encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=1)
        except OSError:
            _log.exception("LP3D 씬 플랜 저장 실패")

    # ---------- ③ 에셋 키트 (자식 잡) ----------
    def _blender_start_kit(self):
        from . import session as session_mod

        self.stage = 'KIT'
        props = self._props()
        if props is None:
            self._finish("실패: 씬을 찾을 수 없습니다", ok=False)
            return
        palette = (self.plan.get("scene") or {}).get("palette") or []
        specs = []
        for asset in self.plan["assets"]:
            prompt = prompts.build_scene_asset_prompt(
                asset, palette,
                scene_plan.asset_tri_limit(asset.get("size_class"), self.style_scale))
            # 자식 에셋은 부모 씬의 스타일을 그대로 물려받는다 — 물려주지 않으면
            # 씬과 그 안의 프랍이 서로 다른 스타일로 나온다
            child = jobs.add_child_job(props, self.uid, prompt, style=self.style)
            specs.append((child.uid, asset))
        if not specs:
            self._finish("실패: 플랜에 만들 에셋이 없습니다", ok=False)
            return
        self.children = {uid: asset["key"] for uid, asset in specs}
        self.landmark_uids = [uid for uid, asset in specs if asset.get("landmark")]
        self._set_status(scene_kit.kit_status_text(0, len(specs)),
                         f"에셋 키트 {len(specs)}종 생성 시작 "
                         f"(랜드마크 {len(self.landmark_uids)}종)", phase='KIT')

        # 시작 실패는 집계를 한 번에 반영한다 — 루프 중간에 집계하면 나머지 자식을
        # 스폰하기도 전에 키트 정리 단계로 넘어갈 수 있다
        failed = []
        for uid, asset in specs:
            # 자식 시작이 그 자리에서 실패하면(예: Popen 실패) 콜백이 동기로 돌아와
            # 집계·중단이 루프 한가운데서 일어난다 — 중단됐으면 더 스폰하지 않는다
            if self._aborting:
                self._mark_unstarted(props, [u for u, _ in specs])
                return
            # 멀티뷰는 랜드마크만 쓴다 (시트 1장당 CLI 호출 1회 — 시간·비용 절감)
            error = session_mod.start_job(
                self.scene_name, uid, multiview_override=bool(asset.get("landmark")))
            if not error:
                continue
            job = props.job_by_uid(uid)
            if job:
                job.state = 'FAILED'
                job.status = f"실패: {error}"
            failed.append((uid, error))
        for uid, error in failed:
            if self._aborting:
                return
            self._log_line(f"에셋 '{self.children[uid]}' 시작 실패: {error}")
            self.on_child_done(uid, False)

    def _mark_unstarted(self, props, uids):
        """중단 때문에 끝내 시작하지 못한 자식을 실패로 못박는다.

        PENDING으로 남겨두면 부모가 사라진 뒤에도 실행도 취소도 되지 않는다."""
        for uid in uids:
            job = props.job_by_uid(uid) if props else None
            if job is not None and job.state == 'PENDING':
                job.state = 'FAILED'
                job.status = "실패: 부모 배경 잡이 중단됨"

    def on_child_done(self, child_uid, ok: bool):
        """자식 에셋 세션이 끝났을 때 부모가 받는 통지 (자식의 _finish에서 호출)."""
        if self._aborting or self.stage != 'KIT':
            return
        if child_uid not in self.children or child_uid in self.child_results:
            return
        self.child_results[child_uid] = bool(ok)
        done = len(self.child_results)
        total = len(self.children)
        if not ok:
            self._log_line(f"에셋 '{self.children[child_uid]}' 실패 — 플랜에서 제외한다")
        verdict, reason = scene_kit.kit_verdict(total, self.landmark_uids,
                                                self.child_results)
        self._set_status(scene_kit.kit_status_text(done, total), phase='KIT')
        if verdict == 'FAILED':
            self._abort_kit(reason)
            return
        if verdict == 'DONE':
            self._submit_blender(self._blender_collect_kit)

    def _abort_kit(self, reason: str):
        """에셋 실패가 임계를 넘었다 — 남은 자식을 끊고 배경 잡을 실패로 마감한다."""
        self._cancel_children()
        self._set_status(f"실패: {reason}", f"에셋 키트 중단: {reason}", phase='KIT')
        self._submit_blender(lambda: self._fail_scene(reason))

    def _fail_scene(self, reason: str):
        self._finish(f"실패: {reason}", ok=False)  # 키트 제거는 _finish가 맡는다

    def _cancel_children(self):
        """자식 에셋 세션을 전부 끊는다 (집계 콜백은 무시한다).

        아직 시작하지 못한 PENDING 자식도 실패로 못박는다 — 부모 없이 남으면
        큐에서 실행도 취소도 되지 않는 좀비가 된다."""
        self._aborting = True
        props = self._props()
        if props is None:
            return
        for child in jobs.children_of(props, self.uid):
            if is_active(child.uid):
                cancel_session(child.uid)
            elif child.state in ('PENDING', 'RUNNING'):
                child.state = 'FAILED'
                child.status = "실패: 부모 배경 잡이 중단됨"

    def _log_line(self, text: str):
        job = self._job()
        if job:
            lines = (job.log + "\n" + text).strip().splitlines()
            job.log = "\n".join(lines[-30:])
        _log.info(text)

    # ---------- ④ 키트 정리 ----------
    def _kit_collection(self):
        """키트 컬렉션 — 세션 컬렉션의 형제로 둔다.

        배치 코드 재시도마다 executor.clear_collection이 세션 컬렉션을 비우므로,
        키트가 그 안에 있으면 첫 실행 실패와 함께 에셋이 통째로 사라진다."""
        coll = bpy.data.collections.get(self.kit_collection_name)
        if coll is None:
            coll = bpy.data.collections.new(self.kit_collection_name)
            scene = bpy.data.scenes.get(self.scene_name) or bpy.context.scene
            scene.collection.children.link(coll)
        return coll

    def _blender_collect_kit(self):
        from .. import lowpoly
        from ..lowpoly.cleanup import game_ready, tri_count

        props = self._props()
        kit = self._kit_collection()
        manifest, taken = [], set()
        lowpoly.set_session(self.kit_collection_name)  # join 결과가 키트로 들어가도록
        try:
            for uid, key in self.children.items():
                if not self.child_results.get(uid):
                    continue
                job = props.job_by_uid(uid) if props else None
                source = (bpy.data.collections.get(job.collection_name)
                          if job and job.collection_name else None)
                meshes = [o for o in source.objects if o.type == 'MESH'] if source else []
                if not meshes:
                    self._log_line(f"에셋 '{key}' 결과 메시가 없어 키트에서 제외한다")
                    self.child_results[uid] = False
                    continue
                name = scene_kit.unique_name(key, taken)
                taken.add(name)
                if len(meshes) > 1:
                    # 프랍 1종 = 오브젝트 1개여야 인스턴스 1개로 배치된다
                    obj = lowpoly.join(meshes, name=name, mode='fast')
                    game_ready(obj)  # 병합 후 원점을 다시 바닥 중앙으로
                else:
                    obj = meshes[0]
                obj.name = name
                for coll in list(obj.users_collection):
                    coll.objects.unlink(obj)
                kit.objects.link(obj)
                manifest.append(scene_kit.manifest_entry(
                    key, obj.name, scene_kit.bbox_size(obj.bound_box), tri_count(obj)))
            # 성공·실패와 무관하게 자식 컬렉션은 전부 치운다 — 메시가 없거나
            # 엠프티만 남은 컬렉션도 씬 아웃라이너에 그대로 쌓인다
            for uid in self.children:
                self._clear_child_collection(props.job_by_uid(uid) if props else None)
        finally:
            lowpoly.set_session(self.collection_name)

        # 결과 컬렉션이 사라진 에셋까지 반영해 중단 임계를 다시 본다 —
        # 랜드마크가 전멸했는데 잡프랍만 남으면 씬이 성립하지 않는다
        verdict, reason = scene_kit.kit_verdict(len(self.children), self.landmark_uids,
                                                self.child_results)
        if not manifest or verdict == 'FAILED':
            self._fail_scene(reason or "키트에 들어간 에셋이 하나도 없습니다")
            return
        lowpoly.set_kit_collection(self.kit_collection_name)
        self.kit_manifest = manifest
        self.place_plan, scale = scene_plan.fit_to_kit(
            scene_kit.prune_plan(self.plan, manifest), manifest, self.scene_size)
        if scale != 1.0:
            extent = self.place_plan["scene"]["extent"]
            self._log_line(f"키트 실제 치수에 맞춰 부지를 {scale:.2f}배로 조정했다 "
                           f"({extent[0]}x{extent[1]}m) — 흩어지거나 파묻히지 않게")
        dropped = scene_kit.dropped_assets(self.plan, manifest)
        self._set_status(f"키트 준비 완료 ({len(manifest)}종)",
                         f"키트 정리 완료: {', '.join(scene_kit.manifest_keys(manifest))}"
                         + (f"\n제외된 에셋: {', '.join(dropped)}" if dropped else ""),
                         phase='KIT')
        self._start_place()

    def _clear_child_collection(self, job):
        """자식 잡의 결과 컬렉션을 통째로 치운다 (쓸 결과는 이미 키트로 옮겼다).

        결과 이름은 비워 둔다 — 키트 컬렉션을 가리키게 하면 자식 항목의
        익스포트·에셋 등록이 씬의 키트 전체를 내보낸다."""
        if job is None:
            return
        source = bpy.data.collections.get(job.collection_name) if job.collection_name else None
        if source is not None:
            for obj in list(source.objects):
                mesh = obj.data if obj.type == 'MESH' else None
                bpy.data.objects.remove(obj)
                if mesh and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            if not source.children:
                bpy.data.collections.remove(source)
        job.collection_name = ""

    # ---------- ⑤ 배치 턴 ----------
    def _start_place(self):
        self.stage = 'PLACE'
        # 플랜 턴의 대화 맥락은 필요 없다 — 확정 플랜과 키트 명단을 프롬프트에 전부 담는다
        self.session_id = None
        self.stateless = False
        self.fallback_used = False
        self.format_retries = 0
        self.exec_retries = 0
        self._initial_dispatch_started = False  # 새 세션이므로 모델 폴백을 다시 허용
        prompt = prompts.build_scene_place_prompt(
            self.place_plan, self.kit_manifest, self.tri_budget,
            sceneview=self._sv_name(), scene_size=self.scene_size)
        images = [self.sceneview] if self.sceneview else None
        self._set_status(f"배치 코드 생성 중 — {self._model_label()} 호출중...",
                         "배치 턴 시작 (새 세션)", phase='PLACE')
        self._dispatch(prompt, images=images)

    def _execute(self, code: str, status):
        self._set_status("배치 실행 대기중...", phase='PLACE')
        self._submit_blender(lambda: self._blender_execute(code, status))

    def _blender_execute(self, code: str, status):
        from .. import lowpoly
        from . import executor

        # 키트 조회 대상은 모듈 전역이라 다른 세션이 덮어쓸 수 있다 — 실행 직전에 고정한다
        lowpoly.set_kit_collection(self.kit_collection_name)
        step = f" (수정 {self.exec_retries}/2)" if self.exec_retries else ""
        self._set_status(f"배치 실행{step}...", phase='PLACE')
        ok, error = executor.execute(code, self.collection_name,
                                     seed=self.exec_retries + 1, workdir=self.workdir)
        if not ok:
            if self.exec_retries < 2:
                self.exec_retries += 1
                self._set_status(f"배치 오류 — 자기수정 {self.exec_retries}/2 호출중...",
                                 f"배치 실행 오류: {error.splitlines()[-1]}", phase='PLACE')
                self._dispatch(prompts.build_error_prompt(error))
                return
            self._finish("실패: 배치 코드 실행 오류 반복", ok=False)
            return
        self.exec_retries = 0
        self.last_code = code
        self._check_budget()

    def _check_budget(self):
        """인스턴스를 포함한 실제 트라이 합산으로 예산을 검사한다."""
        from ..lowpoly.cleanup import collection_tri_count

        coll = bpy.data.collections.get(self.collection_name)
        if not coll or not any(o.type == 'MESH' for o in coll.objects):
            self._finish("실패: 배치된 오브젝트 없음", ok=False)
            return
        self.scene_tris = collection_tri_count(coll)
        if self.tri_budget and self.scene_tris > self.tri_budget:
            if not self.budget_retried:
                self.budget_retried = True
                self._set_status("트라이 예산 초과 — 밀도 축소 재요청...",
                                 f"씬 트라이 {self.scene_tris} > 예산 {self.tri_budget}",
                                 phase='PLACE')
                self._dispatch(prompts.build_scene_budget_prompt(
                    self.scene_tris, self.tri_budget))
                return
            # 축소 재요청까지 썼으면 경고만 남기고 마감한다 — 무한 왕복은 더 나쁘다
            self._log_line(f"경고: 축소 후에도 {self.scene_tris} tris로 "
                           f"예산 {self.tri_budget}을 초과한다")
        self._set_status("배치 실행 완료", f"씬 트라이 {self.scene_tris}", phase='PLACE')
        self._finalize()

    # ---------- ⑥ 마무리 ----------
    def _blender_finalize(self):
        from ..lowpoly.cleanup import collection_tri_count, game_ready, resolve_coplanar_faces

        self._set_status("마무리 정리중...", phase='FINAL')
        coll = bpy.data.collections.get(self.collection_name)
        if not coll:
            self._finish("실패: 생성된 오브젝트 없음", ok=False)
            return
        # 배치 턴이 새로 만든 지형·벽·방·상자끼리 겹친 면만 정리한다. 인스턴스까지 넣으면
        # 수백 개 x 에셋 면수를 훑어 UI가 멈춘다 — 에셋 내부는 키트 단계에서 이미 정리됐다.
        try:
            resolve_coplanar_faces([o for o in coll.objects
                                    if o.type == 'MESH' and o.data.users == 1])
        except Exception:   # 정리 실패가 완성된 배치를 버리게 하면 안 된다
            _log.exception("동일평면 겹침 정리 실패")
        # 은면 컬링은 키트 단계에서 에셋마다 이미 끝났다 — 씬 규모로 다시 돌리면
        # 면수x레이 방향만큼 UI가 수십 초 멈춘다.
        # 인스턴스는 메시를 공유하므로 game_ready(트랜스폼 굽기)를 적용하면 안 된다.
        instanced = 0
        for obj in [o for o in coll.objects if o.type == 'MESH']:
            if obj.data.users > 1:
                instanced += 1
                continue
            game_ready(obj)
        self.scene_tris = collection_tri_count(coll)
        self._hide_kit()
        self._apply_lane()
        over = (" (예산 초과)"
                if self.tri_budget and self.scene_tris > self.tri_budget else "")
        self._final_note = (f"{self.scene_tris} tris{over}, 에셋 {len(self.kit_manifest)}종, "
                            f"인스턴스 {instanced}개")
        self._finish_placed()  # 배경은 팔레트 고정 — 개별 매핑 분기로 가지 않는다

    def _hide_kit(self):
        """키트 컬렉션을 뷰 레이어에서 제외한다 (인스턴스는 메시를 공유해 그대로 보인다)."""
        scene = bpy.data.scenes.get(self.scene_name)
        for view_layer in (scene.view_layers if scene else []):
            layer = _find_layer(view_layer.layer_collection, self.kit_collection_name)
            if layer is not None:
                layer.exclude = True
        self._release_kit_lookup()

    def _release_kit_lookup(self):
        """lp.kit()의 조회 대상 지정을 푼다 — 배치가 끝나면 더 쓸 일이 없고,
        남겨두면 다음 오브젝트 잡의 lp.kit()이 남의 키트를 가리킨다."""
        from .. import lowpoly

        lowpoly.clear_kit_collection(self.kit_collection_name)

    def _remove_kit(self):
        """키트 컬렉션과 그 원본 메시를 지운다 (취소·실패·폐기 경로)."""
        coll = bpy.data.collections.get(self.kit_collection_name)
        if coll is not None:
            for obj in list(coll.objects):
                mesh = obj.data if obj.type == 'MESH' else None
                bpy.data.objects.remove(obj)
                if mesh and mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            bpy.data.collections.remove(coll)
        self._release_kit_lookup()

    # ---------- 라이브러리 / 정리 ----------
    def _archive(self, code: str) -> str:
        """배치 코드와 확정 플랜을 함께 배경 모드 항목으로 축적한다."""
        if not getattr(self.prefs, "use_library", True):
            return ""
        try:
            plan_text = json.dumps(self.place_plan or self.plan or {},
                                   ensure_ascii=False, indent=1)
            header = "\n".join("# " + line for line in
                               ("씬 플랜 (배치 턴 입력)\n" + plan_text).splitlines())
            body = header + "\n\n" + (code or "")
            return library.save_entry(self.request, body,
                                      stats={"tris": self.scene_tris},
                                      multiview=self.sceneview,
                                      agent=self.backend.name, mode='SCENE')
        except Exception:
            _log.exception("라이브러리 저장 실패")
            return ""

    def _finish(self, status: str, ok: bool, detail=None, hint: str = "", state=None):
        # 키트는 결과가 아니라 재료다. 성공하면 인스턴스가 참조하므로 숨기기만 하고,
        # 실패·취소로 끝나면 참조할 배치가 없으니 .blend에 남기지 않는다.
        # (여기서 처리해야 _fail·_handle_error 같은 모든 실패 경로가 함께 덮인다)
        try:
            if ok:
                self._hide_kit()
            else:
                self._remove_kit()
        except Exception:
            _log.exception("LP3D 키트 정리 실패")
        super()._finish(status, ok, detail=detail, hint=hint, state=state)

    def cancel(self):
        self._cancel_children()
        super().cancel()

    def _blender_cancel_cleanup(self):
        if self._stale():
            return
        self._aborting = True
        try:
            self._remove_kit()
        except Exception:
            _log.exception("LP3D 키트 정리 실패")
        super()._blender_cancel_cleanup()

    def _discard(self):
        self._cancel_children()
        try:
            self._remove_kit()
        except Exception:
            _log.exception("LP3D 키트 정리 실패")
        super()._discard()


def _find_layer(layer_collection, name: str):
    """뷰 레이어 트리에서 이름이 같은 LayerCollection을 찾는다."""
    if layer_collection.collection.name == name:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer(child, name)
        if found is not None:
            return found
    return None
