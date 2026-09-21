# 이미지→3D 셰이프(GLB) 가져오기 — 셰이프 서버가 구운 PBR 결과를 씬에 올린다
#
# 순서: GLB 임포트 → 하나로 병합 → 바닥판 제거 → 키·방향·바닥 정규화 → 세션 컬렉션에 링크.
# 리메시·데시메이트·UV 언랩·텍스처 굽기는 모두 셰이프 서버(TRELLIS.2 공식 경로)가 끝내 준다.
# 전부 bmesh/data API로 돈다 — 타이머 콜백에서 호출된다.
import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

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


INTERIOR_RAYS = 32       # 면마다 쏘는 탈출 광선 수
INTERIOR_EPS = 1e-4      # 자기 면에 다시 맞지 않도록 띄우는 거리
RESTORE_PASSES = 4       # 좁은 틈에서 잘못 지운 면을 되살리는 반복 횟수
RESTORE_DOT = 0.5        # 이웃과 노멀이 이만큼 맞아야 같은 껍질로 보고 되살린다


def remove_interior_faces(obj, rays: int = INTERIOR_RAYS) -> int:
    """바깥 껍질만 남기고 안쪽 껍질을 지운다. 지운 면 수를 돌려준다.

    셰이프 서버의 듀얼 컨투어링 리메시(공식 설정)는 표면 둘레 ±1복셀 띠를 만들어 **바깥 껍질과 안쪽
    껍질이 함께** 나온다. 안쪽 껍질은 보이지 않으면서 면수를 두 배로 먹고 편집을 방해한다.

    1차로 면마다 노멀 반구에 광선을 쏴 하나라도 밖으로 나가면 남긴다. 다만 다리 사이·옷과 몸 경계처럼
    좁은 틈에서는 탈출 각도를 못 찾아 보이는 면까지 지워 구멍이 뚫린다(실측 2026-09-20: 광선 128개로도
    열린 엣지 1,302개). 그래서 2차로, 지운 면 중 **이웃 대부분이 살아남았고 노멀 방향도 같은** 면을
    되살린다 — 안쪽 껍질은 접히는 가장자리에서만 바깥 껍질과 만나고 거기서는 노멀이 반대라 되살아나지
    않는다."""
    import math
    import random
    import mathutils
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = mathutils.bvhtree.BVHTree.FromObject(obj, depsgraph)
    mesh = obj.data
    span = max(max(v.co[i] for v in mesh.vertices) - min(v.co[i] for v in mesh.vertices) for i in range(3))
    reach = span * 2.0
    rng = random.Random(7)
    samples = [Vector((math.cos(a) * math.sin(p), math.sin(a) * math.sin(p), math.cos(p)))
               for a, p in ((rng.uniform(0, 2 * math.pi), rng.uniform(0, math.pi / 2.2))
                            for _ in range(max(rays - 1, 0)))]

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    visible = [False] * len(bm.faces)
    for face in bm.faces:
        normal = face.normal
        if normal.length_squared < 1e-12:
            visible[face.index] = True
            continue
        origin = face.calc_center_median() + normal * INTERIOR_EPS
        for direction in [normal] + [_hemisphere(normal, s) for s in samples]:
            if tree.ray_cast(origin, direction, reach)[0] is None:
                visible[face.index] = True
                break
    for _ in range(RESTORE_PASSES):
        revived = []
        for face in bm.faces:
            if visible[face.index]:
                continue
            neighbours = [o for e in face.edges for o in e.link_faces if o is not face]
            if not neighbours:
                continue
            agree = sum(1 for o in neighbours
                        if visible[o.index] and face.normal.dot(o.normal) > RESTORE_DOT)
            if agree * 2 >= len(neighbours):
                revived.append(face.index)
        if not revived:
            break
        for index in revived:
            visible[index] = True
    hidden = [f for f in bm.faces if not visible[f.index]]
    if not hidden:
        bm.free()
        return 0
    bmesh.ops.delete(bm, geom=hidden, context='FACES')
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return len(hidden)


