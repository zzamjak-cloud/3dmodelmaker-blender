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


INNER_SHELL_MAX_RATIO = 0.01   # 본체 면수 대비 이 비율을 넘으면 '부스러기'가 아니라 부품이다


def remove_inner_shells(obj) -> int:
    """다른 셸 안에 완전히 갇힌 **닫힌** 조각을 지운다. 지운 조각 수를 돌려준다.

    등위면 추출은 몸 안쪽에 보이지 않는 작은 껍질을 남길 때가 있다. 지우는 조건은 셋 다 만족할 때뿐이다:
    ① 경계가 없는 닫힌 조각 — 천·망토처럼 열린 시트는 몸 안쪽을 지나도 보이는 부품이다(실측 2026-09-19:
    허리에서 내려온 천 조각이 중심점만으로 판정해 삭제돼 구멍이 났다), ② 본체의 1% 미만 크기,
    ③ 여러 지점이 모두 본체 안쪽. 입 안·눈구멍처럼 의미 있는 안쪽 면은 본체와 이어져 있어 같은 조각이다."""
    import mathutils
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    islands = _face_islands(bm)
    if len(islands) < 2:
        bm.free()
        return 0
    islands.sort(key=len, reverse=True)
    host = islands[0]
    verts, faces, index = [], [], {}
    for fi in host:
        face = bm.faces[fi]
        row = []
        for v in face.verts:
            if v.index not in index:
                index[v.index] = len(verts)
                verts.append(v.co.copy())
            row.append(index[v.index])
        faces.append(row)
    tree = mathutils.bvhtree.BVHTree.FromPolygons(verts, faces)
    lo = Vector([min(v[i] for v in verts) for i in range(3)])
    hi = Vector([max(v[i] for v in verts) for i in range(3)])
    limit = max(len(host) * INNER_SHELL_MAX_RATIO, 1)
    doomed = []
    for comp in islands[1:]:
        if len(comp) > limit:
            continue
        faces = [bm.faces[fi] for fi in comp]
        if any(len(e.link_faces) == 1 for f in faces for e in f.edges):
            continue                      # 열린 시트 — 부피도 안팎도 정의되지 않는다
        pts = [v.co for f in faces for v in f.verts]
        clo = Vector([min(p[i] for p in pts) for i in range(3)])
        chi = Vector([max(p[i] for p in pts) for i in range(3)])
        if any(clo[i] < lo[i] or chi[i] > hi[i] for i in range(3)):
            continue
        probes = [(clo + chi) / 2, clo * 0.75 + chi * 0.25, clo * 0.25 + chi * 0.75]
        if all(_inside(tree, p) for p in probes):
            doomed.append(comp)
    if not doomed:
        bm.free()
        return 0
    bmesh.ops.delete(bm, geom=[bm.faces[fi] for comp in doomed for fi in comp], context='FACES')
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(doomed)


def _inside(tree, point) -> bool:
    """광선 교차 횟수가 홀수면 안쪽 — 축과 나란하지 않은 방향으로 쏴 모서리 통과를 피한다."""
    direction = Vector((0.5773, 0.5774, 0.5775))
    cur = Vector(point)
    hits = 0
    for _ in range(64):
        location = tree.ray_cast(cur, direction)[0]
        if location is None:
            break
        hits += 1
        cur = location + direction * 1e-5
    return hits % 2 == 1


def flip_inverted_shells(obj) -> int:
    """부호 있는 부피가 음수인 **닫힌** 조각을 뒤집는다. 뒤집은 조각 수를 돌려준다.

    서버의 unify_face_orientations 는 한 조각 안의 방향을 맞출 뿐 바깥쪽인지까지는 보장하지 않는다.
    다만 부호 있는 부피는 닫힌 표면에서만 뜻이 있다 — 열린 시트(천·망토)에 적용하면 멀쩡한 면을
    뒤집는다(실측 2026-09-19: 하체 천이 뒤집혀 나왔다)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    flipped = []
    for comp in _face_islands(bm):
        faces = [bm.faces[fi] for fi in comp]
        if any(len(e.link_faces) == 1 for f in faces for e in f.edges):
            continue
        volume = 0.0
        for f in faces:
            verts = f.verts
            for k in range(1, len(verts) - 1):
                volume += verts[0].co.dot(verts[k].co.cross(verts[k + 1].co)) / 6.0
        if volume < 0:
            flipped.append(comp)
    if not flipped:
        bm.free()
        return 0
    bmesh.ops.reverse_faces(bm, faces=[bm.faces[fi] for comp in flipped for fi in comp])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(flipped)


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
    inner = remove_inner_shells(obj)
    flipped = flip_inverted_shells(obj)
    slabs = remove_ground_slabs(obj)
    normalize(obj, height)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj.data.calc_loop_triangles()
    images = {n.image.name for m in obj.data.materials if m and m.use_nodes
              for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image}
    return {"obj": obj, "faces": len(obj.data.polygons), "tris": len(obj.data.loop_triangles),
            "materials": len([m for m in obj.data.materials if m]), "images": sorted(images),
            "ground_slabs": slabs, "welded": welded, "inner_shells": inner,
            "flipped_shells": flipped}
