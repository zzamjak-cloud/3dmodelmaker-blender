# 설정 영속화: 애드온 업데이트·스키마 변경·새 .blend 파일에서도 사용자 설정 유지
#
# AddonPreferences는 프로퍼티 타입이 바뀌면(예: String→Enum) 저장값이 초기화되고,
# 씬 프로퍼티(익스포트 폴더)는 .blend 파일에 종속된다.
# 그래서 사용자 설정을 Blender config 폴더의 JSON에 별도 저장하고
# 등록/파일 열기 시점에 복원한다.
import json
import os

import bpy

_PREF_KEYS = ("codex_path", "timeout", "ai_concurrency",
              "use_multiview", "use_library",
              "asset_library_path", "texture_resolution", "texture_per_view",
              "image_backend", "openrouter_api_key", "image_model", "image_quality",
              "character_compare_turns", "use_shapegen", "shapegen_url", "shapegen_token",
              "shapegen_faces",
              "character_height", "shapegen_multiview", "shapegen_use_ref", "shapegen_texture_size",
              "scene_tri_budget", "scene_max_assets", "scene_timeout_scale")
# 제거된 모델·턴 설정은 복원하지 않아 구버전 파일도 현재 생성 정책을 따른다.
_SCENE_KEYS = ("export_dir",)

_suspended = False  # 복원 중 update 콜백의 재저장 방지


def _path() -> str:
    return os.path.join(bpy.utils.user_resource('CONFIG'), "lp3d_settings.json")


def _load() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(data: dict):
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass  # 저장 실패는 치명적이지 않음


def on_prefs_changed(prefs):
    """환경설정 update 콜백 — 변경 즉시 JSON에 저장."""
    if _suspended:
        return
    data = _load()
    data["prefs"] = {k: getattr(prefs, k) for k in _PREF_KEYS}
    _write(data)


def on_scene_changed(props):
    """익스포트 폴더 설정 update 콜백."""
    if _suspended:
        return
    data = _load()
    data["scene"] = {k: getattr(props, k) for k in _SCENE_KEYS}
    _write(data)


def apply_prefs(prefs):
    """저장된 환경설정을 복원한다. 스키마가 바뀐 항목은 조용히 건너뛴다."""
    global _suspended
    stored = _load().get("prefs", {})
    _suspended = True
    try:
        for key, value in stored.items():
            if key in _PREF_KEYS:
                try:
                    setattr(prefs, key, value)
                except (TypeError, ValueError):
                    pass  # 타입/Enum 항목 변경 시 해당 키만 무시
    finally:
        _suspended = False


def apply_scene(props):
    """저장된 씬 설정을 복원한다 — 이 파일에서 사용자가 바꾼 값(기본값 아님)은 존중."""
    global _suspended
    stored = _load().get("scene", {})
    _suspended = True
    try:
        for key, value in stored.items():
            if key not in _SCENE_KEYS:
                continue
            try:
                rna = props.bl_rna.properties[key]
                if getattr(props, key) != rna.default:
                    continue
                setattr(props, key, value)
            except (KeyError, TypeError, ValueError):
                pass
    finally:
        _suspended = False
