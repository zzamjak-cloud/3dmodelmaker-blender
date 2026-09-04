# 3D 뷰포트 사이드바 패널
import os
import time

import bpy

from .. import preferences
from ..core import models, scheduler, session, snapshots
from . import previews

# 진행 단계 정의 (session.py의 phase 식별자와 일치)
_PHASES = ('GEN', 'EXEC', 'CAPTURE', 'CRITIQUE', 'FINAL')


def _model_labels(job):
    """단계 표시용 생성/비평 모델 이름."""
    if job.agent == 'CODEX':
        scheduled_model = models.model_label('CODEX', models.codex_model_id(
            preferences.get_prefs().codex_model))
        scheduled_critique_model = scheduled_model
    else:
        prefs = preferences.get_prefs()
        scheduled_model = models.model_label(
            'CLAUDE', "" if prefs.gen_model == 'DEFAULT' else prefs.gen_model)
        critique_id = ("" if prefs.critique_model == 'DEFAULT'
                       else prefs.critique_model)
        scheduled_critique_model = models.model_label(
            'CLAUDE', critique_id) if critique_id else scheduled_model
    return models.stage_model_labels(
        state=job.state,
        requested_model=getattr(job, "requested_model", ""),
        effective_model=getattr(job, "effective_model", ""),
        requested_critique_model=getattr(job, "requested_critique_model", ""),
        effective_critique_model=getattr(job, "effective_critique_model", ""),
        scheduled_model=scheduled_model,
        scheduled_critique_model=scheduled_critique_model,
    )


