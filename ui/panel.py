# 3D 뷰포트 사이드바 패널
import bpy

from ..core import session


class LP3D_PT_main(bpy.types.Panel):
    bl_label = "AI 모델 생성"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI 모델러"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lp3d

        col = layout.column()
        col.prop(props, "prompt", text="")
        row = col.row(align=True)
        row.prop(props, "agent", expand=True)
        col.prop(props, "max_iterations")

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
            row = box.row(align=True)
            row.operator("lp3d.improve", icon='FILE_REFRESH')
            row.operator("lp3d.improve_done", icon='CHECKMARK')

        # 진행 상태
        box = layout.box()
        box.label(text=f"상태: {props.status}", icon='INFO')
        if running:
            box.label(text=f"반복: {props.iteration}/{props.max_iterations}")


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
