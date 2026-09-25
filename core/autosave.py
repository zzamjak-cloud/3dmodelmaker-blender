"""생성이 끝난 결과를 즉시 폴더 하나로 남긴다 — 저장을 잊어 결과를 잃지 않도록.

한 잡이 만든 것(참조 이미지·시트·AI 중간 이미지·셰이프 GLB·텍스처·그 결과만 담은 .blend)을 흩어 두지 않고
`~/Downloads/blender/<모드>/<이름>/` 한 폴더에 모은다. 현재 편집 중인 파일 경로는 바꾸지 않는다.
"""
import hashlib
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


def result_file(folder: str, label: str, src: str) -> str:
    """결과 폴더 안에서 재료 하나가 놓일 경로."""
    return os.path.join(folder, label + os.path.splitext(src)[1].lower())


IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp')


def is_loose_archive(path: str) -> bool:
    """보관 폴더 최상단에 애드온이 남긴 잡 재료인가 (클립보드 참조·멀티뷰/컨셉 시트).

    결과 폴더가 생기기 전에 먼저 만들어져 최상단에 떨어진 파일들이다 — 결과가 완성되면
    그 결과 폴더로 옮겨 한 그룹으로 묶는다. 사용자가 다른 곳에서 고른 원본은 옮기지 않는다."""
    from . import clipboard_image, multiview, sceneview
    if not path or not os.path.isfile(path):
        return False
    root = multiview.archive_dir()
    if not root:
        return False
    same_dir = (os.path.normcase(os.path.dirname(os.path.abspath(path)))
                == os.path.normcase(os.path.abspath(root)))
    return same_dir and os.path.basename(path).startswith(
        (clipboard_image.ARCHIVE_PREFIX, sceneview.ARCHIVE_PREFIX, multiview.ARCHIVE_PREFIX))


def _unique(path: str) -> str:
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        path = f"{base}_{n:03d}{ext}"
        n += 1
    return path


