# 턴별 스냅샷: 각 턴의 결과를 옆으로 복제해 남겨 단계별 변화를 눈으로 비교한다.
#
# 생성 루프는 매 턴 세션 컬렉션을 비우고 다시 만들기 때문에 중간 단계가 사라진다.
# 여기서 턴이 끝날 때마다 결과를 별도 컬렉션으로 복제하고 X축으로 밀어둔다.
# 최종 결과는 원점(x=0)에 그대로 남으므로 익스포트·에셋 등록 경로는 영향받지 않는다.
#
# bpy는 함수 안에서 임포트한다 (배치 계산은 Blender 없이 단위 테스트할 수 있도록).
import logging

log = logging.getLogger(__name__)

SUFFIX = "_turn"      # 스냅샷 컬렉션 이름 접미사
_GAP_RATIO = 0.35     # 모델 폭 대비 간격 비율


def collection_name(base: str, turn: int) -> str:
    return f"{base}{SUFFIX}{turn}"


def _model_width(objs) -> float:
    xs = []
    for obj in objs:
        mw = obj.matrix_world
        xs.extend((mw @ v.co).x for v in obj.data.vertices)
    return (max(xs) - min(xs)) if xs else 1.0


def slot_offset(turn: int, total_turns: int, width: float) -> float:
    """턴 번호에 대응하는 X 오프셋.

    최종본이 원점에 오고 이전 단계들이 왼쪽에 시간순으로 놓이도록,
    턴이 이를수록 더 왼쪽에 둔다 (1턴이 가장 왼쪽)."""
    spacing = width * (1.0 + _GAP_RATIO)
    return -spacing * max(total_turns - turn, 0)


def capture_turn(base_collection: str, turn: int, total_turns: int,
                 dy: float = 0.0) -> str:
    """현재 세션 컬렉션의 상태를 스냅샷 컬렉션으로 복제한다. 컬렉션 이름을 반환.

    dy는 잡의 레인 Y 오프셋(core/lanes.lane_dy)이다 — 안 넘기면 여러 잡의 스냅샷이
    모두 Y=0에 겹치고 각자의 최종본과도 떨어진다. 계산을 여기서 하지 않고 받는 이유는
    core/lanes를 이 모듈에 끌어들이지 않고 호출자가 최종본과 같은 값을 쓰게 하기 위함."""
    import bpy

    src = bpy.data.collections.get(base_collection)
    if not src:
        return ""
    objs = [o for o in src.objects if o.type == 'MESH' and len(o.data.vertices)]
    if not objs:
        return ""
    name = collection_name(base_collection, turn)
    old = bpy.data.collections.get(name)
    if old:  # 같은 턴을 다시 실행한 경우(오류 재시도 등) 이전 스냅샷을 대체
        remove_collection(name)
    dst = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(dst)
    bpy.context.view_layer.update()
    dx = slot_offset(turn, total_turns, _model_width(objs))
    for obj in objs:
        dup = obj.copy()
        dup.data = obj.data.copy()   # 메시도 복제 — 다음 턴의 삭제·수정에 영향받지 않도록
        dup.name = f"{obj.name}{SUFFIX}{turn}"
        dup.location.x += dx
        dup.location.y += dy
        dst.objects.link(dup)
    return name


def remove_collection(name: str):
    import bpy

    coll = bpy.data.collections.get(name)
    if not coll:
        return
    for obj in list(coll.objects):
        mesh = obj.data if obj.type == 'MESH' else None
        bpy.data.objects.remove(obj)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.data.collections.remove(coll)


def clear_all(base_collection: str) -> int:
    """해당 세션의 스냅샷 컬렉션을 모두 제거하고 개수를 반환한다."""
    import bpy

    prefix = base_collection + SUFFIX
    names = [c.name for c in bpy.data.collections if c.name.startswith(prefix)]
    for name in names:
        remove_collection(name)
    return len(names)


def count(base_collection: str) -> int:
    import bpy

    prefix = base_collection + SUFFIX
    return sum(1 for c in bpy.data.collections if c.name.startswith(prefix))
