# 게임레디 정리 패스 + 은면 컬링 + 메시 QA 지표
import math
from collections import deque

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

# ---------- 은면 판정 ----------
# 원칙: "다른 닫힌 파트 볼륨 안에 완전히 파묻힌 면"만 은면이다. 컵·관 같은 오목한
# 공간, 노멀이 뒤집힌 면, 테두리만 노출된 면은 절대 지우지 않는다. 삭제는 되돌릴 수
# 없으므로 판정이 애매하면 항상 살려두는 쪽을 택한다.


def _fibonacci_sphere(count):
    """구 표면에 고르게 분포한 단위 방향 벡터 count개."""
    golden = math.pi * (3 - math.sqrt(5))
    out = []
    for i in range(count):
        z = 1 - 2 * (i + 0.5) / count
        r = math.sqrt(max(0.0, 1 - z * z))
        out.append(Vector((r * math.cos(golden * i), r * math.sin(golden * i), z)))
    return tuple(out)


# 최종 확인용 레이 방향: 축·대각 26방향 + 피보나치 64방향 (약 18도 간격)
_FINE_DIRECTIONS = tuple(
    Vector((x, y, z)).normalized()
    for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)
    if (x, y, z) != (0, 0, 0)
) + _fibonacci_sphere(64)
_RAY_OFFSET_MAX = 5e-4   # 면에서 띄우는 거리 상한 (자기 면 재충돌 방지)
_SAMPLE_INSET = 0.06     # 정점/변 근처 샘플을 면 안쪽으로 들이는 비율


def _ray_offset(objs) -> float:
    """모델 크기에 비례한 면 오프셋 (작은 모델에서 얇은 파트를 뚫지 않도록)."""
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not pts:
        return _RAY_OFFSET_MAX
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return min(_RAY_OFFSET_MAX, max(1e-5, (hi - lo).length * 2e-4))


def _build_world_bvh(objs):
    """오브젝트들의 월드 공간 폴리곤 전체로 BVH를 만든다 (가림 판정용)."""
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


def _closed_islands(objs):
    """오브젝트별 (면→아일랜드 번호 목록)과 전역 아일랜드 목록을 만든다.

    아일랜드 = 연결된 면 묶음(파트). 닫힌(모든 변에 면이 정확히 2개) 아일랜드만
    다른 면을 파묻을 수 있는 볼륨으로 인정해 BVH를 만들고, 열린 셸은 None으로 둔다.
    BVH는 recalc_face_normals로 바깥 노멀을 맞춘 뒤 만들어 find_nearest의 노멀
    부호로 내부/외부를 판정할 수 있게 한다."""
    islands = []       # [(bvh 또는 None, aabb_min, aabb_max)]
    face_island = {}   # obj → [면 인덱스별 전역 아일랜드 번호]
    for obj in objs:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(obj.matrix_world)   # bmesh.transform은 감기 순서를 유지 → 아래 recalc로 통일
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.faces.ensure_lookup_table()
        mapping = [-1] * len(bm.faces)
        for seed in bm.faces:
            if mapping[seed.index] != -1:
                continue
            island_id = len(islands)
            faces, queue = [], deque([seed])
            mapping[seed.index] = island_id
            while queue:
                face = queue.popleft()
                faces.append(face)
                for edge in face.edges:
                    for other in edge.link_faces:
                        if mapping[other.index] == -1:
                            mapping[other.index] = island_id
                            queue.append(other)
            closed = all(len(e.link_faces) == 2 for f in faces for e in f.edges)
            verts = {}
            coords, polys = [], []
            for face in faces:
                poly = []
                for v in face.verts:
                    idx = verts.get(v.index)
                    if idx is None:
                        idx = verts[v.index] = len(coords)
                        coords.append(v.co.copy())
                    poly.append(idx)
                polys.append(tuple(poly))
            lo = Vector((min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords)))
            hi = Vector((max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords)))
            bvh = BVHTree.FromPolygons(coords, polys) if closed else None
            islands.append((bvh, lo, hi))
        bm.free()
        face_island[obj] = mapping
    return face_island, islands


def _inside_other_island(point, own, islands, margin) -> bool:
    """점이 자기 아일랜드가 아닌 닫힌 아일랜드 볼륨 안에 있는지."""
    for island_id, (bvh, lo, hi) in enumerate(islands):
        if bvh is None or island_id == own:
            continue
        if not (lo.x - margin <= point.x <= hi.x + margin
                and lo.y - margin <= point.y <= hi.y + margin
                and lo.z - margin <= point.z <= hi.z + margin):
            continue
        co, normal, _index, _dist = bvh.find_nearest(point)
        if co is not None and (point - co).dot(normal) < 0:
            return True
    return False