HOLE_MAX_RATIO = 0.15     # 경계 루프의 폭이 모델 크기의 이 비율 이하면 '구멍'으로 보고 메운다
CRACK_AREA_RATIO = 0.05   # 메운 면적이 폭² 의 이 비율 미만이면 폭 0 의 '틈' — 크기와 무관하게 메운다
HOLE_MAX_EDGES = 400      # 이보다 긴 루프는 손대지 않는다
HOLE_SUBDIVIDE = 2        # 메운 면을 poke 로 쪼개는 횟수 — 새 정점을 원래 표면에 붙여 굴곡을 되살린다
CAVITY_AREA_RATIO = 0.8   # 메운 면적이 폭² 의 이 배수를 넘으면 표면의 구멍이 아니라 공동(자켓 안쪽) 입구다
CAVITY_LIFT_RATIO = 0.2   # 캡 면 중심이 컬링 전 표면에서 폭의 이 비율보다 떠 있으면 공동 입구다
CAVITY_REACH_RATIO = 0.75  # 공동 입구 둘레에서 이 비율 x 폭 안의 지운 면을 되살린다
CAVITY_BACKING_RATIO = 2.5  # 뒤쪽 이 배수 x 껍질 두께 안에 표면이 있으면 안쪽 껍질로 보고 되살리지 않는다


