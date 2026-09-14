# 애드온 환경설정: CLI 경로, 타임아웃, 에셋 라이브러리
import os
import shutil

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)

# macOS Finder로 실행한 Blender는 사용자 PATH를 상속하지 않으므로 흔한 설치 경로를 직접 탐색
_EXTRA_PATHS = (
    "~/.local/bin",
    "~/.npm-global/bin",
    "~/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)


def find_cli(name: str) -> str:
    """PATH + 흔한 설치 경로에서 CLI 실행 파일 절대경로를 찾는다. 없으면 빈 문자열."""
    found = shutil.which(name)
    if found:
        return found
    for base in _EXTRA_PATHS:
        candidate = os.path.join(os.path.expanduser(base), name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def _persist_cb(self, context):
    # 변경 즉시 JSON에 저장 — 애드온 업데이트/스키마 변경에도 설정 유지
    from .core import persist
    persist.on_prefs_changed(self)


class LP3DPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    codex_path: StringProperty(
        name="Codex CLI 경로",
        description="codex 실행 파일 절대경로 (비우면 자동 탐지)",
        subtype='FILE_PATH',
        default="",
        update=_persist_cb,
    )
    timeout: IntProperty(
        name="CLI 타임아웃(초)",
        description="에이전트 호출 1회당 최대 대기 시간",
        default=300, min=30, max=1800,
        update=_persist_cb,
    )
    ai_concurrency: IntProperty(
        name="동시 AI 실행 수",
        description=("동시에 실행할 AI CLI 개수 — 큐에 쌓인 여러 프롬프트의 AI 호출이 "
                     "이만큼 병렬로 진행된다. Blender 작업은 이 값과 무관하게 항상 "
                     "하나씩 순차 실행된다. 1로 두면 예전처럼 완전 순차 동작"),
        default=3, min=1, max=8,
        update=_persist_cb,
    )
    use_multiview: BoolProperty(
        name="멀티뷰 참조 생성",
        description="생성 시작 시 codex image_gen으로 정면/측면/상면/쿼터뷰 참조 시트를 먼저 만들어 모델링 기준으로 사용",
        default=True,
        update=_persist_cb,
    )
    use_library: BoolProperty(
        name="생성 라이브러리 사용",
        description="성공한 생성 결과(프롬프트·코드)를 쌓아두고, 비슷한 요청이 오면 과거 합격 코드를 예시로 참고해 품질을 높인다",
        default=True,
        update=_persist_cb,
    )
    texture_resolution: EnumProperty(
        name="개별 매핑 텍스처 크기",
        description="개별 매핑 타입의 베이크 텍스처 한 변 픽셀 수 — 클수록 CPU 베이크가 오래 걸린다",
        items=[
            ('512', "512", "빠름 — 소품용"),
            ('1024', "1024", "기본 — 품질과 베이크 시간의 균형"),
            ('2048', "2048", "느림 — 큰 건물용"),
        ],
        default='1024',
        update=_persist_cb,
    )
    scene_tri_budget: IntProperty(
        name="배경 씬 트라이 예산",
        description="배경 공간 하나가 쓸 수 있는 전체 삼각형 수 상한 — 플랜의 에셋 개수·밀도를 여기에 맞춰 줄인다",
        default=80000, min=10000, max=500000,
        update=_persist_cb,
    )
    scene_max_assets: IntProperty(
        name="배경 에셋 종류 상한",
        description="배경 플랜이 요청할 수 있는 고유 에셋 종류 수 — 많을수록 키트 생성 시간이 길어진다",
        default=12, min=4, max=24,
        update=_persist_cb,
    )
    scene_timeout_scale: FloatProperty(
        name="배경 턴 타임아웃 배수",
        description="배경 모드의 플랜·배치 턴은 오브젝트 한 개보다 오래 걸린다 — CLI 타임아웃에 이 배수를 곱한다",
        default=2.0, min=1.0, max=5.0,
        update=_persist_cb,
    )
    asset_library_path: StringProperty(
        name="에셋 라이브러리 경로",
        description="Asset Browser 라이브러리 루트 (카탈로그 파일 위치)",
        subtype='DIR_PATH',
        default="",
        update=_persist_cb,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "codex_path")
        col.prop(self, "timeout")
        col.prop(self, "ai_concurrency")
        col.prop(self, "use_multiview")
        col.prop(self, "use_library")
        col.prop(self, "texture_resolution")
        scene_box = self.layout.box()
        scene_box.label(text="배경 공간", icon='WORLD')
        scene_box.prop(self, "scene_tri_budget")
        scene_box.prop(self, "scene_max_assets")
        scene_box.prop(self, "scene_timeout_scale")
        from .core import library
        try:
            info = library.stats()
            col.label(text=f"라이브러리: {info['count']}개 축적 ({info['rated']}개 평가됨)", icon='ASSET_MANAGER')
        except Exception:
            pass
        col.prop(self, "asset_library_path")


class _Defaults:
    """애드온으로 활성화되지 않은 상태(테스트 등)에서 쓰는 기본값."""
    codex_path = ""
    timeout = 300
    ai_concurrency = 3
    use_multiview = True
    use_library = True
    asset_library_path = ""
    texture_resolution = '1024'
    scene_tri_budget = 80000
    scene_max_assets = 12
    scene_timeout_scale = 2.0


_DEFAULTS = _Defaults()


def get_prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else _DEFAULTS


def resolve_cli_path(agent: str = 'CODEX') -> str:
    """설정값을 우선하고 없으면 Codex CLI를 자동 탐지한다."""
    prefs = get_prefs()
    return prefs.codex_path or find_cli("codex")


def _restore_deferred():
    # 애드온 활성화가 끝난 뒤 저장된 설정을 복원한다
    addon = bpy.context.preferences.addons.get(__package__)
    if addon:
        from .core import persist
        persist.apply_prefs(addon.preferences)
    return None


def register():
    bpy.utils.register_class(LP3DPreferences)
    bpy.app.timers.register(_restore_deferred, first_interval=0.2)


def unregister():
    bpy.utils.unregister_class(LP3DPreferences)
