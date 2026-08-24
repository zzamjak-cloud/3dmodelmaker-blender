# 씬 단위 상태: 프롬프트, 에이전트 선택, 진행 상태, 로그
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty


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
        name="개선 반복",
        description="시각 피드백 루프 최대 반복 횟수 (1이면 원샷 생성)",
        default=3, min=1, max=8,
    )
    # --- 이하 런타임 상태 (UI 표시용) ---
    is_running: BoolProperty(default=False)
    status: StringProperty(default="대기 중")
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
