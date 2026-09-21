# 씬 단위 상태: 프롬프트, 진행 상태, 로그
import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, PointerProperty,
                       StringProperty)

# 스타일 목록은 콜백으로 넘긴다 — properties.py는 bpy만 대역으로 세워 두고
# 패키지 컨텍스트 없이 로드되는 경로(유닛 테스트)가 있어 상위 패키지를 모듈 로드
# 시점에 import할 수 없다. 반환 리스트는 반드시 파이썬 쪽에서 참조를 붙들어야
# 한다 — Blender는 동적 enum의 문자열 수명을 보장하지 않아 라벨이 깨진다.
_STYLE_ITEMS = []


def _style_items(self, context):
    if not _STYLE_ITEMS:
        from .core import styles
        _STYLE_ITEMS.extend(styles.enum_items())
    return _STYLE_ITEMS


def _mode_changed_cb(self, context):
    # 모드를 바꾸면 그 모드에 맞는 기본값으로 나머지 옵션을 맞춘다 — 매번 전부 고르지 않게
    from .core import jobs
    jobs.apply_mode_defaults(self)


def _persist_cb(self, context):
    # 익스포트 폴더·리토폴로지 옵션은 파일이 바뀌어도 유지되도록 JSON에 저장
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
        description="이 항목으로 무엇을 만들지 — 바꾸면 나머지 옵션이 그 모드의 기본값으로 맞춰진다",
        items=[
            ('OBJECT', "오브젝트", "단일 오브젝트 생성 (멀티뷰 3면도 기반)"),
            ('SCENE', "배경 공간",
             "여러 에셋으로 구성된 배경 공간 생성 (플랜 → 에셋 키트 → 배치) — 머티리얼은 컬러 스와치 고정"),
            ('CHARACTER', "캐릭터",
             "게임 캐릭터 생성 — 정면 원화를 셰이프 서버에 넣어 셰이프와 PBR 텍스처를 한 번에 받는다"),
        ],
        default='OBJECT',
        update=_mode_changed_cb,
    )
    front_image: EnumProperty(
        name="정면 이미지",
        description="셰이프 서버에 넣을 정면 전신 이미지를 어떻게 마련할지",
        items=[
            ('GENERATE', "정면 원화 생성",
             "첨부한 원화(또는 프롬프트)로 정면 전신 한 컷을 새로 그려 넣는다 — 3/4 시점·배경·무기가 섞인 원화도 안전"),
            ('USE_REF', "원화 그대로 사용",
             "첨부한 원화를 그대로 넣는다 — 이미 정면 전신이면 비율·디자인이 100% 보존되지만, "
             "3/4 시점이거나 배경·무기가 함께 있으면 형상이 망가진다"),
        ],
        default='GENERATE',
    )
    character_type: EnumProperty(
        name="캐릭터 유형",
        description="비율·골격 규칙을 정한다 — 자동이면 요청문·원화에서 판단",
        items=[
            ('AUTO', "자동", "요청문과 원화에서 유형을 판단"),
            ('HUMANOID', "인간형", "두신 비율, A-포즈 (팔을 45도 아래로 — 리깅하기 좋은 중립 자세)"),
            ('ANIMAL', "동물형", "실제 동물 골격 비율과 관절 방향, 네 발 중립 자세"),
            ('CREATURE', "크리처형", "동물 부위 조합이되 하나의 골격 논리"),
        ],
        default='AUTO',
    )
    scene_size: EnumProperty(
        name="씬 규모",
        description="무엇을 만드는지 — 공간 크기와 함께 에셋 종류·배치 총량·실내 여부가 정해진다",
        items=[
            ('S', "실내 (건물 내부)", "약 12m 방·홀·상점 내부 — 지형 대신 바닥·벽을 만들고 가구를 촘촘히 배치"),
            ('M', "구역", "약 40m — 건물 여러 채와 주변 요소가 들어가는 특정 구역"),
            ('L', "대규모", "약 100m — 대도시·대형 성채"),
        ],
        default='M',
    )
    # 배경 잡이 플랜에 따라 스폰한 에셋 잡은 부모 uid를 문자열로 들고 있다.
    # 빈 문자열이면 사용자가 직접 만든 최상위 잡이다.
    parent_uid: StringProperty(default="")
    style: EnumProperty(
        name="스타일",
        description="결과 모델의 아트 스타일 — 형태 규칙·폴리 버짓·배색과 참조 시트 스타일을 함께 정한다",
        items=_style_items,
    )
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
    log: StringProperty(default="")

    # --- 결과 ---
    collection_name: StringProperty(default="")
    code: StringProperty(default="")
    entry_id: StringProperty(default="")       # 라이브러리에 축적된 결과의 id
    multiview_path: StringProperty(default="")  # 이 잡의 멀티뷰 시트 경로
    texture_path: StringProperty(default="")    # 개별 매핑 결과 텍스처 PNG 경로
    image_backend: StringProperty(default="")   # 이 잡의 참조 시트를 만든 백엔드 (표시용)
    lane: IntProperty(default=0)                # 결과를 Y축으로 밀어둘 레인 번호
    requested_model: StringProperty(default="")           # 생성 요청 모델 snapshot
    effective_model: StringProperty(default="")           # 실제 생성 모델 snapshot
    model_fallback: BoolProperty(default=False)             # Astra fallback 여부


class LP3DSceneProps(bpy.types.PropertyGroup):
    # 생성 큐 — 프롬프트를 항목으로 쌓아두고 한 번에 실행한다
    jobs: CollectionProperty(type=LP3DJobItem)
    job_index: IntProperty(default=0)
    next_uid: IntProperty(default=1)  # 다음 항목에 발급할 uid

    # --- 씬 설정 (익스포트 폴더는 설정 JSON으로 영속화) ---
    export_dir: StringProperty(
        name="익스포트 폴더",
        subtype='DIR_PATH',
        default="//exports/",
        update=_persist_cb,
    )
    # 리토폴로지 옵션 — 결과물 패널의 [리토폴로지] 버튼 옆에서 고른다 (설정 JSON으로 영속화)
    retopo_faces: IntProperty(
        name="목표 면수",
        description="쿼드 메시의 목표 면수 — QuadriFlow 가 이 수에 맞춰 와이어를 깔고, 실패하면 데시메이트로 맞춘다",
        default=12000, min=500, max=60000,
        update=_persist_cb,
    )
    retopo_symmetry: BoolProperty(
        name="X 대칭",
        description="와이어를 X 축 기준 좌우 대칭으로 깐다 — 캐릭터는 켜는 편이 낫다",
        default=True,
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
