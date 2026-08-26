# 게임레디 정리 패스 + 은면 컬링 + 메시 QA 지표
import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

# 은면 판정용 레이 방향(26방향). 면의 노멀 방향을 포함해 어느 방향으로도
# 바깥으로 나가지 못하는(전부 막힌) 면만 내부로 판정한다.
_CULL_DIRECTIONS = tuple(
    Vector((x, y, z)).normalized()
    for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
    if (x, y, z) != (0, 0, 0)
)
_RAY_OFFSET = 5e-4  # 면에서 노멀 방향으로 띄우는 거리(자기 면 재충돌 방지)


def _build_world_bvh(objs):
    """오브젝트들의 월드 공간 폴리곤 전체로 BVH를 만든다."""
    verts, polys = [], []
    for obj in objs:
        mw = obj.matrix_world
        base = len(verts)
        mesh = obj.data
        verts.extend(mw @ v.co for v in mesh.vertices)
        polys.extend(tuple(base + vi for vi in p.vertices) for p in mesh.polygons)
    if not polys:
        return None
    return BVHTree.FromPolygons(verts, polys)


def _sees_outside(bvh, origin, normal) -> bool:
    if bvh.ray_cast(origin, normal)[0] is None:  # 노멀 방향 우선(가장 흔한 탈출로)
        return True
    return any(bvh.ray_cast(origin, d)[0] is None for d in _CULL_DIRECTIONS)