class LP3D_UL_jobs(bpy.types.UIList):
    """생성 큐 리스트 — 상태 아이콘 + 프롬프트 + 진행도."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        from ..properties import STATE_ICONS

        row = layout.row(align=True)
        row.alert = item.state == 'FAILED'  # 실패는 눈에 띄어야 한다
        row.label(text="", icon=STATE_ICONS.get(item.state, 'DOT'))
        row.label(text=item.prompt or "(빈 프롬프트)")
        if item.state == 'RUNNING':
            generation_model, critique_model = _model_labels(item)
            model = models.current_model_label(
                item.phase, generation_model, critique_model)
            if model == "GPT-6 Astra":
                progress = f"Astra {item.iteration}/{item.total_turns}"
            elif model and model != models.NO_MODEL_RECORD_LABEL:
                compact_model = "Codex 기본" if model == models.CODEX_DEFAULT_LABEL else model
                progress = f"{compact_model} {item.iteration}/{item.total_turns}"
            else:
                progress = f"{item.iteration}/{item.total_turns}"
            row.label(text=progress)
        elif item.state == 'FAILED':
            row.label(text="실패")
        elif item.state == 'DONE':
            row.label(text="완료")


class LP3D_PT_main(bpy.types.Panel):
    bl_label = "AI 모델 생성"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI 모델러"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lp3d

        # --- 생성 큐 리스트 ---
        layout.label(text="생성 큐:")
        row = layout.row()
        row.template_list("LP3D_UL_jobs", "", props, "jobs", props, "job_index", rows=4)
        side = row.column(align=True)
        side.operator("lp3d.job_add", text="", icon='ADD')
        side.operator("lp3d.job_remove", text="", icon='REMOVE')
        side.separator()
        side.operator("lp3d.job_duplicate", text="", icon='DUPLICATE')
        side.separator()
        side.operator("lp3d.job_move", text="", icon='TRIA_UP').delta = -1
        side.operator("lp3d.job_move", text="", icon='TRIA_DOWN').delta = 1

        # --- 실행 컨트롤 ---
        run_row = layout.row(align=True)
        run_row.scale_y = 1.2
        run_row.operator("lp3d.queue_start", icon='PLAY')
        if session.is_active():
            run_row.operator("lp3d.queue_stop", text="", icon='CANCEL')

        counts = scheduler.counts()
        layout.label(
            text=(f"대기 {counts['ai_waiting']} · AI 실행 {counts['ai_running']} · "
                  f"Blender 대기 {counts['blender_waiting']}"),
            icon='SORTTIME')

        job = props.active_job()
        if job is None:
            layout.label(text="[＋]로 프롬프트 항목을 추가하세요", icon='INFO')
            return

        # --- 선택 항목 상세 ---
        box = layout.box()
        box.label(text=f"항목 {props.job_index + 1} / {len(props.jobs)}", icon='TEXT')
        box.prop(job, "prompt", text="")
        # 주의: 입력 필드에 scale을 주면 macOS IME(한글 조합)가 더 불안정해짐.
        # 한글은 필드 직접 입력 대신 [프롬프트 입력] 버튼의 OS 네이티브 팝업을 쓴다.
        box.operator("lp3d.edit_prompt", text="프롬프트 입력", icon='TEXT').target = 'prompt'
        box.prop(job, "ref_image_path", text="참조 이미지")
        ref_row = box.row(align=True)
        ref_row.operator("lp3d.paste_ref_image", text="클립보드에서 붙여넣기", icon='PASTEDOWN')
        if job.ref_image_path:
            ref_row.operator("lp3d.clear_ref_image", text="", icon='X')
        agent_row = box.row(align=True)
        agent_row.prop(job, "agent", expand=True)
        box.prop(job, "auto_turns")

        self._draw_multiview(box, props, job)
        self._draw_status(layout, job)

    def _draw_multiview(self, layout, props, job):
        """AI가 만든 멀티뷰(3면도) 시트 — 패널에서 바로 확인하고 참조로 재사용할 수 있게 한다.

        경로가 비어 있어도 상자를 그린다: 지난 세션 시트를 파일에서 되찾는 버튼이 필요하다."""
        mv = layout.box()
        mv_path = job.multiview_path
        if not mv_path:
            mv.operator("lp3d.load_last_multiview",
                        text="저장된 멀티뷰 미리보기", icon='IMAGE_DATA')
            return
        header = mv.row(align=True)
        header.prop(props, "multiview_preview_open", text="", emboss=False,
                    icon='DISCLOSURE_TRI_DOWN' if props.multiview_preview_open
                    else 'DISCLOSURE_TRI_RIGHT')
        header.label(text=f"멀티뷰: {os.path.basename(mv_path)}", icon='IMAGE_DATA')
        if props.multiview_preview_open:
            icon = previews.icon_id(mv_path)
            if icon:
                mv.template_icon(icon_value=icon, scale=7.5)
            elif not os.path.isfile(mv_path):
                mv.label(text="시트 파일이 사라졌습니다", icon='ERROR')
            else:
                mv.label(text="미리보기를 만들 수 없습니다", icon='ERROR')
        row = mv.row(align=True)
        row.operator("lp3d.show_multiview", text="크게 보기", icon='ZOOM_IN')
        row.operator("lp3d.use_multiview_as_ref", text="참조로 사용", icon='FILE_REFRESH')
        row.operator("lp3d.open_multiview_folder", text="", icon='FILEBROWSER')

    def _draw_status(self, layout, job):
        """선택 항목의 진행 상태: 현재 작업 + 경과 시간 + 단계 목록."""
        box = layout.box()
        # 실패는 눈에 띄어야 한다 — 조용히 지나가면 원인을 놓친다 (로그인 만료 사고)
        failed = job.state == 'FAILED' or "실패" in job.status
        head = box.row()
        head.alert = failed
        head.label(text=f"상태: {job.status}", icon='ERROR' if failed else 'INFO')
        generation_model, critique_model = _model_labels(job)
        tracked = any((
            getattr(job, "requested_model", ""),
            getattr(job, "effective_model", ""),
            getattr(job, "requested_critique_model", ""),
            getattr(job, "effective_critique_model", ""),
        ))
        if generation_model == models.NO_MODEL_RECORD_LABEL:
            box.label(text=models.NO_MODEL_RECORD_LABEL, icon='SETTINGS')
        elif job.state == 'PENDING' and not tracked and generation_model != critique_model:
            box.label(text=f"예정 생성 모델: {generation_model}", icon='SETTINGS')
            box.label(text=f"예정 비평 모델: {critique_model}", icon='SETTINGS')
        elif job.state in ('DONE', 'FAILED', 'CANCELLED') and generation_model != critique_model:
            box.label(text=f"생성 모델: {generation_model}", icon='SETTINGS')
            box.label(text=f"비평 모델: {critique_model}", icon='SETTINGS')
        else:
            current_model = models.current_model_label(
                job.phase, generation_model, critique_model)
            requested_model = (
                getattr(job, "requested_critique_model", "")
                if job.phase == 'CRITIQUE'
                else getattr(job, "requested_model", "")
            ) or current_model
            effective_model = current_model if tracked else ""
            box.label(text=models.job_model_label(
                requested_model, effective_model, job.model_fallback), icon='SETTINGS')
        if failed:
            if job.status_hint:
                box.label(text=job.status_hint, icon='CONSOLE')
            box.label(text="자세한 원인은 [로그] 패널 참고", icon='TEXT')
        if job.state in ('FAILED', 'CANCELLED'):
            box.operator("lp3d.job_retry", icon='FILE_REFRESH')
        if not session.is_active(job.uid):
            return
        box.operator("lp3d.job_cancel", icon='CANCEL')
        elapsed = int(time.time() - job.started_at) if job.started_at else 0
        box.label(text=f"경과 {elapsed // 60}:{elapsed % 60:02d} · 턴 {job.iteration}/{job.total_turns}",
                  icon='TIME')
        gen_label, crit_label = generation_model, critique_model
        steps = (
            ('GEN', f"코드 생성 — {gen_label}"),
            ('EXEC', "Blender 실행"),
            ('CAPTURE', "뷰포트 캡처"),
            ('CRITIQUE', f"스크린샷 비평 — {crit_label}"),
            ('FINAL', "마무리 정리"),
        )
        cur_idx = _PHASES.index(job.phase) if job.phase in _PHASES else -1
        sub = box.column(align=True)
        sub.scale_y = 0.85
        for i, (_pid, label) in enumerate(steps):
            icon = 'PLAY' if i == cur_idx else ('CHECKMARK' if i < cur_idx else 'DOT')
            sub.label(text=f"{i + 1}. {label}", icon=icon)


class LP3D_PT_log(bpy.types.Panel):
    bl_label = "로그"
    bl_parent_id = "LP3D_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        job = context.scene.lp3d.active_job()
        col = self.layout.column(align=True)
        col.scale_y = 0.7
        if job is None:
            col.label(text="선택된 항목이 없습니다")
            return
        for line in job.log.splitlines()[-15:]:
            col.label(text=line)


class LP3D_PT_output(bpy.types.Panel):
    bl_label = "결과물"
    bl_parent_id = "LP3D_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        props = context.scene.lp3d
        job = props.active_job()
        col = layout.column()
        if job is None or not job.collection_name:
            col.label(text="완료된 항목을 선택하세요", icon='INFO')
            col.separator()
            col.operator("lp3d.dev_reload", icon='FILE_REFRESH')
            return

        col.label(text=f"결과: {job.collection_name}", icon='OUTLINER_COLLECTION')

        # 개선 사이클: 결과가 마음에 안 들면 [개선하기]를 필요한 만큼 반복
        if job.code and not session.is_active(job.uid):
            box = layout.box()
            box.label(text="개선", icon='MODIFIER')
            box.prop(job, "improve_feedback", text="")
            box.operator("lp3d.edit_prompt", text="개선 프롬프트 입력",
                         icon='TEXT').target = 'improve_feedback'
            box.operator("lp3d.improve", icon='FILE_REFRESH')
            # 턴별 중간 결과를 옆에 남겨뒀다면 비교가 끝난 뒤 정리할 수 있게 한다
            snaps = snapshots.count(job.collection_name)
            if snaps:
                box.operator("lp3d.clear_snapshots",
                             text=f"단계 스냅샷 {snaps}개 정리", icon='TRASH')
            # 라이브러리 축적: 잘 나온 결과를 우수로 표시하면 다음 생성의 예시로 우선 쓰인다
            if job.entry_id:
                rate = box.row(align=True)
                rate.operator("lp3d.rate", text="우수", icon='SOLO_ON').rating = 2
                rate.operator("lp3d.library_discard", text="제외", icon='TRASH')

        col = layout.column()
        col.prop(props, "export_dir")
        row = col.row(align=True)
        row.operator("lp3d.export", text="FBX").format = 'FBX'
        row.operator("lp3d.export", text="glTF").format = 'GLTF'
        col.operator("lp3d.mark_asset", icon='ASSET_MANAGER')
        col.operator("lp3d.variation", icon='DUPLICATE')
        col.separator()
        col.operator("lp3d.dev_reload", icon='FILE_REFRESH')


_CLASSES = (LP3D_UL_jobs, LP3D_PT_main, LP3D_PT_log, LP3D_PT_output)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
