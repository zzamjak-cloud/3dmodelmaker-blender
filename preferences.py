# 애드온 환경설정: CLI 경로, 타임아웃, 캡처 설정, 에셋 라이브러리
import os
import shutil

import bpy
from bpy.props import IntProperty, StringProperty

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


class LP3DPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    claude_path: StringProperty(
        name="Claude CLI 경로",
        description="claude 실행 파일 절대경로 (비우면 자동 탐지)",
        subtype='FILE_PATH',
        default="",
    )
    codex_path: StringProperty(
        name="Codex CLI 경로",
        description="codex 실행 파일 절대경로 (비우면 자동 탐지)",
        subtype='FILE_PATH',
        default="",
    )
    gen_model: StringProperty(
        name="생성 모델",
        description="초기 코드 생성 모델 (비우면 CLI 기본). 예: Claude는 sonnet/opus, Codex는 gpt-5 계열",
        default="",
    )
    critique_model: StringProperty(
        name="비평 모델",
        description="이미지 비평 턴 전용 빠른 모델 — 생성 속도에 직결 (비우면 생성 모델과 동일, Claude 전용)",
        default="",
    )
    timeout: IntProperty(
        name="CLI 타임아웃(초)",
        description="에이전트 호출 1회당 최대 대기 시간",
        default=300, min=30, max=1800,
    )
    capture_count: IntProperty(
        name="캡처 앵글 수",
        description="비평 턴에 보여줄 컬러 캡처 장수 (실루엣 1장은 별도) — 적을수록 비평이 빠르다",
        default=2, min=1, max=6,
    )
    capture_resolution: IntProperty(
        name="캡처 해상도",
        default=512, min=256, max=1024,
    )
    asset_library_path: StringProperty(
        name="에셋 라이브러리 경로",
        description="Asset Browser 라이브러리 루트 (카탈로그 파일 위치)",
        subtype='DIR_PATH',
        default="",
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
    gen_model = ""
    critique_model = ""
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


def register():
    bpy.utils.register_class(LP3DPreferences)


def unregister():
    bpy.utils.unregister_class(LP3DPreferences)
