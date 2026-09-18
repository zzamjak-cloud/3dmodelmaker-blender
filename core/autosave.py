"""생성이 끝난 결과를 즉시 폴더 하나로 남긴다 — 저장을 잊어 결과를 잃지 않도록.

한 잡이 만든 것(원화·턴어라운드 시트·셰이프 GLB·텍스처·최종 .blend)을 흩어 두지 않고
`~/Downloads/blender/<모드>/<이름>/` 한 폴더에 모은다. 현재 편집 중인 파일 경로는 바꾸지 않는다(사본 저장).
"""
import logging
import os
import shutil

log = logging.getLogger(__name__)

MODE_DIRS = {'CHARACTER': 'character', 'SCENE': 'scene', 'OBJECT': 'object'}


def result_dir(system_mode: str = 'OBJECT') -> str:
    """제작 모드별 결과 폴더의 상위 경로. 만들 수 없으면 빈 문자열."""
    from . import multiview
    base = multiview.archive_dir()
    if not base:
        return ""
    path = os.path.join(base, MODE_DIRS.get(str(system_mode).upper(), 'object'))
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        log.exception("결과 저장 폴더 생성 실패")
        return ""
    return path


def group_dir(name: str, system_mode: str = 'OBJECT') -> str:
    """이번 결과를 담을 폴더를 만든다. 같은 이름이 있으면 `_001` 로 비켜간다(이전 결과를 덮지 않는다)."""
    parent = result_dir(system_mode)
    if not parent:
        return ""
    base = (name or "model").strip() or "model"
    path = os.path.join(parent, base)
    n = 1
    while os.path.exists(path):
        path = os.path.join(parent, f"{base}_{n:03d}")
        n += 1
    try:
        os.makedirs(path)
    except OSError:
        log.exception("결과 폴더 생성 실패")
        return ""
    return path


def save_result(name: str, system_mode: str = 'OBJECT', files: dict = None) -> str:
    """결과 폴더를 만들어 재료(files)를 복사하고 현재 파일을 .blend 사본으로 저장한다.

    files: {폴더 안에 쓸 이름(확장자 없이): 원본 경로}. 없는 경로는 조용히 건너뛴다.
    `copy=True` 로 저장하므로 Blender 가 편집 중인 파일 경로는 그대로다 — 사용자가 따로 저장하던
    파일을 자동 저장이 가로채지 않는다. 폴더 경로를 돌려주고, 실패하면 빈 문자열."""
    import bpy
    folder = group_dir(name, system_mode)
    if not folder:
        return ""
    for label, src in (files or {}).items():
        if not src or not os.path.isfile(src):
            continue
        try:
            shutil.copy(src, os.path.join(folder, label + os.path.splitext(src)[1].lower()))
        except OSError:
            log.exception("결과 재료 복사 실패: %s", src)
    blend = os.path.join(folder, (name or "model") + ".blend")
    try:
        # 타이머 콜백에서 부르므로 창 컨텍스트를 실어 준다 — UI 모드에서 poll() 이 실패하지 않게
        window = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
        if window is not None:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, compress=True)
        else:
            bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, compress=True)
    except Exception:   # 저장 실패가 생성 결과를 잃게 하면 안 된다 — 로그만 남긴다
        log.exception("결과 .blend 저장 실패")
        return folder if os.listdir(folder) else ""
    return folder
