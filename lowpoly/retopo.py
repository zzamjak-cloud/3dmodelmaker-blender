# 이미지→3D 셰이프(GLB) 가져오기 — 셰이프 서버가 구운 PBR 결과를 씬에 올린다
#
# 순서: GLB 임포트 → 하나로 병합 → 바닥판 제거 → 키·방향·바닥 정규화 → 세션 컬렉션에 링크.
# 리메시·데시메이트·UV 언랩·텍스처 굽기는 모두 셰이프 서버(TRELLIS.2 공식 경로)가 끝내 준다.
# 전부 bmesh/data API로 돈다 — 타이머 콜백에서 호출된다.
import bmesh
import bpy
from mathutils import Vector

from .names import safe_id_name


def import_glb(path: str) -> list:
    """GLB를 가져와 새 메시 오브젝트 목록을 돌려준다 (씬 컬렉션 링크는 호출자가 정리)."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    for o in new:
        if o.type != 'MESH':
            for child in list(o.children):
                world = child.matrix_world.copy()
                child.parent = None
                child.matrix_world = world
            bpy.data.objects.remove(o, do_unlink=True)  # 빈 노드·카메라 등
    return meshes


def _face_islands(bm) -> list:
    """bmesh 면의 연결 요소(면 인덱스 리스트) 목록."""
    bm.faces.ensure_lookup_table()
    seen = set()
    islands = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, comp = [f], []
        while stack:
            cur = stack.pop()
            if cur.index in seen:
                continue
            seen.add(cur.index)
            comp.append(cur.index)
            for e in cur.edges:
                for lf in e.link_faces:
                    if lf.index not in seen:
                        stack.append(lf)
        islands.append(comp)
    return islands


GROUND_FLAT_RATIO = 0.06   # 가장 긴 변 대비 두께가 이 비율 미만이면 판
GROUND_WIDE_RATIO = 0.5    # 가로·세로가 전체 폭의 이 비율 이상이면 바닥


def remove_ground_slabs(obj) -> int:
    """넓고 얇은 판(생성 이미지의 바닥선·그림자가 복원된 슬랩)을 지운다. 지운 셸 수를 돌려준다.

    실측: 1.0 x 1.0 x 0.021 짜리 바닥판이 몸통 면수의 21%나 돼 파편 기준(5%)으로는 걸러지지 않았고,
    키 정규화의 기준 상자를 망가뜨렸다. 크기가 아니라 '납작하고 넓다'는 형태로 판정한다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    islands = _face_islands(bm)
    if len(islands) <= 1:
        bm.free()
        return 0
    verts = [v.co for v in bm.verts]
    span = [max(c[a] for c in verts) - min(c[a] for c in verts) for a in range(3)]
    width = max(max(span[0], span[1]), 1e-6)
    kill = []
    for comp in islands:
        points = [v.co for i in comp for v in bm.faces[i].verts]
        size = [max(p[a] for p in points) - min(p[a] for p in points) for a in range(3)]
        longest = max(max(size), 1e-6)
        if min(size) < GROUND_FLAT_RATIO * longest and max(size[0], size[1]) >= GROUND_WIDE_RATIO * width:
            kill.append(comp)
    if not kill or len(kill) == len(islands):
        bm.free()
        return 0
    drop = {i for comp in kill for i in comp}
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index in drop], context='FACES')
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(kill)


def normalize(obj, height: float, face_axis: str = '-Y'):
    """키를 height(m)에 맞추고 발바닥을 z=0, 중심을 X=Y=0에 둔다. 적용한 월드 변환 행렬을 돌려준다.

    glTF 임포트 결과는 원점 중심·1m 안팎 크기다. face_axis는 정면이 향하는 축으로,
    셰이프 서버 GLB는 glTF 규약(+Z 정면)이라 Blender에서 -Y로 들어와 기본값이 맞는다."""
    from mathutils import Matrix
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    zmin, zmax = min(p.z for p in pts), max(p.z for p in pts)
    scale = float(height) / max(zmax - zmin, 1e-6)
    cx = (min(p.x for p in pts) + max(p.x for p in pts)) / 2 * scale
    cy = (min(p.y for p in pts) + max(p.y for p in pts)) / 2 * scale
    matrix = Matrix.Translation((-cx, -cy, -zmin * scale)) @ Matrix.Scale(scale, 4)
    # 트랜스폼을 메시에 굽는다 — 이후 단계(베이크·익스포트)가 단위 변환을 전제한다
    apply_world(obj, matrix)
    return matrix


def apply_world(obj, matrix) -> None:
    """월드 변환 matrix 를 메시 좌표에 굽고 오브젝트 변환을 항등으로 되돌린다."""
    from mathutils import Matrix
    obj.data.transform(matrix @ obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    obj.data.update()


def import_textured(path: str, name: str, collection, height: float = 1.8) -> dict:
    """서버가 PBR 텍스처까지 구워 준 GLB 를 그대로 가져온다 — 재질을 지우지 않고 키·위치만 맞춘다.

    TRELLIS.2 공식 경로(o_voxel.postprocess.to_glb)가 이미 리메시·데시메이트·UV 언랩·PBR 굽기를 마쳤으므로
    애드온에서 다시 손대지 않는다."""
    meshes = import_glb(path)
    if not meshes:
        raise RuntimeError("GLB에 메시가 없다")
    obj = meshes[0]
    if len(meshes) > 1:
        with bpy.context.temp_override(object=obj, active_object=obj,
                                       selected_objects=meshes, selected_editable_objects=meshes):
            bpy.ops.object.join()
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    obj.name = safe_id_name(name)
    obj.data.name = obj.name
    slabs = remove_ground_slabs(obj)
    normalize(obj, height)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj.data.calc_loop_triangles()
    images = {n.image.name for m in obj.data.materials if m and m.use_nodes
              for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image}
    return {"obj": obj, "faces": len(obj.data.polygons), "tris": len(obj.data.loop_triangles),
            "materials": len([m for m in obj.data.materials if m]), "images": sorted(images),
            "ground_slabs": slabs}
