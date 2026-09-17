# 3D 뷰포트 사이드바 패널
import os
import time

import bpy

from ..core import models, scheduler, session, styles
from . import previews

# 진행 단계 정의 (session.py의 phase 식별자와 일치)
_PHASES = ('GEN', 'EXEC', 'FINAL', 'TEX')
# 배경 공간 모드는 3면도 대신 컨셉 시트 → 플랜 → 에셋 키트 → 배치 순으로 진행한다
_SCENE_PHASES = ('VIEW', 'PLAN', 'KIT', 'PLACE', 'FINAL')
_SCENE_STEPS = (
    ("VIEW", "컨셉"),
    ("PLAN", "플랜"),
    ("KIT", "에셋"),
    ("PLACE", "배치"),
    ("FINAL", "마무리"),
)


def _parent_job(props, job):
    """자식 에셋 잡의 부모 배경 잡. 부모가 아니거나 못 찾으면 None."""
    parent_uid = getattr(job, "parent_uid", "")
    if not parent_uid:
        return None
    for candidate in props.jobs:
        if str(candidate.uid) == parent_uid:
            return candidate
    return None


def _model_label(job):
    """생성 예정 모델 또는 기록된 실제 모델 이름."""
    return models.generation_model_label(
        state=job.state,
        requested_model=getattr(job, "requested_model", ""),
        effective_model=getattr(job, "effective_model", ""),
    )