def _hidden_face_indices(obj, bvh):
    """어느 샘플점에서도 바깥이 보이지 않는(완전히 파묻힌) 면 인덱스 목록.

    샘플점은 폴리곤 테셀레이션 삼각형 위에서만 뽑는다 — 오목/고리형 ngon은
    poly.center가 면 밖(구멍 위)에 놓여 가시면을 은면으로 오판하기 때문.
    부분만 가려진 면을 지우지 않도록 모든 샘플이 막혀야 은면으로 판정한다."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    mw = obj.matrix_world
    nmat = mw.inverted_safe().transposed().to_3x3()
    wverts = [mw @ v.co for v in mesh.vertices]
    samples = {}  # 폴리곤 인덱스 → 면 위 샘플점 목록
    for lt in mesh.loop_triangles:
        tri = [wverts[vi] for vi in lt.vertices]
        center = (tri[0] + tri[1] + tri[2]) / 3
        pts = samples.setdefault(lt.polygon_index, [])
        pts.append(center)
        pts.extend(v.lerp(center, 0.35) for v in tri)
    hidden = []
    for poly in mesh.polygons:
        normal = nmat @ poly.normal
        if normal.length_squared < 1e-12:
            continue  # 퇴화면은 판정 불가 — 살려둔다
        normal.normalize()
        pts = samples.get(poly.index)
        if pts and not any(_sees_outside(bvh, p + normal * _RAY_OFFSET, normal) for p in pts):
            hidden.append(poly.index)
    return hidden


def find_hidden_faces(objs) -> dict:
    """오브젝트별 은면 인덱스 목록을 반환한다 (판정만, 삭제 없음)."""
    objs = [o for o in objs if o.type == 'MESH' and len(o.data.polygons)]
    if not objs:
        return {}
    bpy.context.view_layer.update()
    bvh = _build_world_bvh(objs)
    if bvh is None:
        return {}
    return {obj: _hidden_face_indices(obj, bvh) for obj in objs}


def count_hidden_faces(objs) -> int:
    """완전히 가려진 은면 수 (비평 통계용)."""
    return sum(len(v) for v in find_hidden_faces(objs).values())


def cull_hidden_faces(objs) -> int:
    """겹친 파트 안에 완전히 파묻힌 면을 삭제한다. 삭제한 면 수를 반환.

    join(union)을 거치지 않은 잔여 겹침의 안전망. 메시를 공유하는 인스턴스는
    (인스턴스별 은면이 달라 공유 데이터를 훼손하므로) 건너뛴다."""
    removed = 0
    for obj, indices in find_hidden_faces(objs).items():
        if not indices or obj.data.users > 1:
            continue
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[i] for i in indices], context='FACES')
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        removed += len(indices)
    return removed


def _island_bounds(objs):
    """모든 메시 오브젝트의 연결 요소(느슨한 파트)별 월드 AABB 목록을 반환."""
    boxes = []
    for obj in objs:
        if obj.type != 'MESH' or not len(obj.data.vertices):
            continue
        mesh = obj.data
        mw = obj.matrix_world
        parent = list(range(len(mesh.vertices)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]  # 경로 압축
                i = parent[i]
            return i

        for edge in mesh.edges:
            a, b = edge.vertices
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        groups = {}
        for vi, v in enumerate(mesh.vertices):
            co = mw @ v.co
            root = find(vi)
            box = groups.get(root)
            if box is None:
                groups[root] = [Vector(co), Vector(co)]
            else:
                box[0].x = min(box[0].x, co.x); box[0].y = min(box[0].y, co.y); box[0].z = min(box[0].z, co.z)
                box[1].x = max(box[1].x, co.x); box[1].y = max(box[1].y, co.y); box[1].z = max(box[1].z, co.z)
        boxes.extend(groups.values())
    return boxes


def _aabb_touch(a, b, tol):
    return all(a[0][i] - tol <= b[1][i] and b[0][i] - tol <= a[1][i] for i in range(3))


def floating_part_count(objs, tol=0.02) -> int:
    """바닥이나 다른 파트와 접촉 사슬로 이어지지 않고 공중에 뜬 파트 수.

    파트 = 느슨한 연결 요소. 모델 최저점에 닿은 파트를 뿌리로 AABB 접촉 그래프를
    탐색해, 도달 불가능한 파트를 '떠 있음'으로 센다 (굴뚝이 지붕에서 분리된 경우 등)."""
    bpy.context.view_layer.update()  # 방금 배치한 트랜스폼을 matrix_world에 반영
    boxes = _island_bounds(objs)
    if len(boxes) <= 1:
        return 0
    ground_z = min(box[0].z for box in boxes)
    reached = set(i for i, box in enumerate(boxes) if box[0].z <= ground_z + tol)
    queue = list(reached)
    while queue:
        cur = queue.pop()
        for i, box in enumerate(boxes):
            if i not in reached and _aabb_touch(boxes[cur], box, tol):
                reached.add(i)
                queue.append(i)
    return len(boxes) - len(reached)


def nonmanifold_edge_count(objs) -> int:
    """3개 이상 면이 공유하는 엣지 수 — 파트 관통·내부 벽 잔재의 결함 지표."""
    total = 0
    for obj in objs:
        if obj.type != 'MESH':
            continue
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        total += sum(1 for e in bm.edges if len(e.link_faces) > 2)
        bm.free()
    return total


def game_ready(obj, origin='BOTTOM'):
    """오브젝트를 게임엔진 임포트에 적합하게 정리한다. 시스템이 자동 호출한다.

    트랜스폼을 메시에 굽고, 중복 정점 병합·퇴화면 제거·루즈 정점 삭제 후
    원점을 바닥 중앙(origin='BOTTOM') 또는 중심(origin='CENTER')으로 옮기고,
    노멀 재계산 + 플랫 셰이딩을 적용한다."""
    mesh = obj.data
    # 1) 트랜스폼 적용 (최신 matrix_world 보장을 위해 depsgraph 갱신)
    bpy.context.view_layer.update()
    mesh.transform(obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    # 2) 메시 위생: 중복 정점 병합 → 퇴화면 제거 → 루즈 정점 삭제 → 노멀 재계산
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=1e-5)
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    # 3) 원점 정규화 (위생 처리 후 좌표 기준)
    if mesh.vertices:
        xs = [v.co.x for v in mesh.vertices]
        ys = [v.co.y for v in mesh.vertices]
        zs = [v.co.z for v in mesh.vertices]
        center = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.0))
        center.z = min(zs) if origin == 'BOTTOM' else (min(zs) + max(zs)) / 2
        mesh.transform(Matrix.Translation(-center))
        obj.location = center
    # 4) 플랫 셰이딩
    for poly in mesh.polygons:
        poly.use_smooth = False
    mesh.update()
    return obj


def tri_count(obj) -> int:
    """트라이앵글 수를 센다 (폴리 버짓 검사용)."""
    return sum(max(len(p.vertices) - 2, 0) for p in obj.data.polygons)


def collection_tri_count(coll) -> int:
    return sum(tri_count(o) for o in coll.objects if o.type == 'MESH')
