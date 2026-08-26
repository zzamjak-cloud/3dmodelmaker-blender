# 오퍼레이터: 생성/취소/익스포트/에셋 등록/변형/개발 리로드
import os

import bpy
from bpy.props import EnumProperty, IntProperty

from ..core import session


class LP3D_OT_generate(bpy.types.Operator):
    bl_idname = "lp3d.generate"
    bl_label = "모델 생성"
    bl_description = "프롬프트로 AI 에이전트에게 로우폴리 모델 생성을 요청"

    @classmethod
    def poll(cls, context):
        return not session.is_active()

    def execute(self, context):
        error = session.start_session(context)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_cancel(bpy.types.Operator):
    bl_idname = "lp3d.cancel"
    bl_label = "취소"
    bl_description = "진행 중인 생성 세션을 중단하고 생성물을 정리"

    @classmethod
    def poll(cls, context):
        return session.is_active()

    def execute(self, context):
        session.cancel_session()
        return {'FINISHED'}


class LP3D_OT_variation(bpy.types.Operator):
    bl_idname = "lp3d.variation"
    bl_label = "변형 생성"
    bl_description = "마지막 결과와 같은 스타일의 변형(variation)을 생성"

    count: IntProperty(name="변형 수", default=3, min=1, max=8)

    @classmethod
    def poll(cls, context):
        return not session.is_active() and bool(context.scene.lp3d.last_code)

    def execute(self, context):
        props = context.scene.lp3d
        error = session.start_session(context, variation_of=props.last_code,
                                      variation_count=self.count)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_improve(bpy.types.Operator):
    bl_idname = "lp3d.improve"
    bl_label = "개선하기"
    bl_description = "마지막 결과를 캡처해 한 단계 개선 (개선 요청 텍스트가 있으면 최우선 반영)"

    @classmethod
    def poll(cls, context):
        return not session.is_active() and bool(context.scene.lp3d.last_code)

    def execute(self, context):
        error = session.start_session(context, improve=True)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_improve_done(bpy.types.Operator):
    bl_idname = "lp3d.improve_done"
    bl_label = "개선 종료"
    bl_description = "현재 결과를 확정하고 패널을 새 모델 생성을 위한 초기 상태로 되돌림 (모델·익스포트 기능은 유지)"

    @classmethod
    def poll(cls, context):
        return not session.is_active()

    def execute(self, context):
        props = context.scene.lp3d
        props.improve_open = False
        props.prompt = ""
        props.improve_feedback = ""
        props.status = "대기 중"
        props.iteration = 0
        props.log = ""
        return {'FINISHED'}


class LP3D_OT_export(bpy.types.Operator):
    bl_idname = "lp3d.export"
    bl_label = "익스포트"
    bl_description = "마지막 생성 결과를 게임엔진용으로 내보내기"

    format: EnumProperty(
        name="포맷",
        items=[('FBX', "FBX (Unity)", ""), ('GLTF', "glTF", "")],
        default='FBX',
    )

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.last_collection)

    def execute(self, context):
        from ..pipeline import export
        props = context.scene.lp3d
        coll = bpy.data.collections.get(props.last_collection)
        if not coll:
            self.report({'ERROR'}, "마지막 생성 컬렉션을 찾을 수 없습니다")
            return {'CANCELLED'}
        out_dir = bpy.path.abspath(props.export_dir)
        os.makedirs(out_dir, exist_ok=True)
        try:
            path = export.export_collection(coll, out_dir, self.format)
        except Exception as e:
            self.report({'ERROR'}, f"익스포트 실패: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"내보냄: {path}")
        return {'FINISHED'}


class LP3D_OT_mark_asset(bpy.types.Operator):
    bl_idname = "lp3d.mark_asset"
    bl_label = "에셋 등록"
    bl_description = "마지막 생성 결과를 Asset Browser에 등록 (프리뷰 + 카탈로그)"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.last_collection)

    def execute(self, context):
        from ..pipeline import assets
        props = context.scene.lp3d
        coll = bpy.data.collections.get(props.last_collection)
        if not coll:
            self.report({'ERROR'}, "마지막 생성 컬렉션을 찾을 수 없습니다")
            return {'CANCELLED'}
        try:
            catalog = assets.register_asset(context, coll, props.last_prompt)
        except Exception as e:
            self.report({'ERROR'}, f"에셋 등록 실패: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"에셋 등록됨: {coll.name} → {catalog}")
        return {'FINISHED'}


class LP3D_OT_dev_reload(bpy.types.Operator):
    bl_idname = "lp3d.dev_reload"
    bl_label = "Dev Reload"
    bl_description = "애드온 모듈을 다시 로드 (개발용)"

    def execute(self, context):
        import importlib
        # 리로드하면 세션 모듈의 전역 상태가 초기화되므로, 진행 중인 세션은 먼저 정리한다
        # (안 하면 CLI 프로세스와 타이머 펌프가 구 모듈에 남아 떠돈다)
        session.cancel_session()
        root = importlib.import_module(__package__.rsplit(".", 1)[0])
        root.dev_reload()
        self.report({'INFO'}, "리로드 완료")
        return {'FINISHED'}


_CLASSES = (
    LP3D_OT_generate, LP3D_OT_cancel, LP3D_OT_variation,
    LP3D_OT_improve, LP3D_OT_improve_done,
    LP3D_OT_export, LP3D_OT_mark_asset, LP3D_OT_dev_reload,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
