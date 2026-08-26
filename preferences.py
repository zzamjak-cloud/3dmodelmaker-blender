# 애드온 환경설정: CLI 경로, 타임아웃, 캡처 설정, 에셋 라이브러리
import os
import shutil

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

# macOS Finder로 실행한 Blender는 사용자 PATH를 상속하지 않으므로 흔한 설치 경로를 직접 탐색
_EXTRA_PATHS = (
    "~/.local/bin",
    "~/.claude/local",
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

    claude_path: StringProperty(
        name="Claude CLI 경로",
        description="claude 실행 파일 절대경로 (비우면 자동 탐지)",
        subtype='FILE_PATH',
        default="",
        update=_persist_cb,
    )
    codex_path: StringProperty(
        name="Codex CLI 경로",
        description="codex 실행 파일 절대경로 (비우면 자동 탐지)",
        subtype='FILE_PATH',
        default="",
        update=_persist_cb,
    )
    _MODEL_ITEMS = [
        ('DEFAULT', "CLI 기본", "claude CLI에 설정된 기본 모델 사용"),
        ('opus', "Opus (고품질)", "가장 정교한 결과, 느림"),
        ('sonnet', "Sonnet (균형)", "품질과 속도의 균형"),
        ('haiku', "Haiku (빠름)", "가장 빠름, 단순한 작업에 적합"),
    ]
    gen_model: EnumProperty(
        name="생성 모델",
        description="초기 코드 생성 모델 (Claude 전용 — Codex는 CLI 기본 설정 사용)",
        items=_MODEL_ITEMS,
        default='DEFAULT',
        update=_persist_cb,
    )
    critique_model: EnumProperty(
        name="비평 모델",
        description="이미지 비평·개선 턴 전용 모델 — 빠른 모델일수록 개선이 빨라짐 (Claude 전용)",
        items=_MODEL_ITEMS,
        default='DEFAULT',
        update=_persist_cb,
    )
    timeout: IntProperty(
        name="CLI 타임아웃(초)",
        description="에이전트 호출 1회당 최대 대기 시간",
        default=300, min=30, max=1800,
        update=_persist_cb,
    )
    capture_count: IntProperty(
        name="캡처 앵글 수",
        description="비평 턴에 보여줄 컬러 캡처 장수 (실루엣 1장은 별도) — 적을수록 비평이 빠르다",
        default=2, min=1, max=6,
        update=_persist_cb,
    )
    capture_resolution: IntProperty(
        name="캡처 해상도",
        default=512, min=256, max=1024,
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
        col.prop(self, "claude_path")
        col.prop(self, "codex_path")
        col.prop(self, "gen_model")
        col.prop(self, "critique_model")
        col.prop(self, "timeout")
        col.prop(self, "capture_count")
        col.prop(self, "capture_resolution")
        col.prop(self, "asset_library_path")


class _Defaults:
    """애드온으로 활성화되지 않은 상태(테스트 등)에서 쓰는 기본값."""
    claude_path = ""
    codex_path = ""
    gen_model = 'DEFAULT'
    critique_model = 'DEFAULT'
    timeout = 300
    capture_count = 2
    capture_resolution = 512
    asset_library_path = ""


_DEFAULTS = _Defaults()


def get_prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else _DEFAULTS


def resolve_cli_path(agent: str) -> str:
    """설정값 우선, 없으면 자동 탐지. agent는 'CLAUDE' 또는 'CODEX'."""
    prefs = get_prefs()
    if agent == 'CLAUDE':
        return prefs.claude_path or find_cli("claude")
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