def _face_samples(mesh, mw):
    """폴리곤 인덱스 → 면 위 월드 샘플점 목록.

    샘플점은 폴리곤 테셀레이션 삼각형 위에서만 뽑는다 — 오목/고리형 ngon은
    poly.center가 면 밖(구멍 위)에 놓이기 때문. 삼각형마다 중심·정점 근처·변 중점
    근처 7점을 뽑아, 볼록한 가림체라면 "모든 샘플이 가려짐 ⇒ 면 전체가 가려짐"이
    성립하게 한다(테두리만 노출된 받침대 윗면 등을 지우지 않기 위함)."""
    mesh.calc_loop_triangles()
    wverts = [mw @ v.co for v in mesh.vertices]
    samples = {}
    for lt in mesh.loop_triangles:
        a, b, c = (wverts[vi] for vi in lt.vertices)
        center = (a + b + c) / 3
        pts = samples.setdefault(lt.polygon_index, [])
        pts.append(center)
        pts.extend(v.lerp(center, _SAMPLE_INSET) for v in (a, b, c))
        pts.extend(((p + q) / 2).lerp(center, _SAMPLE_INSET) for p, q in ((a, b), (b, c), (c, a)))
    return samples


def _escapes(bvh, origin, direction) -> bool:
    return bvh.ray_cast(origin, direction)[0] is None


def _sees_outside_fine(bvh, point, normal, eps) -> bool:
    """면 양쪽 반구의 세밀 방향 중 하나라도 바깥으로 빠져나가는지."""
    front, back = point + normal * eps, point - normal * eps
    return any(_escapes(bvh, front if d.dot(normal) >= 0 else back, d) for d in _FINE_DIRECTIONS)


def _hidden_face_indices(obj, bvh, islands, face_island, eps):
    """다른 닫힌 파트 안에 완전히 파묻힌 면 인덱스 목록.

    면의 노멀이 뒤집혀 있어도 오판하지 않도록 양쪽(±노멀) 오프셋 점을 모두 본다.
    1) 어느 샘플이든 ±노멀 방향으로 빠져나가면 가시면 (대부분 여기서 끝난다)
    2) 모든 샘플이 다른 닫힌 아일랜드 안에 있어야 은면 후보 (오목 공간은 탈락)
    3) 후보만 세밀 방향으로 최종 확인 — 하나라도 빠져나가면 살려둔다"""
    mesh = obj.data
    mw = obj.matrix_world
    nmat = mw.inverted_safe().transposed().to_3x3()
    samples = _face_samples(mesh, mw)
    hidden = []
    for poly in mesh.polygons:
        normal = nmat @ poly.normal
        pts = samples.get(poly.index)
        if not pts or normal.length_squared < 1e-12:
            continue  # 퇴화면은 판정 불가 — 살려둔다
        normal.normalize()
        if any(_escapes(bvh, p + normal * eps, normal) or _escapes(bvh, p - normal * eps, -normal)
               for p in pts):
            continue
        own = face_island[poly.index] if poly.index < len(face_island) else -1
        if not all(_inside_other_island(p + normal * eps, own, islands, eps)
                   or _inside_other_island(p - normal * eps, own, islands, eps) for p in pts):
            continue
        if any(_sees_outside_fine(bvh, p, normal, eps) for p in pts):
            continue
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
    face_island, islands = _closed_islands(objs)
    if not any(island[0] is not None for island in islands):
        return {obj: [] for obj in objs}   # 닫힌 파트가 없으면 파묻힐 곳도 없다
    eps = _ray_offset(objs)
    return {obj: _hidden_face_indices(obj, bvh, islands, face_island[obj], eps) for obj in objs}


def count_hidden_faces(objs) -> int:
    """완전히 가려진 은면 수 (비평 통계용)."""
    return sum(len(v) for v in find_hidden_faces(objs).values())


