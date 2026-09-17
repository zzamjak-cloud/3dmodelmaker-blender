# 이미지→3D 셰이프(GLB) 가져오기와 리토폴로지 — 하이폴리 "찰흙" 메시를 게임용 메시로
#
# 순서: GLB 임포트 → 가장 큰 연결 덩어리만 남김(떠다니는 조각 제거) → 복셀 리메시로 표면 통일 →
# QuadriFlow 리토폴로지(실패하면 데시메이트) → 키·방향·바닥 정규화 → 세션 컬렉션에 링크.
# 전부 bmesh/data API 또는 컨텍스트 override로 돈다 — 타이머 콜백에서 호출된다.
import bmesh
import bpy
from mathutils import Vector

from .names import safe_id_name


def _override(obj):
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    kwargs = dict(object=obj, active_object=obj, selected_objects=[obj],
                  selected_editable_objects=[obj])
    if win is not None:
        kwargs["window"] = win
    return bpy.context.temp_override(**kwargs)


def import_glb(path: str) -> list:
    """GLB를 가져와 새 메시 오브젝트 목록을 돌려준다 (씬 컬렉션 링크는 호출자가 정리)."""
    before = set(bpy.data.objects)
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    for o in new:
        if o.type != 'MESH':
            bpy.data.objects.remove(o, do_unlink=True)  # 빈 노드·카메라 등
    return meshes