class LP3D_UL_jobs(bpy.types.UIList):
    """생성 큐 리스트 — 상태 아이콘 + 프롬프트 + 진행도."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        from ..properties import STATE_ICONS

        row = layout.row(align=True)
        row.alert = item.state == 'FAILED'  # 실패는 눈에 띄어야 한다
        is_child = bool(getattr(item, "parent_uid", ""))
        if is_child:
            # 배경 잡이 스폰한 에셋은 부모에 딸린 항목임이 한눈에 보여야 한다
            row.separator()
            row.label(text="", icon='LINKED')
        row.label(text="", icon=STATE_ICONS.get(item.state, 'DOT'))
        label = item.prompt or "(빈 프롬프트)"
        row.label(text=f"└ {label}" if is_child else label)
        if item.state == 'RUNNING':
            model = _model_label(item)
            if model == "GPT-6 Astra":
                progress = "Astra"
            elif model and model != models.NO_MODEL_RECORD_LABEL:
                compact_model = "Codex 기본" if model == models.CODEX_DEFAULT_LABEL else model
                progress = compact_model
            else:
                progress = "실행 중"
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
        box.operator("lp3d.edit_prompt", text="프롬프트 입력", icon='TEXT')
        self._draw_mode(box, props, job)
        box.prop(job, "ref_image_path", text="참조 이미지")
        ref_row = box.row(align=True)
        ref_row.operator("lp3d.paste_ref_image", text="클립보드에서 붙여넣기", icon='PASTEDOWN')
        if job.ref_image_path:
            ref_row.operator("lp3d.clear_ref_image", text="", icon='X')

        self._draw_multiview(box, props, job)
        self._draw_status(layout, job)

    def _draw_mode(self, layout, props, job):
        """제작 모드 선택 — 배경 공간은 머티리얼이 팔레트로 고정되고 씬 규모를 대신 고른다."""
        parent = _parent_job(props, job)
        if getattr(job, "parent_uid", ""):
            # 에셋 잡의 모드·스타일은 부모 플랜이 정한다 — 사용자가 바꿀 값이 아니다
            owner = (parent.prompt[:20] if parent else "")
            layout.label(text=f"배경 '{owner}'의 에셋", icon='LINKED')
            if parent is not None:
                layout.label(text=f"스타일: {styles.style_def(parent.style)['label']} (부모 승계)")
            return
        layout.prop(job, "style", text="스타일")
        layout.prop(job, "creation_mode", text="제작 모드")
        if job.creation_mode == 'SCENE':
            layout.prop(job, "scene_size", text="씬 규모")
            layout.label(text="머티리얼: 컬러 스와치 (고정)")
        elif job.creation_mode == 'CHARACTER':
            layout.prop(job, "character_type", text="캐릭터 유형")
            layout.prop(job, "modeling_type", text="모델링 타입")
            hint = layout.column(align=True)
            hint.scale_y = 0.85
            if job.ref_image_path.strip():
                hint.label(text="원화 → 6면도 턴어라운드 → 리깅 자세 모델링", icon='ARMATURE_DATA')
            else:
                hint.label(text="원화(참조 이미지)를 넣으면 그 캐릭터로 6면도를 만듭니다", icon='INFO')
            if job.modeling_type != 'TEXTURE':
                hint.label(text="얼굴·의상 디테일은 '개별 매핑'이 유리합니다", icon='INFO')
        else:
            layout.prop(job, "modeling_type", text="모델링 타입")

    def _draw_multiview(self, layout, props, job):
        """AI가 만든 멀티뷰(3면도) 시트 — 패널에서 바로 확인하고 참조로 재사용할 수 있게 한다.

        경로가 비어 있어도 상자를 그린다: 지난 세션 시트를 파일에서 되찾는 버튼이 필요하다."""
        mv = layout.box()
        # 지금 설정으로 어느 백엔드가 쓰이는지 항상 보여준다 — 잡이 이미 돌았으면
        # 그때 실제로 쓴 값을, 아니면 현재 설정에서 계산한 값을 표시한다
        from .. import preferences
        from ..core import multiview
        enabled = bool(getattr(preferences.get_prefs(), "use_multiview", True))
        used = getattr(job, "image_backend", "")
        label = used if (used and job.state != 'PENDING') else multiview.backend_label(enabled)
        active = label.startswith("OpenRouter")
        row = mv.row()
        row.alert = label.startswith("미사용") or "폴백" in label
        row.label(text=f"참조 이미지: {label}",
                  icon='URL' if active else ('CONSOLE' if label.startswith("Codex") else 'CANCEL'))
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
        generation_model = _model_label(job)
        tracked = any((
            getattr(job, "requested_model", ""),
            getattr(job, "effective_model", ""),
        ))
        if generation_model == models.NO_MODEL_RECORD_LABEL:
            box.label(text=models.NO_MODEL_RECORD_LABEL, icon='SETTINGS')
        else:
            requested_model = getattr(job, "requested_model", "") or generation_model
            effective_model = generation_model if tracked else ""
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
        box.label(text=f"경과 {elapsed // 60}:{elapsed % 60:02d}",
                  icon='TIME')
        if getattr(job, "creation_mode", 'OBJECT') == 'SCENE':
            steps = list(_SCENE_STEPS)
            phases = _SCENE_PHASES
        else:
            steps = [
                ('GEN', f"코드 생성 — {generation_model}"),
                ('EXEC', "Blender 실행"),
                ('FINAL', "마무리 정리"),
            ]
            if getattr(job, "modeling_type", 'PALETTE') == 'TEXTURE':
                steps.append(('TEX', "언랩·6면도 텍스처 베이크"))
            phases = _PHASES
        cur_idx = phases.index(job.phase) if job.phase in phases else -1
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
            # 에셋 잡의 결과는 마무리 단계에서 부모 키트로 병합되어 단독 컬렉션이 없다
            if job is not None and getattr(job, "parent_uid", "") and job.state == 'DONE':
                col.label(text="부모 배경 키트에 병합됨", icon='LINKED')
            else:
                col.label(text="완료된 항목을 선택하세요", icon='INFO')
            col.separator()
            col.operator("lp3d.dev_reload", icon='FILE_REFRESH')
            return

        col.label(text=f"결과: {job.collection_name}", icon='OUTLINER_COLLECTION')
        if job.texture_path:
            col.label(text=f"텍스처: {os.path.basename(job.texture_path)}", icon='TEXTURE')

        if job.entry_id and not session.is_active(job.uid):
            rate = layout.row(align=True)
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