def _digest(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store(folder: str, files: dict = None, move: dict = None, loose: list = None,
          process_dirs: list = None) -> dict:
    """결과 폴더 한 곳(하위 폴더 없이 .blend 옆)에 재료를 모은다. {옮기기 전 경로: 새 경로}를 돌려준다.

    files: {이름(확장자 없이): 원본} — 복사한다.
    move: files와 같은 형식이되 옮긴다 — 보관 폴더 최상단에 따로 남지 않게.
    loose: 이름을 바꾸지 않고 옮길 파일 목록 (배경 에셋 시트 등).
    process_dirs: 이 폴더들(세션 작업 폴더)의 AI 이미지를 복사한다. 두 번째부터는 배경 에셋 잡이라
    `에셋NN_` 접두어를 붙인다.
    같은 작업 폴더 원본·보관본·붙여넣기 사본이 한 그림의 여러 사본이므로 **내용이 같은 파일은 한 번만** 둔다
    — 옮길 원본이 이미 있는 그림이면 원본만 지우고 있는 파일을 새 경로로 알려 준다."""
    moved = {}
    folder_norm = os.path.normcase(os.path.abspath(folder)) + os.sep

    def inside(path):
        # 앞선 저장에서 이미 이 폴더로 옮긴 재료 — 다시 옮기면 _001 사본이 생긴다
        return os.path.normcase(os.path.abspath(path)).startswith(folder_norm)

    known = {}   # 내용 해시 → 폴더 안 경로
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.lower().endswith(IMAGE_EXTS + ('.glb',)):
            try:
                known.setdefault(_digest(path), path)
            except OSError:
                pass

    def place(src, dest, relocate, exact=False):
        try:
            digest = _digest(src)
            if digest in known:
                if relocate:
                    os.remove(src)
                    moved[src] = known[digest]
                return
            target = dest if exact else _unique(dest)
            (shutil.move if relocate else shutil.copy)(src, target)
            known[digest] = target
            if relocate:
                moved[src] = target
        except OSError:
            log.exception("결과 재료 저장 실패: %s", src)

    for label, src in (files or {}).items():
        if src and os.path.isfile(src) and not inside(src):
            place(src, result_file(folder, label, src), relocate=False, exact=True)
    for label, src in (move or {}).items():
        if src and os.path.isfile(src) and not inside(src) and src not in moved:
            place(src, result_file(folder, label, src), relocate=True)
    for src in loose or []:
        if src and os.path.isfile(src) and not inside(src) and src not in moved:
            place(src, os.path.join(folder, os.path.basename(src)), relocate=True)
    for i, work in enumerate(d for d in (process_dirs or []) if d and os.path.isdir(d)):
        prefix = "" if i == 0 else f"에셋{i:02d}_"
        for root, _dirs, names in os.walk(work):
            for name in sorted(names):
                if not name.lower().endswith(IMAGE_EXTS):
                    continue
                rel = os.path.relpath(os.path.join(root, name), work).replace(os.sep, "_")
                place(os.path.join(root, name), os.path.join(folder, prefix + rel), relocate=False)
    return moved


def _window_override():
    import bpy
    window = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    return bpy.context.temp_override(window=window) if window is not None else None


def write_blend(folder: str, name: str, collections=None, hidden=None) -> str:
    """결과 .blend를 쓴다. 경로 또는 실패 시 빈 문자열.

    collections(컬렉션 이름 목록)를 주면 그 결과만 담은 임시 씬을 만들어 그 씬만 쓴다 — 큐에서 함께
    만든 다른 결과가 섞이지 않게. 없으면 현재 파일 전체를 사본으로 저장한다(`copy=True`: 편집 중인
    파일 경로는 그대로다). hidden 컬렉션은 함께 담되 뷰 레이어에서 제외한다(배경 키트 원본).
    씬에서 빈자리로 옮겨 둔 결과는 파일 안에서는 원점으로 되돌려 쓴다.
    실패는 로그만 남긴다 — 저장 실패가 생성 결과를 잃게 하면 안 된다."""
    import bpy
    from .jobs import PLACE_MARK
    blend = os.path.join(folder, (name or "model") + ".blend")
    colls = [bpy.data.collections.get(n) for n in (collections or [])]
    colls = [c for c in colls if c is not None]
    hide = [c for c in (bpy.data.collections.get(n) for n in (hidden or [])) if c is not None]
    try:
        if colls:
            temp = bpy.data.scenes.new(name or "model")
            shifted = []
            try:
                for coll in colls + [c for c in hide if c not in colls]:
                    temp.collection.children.link(coll)
                view_layer = temp.view_layers[0]
                view_layer.update()   # 새 씬의 레이어 트리는 갱신 전까지 비어 있다
                for layer in view_layer.layer_collection.children:
                    if layer.collection in hide:
                        layer.exclude = True
                # 장면에 딸린 월드·단위 설정은 원래 씬 것을 따른다
                source = bpy.context.scene
                temp.world = source.world
                temp.unit_settings.system = source.unit_settings.system
                temp.unit_settings.scale_length = source.unit_settings.scale_length
                for coll in colls:
                    offset = coll.get(PLACE_MARK)
                    if offset is None:
                        continue
                    for obj in coll.all_objects:
                        if obj.parent is None:
                            obj.location.x -= offset[0]
                            obj.location.y -= offset[1]
                            shifted.append((obj, offset[0], offset[1]))
                bpy.data.libraries.write(blend, {temp}, path_remap='ABSOLUTE', compress=True)
            finally:
                for obj, dx, dy in shifted:
                    obj.location.x += dx
                    obj.location.y += dy
                bpy.data.scenes.remove(temp)
        else:
            override = _window_override()
            if override is not None:
                # 타이머 콜백에서 부르므로 창 컨텍스트를 실어 준다 — UI 모드에서 poll() 이 실패하지 않게
                with override:
                    bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, compress=True)
            else:
                bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, compress=True)
    except Exception:   # 저장 실패가 생성 결과를 잃게 하면 안 된다 — 로그만 남긴다
        log.exception("결과 .blend 저장 실패")
        return ""
    return blend
