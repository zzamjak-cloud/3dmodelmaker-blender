# 이전 버전에서 생성한 턴 스냅샷의 정리만 지원한다.

SUFFIX = "_turn"


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
