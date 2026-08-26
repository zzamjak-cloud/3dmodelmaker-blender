# 씬 단위 상태: 프롬프트, 에이전트 선택, 진행 상태, 로그
import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)


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
    )
    max_iterations: IntProperty(
        name="자동 반복",
        description="자동 시각 피드백 루프 최대 반복 횟수 — 이후에도 개선하기 버튼으로 추가 반복 가능",
        default=3, min=1, max=8,
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
    phase: StringProperty(default="")        # 현재 단계 식별자 (GEN/EXEC/CAPTURE/CRITIQUE/FINAL)
    started_at: FloatProperty(default=0.0)   # 세션 시작 시각 (경과 시간 표시용)
    iteration: IntProperty(default=0)
    log: StringProperty(default="")
    # 마지막 성공 세션의 결과 (variation/익스포트/에셋 등록에 사용)
    last_collection: StringProperty(default="")
    last_code: StringProperty(default="")
    last_prompt: StringProperty(default="")
    export_dir: StringProperty(
        name="익스포트 폴더",
        subtype='DIR_PATH',
        default="//exports/",
    )


def register():
    bpy.utils.register_class(LP3DSceneProps)
    bpy.types.Scene.lp3d = PointerProperty(type=LP3DSceneProps)


def unregister():
    del bpy.types.Scene.lp3d
    bpy.utils.unregister_class(LP3DSceneProps)