def cull_hidden_faces(objs) -> int:
    """다른 파트 안에 완전히 파묻힌 면을 삭제한다. 삭제한 면 수를 반환.

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


# ---------- 동일평면 겹침(Z-fighting) 띄우기 ----------
# 판정 계산은 lowpoly/coplanar.py(순수 기하)가 하고, 여기서는 결과 이동량을 파트 꼭짓점에 적용한다.


def _loose_part_ids(mesh):
    """꼭짓점 인덱스 → 연결 요소(느슨한 파트) 번호."""
    parent = list(range(len(mesh.vertices)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for edge in mesh.edges:
        a, b = find(edge.vertices[0]), find(edge.vertices[1])
        if a != b:
            parent[a] = b
    return [find(i) for i in range(len(mesh.vertices))]


def _coplanar_scale(objs) -> float:
    """판정 허용 오차의 기준 크기(m). 배경처럼 큰 씬에서 오차가 cm 단위로 커지지 않게 오브젝트 하나 크기로 잡는다."""
    size = 0.0
    for obj in objs:
        pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        size = max(size, (hi - lo).length)
    return min(10.0, max(0.1, size))


def resolve_coplanar_faces(objs) -> int:
    """서로 다른 파트의 면이 같은 방향으로 한 평면에 겹친 곳의 Z-fighting을 없앤다. 옮긴 파트 수를 반환.

    면을 자르거나 지우지 않고, 좁은 쪽 파트(벽에 붙인 패널 등)를 통째로 노멀 방향으로 수 mm 띄운다.
    맞댄 면(노멀 반대)은 두 솔리드 사이라 보이지 않으므로 그대로 둔다.
    메시를 공유하는 인스턴스는 움직이지 않고 상대 파트를 민다."""
    from . import coplanar

    meshes = [o for o in objs if o.type == 'MESH' and o.data.polygons]
    if not meshes:
        return 0
    faces, roots = [], {}
    for obj in meshes:
        mesh, mw = obj.data, obj.matrix_world
        mutable = mesh.users == 1
        root = _loose_part_ids(mesh)
        roots[obj.name] = root
        world = [mw @ v.co for v in mesh.vertices]
        # 음수 스케일은 월드 좌표의 감기 방향을 뒤집는다 — Blender가 보여 주는 노멀과 맞추려면 되돌린다
        order = -1 if mw.determinant() < 0 else 1
        for poly in mesh.polygons:
            faces.append(coplanar.Face(
                key=(obj.name, poly.index), island=(obj.name, root[poly.vertices[0]]),
                points=[tuple(world[i]) for i in poly.vertices][::order], mutable=mutable))
    shifts = coplanar.resolve(faces, scale=_coplanar_scale(meshes))
    if not shifts:
        return 0
    by_obj = {}
    for (name, part), offset in shifts.items():
        by_obj.setdefault(name, {})[part] = Vector(offset)
    for obj in meshes:
        todo = by_obj.get(obj.name)
        if not todo or obj.data.users > 1:
            continue
        to_local = obj.matrix_world.to_3x3().inverted()
        local = {part: to_local @ offset for part, offset in todo.items()}
        root = roots[obj.name]
        for i, v in enumerate(obj.data.vertices):
            offset = local.get(root[i])
            if offset is not None:
                v.co += offset
        obj.data.update()
    return len(shifts)


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


def _world_points(objs):
    """모든 메시 정점의 월드 좌표 목록."""
    pts = []
    for obj in objs:
        if obj.type != 'MESH':
            continue
        mw = obj.matrix_world
        pts.extend(mw @ v.co for v in obj.data.vertices)
    return pts


def symmetry_score(objs, axis=None, tol=0.03) -> float:
    """좌우 대칭도 0~1. 1이면 완전 대칭.

    모델 중심을 기준으로 정점을 축 방향으로 뒤집어, 짝이 되는 정점이 tol 안에
    있는 비율을 센다. 차량·캐릭터·가구처럼 대칭이 당연한 대상에서 미러 누락
    (백미러 한쪽만 있는 경우 등)을 잡는 지표다.

    axis=None이면 X/Y 중 점수가 높은 쪽을 쓴다 — 모델의 '좌우'가 어느 축인지는
    대상마다 다르므로(자동차는 길이가 X면 좌우는 Y) 축을 고정하면 정상 모델도
    낮게 나온다. 비대칭이 의도인 대상도 있으므로 낮다고 무조건 결함은 아니다."""
    bpy.context.view_layer.update()
    pts = _world_points(objs)
    if len(pts) < 8:
        return 1.0
    if axis is None:
        return max(symmetry_score(objs, a, tol) for a in ('X', 'Y'))
    i = {'X': 0, 'Y': 1, 'Z': 2}[axis]
    # 공간 해싱: 정점이 많아도 선형 시간으로 짝을 찾는다
    cell = max(tol, 1e-6)
    grid = {}
    for p in pts:
        key = (round(p.x / cell), round(p.y / cell), round(p.z / cell))
        grid.setdefault(key, []).append(p)

    def matched_ratio(center):
        matched = 0
        for p in pts:
            m = p.copy()
            m[i] = 2 * center - p[i]
            bk = (round(m.x / cell), round(m.y / cell), round(m.z / cell))
            found = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for q in grid.get((bk[0] + dx, bk[1] + dy, bk[2] + dz), ()):
                            if (q - m).length <= tol:
                                found = True
                                break
                        if found:
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                matched += 1
        return matched / len(pts)

    # 중심 후보를 둘 다 시도해 더 나은 쪽을 쓴다. 정점 평균만 쓰면 비대칭 파트
    # 하나가 중심을 끌어당겨 나머지 정점까지 짝을 잃고 점수가 0에 가까워진다.
    coords = [p[i] for p in pts]
    candidates = {(min(coords) + max(coords)) / 2, sum(coords) / len(coords)}
    return round(max(matched_ratio(c) for c in candidates), 3)


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
