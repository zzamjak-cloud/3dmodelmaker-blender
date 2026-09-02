# 패널에 이미지 썸네일을 그리기 위한 프리뷰 캐시
#
# Blender의 layout.template_icon()은 icon_id만 받으므로, 파일 경로를
# bpy.utils.previews로 로드해 id를 얻어야 한다. draw()는 매 리드로우마다
# 불리므로 경로+수정시각으로 캐시해 디스크 접근과 재로드를 피한다.
import logging
import os

import bpy.utils.previews

log = logging.getLogger(__name__)

_pcoll = None
_MAX_ENTRIES = 24  # 세션을 오래 쓰면 계속 쌓이므로 상한을 둔다


def _key(path: str, mtime: int) -> str:
    # 경로를 그대로 쓰면 프리뷰 이름 제한에 걸리므로 해시로 줄인다
    return f"lp3d_{abs(hash(path)):x}_{mtime}"


def icon_id(path: str) -> int:
    """이미지 파일의 프리뷰 아이콘 id. 없거나 실패하면 0 (아이콘 없음)."""
    if _pcoll is None or not path or not os.path.isfile(path):
        return 0
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return 0
    name = _key(path, mtime)
    preview = _pcoll.get(name)
    if preview is None:
        if len(_pcoll) >= _MAX_ENTRIES:
            _pcoll.clear()
        try:
            preview = _pcoll.load(name, path, 'IMAGE')
        except Exception:
            log.exception("프리뷰 로드 실패: %s", path)
            return 0
    return preview.icon_id


def register():
    global _pcoll
    _pcoll = bpy.utils.previews.new()


def unregister():
    global _pcoll
    if _pcoll is not None:
        bpy.utils.previews.remove(_pcoll)
        _pcoll = None
