# 오퍼레이터: 생성 큐 항목 조작(추가/삭제/복제/이동/재시도/중단)과
# 선택 항목 기준 결과물 처리(평가/변형/개선/익스포트/에셋 등록)/개발 리로드
import logging
import os

import bpy
from bpy.props import EnumProperty, IntProperty

_log = logging.getLogger(__name__)

from ..core import (clipboard_image, jobs, library, multiview, native_input,
                    session, snapshots)


class LP3D_OT_job_add(bpy.types.Operator):
    bl_idname = "lp3d.job_add"
    bl_label = "항목 추가"
    bl_description = "생성 큐에 새 프롬프트 항목을 추가한다 (OS 네이티브 입력 창)"

    @classmethod
    def poll(cls, context):
        return not native_input.is_open()

    def execute(self, context):
        scene_name = context.scene.name

        def on_done(text):
            if text is None:
                return  # 취소 — 항목을 만들지 않는다
            scene = bpy.data.scenes.get(scene_name)
            if scene and getattr(scene, "lp3d", None):
                jobs.add_job(scene.lp3d, native_input.to_single_line(text))

        error = native_input.open_dialog("프롬프트 입력", "", on_done)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_job_remove(bpy.types.Operator):
    bl_idname = "lp3d.job_remove"
    bl_label = "항목 삭제"
    bl_description = "선택한 항목을 큐에서 제거한다 (실행 중이면 먼저 중단)"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.jobs)

    def execute(self, context):
        jobs.remove_job(context, context.scene.lp3d.job_index)
        return {'FINISHED'}


class LP3D_OT_job_duplicate(bpy.types.Operator):
    bl_idname = "lp3d.job_duplicate"
    bl_label = "항목 복제"
    bl_description = "선택한 항목과 같은 프롬프트·설정으로 새 대기 항목을 만든다"

    @classmethod
    def poll(cls, context):
        return context.scene.lp3d.active_job() is not None

    def execute(self, context):
        props = context.scene.lp3d
        if jobs.duplicate_job(props, props.job_index) is None:
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_job_move(bpy.types.Operator):
    bl_idname = "lp3d.job_move"
    bl_label = "항목 이동"
    bl_description = "실행 순서를 바꾼다"

    delta: IntProperty(default=-1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return len(context.scene.lp3d.jobs) > 1

    def execute(self, context):
        props = context.scene.lp3d
        jobs.move_job(props, props.job_index, self.delta)
        return {'FINISHED'}


class LP3D_OT_job_retry(bpy.types.Operator):
    bl_idname = "lp3d.job_retry"
    bl_label = "재시도"
    bl_description = "실패하거나 취소된 항목을 대기로 되돌리고 다시 실행한다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and job.state in ('FAILED', 'CANCELLED')

    def execute(self, context):
        error = jobs.retry_job(context, context.scene.lp3d.job_index)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_job_cancel(bpy.types.Operator):
    bl_idname = "lp3d.job_cancel"
    bl_label = "항목 중단"
    bl_description = "선택한 항목의 생성만 중단한다 (다른 항목은 계속 진행)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and session.is_active(job.uid)

    def execute(self, context):
        session.cancel_session(context.scene.lp3d.active_job().uid)
        return {'FINISHED'}


class LP3D_OT_queue_start(bpy.types.Operator):
    bl_idname = "lp3d.queue_start"
    bl_label = "전체 실행"
    bl_description = ("대기 중인 항목을 모두 실행한다 — AI 호출은 환경설정의 동시 실행 수만큼 "
                      "병렬로, Blender 작업은 하나씩 순차로 진행된다")

    @classmethod
    def poll(cls, context):
        return any(job.state == 'PENDING' for job in context.scene.lp3d.jobs)

    def execute(self, context):
        started, error = jobs.start_all(context)
        if not started:
            self.report({'ERROR'}, error or "실행할 대기 항목이 없습니다")
            return {'CANCELLED'}
        if error:
            self.report({'WARNING'}, f"{started}개 시작 — 일부 실패: {error}")
        else:
            self.report({'INFO'}, f"{started}개 항목 실행 시작")
        return {'FINISHED'}


class LP3D_OT_queue_stop(bpy.types.Operator):
    bl_idname = "lp3d.queue_stop"
    bl_label = "전체 중지"
    bl_description = "진행 중인 모든 항목을 중단하고 생성물을 정리한다"

    @classmethod
    def poll(cls, context):
        return session.is_active()

    def execute(self, context):
        stopped = jobs.stop_all(context)
        self.report({'INFO'}, f"{stopped}개 항목 중단됨")
        return {'FINISHED'}


class LP3D_OT_paste_ref_image(bpy.types.Operator):
    bl_idname = "lp3d.paste_ref_image"
    bl_label = "클립보드에서 붙여넣기"
    bl_description = "브라우저 등에서 복사한 이미지를 선택 항목의 참조 이미지로 붙여넣는다 (.blend 옆에 PNG로 저장)"

    @classmethod
    def poll(cls, context):
        return clipboard_image.is_supported() and context.scene.lp3d.active_job() is not None

    def execute(self, context):
        # .blend 옆(저장 전이면 다운로드 폴더)에 남겨 다음에도 참조로 재사용할 수 있게 한다
        path, error = clipboard_image.paste_to(multiview.archive_dir())
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        context.scene.lp3d.active_job().ref_image_path = path
        self.report({'INFO'}, f"참조 이미지로 붙여넣음: {os.path.basename(path)}")
        return {'FINISHED'}


class LP3D_OT_clear_ref_image(bpy.types.Operator):
    bl_idname = "lp3d.clear_ref_image"
    bl_label = "참조 이미지 해제"
    bl_description = "참조 이미지 지정을 해제한다 (파일은 지우지 않는다)"

    @classmethod
    def poll(cls, context):
        return context.scene.lp3d.active_job() is not None

    def execute(self, context):
        context.scene.lp3d.active_job().ref_image_path = ""
        return {'FINISHED'}


class LP3D_OT_show_multiview(bpy.types.Operator):
    bl_idname = "lp3d.show_multiview"
    bl_label = "멀티뷰 보기"
    bl_description = "AI가 만든 정면/측면/상면/쿼터 참조 시트를 이미지 에디터 창으로 연다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.multiview_path)

    def execute(self, context):
        path = context.scene.lp3d.active_job().multiview_path
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


class LP3D_OT_open_multiview_folder(bpy.types.Operator):
    bl_idname = "lp3d.open_multiview_folder"
    bl_label = "저장 폴더 열기"
    bl_description = "멀티뷰 시트가 저장된 폴더를 파일 탐색기로 연다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.multiview_path)

    def execute(self, context):
        folder = os.path.dirname(context.scene.lp3d.active_job().multiview_path)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"폴더를 찾을 수 없습니다: {folder}")
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=folder)
        return {'FINISHED'}


