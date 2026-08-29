# 3D 뷰포트 사이드바 패널
import time

import bpy

from .. import preferences
from ..core import session

# 진행 단계 정의 (session.py의 phase 식별자와 일치)
_PHASES = ('GEN', 'EXEC', 'CAPTURE', 'CRITIQUE', 'FINAL')


def _model_labels(props):
    """단계 표시용 생성/비평 모델 이름."""
    if props.agent == 'CODEX':
        return "Codex", "Codex"
    prefs = preferences.get_prefs()
    gen = "기본 모델" if prefs.gen_model == 'DEFAULT' else prefs.gen_model.capitalize()
    crit = gen if prefs.critique_model == 'DEFAULT' else prefs.critique_model.capitalize()
    return gen, crit


class LP3D_PT_main(bpy.types.Panel):
    bl_label = "AI 모델 생성"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI 모델러"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lp3d

        col = layout.column()
        col.label(text="프롬프트:")
        # 주의: 입력 필드에 scale을 주면 macOS IME(한글 조합)가 더 불안정해짐.
        # 한글은 필드 직접 입력 대신 [프롬프트 입력] 버튼의 OS 네이티브 팝업을 쓴다.
        col.prop(props, "prompt", text="")
        col.operator("lp3d.edit_prompt", text="프롬프트 입력", icon='TEXT').target = 'prompt'
        col.prop(props, "ref_image_path", text="참조 이미지")
        row = col.row(align=True)
        row.prop(props, "agent", expand=True)
        col.prop(props, "auto_turns")

        col.separator()
        # 진행 여부의 기준은 씬 프로퍼티가 아니라 실제 세션 객체다.
        # is_running은 Dev Reload·파일 다시 열기로 세션이 사라져도 True로 남아
        # 취소/생성 버튼이 모두 잠기는 교착을 만든다 (draw에서는 수정 불가).
        running = session.is_active()
        if running:
            col.operator("lp3d.cancel", icon='CANCEL')
        else:
            col.operator("lp3d.generate", icon='PLAY')

        # 개선 사이클: 결과가 마음에 안 들면 [개선하기]를 필요한 만큼, 만족하면 [개선 종료]
        if not running and props.improve_open and props.last_code:
            box = layout.box()
            box.label(text=f"개선: {props.last_collection}", icon='MODIFIER')
            box.prop(props, "improve_feedback", text="")
            box.operator("lp3d.edit_prompt", text="개선 프롬프트 입력", icon='TEXT').target = 'improve_feedback'
            row = box.row(align=True)
            row.operator("lp3d.improve", icon='FILE_REFRESH')
            row.operator("lp3d.improve_done", icon='CHECKMARK')
            # 라이브러리 축적: 잘 나온 결과를 우수로 표시하면 다음 생성의 예시로 우선 쓰인다
            if props.last_entry_id:
                rate = box.row(align=True)
                rate.operator("lp3d.rate", text="우수", icon='SOLO_ON').rating = 2
                rate.operator("lp3d.library_discard", text="제외", icon='TRASH')

        # 진행 상태: 현재 작업 + 경과 시간 + 단계 목록
        box = layout.box()
        box.label(text=f"상태: {props.status}", icon='INFO')
        if running:
            elapsed = int(time.time() - props.started_at) if props.started_at else 0
            box.label(text=f"경과 {elapsed // 60}:{elapsed % 60:02d} · 턴 {props.iteration}/{props.total_turns}",
                      icon='TIME')
            gen_label, crit_label = _model_labels(props)
            steps = (
                ('GEN', f"코드 생성 — {gen_label}"),
                ('EXEC', "Blender 실행"),
                ('CAPTURE', "뷰포트 캡처"),
                ('CRITIQUE', f"스크린샷 비평 — {crit_label}"),
                ('FINAL', "마무리 정리"),
            )
            cur_idx = _PHASES.index(props.phase) if props.phase in _PHASES else -1
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
        col = self.layout.column(align=True)
        col.scale_y = 0.7
        for line in context.scene.lp3d.log.splitlines()[-15:]:
            col.label(text=line)


class LP3D_PT_output(bpy.types.Panel):
    bl_label = "결과물"
    bl_parent_id = "LP3D_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        props = context.scene.lp3d
        col = layout.column()
        if props.last_collection:
            col.label(text=f"마지막: {props.last_collection}", icon='OUTLINER_COLLECTION')
        col.prop(props, "export_dir")
        row = col.row(align=True)
        row.operator("lp3d.export", text="FBX").format = 'FBX'
        row.operator("lp3d.export", text="glTF").format = 'GLTF'
        col.operator("lp3d.mark_asset", icon='ASSET_MANAGER')
        col.operator("lp3d.variation", icon='DUPLICATE')
        col.separator()
        col.operator("lp3d.dev_reload", icon='FILE_REFRESH')


_CLASSES = (LP3D_PT_main, LP3D_PT_log, LP3D_PT_output)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