def keep_largest_island(obj, min_ratio: float = 0.0) -> int:
    """가장 큰 연결 덩어리만 남긴다. 제거한 덩어리 수를 돌려준다.

    이미지→3D 결과에는 몸에서 떨어진 작은 파편이 수십 개 붙어 나온다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    seen, islands = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, comp = [v], []
        while stack:
            cur = stack.pop()
            if cur.index in seen:
                continue
            seen.add(cur.index)
            comp.append(cur)
            for e in cur.link_edges:
                n = e.other_vert(cur)
                if n.index not in seen:
                    stack.append(n)
        islands.append(comp)
    if len(islands) <= 1:
        bm.free()
        return 0
    islands.sort(key=len, reverse=True)
    keep = set(v.index for v in islands[0])
    # 큰 부속(두 번째 덩어리가 본체의 일정 비율 이상 — 분리된 무기 등)은 남긴다
    for comp in islands[1:]:
        if min_ratio and len(comp) >= len(islands[0]) * min_ratio:
            keep.update(v.index for v in comp)
    removed = sum(1 for comp in islands[1:] if comp[0].index not in keep)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.index not in keep], context='VERTS')
    bm.to_mesh(obj.data)
    bm.free()
    return removed


def voxel_remesh(obj, voxel_size: float) -> None:
    """복셀 리메시 — 겹친 셸·자기교차를 하나의 닫힌 표면으로 녹인다."""
    obj.data.remesh_voxel_size = max(float(voxel_size), 0.002)
    obj.data.remesh_voxel_adaptivity = 0.0
    obj.data.use_remesh_fix_poles = True
    with _override(obj):
        bpy.ops.object.voxel_remesh()


def retopo(obj, target_faces: int) -> str:
    """QuadriFlow 리토폴로지, 실패하면 데시메이트. 쓴 방법 이름을 돌려준다."""
    before = len(obj.data.polygons)
    try:
        with _override(obj):
            bpy.ops.object.quadriflow_remesh(target_faces=int(target_faces),
                                             use_preserve_sharp=False,
                                             use_mesh_symmetry=False, seed=1)
    except RuntimeError:
        pass
    if len(obj.data.polygons) != before:
        return "quadriflow"
    # QuadriFlow는 셸이 여러 개거나 노멀이 어긋나면 조용히 아무것도 안 한다
    obj.data.calc_loop_triangles()
    tris = len(obj.data.loop_triangles)
    ratio = min(1.0, (int(target_faces) * 2.0) / max(tris, 1))
    mod = obj.modifiers.new("LP3D_Decimate", 'DECIMATE')
    mod.ratio = ratio
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return "decimate"


def normalize(obj, height: float, face_axis: str = '-Y') -> None:
    """키를 height(m)에 맞추고 발바닥을 z=0, 중심을 X=Y=0에 둔다.

    glTF 임포트 결과는 원점 중심·1m 안팎 크기다. face_axis는 정면이 향하는 축으로,
    Hunyuan3D 출력은 glTF +Z(정면)가 Blender -Y로 들어와 기본값이 맞는다."""
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    zmin, zmax = min(p.z for p in pts), max(p.z for p in pts)
    span = max(zmax - zmin, 1e-6)
    scale = float(height) / span
    obj.scale = (obj.scale.x * scale, obj.scale.y * scale, obj.scale.z * scale)
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    cx = (min(p.x for p in pts) + max(p.x for p in pts)) / 2
    cy = (min(p.y for p in pts) + max(p.y for p in pts)) / 2
    obj.location.x -= cx
    obj.location.y -= cy
    obj.location.z -= min(p.z for p in pts)
    # 트랜스폼을 메시에 굽는다 — 이후 단계(언랩·베이크·익스포트)가 단위 변환을 전제한다
    with _override(obj):
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def decimate(obj, target_tris: int) -> None:
    """데시메이트(collapse)로 목표 트라이 수까지 줄인다 — 실루엣·디테일을 가장 잘 지킨다.

    마칭큐브 출력은 매니폴드가 아니어서 QuadriFlow가 거부하고, 복셀 리메시를 거치면
    얼굴·털 같은 작은 디테일이 뭉개진다. 게임용 트라이 메시가 목표라면 데시메이트가 낫다."""
    obj.data.calc_loop_triangles()
    tris = len(obj.data.loop_triangles)
    if tris <= target_tris:
        return
    mod = obj.modifiers.new("LP3D_Decimate", 'DECIMATE')
    mod.ratio = float(target_tris) / float(tris)
    mod.use_collapse_triangulate = True
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def process_glb(path: str, name: str, collection, height: float = 1.8,
                target_faces: int = 12000, voxel_size: float = 0.012,
                method: str = 'DECIMATE') -> dict:
    """GLB 한 개를 게임용 메시 오브젝트 하나로 만들어 collection에 넣는다. 통계 dict 반환.

    method='DECIMATE'(기본): 조각 제거 → 데시메이트. 디테일 보존이 좋다 (트라이 메시).
    method='QUADRIFLOW': 조각 제거 → 복셀 리메시 → QuadriFlow 쿼드 리토폴로지. 리깅·변형용
    쿼드 흐름이 필요할 때 — 복셀 단계에서 작은 디테일이 뭉개진다."""
    meshes = import_glb(path)
    if not meshes:
        raise RuntimeError("GLB에 메시가 없다")
    obj = meshes[0]
    for extra in meshes[1:]:  # 여러 조각이면 하나로 합친다
        bpy.data.objects.remove(extra, do_unlink=True)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    obj.name = safe_id_name(name)
    obj.data.name = obj.name
    obj.data.materials.clear()
    raw_tris = len(obj.data.polygons)
    removed = keep_largest_island(obj)
    if str(method).upper() == 'QUADRIFLOW':
        voxel_remesh(obj, voxel_size)
        remeshed = len(obj.data.polygons)
        method = retopo(obj, target_faces)
    else:
        remeshed = len(obj.data.polygons)
        decimate(obj, int(target_faces) * 2)  # target_faces는 쿼드 기준 — 트라이는 2배
        method = "decimate"
    for p in obj.data.polygons:
        p.use_smooth = True
    normalize(obj, height)
    obj.data.calc_loop_triangles()
    return {"obj": obj, "raw_faces": raw_tris, "floaters_removed": removed,
            "remeshed_faces": remeshed, "method": method,
            "tris": len(obj.data.loop_triangles)}