def fill_small_holes(obj, guide: BVHTree, size: float, pristine=None) -> dict:
    """컬링이 남긴 작은 구멍과 틈을 메우고, 새 정점을 컬링 전 표면(guide)에 붙인다.

    컬링은 겨드랑이·눈구멍·귀 뒤·입속처럼 좁은 공간에서 보이는 면까지 지운다(실측 2026-09-21: 경계 루프
    193개, 열린 엣지 2,177). 되살리기 패스로는 못 잡는다 — 통째로 지워진 패치는 '보이는 이웃 다수' 조건을
    만족하는 면이 하나도 없기 때문이다. 그래서 루프 단위로 메운다.

    소매·밑단·깃처럼 옷의 진짜 열린 테두리(폭이 모델의 15% 이상, 메운 면적도 큼)는 건드리지 않는다.
    메운 면은 평평하므로 poke 로 쪼개고 새 정점마다 guide 의 최근접점으로 옮긴다 — guide 에는 지운 면이
    그대로 있어 원래 굴곡이 돌아온다(안쪽 껍질에 붙어도 껍질 두께만큼만 어긋난다).

    **공동 입구**(자켓 앞섶처럼 옷과 몸 사이 빈 공간으로 뚫린 구멍)는 캡으로 막으면 안 된다 — 캡이 옷 안쪽 벽과
    가슴 사이를 가로지르는 거대한 면이 되고, 정점을 표면에 붙이면 어느 쪽에 붙을지 제멋대로라 찢어진다
    (실측 2026-09-21, LP3D_Model_015). 캡 면적이 폭² 을 넘거나 캡이 표면에서 많이 떠 있으면 공동으로 보고,
    대신 pristine(컬링 전 메시)에서 그 둘레의 지운 면 — 옷 안쪽 벽과 그 아래 피부 — 을 되살린다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    original = len(bm.verts)           # 새 정점은 뒤에 붙는다 — 이 인덱스 이상만 표면에 붙인다
    stats = {"loops": 0, "holes": 0, "cracks": 0, "open": 0, "cavities": 0, "revived": 0}
    new_faces = []
    cavities = []
    for loop in _boundary_loops(bm):
        stats["loops"] += 1
        if len(loop) > HOLE_MAX_EDGES:
            stats["open"] += 1
            continue
        points = [v.co for e in loop for v in e.verts]
        extent = max(max(p[a] for p in points) - min(p[a] for p in points) for a in range(3))
        faces = _fill_loop(bm, loop)
        if not faces:
            stats["open"] += 1
            continue
        area = sum(f.calc_area() for f in faces)
        crack = area < CRACK_AREA_RATIO * extent * extent
        if not crack and extent > HOLE_MAX_RATIO * size:
            bmesh.ops.delete(bm, geom=faces, context='FACES_ONLY')
            stats["open"] += 1
            continue
        if not crack and _is_cavity(faces, guide, area, extent):
            bmesh.ops.delete(bm, geom=faces, context='FACES_ONLY')
            stats["cavities"] += 1
            cavities.append((sum(points, Vector()) / len(points), extent))
            continue
        stats["cracks" if crack else "holes"] += 1
        new_faces += faces
    if cavities and pristine is not None:
        stats["revived"] = _revive_cavity_faces(bm, pristine, guide, cavities)
    if new_faces:
        faces = bmesh.ops.triangulate(bm, faces=new_faces)["faces"]
        for _ in range(HOLE_SUBDIVIDE):
            # poke 는 면마다 중심 정점을 더해 부채로 쪼갠다 — 이웃 면과 공유하는 엣지는 건드리지 않는다
            faces = bmesh.ops.poke(bm, faces=[f for f in faces if f.is_valid])["faces"]
        bm.verts.index_update()
        for vert in bm.verts:
            if vert.index >= original:
                nearest = guide.find_nearest(vert.co)
                if nearest[0] is not None:
                    vert.co = nearest[0]
    stats["faces"] = len(bm.faces) - len(obj.data.polygons)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return stats


def _is_cavity(faces, guide: BVHTree, area: float, extent: float) -> bool:
    """메운 캡이 표면의 구멍을 덮은 게 아니라 공동 입구를 가로막았는지.

    표면 구멍의 캡은 원래 표면 근처에 붙어 있고 면적도 지름 기준 원(0.79 x 폭²) 안이다. 공동 입구는
    둘레가 3차원으로 감겨 캡 면적이 폭² 을 넘거나(실측 1.7~2.0), 캡 중심이 표면에서 폭의 20% 이상 떠 있다."""
    if area > CAVITY_AREA_RATIO * extent * extent:
        return True
    lift = 0.0
    for face in faces:
        center = face.calc_center_median()
        nearest = guide.find_nearest(center)
        if nearest[0] is not None:
            lift = max(lift, (nearest[0] - center).length)
    return lift > CAVITY_LIFT_RATIO * extent


def _revive_cavity_faces(bm, pristine, guide: BVHTree, cavities: list) -> int:
    """공동 입구 둘레의 지운 면을 컬링 전 메시(pristine)에서 되살린다. 되살린 면 수를 돌려준다.

    후보는 입구 중심에서 CAVITY_REACH_RATIO x 폭 안의 pristine 면 중 지금 메시에 없는 것이다. 그중
    **안쪽 껍질**(바깥면 바로 뒤에 겹으로 붙은 면)은 제외한다 — 면 뒤쪽(-노멀)으로 껍질 두께의 몇 배 안에
    지금 메시의 표면이 있으면 안쪽 껍질이다. 옷 안쪽 벽의 뒤에는 옷 두께만큼 떨어져 바깥 벽이 있고,
    옷 아래 피부의 뒤는 몸속이라 표면이 멀다. 껍질 두께는 지금 메시의 바깥면 뒤로 광선을 쏴 pristine 의
    첫 교차 거리 중위값으로 잰다. UV 는 pristine 의 루프에서 그대로 옮긴다."""
    from mathutils.kdtree import KDTree
    current = BVHTree.FromBMesh(bm)
    thickness = _shell_thickness(bm, guide)
    polygons = pristine.polygons
    tree = KDTree(len(polygons))
    for polygon in polygons:
        tree.insert(polygon.center, polygon.index)
    tree.balance()
    wanted = set()
    for center, extent in cavities:
        reach = extent * CAVITY_REACH_RATIO
        for _co, index, _dist in tree.find_range(center, reach):
            polygon = polygons[index]
            hit = current.find_nearest(polygon.center)
            if hit[0] is not None and (hit[0] - polygon.center).length < 1e-5:
                continue    # 이미 있는 면
            backing = current.ray_cast(polygon.center - polygon.normal * 1e-4, -polygon.normal,
                                       thickness * CAVITY_BACKING_RATIO)
            if backing[0] is not None:
                continue    # 바깥면 바로 뒤에 겹친 안쪽 껍질
            wanted.add(index)
    if not wanted:
        return 0
    uv_layer = bm.loops.layers.uv.active
    source_uv = pristine.uv_layers.active
    vert_map = {}
    for index in wanted:
        polygon = polygons[index]
        verts = []
        for vertex_index in polygon.vertices:
            if vertex_index not in vert_map:
                vert_map[vertex_index] = bm.verts.new(pristine.vertices[vertex_index].co)
            verts.append(vert_map[vertex_index])
        try:
            face = bm.faces.new(verts)
        except ValueError:
            continue    # 같은 정점 조합의 면이 이미 있다
        face.material_index = polygon.material_index
        if uv_layer is not None and source_uv is not None:
            for loop, loop_index in zip(face.loops, polygon.loop_indices):
                loop[uv_layer].uv = source_uv.data[loop_index].uv
    bmesh.ops.remove_doubles(bm, verts=list(vert_map.values()), dist=1e-5)
    return len(wanted)


def _shell_thickness(bm, guide: BVHTree, samples: int = 400) -> float:
    """바깥면 뒤에 붙은 안쪽 껍질까지의 거리 중위값 — 셰이프 서버 밴드 메시의 껍질 두께."""
    bm.faces.ensure_lookup_table()
    step = max(len(bm.faces) // samples, 1)
    distances = []
    for index in range(0, len(bm.faces), step):
        face = bm.faces[index]
        center = face.calc_center_median()
        hit = guide.ray_cast(center - face.normal * 1e-4, -face.normal)
        if hit[0] is not None:
            distances.append((hit[0] - center).length)
    if not distances:
        return 0.0
    distances.sort()
    return distances[len(distances) // 2]


def _boundary_loops(bm) -> list:
    """경계 엣지를 연결된 루프(엣지 목록)로 묶는다."""
    seen = set()
    loops = []
    for edge in bm.edges:
        if edge in seen or not edge.is_boundary:
            continue
        stack, loop = [edge], []
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            loop.append(cur)
            for v in cur.verts:
                stack.extend(e for e in v.link_edges if e.is_boundary and e not in seen)
        loops.append(loop)
    return loops


def _fill_loop(bm, loop) -> list:
    """루프 하나를 메우고 새 면 목록을 돌려준다. 64변 이하는 n각형 하나, 그 이상은 삼각형으로."""
    if len(loop) <= 64:
        faces = [f for f in bmesh.ops.holes_fill(bm, edges=loop, sides=64).get("faces", []) if f.is_valid]
        if faces:
            return faces
    result = bmesh.ops.triangle_fill(bm, edges=loop, use_beauty=True, use_dissolve=False)
    return [g for g in result.get("geom", []) if isinstance(g, bmesh.types.BMFace) and g.is_valid]


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
    # 컬링 전 표면 — 메운 구멍의 새 정점을 여기에 붙여 원래 굴곡을 되살린다
    guide = BVHTree.FromObject(obj, bpy.context.evaluated_depsgraph_get())
    pristine = obj.data.copy()   # 공동 입구 둘레의 지운 면을 되살릴 원본
    inner = remove_interior_faces(obj)
    slabs = remove_ground_slabs(obj)
    holes = fill_small_holes(obj, guide, max(obj.dimensions), pristine=pristine)
    bpy.data.meshes.remove(pristine)
    normalize(obj, height)
    for polygon in obj.data.polygons:
        polygon.use_smooth = True
    obj.data.calc_loop_triangles()
    images = {n.image.name for m in obj.data.materials if m and m.use_nodes
              for n in m.node_tree.nodes if n.type == 'TEX_IMAGE' and n.image}
    return {"obj": obj, "faces": len(obj.data.polygons), "tris": len(obj.data.loop_triangles),
            "materials": len([m for m in obj.data.materials if m]), "images": sorted(images),
            "ground_slabs": slabs, "welded": welded, "interior_faces": inner,
            "filled_holes": holes["holes"] + holes["cracks"], "open_loops": holes["open"],
            "cavities": holes["cavities"], "revived_faces": holes["revived"]}