class LP3D_OT_use_multiview_as_ref(bpy.types.Operator):
    bl_idname = "lp3d.use_multiview_as_ref"
    bl_label = "참조로 사용"
    bl_description = "이 멀티뷰 시트를 선택 항목의 참조 이미지로 지정한다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.multiview_path)

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        if not os.path.isfile(job.multiview_path):
            self.report({'ERROR'}, "멀티뷰 파일을 찾을 수 없습니다 (.blend 저장 후 다시 생성하세요)")
            return {'CANCELLED'}
        job.ref_image_path = job.multiview_path
        self.report({'INFO'}, "참조 이미지로 지정됨")
        return {'FINISHED'}


class LP3D_OT_load_last_multiview(bpy.types.Operator):
    bl_idname = "lp3d.load_last_multiview"
    bl_label = "저장된 멀티뷰 불러오기"
    bl_description = ("보관 폴더(.blend 옆 또는 다운로드/blender)에 저장된 "
                      "가장 최근 멀티뷰 시트를 미리보기로 불러온다")

    @classmethod
    def poll(cls, context):
        return context.scene.lp3d.active_job() is not None

    def execute(self, context):
        path = multiview.latest_archived()
        if not path:
            self.report({'WARNING'},
                        f"저장된 멀티뷰 시트가 없습니다: {multiview.archive_dir()}")
            return {'CANCELLED'}
        context.scene.lp3d.active_job().multiview_path = path
        self.report({'INFO'}, f"불러옴: {os.path.basename(path)}")
        return {'FINISHED'}


