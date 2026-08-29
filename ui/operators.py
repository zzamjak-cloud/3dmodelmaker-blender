# 오퍼레이터: 생성/취소/익스포트/에셋 등록/변형/개발 리로드
import os

import bpy
from bpy.props import EnumProperty, IntProperty

from ..core import library, native_input, session, snapshots


class LP3D_OT_show_multiview(bpy.types.Operator):
    bl_idname = "lp3d.show_multiview"
    bl_label = "멀티뷰 보기"
    bl_description = "AI가 만든 정면/측면/상면/쿼터 참조 시트를 이미지 에디터 창으로 연다"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.multiview_path)

    def execute(self, context):
        path = context.scene.lp3d.multiview_path
        if not os.path.isfile(path):
            self.report({'ERROR'}, f"파일을 찾을 수 없습니다: {path}")
            return {'CANCELLED'}
        img = next((i for i in bpy.data.images if i.filepath == path), None)
        if img is None:
            img = bpy.data.images.load(path)
        # 기존 이미지 에디터가 있으면 재사용하고, 없으면 새 창을 띄운다
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.image = img
                    return {'FINISHED'}
        bpy.ops.wm.window_new()
        area = context.window_manager.windows[-1].screen.areas[0]
        area.type = 'IMAGE_EDITOR'
        area.spaces.active.image = img
        return {'FINISHED'}


class LP3D_OT_use_multiview_as_ref(bpy.types.Operator):
    bl_idname = "lp3d.use_multiview_as_ref"
    bl_label = "참조로 사용"
    bl_description = "이 멀티뷰 시트를 참조 이미지로 지정해 다음 생성에 사용한다"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.multiview_path)

    def execute(self, context):
        props = context.scene.lp3d
        if not os.path.isfile(props.multiview_path):
            self.report({'ERROR'}, "멀티뷰 파일을 찾을 수 없습니다 (.blend 저장 후 다시 생성하세요)")
            return {'CANCELLED'}
        props.ref_image_path = props.multiview_path
        self.report({'INFO'}, "참조 이미지로 지정됨")
        return {'FINISHED'}


class LP3D_OT_clear_snapshots(bpy.types.Operator):
    bl_idname = "lp3d.clear_snapshots"
    bl_label = "단계 스냅샷 정리"
    bl_description = "옆에 남겨둔 턴별 중간 결과를 모두 삭제한다 (최종 모델은 유지)"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.last_collection)

    def execute(self, context):
        removed = snapshots.clear_all(context.scene.lp3d.last_collection)
        self.report({'INFO'}, f"스냅샷 {removed}개 정리됨" if removed else "정리할 스냅샷이 없습니다")
        return {'FINISHED'}


class LP3D_OT_rate(bpy.types.Operator):
    bl_idname = "lp3d.rate"
    bl_label = "평가"
    bl_description = "이 결과를 라이브러리에서 평가 — 우수로 표시하면 다음 생성의 예시로 우선 사용된다"

    # 0=평가 취소, 1=합격, 2=우수
    rating: IntProperty(default=2, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.last_entry_id)

    def execute(self, context):
        entry_id = context.scene.lp3d.last_entry_id
        if not library.set_rating(entry_id, self.rating):
            self.report({'WARNING'}, "라이브러리에서 항목을 찾을 수 없습니다")
            return {'CANCELLED'}
        labels = {0: "평가 해제", 1: "합격", 2: "우수"}
        self.report({'INFO'}, f"평가: {labels.get(self.rating, self.rating)}")
        return {'FINISHED'}


class LP3D_OT_library_discard(bpy.types.Operator):
    bl_idname = "lp3d.library_discard"
    bl_label = "라이브러리에서 제외"
    bl_description = "이 결과를 라이브러리에서 삭제 — 품질이 낮아 예시로 쓰고 싶지 않을 때"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.last_entry_id)

    def execute(self, context):
        props = context.scene.lp3d
        if library.delete_entry(props.last_entry_id):
            props.last_entry_id = ""
            self.report({'INFO'}, "라이브러리에서 제외됨")
            return {'FINISHED'}
        self.report({'WARNING'}, "라이브러리에서 항목을 찾을 수 없습니다")
        return {'CANCELLED'}


class LP3D_OT_edit_prompt(bpy.types.Operator):
    bl_idname = "lp3d.edit_prompt"
    bl_label = "프롬프트 입력"
    bl_description = "OS 네이티브 입력 창을 열어 한글 입력 문제 없이 작성 (입력완료 시 필드에 반영)"

    # 프롬프트/개선 요청 두 필드를 하나의 오퍼레이터로 처리 — 값은 프로퍼티 이름과 일치
    target: EnumProperty(
        items=[('prompt', "프롬프트", ""), ('improve_feedback', "개선 요청", "")],
        default='prompt', options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        return not native_input.is_open()

    def execute(self, context):
        props = context.scene.lp3d
        target = self.target
        title = "프롬프트 입력" if target == 'prompt' else "개선 프롬프트 입력"
        scene_name = context.scene.name

        def on_done(text):
            if text is None:
                return  # 취소 — 기존 값 유지
            # 다이얼로그가 떠 있는 동안 씬이 바뀌었을 수 있으므로 이름으로 다시 찾는다
            scene = bpy.data.scenes.get(scene_name)
            if scene and getattr(scene, "lp3d", None):
                setattr(scene.lp3d, target, native_input.to_single_line(text))

        error = native_input.open_dialog(title, getattr(props, target, ""), on_done)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


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
        # 개선을 끝냈다는 건 결과를 받아들였다는 뜻 — 라이브러리에서 합격으로 표시한다
        if props.last_entry_id:
            library.set_rating(props.last_entry_id, 1)
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
        # 리로드하면 세션 모듈의 전역 상태가 초기화되므로, 진행 중인 세션은 먼저 정리한다
        # (안 하면 CLI 프로세스와 타이머 펌프가 구 모듈에 남아 떠돈다)
        session.cancel_session()
        pkg = __package__.rsplit(".", 1)[0]

        # 실제 리로드는 타이머로 미룬다 — 오퍼레이터 실행 스택 안에서 자기 클래스를
        # 등록 해제·재등록하면 self의 RNA가 해제된 채 접근되어 크래시한다 (macOS GUI).
        def _do_reload():
            import importlib
            root = importlib.import_module(pkg)
            root.dev_reload()
            print("LP3D Dev Reload 완료")
            return None

        bpy.app.timers.register(_do_reload, first_interval=0.1)
        self.report({'INFO'}, "리로드 예약됨")
        return {'FINISHED'}


_CLASSES = (
    LP3D_OT_edit_prompt, LP3D_OT_rate, LP3D_OT_library_discard,
    LP3D_OT_show_multiview, LP3D_OT_use_multiview_as_ref, LP3D_OT_clear_snapshots,
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
