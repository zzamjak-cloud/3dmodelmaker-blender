# 씬 단위 상태: 프롬프트, 진행 상태, 로그
import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, PointerProperty,
                       StringProperty)


def _persist_cb(self, context):
    # 익스포트 폴더는 파일이 바뀌어도 유지되도록 JSON에 저장
    from .core import persist
    persist.on_scene_changed(self)


# 잡 상태 → 리스트에 표시할 아이콘. 상태를 한눈에 구분할 수 있어야 한다.
STATE_ICONS = {
    'PENDING': 'DOT',
    'RUNNING': 'PLAY',
    'DONE': 'CHECKMARK',
    'FAILED': 'ERROR',
    'CANCELLED': 'X',
}


class LP3DJobItem(bpy.types.PropertyGroup):
    """생성 큐의 항목 하나 — 프롬프트 입력값과 그 실행 상태·결과를 함께 담는다.

    예전에는 이 값들이 씬에 단 하나씩만 있어서 세션을 동시에 하나만 돌릴 수 있었다.
    항목별로 상태를 갖게 하면서 여러 생성을 병렬로 진행할 수 있게 됐다."""

    # --- 입력 ---
    uid: IntProperty()  # 세션과 항목을 잇는 식별자 (리스트 인덱스는 정렬·삭제로 바뀐다)
    prompt: StringProperty(
        name="프롬프트",
        description="만들고 싶은 로우폴리 모델 설명 (예: 낡은 나무 배럴, 금속 밴드 2개)",
        default="",
    )
    ref_image_path: StringProperty(
        name="참조 이미지",
        description="모델링 시 참고할 이미지 (선택) — 형태·비율·색 구성을 이 이미지에 맞춰 생성",
        subtype='FILE_PATH',
        default="",
    )
    creation_mode: EnumProperty(
        name="제작 모드",
        description="이 항목으로 무엇을 만들지 — 단일 오브젝트인지, 여러 에셋으로 구성된 배경 공간인지",
        items=[
            ('OBJECT', "오브젝트", "단일 오브젝트 생성 (멀티뷰 3면도 기반)"),
            ('SCENE', "배경 공간", "여러 에셋으로 구성된 배경 공간 생성 (플랜 → 에셋 키트 → 배치)"),
        ],
        default='OBJECT',
    )
    scene_size: EnumProperty(
        name="씬 규모",
        description="배경 공간의 한 변 기준 크기 — 트라이 예산과 에셋 밀도의 기준이 된다",
        items=[
            ('S', "소형", "약 20m 규모"),
            ('M', "중형", "약 40m 규모"),
            ('L', "대형", "약 80m 규모"),
        ],
        default='M',
    )
    # 배경 잡이 플랜에 따라 스폰한 에셋 잡은 부모 uid를 문자열로 들고 있다.
    # 빈 문자열이면 사용자가 직접 만든 최상위 잡이다.
    parent_uid: StringProperty(default="")
    modeling_type: EnumProperty(
        name="모델링 타입",
        description="결과 모델의 재질 방식",
        items=[
            ('PALETTE', "컬러 스와치", "공유 팔레트 텍스처의 색 셀에 면을 매핑 (드로우콜 1개)"),
            ('TEXTURE', "개별 매핑", "모델을 언랩하고 6면도 AI 텍스처를 모델 이름의 개별 텍스처로 베이크"),
        ],
        default='PALETTE',
    )

    # --- 실행 상태 ---
    state: EnumProperty(
        items=[
            ('PENDING', "대기", "아직 실행되지 않음"),
            ('RUNNING', "실행 중", "AI 호출 또는 Blender 작업 진행 중"),
            ('DONE', "완료", "생성 성공"),
            ('FAILED', "실패", "생성 실패 — 원인은 상태·로그 참고"),
            ('CANCELLED', "취소됨", "사용자가 중단함"),
        ],
        default='PENDING',
    )
    status: StringProperty(default="대기 중")
    # 실패 시 사용자가 할 일 (예: "터미널에서 `codex login` 실행 후 다시 시도").
    # 상태줄에 함께 넣으면 사이드바 폭에서 가운데가 잘려 정작 조치가 사라진다.
    status_hint: StringProperty(default="")
    # 오브젝트 모드: GEN/EXEC/FINAL/TEX, 배경 모드: VIEW/PLAN/KIT/PLACE/FINAL
    phase: StringProperty(default="")
    started_at: FloatProperty(default=0.0)   # 경과 시간 표시용
    iteration: IntProperty(default=0)
    total_turns: IntProperty(default=0)
    log: StringProperty(default="")

    # --- 결과 ---
    collection_name: StringProperty(default="")
    code: StringProperty(default="")
    entry_id: StringProperty(default="")       # 라이브러리에 축적된 결과의 id
    multiview_path: StringProperty(default="")  # 이 잡의 멀티뷰 시트 경로
    texture_path: StringProperty(default="")    # 개별 매핑 결과 텍스처 PNG 경로
    lane: IntProperty(default=0)                # 결과를 Y축으로 밀어둘 레인 번호
    requested_model: StringProperty(default="")           # 생성 요청 모델 snapshot
    effective_model: StringProperty(default="")           # 실제 생성 모델 snapshot
    model_fallback: BoolProperty(default=False)             # Astra fallback 여부


class LP3DSceneProps(bpy.types.PropertyGroup):
    # 생성 큐 — 프롬프트를 항목으로 쌓아두고 한 번에 실행한다
    jobs: CollectionProperty(type=LP3DJobItem)
    job_index: IntProperty(default=0)
    next_uid: IntProperty(default=1)  # 다음 항목에 발급할 uid

    # --- 새 항목의 기본값이 되는 씬 설정 (설정 JSON으로 영속화) ---
    export_dir: StringProperty(
        name="익스포트 폴더",
        subtype='DIR_PATH',
        default="//exports/",
        update=_persist_cb,
    )
    multiview_preview_open: BoolProperty(
        name="멀티뷰 미리보기",
        description="패널에 멀티뷰(3면도) 시트 썸네일을 펼쳐 보여준다",
        default=True,
    )

    def active_job(self):
        """리스트에서 선택된 항목. 없으면 None."""
        if 0 <= self.job_index < len(self.jobs):
            return self.jobs[self.job_index]
        return None

    def job_by_uid(self, uid: int):
        for job in self.jobs:
            if job.uid == uid:
                return job
        return None


def _apply_saved(scene=None):
    """저장된 익스포트 폴더를 복원하고, 멈춘 잡 상태를 정리한다."""
    from .core import jobs, persist
    scenes = [scene] if scene else bpy.data.scenes
    for sc in scenes:
        if getattr(sc, "lp3d", None):
            persist.apply_scene(sc.lp3d)
            # 파일을 다시 열거나 Dev Reload를 하면 세션 객체는 사라지는데
            # 항목은 RUNNING으로 남아 실행 버튼이 잠긴다 — 대기로 되돌린다
            jobs.reset_stale(sc.lp3d)


@persistent
def _on_load_post(_filepath):
    # 새 파일/기존 파일을 열 때마다 사용자 설정을 이어받는다
    _apply_saved()


def _restore_deferred():
    _apply_saved()
    return None


def register():
    bpy.utils.register_class(LP3DJobItem)
    bpy.utils.register_class(LP3DSceneProps)
    bpy.types.Scene.lp3d = PointerProperty(type=LP3DSceneProps)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.timers.register(_restore_deferred, first_interval=0.2)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    del bpy.types.Scene.lp3d
    bpy.utils.unregister_class(LP3DSceneProps)
    bpy.utils.unregister_class(LP3DJobItem)
