# 씬 단위 상태: 프롬프트, 에이전트 선택, 진행 상태, 로그
import bpy
from bpy.app.handlers import persistent
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)


def _persist_cb(self, context):
    # 에이전트·반복 수·익스포트 폴더는 파일이 바뀌어도 유지되도록 JSON에 저장
    from .core import persist
    persist.on_scene_changed(self)


class LP3DSceneProps(bpy.types.PropertyGroup):
    prompt: StringProperty(
        name="프롬프트",
        description="만들고 싶은 로우폴리 모델 설명 (예: 낡은 나무 배럴, 금속 밴드 2개)",
        default="",
    )
    agent: EnumProperty(
        name="에이전트",
        items=[
            ('CLAUDE', "Claude", "claude -p 서브프로세스 사용"),
            ('CODEX', "Codex", "codex exec 서브프로세스 사용"),
        ],
        default='CLAUDE',
        update=_persist_cb,
    )
    # 구버전 auto_cycles(사이클 단위, 1회=3턴)에서 턴 단위 1:1로 바꿨다 —
    # 저장값을 그대로 이어받으면 의미가 3배로 어긋나므로 키 교체로 마이그레이션한다
    auto_turns: IntProperty(
        name="자동 반복(턴)",
        description="자동 시각 피드백 루프의 총 턴 수 — 값 그대로가 턴 수 (권장 3: 생성 1 + 비평·개선 2). 이후에도 개선하기 버튼으로 추가 반복 가능",
        default=3, min=1, max=9,
        update=_persist_cb,
    )
    ref_image_path: StringProperty(
        name="참조 이미지",
        description="모델링 시 참고할 이미지 (선택) — 형태·비율·색 구성을 이 이미지에 맞춰 생성·개선",
        subtype='FILE_PATH',
        default="",
    )
    improve_feedback: StringProperty(
        name="개선 요청",
        description="어디가 마음에 안 드는지 설명 (선택 — 비워두면 자동 비평만으로 개선)",
        default="",
    )
    improve_open: BoolProperty(default=False)  # 생성 성공 후 개선 UI 노출 여부
    # --- 이하 런타임 상태 (UI 표시용) ---
    is_running: BoolProperty(default=False)
    status: StringProperty(default="대기 중")
    # 실패 시 사용자가 할 일 (예: "터미널에서 `codex login` 실행 후 다시 시도").
    # 상태줄에 함께 넣으면 사이드바 폭에서 가운데가 잘려 정작 조치가 사라진다.
    status_hint: StringProperty(default="")
    phase: StringProperty(default="")        # 현재 단계 식별자 (GEN/EXEC/CAPTURE/CRITIQUE/FINAL)
    started_at: FloatProperty(default=0.0)   # 세션 시작 시각 (경과 시간 표시용)
    iteration: IntProperty(default=0)
    total_turns: IntProperty(default=0)      # 진행 표시용 총 턴 수 (세션 시작 시 설정)
    log: StringProperty(default="")
    # 마지막 성공 세션의 결과 (variation/익스포트/에셋 등록에 사용)
    last_collection: StringProperty(default="")
    last_code: StringProperty(default="")
    last_prompt: StringProperty(default="")
    last_entry_id: StringProperty(default="")  # 라이브러리에 축적된 마지막 결과의 id
    multiview_path: StringProperty(default="")  # 이번 세션의 멀티뷰 시트 경로
    multiview_preview_open: BoolProperty(
        name="멀티뷰 미리보기",
        description="패널에 멀티뷰(3면도) 시트 썸네일을 펼쳐 보여준다",
        default=True,
    )
    export_dir: StringProperty(
        name="익스포트 폴더",
        subtype='DIR_PATH',
        default="//exports/",
        update=_persist_cb,
    )


def _apply_saved(scene=None):
    """저장된 씬 설정(agent/반복/익스포트 폴더)을 복원한다."""
    from .core import persist
    scenes = [scene] if scene else bpy.data.scenes
    for sc in scenes:
        if getattr(sc, "lp3d", None):
            persist.apply_scene(sc.lp3d)


@persistent
def _on_load_post(_filepath):
    # 새 파일/기존 파일을 열 때마다 사용자 설정을 이어받는다
    _apply_saved()


def _restore_deferred():
    _apply_saved()
    return None


def register():
    bpy.utils.register_class(LP3DSceneProps)
    bpy.types.Scene.lp3d = PointerProperty(type=LP3DSceneProps)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.timers.register(_restore_deferred, first_interval=0.2)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    del bpy.types.Scene.lp3d
    bpy.utils.unregister_class(LP3DSceneProps)
