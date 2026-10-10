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


HOLE_MAX_PERIMETER = 0.3   # 키 대비 열린 루프 둘레 상한 — 이보다 큰 구멍은 메우지 않는다


def fill_small_holes(obj, max_ratio: float = HOLE_MAX_PERIMETER) -> int:
    """용접 뒤에도 남은 열린 루프를 메운다. 메운 루프 수를 돌려준다.

    서버(v0.42.0~)는 속을 채운 닫힌 한 겹을 보내므로 열린 루프는 전부 결함이다. 서버의 cumesh fill_holes 는
    둘레 제한 안이어도 가지가 있는 복잡한 루프를 남긴다(실측 2026-10-10, 소녀 피규어: 부츠 옆면 둘레 0.16
    짜리 73엣지 루프 → 바닥에서 올려다보면 구멍). bmesh.ops.holes_fill 은 같은 메시에서 단순 루프를 남기고 비매니폴드
    엣지를 만들어 쓰지 않는다 — 루프마다 면을 직접 만들고, 코너 UV 는 옆 면에서 옮겨 온다.
    용접 전에 돌리면 UV 심이 전부 경계라 메우면 안 되는 곳까지 막는다 — weld_seams 뒤에 부른다."""
    zs = [v.co.z for v in obj.data.vertices]
    limit = (max(zs) - min(zs)) * max_ratio if zs else 0.0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    # 두 루프가 정점 하나에서 맞닿은 8자 루프(실측: 경계 차수 4·6 정점 43개)는 따라가는 길이 갈린다 —
    # 그런 정점을 면 부채꼴마다 쪼개 단순 루프로 만든 뒤 메운다.
    for vert in [v for v in bm.verts if sum(1 for e in v.link_edges if e.is_boundary) > 2]:
        bmesh.utils.vert_separate(vert, [e for e in vert.link_edges if e.is_boundary])
    uv_layer = bm.loops.layers.uv.active
    # 서버 메시는 면 방향이 군데군데 섞여 있어(실측: 남은 루프 7개 모두 옆 면 방향이 한 곳 이상 반대) 방향을 따라
    # 걷지 않는다. 경계 차수 2 인 정점끼리 방향 없이 고리를 돌고, 면 방향은 옆 면들의 다수결로 정한다.
    links = {}
    for edge in bm.edges:
        if edge.is_boundary:
            for vert in edge.verts:
                links.setdefault(vert, []).append(edge)
    filled = 0
    seen = set()
    for first in list(links):
        if first in seen or len(links[first]) != 2:
            continue
        ring, edges, vert, edge = [first], [], first, links[first][0]
        seen.add(first)
        closed = False
        while True:
            edges.append(edge)
            vert = edge.other_vert(vert)
            if vert is first:
                closed = True
                break
            if vert in seen or len(links.get(vert, ())) != 2:
                break                              # 가지가 남은 경계는 건드리지 않는다
            seen.add(vert)
            ring.append(vert)
            edge = links[vert][0] if links[vert][1] is edge else links[vert][1]
        if not closed or len(ring) < 3:
            continue
        perimeter = sum(e.calc_length() for e in edges)
        if perimeter > limit:
            continue
        # 옆 면이 a→b 로 돌면 메울 면은 b→a — ring 순서(ring[i]→ring[i+1])와 같은 방향인 옆 면이 많으면 뒤집는다
        index = {v: i for i, v in enumerate(ring)}
        same = 0
        for e in edges:
            corner = e.link_loops[0]
            a, b = index[corner.vert], index[corner.link_loop_next.vert]
            same += 1 if (b - a) % len(ring) == 1 else -1
        if same > 0:
            ring.reverse()
        corners = {}
        for e in edges:
            for corner in (e.link_loops[0], e.link_loops[0].link_loop_next):
                corners.setdefault(corner.vert, corner)
        try:
            face = bm.faces.new(ring)
        except ValueError:                         # 같은 정점의 면이 이미 있다
            continue
        face.material_index = edges[0].link_faces[0].material_index
        face.smooth = True
        if uv_layer is not None:
            for corner in face.loops:
                corner[uv_layer].uv = corners[corner.vert][uv_layer].uv
        if len(ring) > 3:
            # triangulate 의 대각선이 고리 건너편과 이미 이어진 엣지와 겹치면 면 3장짜리 엣지가 된다(실측 20개) —
            # 가운데 정점 부채꼴은 고리 정점끼리 새 엣지를 만들지 않는다. 가운데 UV 는 코너 평균으로 보간된다
            bmesh.ops.poke(bm, faces=[face])
        filled += 1
    if filled:
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return filled


def import_textured(path: str, name: str, collection, height: float = 1.8) -> dict:
    """서버가 PBR 텍스처까지 구워 준 GLB 를 그대로 가져온다 — 재질을 지우지 않고 키·위치만 맞춘다.

    TRELLIS.2 공식 경로(o_voxel.postprocess.to_glb)가 이미 리메시·데시메이트·UV 언랩·PBR 굽기를 마쳤으므로
    애드온에서 다시 손대지 않는다.

    서버(v0.42.0~)는 속을 채운 한 겹을 보내지만, 그 전 서버의 메시는 바깥 껍질 바로 뒤(4mm 안팎)에 안쪽 껍질이 붙은
    속 빈 이중 껍질이다 — 어느 쪽이든 **안쪽 껍질을 지우지 않는다.** 면마다 광선으로 가리던 예전 컬링은 옷과 몸 사이 좁은 틈에서 보이는 면까지 지워 구멍을 냈고, 그 구멍을 메운
    캡이 안쪽 껍질에 붙어 표면을 뚫고 UV 없는 흰 면으로 드러났다(실측 2026-09-28, 좀비: 열린 엣지 1,108 · 메운 면
    7,363 · 자기 교차 5,154쌍). 안쪽 껍질은 바깥에서 보이지 않고, 리토폴로지는 속 채우기(solid_fill)로 두 껍질을
    한 덩어리로 녹이므로 남겨 두는 편이 낫다."""
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
    holes = fill_small_holes(obj)
    slabs = remove_ground_slabs(obj)
    normalize(obj, height)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj.data.calc_loop_triangles()
    images = {n.image.name for m in obj.data.materials if m and m.use_nodes
              for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image}
    return {"obj": obj, "faces": len(obj.data.polygons), "tris": len(obj.data.loop_triangles),
            "materials": len([m for m in obj.data.materials if m]), "images": sorted(images),
            "ground_slabs": slabs, "welded": welded, "holes": holes}
