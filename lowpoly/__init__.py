# 에이전트 코드에 `lp`로 주입되는 로우폴리 헬퍼 라이브러리 공개 API
#
# 이 모듈의 __all__ 함수 시그니처와 독스트링은 시스템 프롬프트의 API 레퍼런스로
# 자동 추출된다 (core/prompts.py). 독스트링 첫 단락을 에이전트가 그대로 읽는다.
import bpy

from .primitives import box, cylinder, cone, sphere, plane, lathe, prism, tube, join
from .modeling import (bevel, mirror_x, array, scatter, taper, shade_flat,
                       bend, bulge, shear, stretch_at, jitter)
from .voxel import voxel, voxel_box, voxel_column
from .palette import set_color
from .cleanup import game_ready
from .scene import (terrain, room, instance, place_grid, place_along, place_scatter,
                    wall_run, fence_run, path_strip, ground_snap, kit)

__all__ = [
    "root", "box", "cylinder", "cone", "sphere", "plane", "lathe", "prism", "tube", "join",
    "bevel", "mirror_x", "array", "scatter", "taper", "bend", "bulge", "shear",
    "stretch_at", "jitter", "shade_flat",
    "set_color", "game_ready",
]

# 복셀 스타일 프롬프트에만 노출되는 격자 헬퍼 — 다른 스타일에서는 어휘를 늘리지 않는다
VOXEL_API = ["voxel", "voxel_box", "voxel_column"]

# 배경(SCENE) 모드 프롬프트에만 노출되는 씬 헬퍼 어휘 — __all__과 분리해 둔다
SCENE_API = [
    "terrain", "room", "instance", "place_grid", "place_along", "place_scatter",
    "wall_run", "fence_run", "path_strip", "ground_snap", "kit",
]

# 현재 생성 세션의 전용 컬렉션 이름 — executor가 실행 전에 설정
_session_collection_name = "LP3D_Model"


# kit()이 원본 에셋을 찾는 컬렉션 이름 — None이면 세션 컬렉션에서 찾는다
_kit_collection_name = None


def set_session(collection_name: str):
    global _session_collection_name
    _session_collection_name = collection_name


def set_kit_collection(name):
    """kit()이 조회할 에셋 키트 컬렉션 이름을 지정한다. None이면 세션 컬렉션을 쓴다."""
    global _kit_collection_name
    _kit_collection_name = name


def clear_kit_collection(name=None):
    """키트 조회 대상을 해제한다.

    name을 주면 현재 지정값이 그 이름일 때만 해제한다 — 배경 세션이 여럿이면
    먼저 끝난 쪽이 다른 세션이 막 지정해 둔 키트를 빼앗으면 안 된다."""
    global _kit_collection_name
    if name is None or _kit_collection_name == name:
        _kit_collection_name = None


def root() -> bpy.types.Collection:
    """현재 세션 전용 컬렉션을 반환한다. 모든 생성 오브젝트는 이 컬렉션에 속해야 한다.

    헬퍼 함수(box, cylinder 등)는 자동으로 이 컬렉션에 오브젝트를 넣으므로
    직접 호출할 일은 거의 없다."""
    coll = bpy.data.collections.get(_session_collection_name)
    if coll is None:
        coll = bpy.data.collections.new(_session_collection_name)
        bpy.context.scene.collection.children.link(coll)
    return coll


def link_to_root(obj: bpy.types.Object):
    """오브젝트를 세션 컬렉션에 링크한다 (다른 컬렉션에서는 제거)."""
    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)
    root().objects.link(obj)
