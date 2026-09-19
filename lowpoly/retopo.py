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


def weld_seams(obj, dist: float = 1e-5) -> int:
    """같은 자리에 겹쳐 있는 정점을 합친다. 합쳐서 줄어든 정점 수를 돌려준다.

    서버의 UV 언랩(xatlas)은 UV 섬 경계마다 정점을 쪼개고 GLB 는 정점당 UV 하나만 담으므로,
    가져온 메시는 심을 따라 조각조각 끊겨 있다(실측: 27,530 → 용접 후 14,441, 48%가 중복).
    UV 는 루프(면 코너)마다 저장되니 용접해도 텍스처는 그대로고, 편집·웨이트·법선만 이어진다."""
    before = len(obj.data.vertices)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=dist)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return before - len(obj.data.vertices)


INTERIOR_RAYS = 32       # 면마다 쏘는 탈출 광선 수 — 적으면 좁은 틈의 보이는 면까지 지워 반대편 뒷면이 드러난다
INTERIOR_EPS = 1e-4      # 자기 면에 다시 맞지 않도록 띄우는 거리


def remove_interior_faces(obj, rays: int = INTERIOR_RAYS) -> int:
    """바깥에서 어느 방향으로도 보이지 않는 면을 지운다. 지운 면 수를 돌려준다.

    셰이프 서버의 듀얼 컨투어링 리메시(공식 설정)는 표면 둘레 ±1복셀 띠를 만들어 **바깥 껍질과 안쪽
    껍질이 함께** 나온다. 안쪽 껍질은 절대 보이지 않으면서 면수를 두 배로 먹고 편집을 방해한다.
    면 중심에서 노멀 반구 방향으로 광선을 쏴 하나라도 밖으로 빠져나가면 보이는 면으로 본다 —
    입 안·눈구멍처럼 트인 곳은 그 틈으로 광선이 빠져나가므로 남는다. 앞으로는 못 나가고 뒤로만
    나가는 면은 안쪽 껍질이 틈 사이로 드러난 경우라, 지우는 대신 뒤집어 앞면이 보이게 한다."""
    import math
    import random
    import mathutils
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = mathutils.bvhtree.BVHTree.FromObject(obj, depsgraph)
    mesh = obj.data
    span = max(max(v.co[i] for v in mesh.vertices) - min(v.co[i] for v in mesh.vertices) for i in range(3))
    reach = span * 2.0
    rng = random.Random(7)
    # 반구 표본 — 노멀을 축으로 한 고정 각도 집합(재현 가능하게 시드 고정)
    samples = [Vector((math.cos(a) * math.sin(t), math.sin(a) * math.sin(t), math.cos(t)))
               for a, t in ((rng.uniform(0, 2 * math.pi), rng.uniform(0, math.pi / 2.2))
                            for _ in range(max(rays - 1, 1)))]
    def escapes(center, axis):
        for direction in [axis] + [_hemisphere(axis, s) for s in samples]:
            if tree.ray_cast(center + axis * INTERIOR_EPS, direction, reach)[0] is None:
                return True
        return False

    hidden, backwards = [], []
    for face in mesh.polygons:
        normal = face.normal
        if normal.length_squared < 1e-12:
            continue
        if escapes(face.center, normal):
            continue                       # 앞면이 바깥에서 보인다 — 그대로 둔다
        if escapes(face.center, -normal):
            backwards.append(face.index)   # 뒤에서만 보인다 — 안쪽 껍질이 틈으로 드러난 면
        else:
            hidden.append(face.index)      # 어느 쪽으로도 못 나간다 — 완전히 파묻힌 면
    if not hidden and not backwards:
        return 0
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    if backwards:
        bmesh.ops.reverse_faces(bm, faces=[bm.faces[i] for i in backwards])
    if hidden:
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in hidden], context='FACES')
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return len(hidden)


def _hemisphere(normal, sample):
    """표본 벡터를 노멀이 +Z 인 좌표계로 돌린다."""
    axis = Vector((0.0, 0.0, 1.0))
    if abs(normal.z) > 0.999:
        return sample if normal.z > 0 else Vector((sample.x, sample.y, -sample.z))
    rotation = axis.rotation_difference(normal)
    return rotation @ sample


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
    welded = weld_seams(obj)
    inner = remove_interior_faces(obj)
    slabs = remove_ground_slabs(obj)
    normalize(obj, height)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj.data.calc_loop_triangles()
    images = {n.image.name for m in obj.data.materials if m and m.use_nodes
              for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image}
    return {"obj": obj, "faces": len(obj.data.polygons), "tris": len(obj.data.loop_triangles),
            "materials": len([m for m in obj.data.materials if m]), "images": sorted(images),
            "ground_slabs": slabs, "welded": welded, "interior_faces": inner}