class LP3D_OT_clear_snapshots(bpy.types.Operator):
    bl_idname = "lp3d.clear_snapshots"
    bl_label = "단계 스냅샷 정리"
    bl_description = "옆에 남겨둔 턴별 중간 결과를 모두 삭제한다 (최종 모델은 유지)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.collection_name)

    def execute(self, context):
        removed = snapshots.clear_all(context.scene.lp3d.active_job().collection_name)
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
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.entry_id)

    def execute(self, context):
        entry_id = context.scene.lp3d.active_job().entry_id
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
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.entry_id)

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        if library.delete_entry(job.entry_id):
            job.entry_id = ""
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
        return not native_input.is_open() and context.scene.lp3d.active_job() is not None

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        target = self.target
        title = "프롬프트 입력" if target == 'prompt' else "개선 프롬프트 입력"
        scene_name = context.scene.name
        uid = job.uid

        def on_done(text):
            if text is None:
                return  # 취소 — 기존 값 유지
            # 다이얼로그가 떠 있는 동안 씬·리스트가 바뀌었을 수 있으므로 uid로 다시 찾는다
            scene = bpy.data.scenes.get(scene_name)
            if not scene or not getattr(scene, "lp3d", None):
                return
            target_job = scene.lp3d.job_by_uid(uid)
            if target_job:
                setattr(target_job, target, native_input.to_single_line(text))

        error = native_input.open_dialog(title, getattr(job, target, ""), on_done)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_variation(bpy.types.Operator):
    bl_idname = "lp3d.variation"
    bl_label = "변형 생성"
    bl_description = "선택 항목과 같은 스타일의 변형(variation)을 새 큐 항목으로 만들어 실행"

    count: IntProperty(name="변형 수", default=3, min=1, max=8)

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.code)

    def execute(self, context):
        props = context.scene.lp3d
        src = props.active_job()
        # 원본 값을 먼저 복사한다 — jobs.add_job()이 컬렉션을 재할당하면 src 참조가
        # 무효가 되어 접근 시 크래시할 수 있다
        code, prompt = src.code, src.prompt
        agent, turns = src.agent, src.auto_turns
        # 변형은 원본을 덮지 않고 새 항목·새 레인에 만든다
        new_job = jobs.add_job(props, prompt)
        new_job.agent = agent
        new_job.auto_turns = turns
        error = session.start_job(context.scene.name, new_job.uid,
                                  variation_of=code, variation_count=self.count)
        if error:
            new_job.state = 'FAILED'
            new_job.status = f"실패: {error}"
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_improve(bpy.types.Operator):
    bl_idname = "lp3d.improve"
    bl_label = "개선하기"
    bl_description = "선택 항목의 결과를 캡처해 한 단계 개선 (개선 요청 텍스트가 있으면 최우선 반영)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.code) and not session.is_active(job.uid)

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        error = session.start_job(context.scene.name, job.uid, improve=True)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_export(bpy.types.Operator):
    bl_idname = "lp3d.export"
    bl_label = "익스포트"
    bl_description = "선택 항목의 결과를 게임엔진용으로 내보내기"

    format: EnumProperty(
        name="포맷",
        items=[('FBX', "FBX (Unity)", ""), ('GLTF', "glTF", "")],
        default='FBX',
    )

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.collection_name)

    def execute(self, context):
        from ..pipeline import export
        props = context.scene.lp3d
        job = props.active_job()
        coll = bpy.data.collections.get(job.collection_name)
        if not coll:
            self.report({'ERROR'}, "생성 컬렉션을 찾을 수 없습니다")
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
    bl_description = "선택 항목의 결과를 Asset Browser에 등록 (프리뷰 + 카탈로그)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.collection_name)

    def execute(self, context):
        from ..pipeline import assets
        job = context.scene.lp3d.active_job()
        coll = bpy.data.collections.get(job.collection_name)
        if not coll:
            self.report({'ERROR'}, "생성 컬렉션을 찾을 수 없습니다")
            return {'CANCELLED'}
        try:
            catalog = assets.register_asset(context, coll, job.prompt)
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
        # (안 하면 CLI 프로세스와 타이머 펌프가 구 모듈에 남아 떠돈다).
        # cancel_all()이 아니라 shutdown()을 쓴다 — 정리를 Blender 큐에 넣기만 하면
        # 큐가 비워지기 전에 리로드가 끼어들어 정리 자체가 사라진다
        session.shutdown()
        pkg = __package__.rsplit(".", 1)[0]

        # 실제 리로드는 타이머로 미룬다 — 오퍼레이터 실행 스택 안에서 자기 클래스를
        # 등록 해제·재등록하면 self의 RNA가 해제된 채 접근되어 크래시한다 (macOS GUI).

        def _do_reload():
            import importlib
            try:
                root = importlib.import_module(pkg)
                root.dev_reload()
                message = "Dev Reload 완료"
            except Exception as e:
                # 타이머 안에서는 report를 쓸 수 없으므로 콘솔로 알린다.
                # 조용히 실패하면 구버전 모듈이 섞인 채로 계속 쓰게 된다.
                message = f"Dev Reload 실패: {e}"
                _log.exception("Dev Reload 실패")
            # 결과는 콘솔에만 남긴다 — 상태 줄은 잡 항목별 필드가 되어,
            # 리로드 결과를 적으면 무관한 항목의 상태를 덮어쓰게 된다
            print(f"LP3D {message}")
            return None

        bpy.app.timers.register(_do_reload, first_interval=0.1)
        self.report({'INFO'}, "리로드 예약됨")
        return {'FINISHED'}


_CLASSES = (
    LP3D_OT_edit_prompt, LP3D_OT_rate, LP3D_OT_library_discard,
    LP3D_OT_show_multiview, LP3D_OT_use_multiview_as_ref, LP3D_OT_open_multiview_folder,
    LP3D_OT_load_last_multiview,
    LP3D_OT_clear_snapshots,
    LP3D_OT_paste_ref_image, LP3D_OT_clear_ref_image,
    LP3D_OT_job_add, LP3D_OT_job_remove, LP3D_OT_job_duplicate, LP3D_OT_job_move,
    LP3D_OT_job_retry, LP3D_OT_job_cancel,
    LP3D_OT_queue_start, LP3D_OT_queue_stop,
    LP3D_OT_variation, LP3D_OT_improve,
    LP3D_OT_export, LP3D_OT_mark_asset, LP3D_OT_dev_reload,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
