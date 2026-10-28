# 프리미티브 생성 헬퍼 — bpy.ops 대신 bmesh/data API 사용 (컨텍스트 의존 오류 방지)
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from .names import safe_id_name


def _new_object(name: str, bm: bmesh.types.BMesh, location, rotation, scale) -> bpy.types.Object:
    name = safe_id_name(name)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = Vector(location)
    obj.rotation_euler = rotation
    obj.scale = Vector(scale)
    from . import link_to_root
    link_to_root(obj)
    return obj


def box(name="Box", size=(1, 1, 1), location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """직육면체를 만든다. size=(x,y,z) 미터 단위, 원점은 중심."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def cylinder(name="Cylinder", radius=0.5, depth=1.0, segments=8,
             location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """원기둥. 로우폴리는 segments 6~12 권장. depth는 Z축 높이, 원점은 중심. 짝수 segments면 캡이 쿼드 격자다."""
    bm = bmesh.new()
    _frustum(bm, radius, radius, depth, segments)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def cone(name="Cone", radius_bottom=0.5, radius_top=0.0, depth=1.0, segments=8,
         location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """원뿔/절두체. radius_top>0이면 절두체(테이퍼 기둥)가 된다. 짝수 segments면 캡이 쿼드 격자다."""
    bm = bmesh.new()
    _frustum(bm, radius_bottom, radius_top, depth, segments)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def _frustum(bm, radius_bottom, radius_top, depth, segments) -> None:
    """Z축 원뿔대를 bm에 만든다. 반지름 0인 끝은 극점(삼각형 부채), 나머지 끝은 _cap으로 막는다."""
    segments = max(3, int(segments))
    half = depth / 2

    def ring(r, z):
        if r <= 1e-6:
            return bm.verts.new((0.0, 0.0, z))
        return [bm.verts.new((r * math.cos(math.tau * i / segments), r * math.sin(math.tau * i / segments), z))
                for i in range(segments)]

    _bridge_rings(bm, [ring(radius_bottom, -half), ring(radius_top, half)], segments)


def _sphere_grid(subdivisions) -> int:
    """subdivisions → 큐브 한 면의 격자 분할 수(2^n). 면 수가 예전 아이코스피어와 비슷하고, 짝수라
    X=0·Y=0·Z=0에 edge loop가 지나가 mirror_x 이음새가 깔끔하다."""
    return 2 ** max(1, int(subdivisions))


def sphere(name="Sphere", radius=0.5, subdivisions=1,
           location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """쿼드 스피어(큐브 격자를 구로 투영). 면이 전부 사각형이라 edge loop가 이어진다.

    subdivisions 1=24쿼드(48트라이), 2=96쿼드(192트라이), 3=384쿼드(768트라이), 4=1536쿼드.
    로우폴리는 1~2, 캐릭터 머리·관절은 2~3."""
    grid = _sphere_grid(subdivisions)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=grid - 1, use_grid_fill=True)
    for v in bm.verts:
        # 등각 큐브맵: 격자 간격을 tan으로 펴서 면 모서리 쪽 쿼드가 찌그러지지 않게 한 뒤 구로 투영
        p = Vector(tuple(math.tan(c * math.pi / 4) for c in v.co))
        v.co = p.normalized() * radius
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def _cap_grid(ring):
    """닫힌 고리(짝수 개 꼭짓점)를 쿼드 격자로 채울 좌표를 계산한다 — 실패하면 None.

    고리를 a, b, a, b 개 변으로 나눈 네 변으로 보고 Coons 패치로 내부 점을 보간한다.
    반환: (a, b, points) — points[i][j]는 Vector 또는 고리 인덱스(int)."""
    n = len(ring)
    if n < 4 or n % 2:
        return None
    a = n // 4
    b = n // 2 - a
    co = [Vector(v.co) for v in ring]

    def edge(i, j):
        # 경계 격자점 → 고리 인덱스
        if j == 0:
            return i
        if i == a:
            return a + j
        if j == b:
            return 2 * a + b - i
        return (n - j) % n

    p00, pa0, p0b, pab = co[edge(0, 0)], co[edge(a, 0)], co[edge(0, b)], co[edge(a, b)]
    points = []
    for i in range(a + 1):
        row = []
        for j in range(b + 1):
            if i in (0, a) or j in (0, b):
                row.append(edge(i, j))
                continue
            u, v = i / a, j / b
            row.append((1 - v) * co[edge(i, 0)] + v * co[edge(i, b)]
                       + (1 - u) * co[edge(0, j)] + u * co[edge(a, j)]
                       - ((1 - u) * (1 - v) * p00 + u * (1 - v) * pa0 + (1 - u) * v * p0b + u * v * pab))
        points.append(row)

    # 고리 감기 방향 노멀(Newell)과 어긋나는 쿼드가 있으면(오목한 윤곽) 격자가 접힌 것이다
    normal = Vector()
    for k in range(n):
        c, d = co[k], co[(k + 1) % n]
        normal += Vector(((c.y - d.y) * (c.z + d.z), (c.z - d.z) * (c.x + d.x), (c.x - d.x) * (c.y + d.y)))
    if normal.length < 1e-12:
        return None

    def at(i, j):
        p = points[i][j]
        return co[p] if isinstance(p, int) else p

    for i in range(a):
        for j in range(b):
            quad = (at(i, j), at(i + 1, j), at(i + 1, j + 1), at(i, j + 1))
            # 네 모서리가 모두 같은 쪽으로 꺾여야(볼록) 접히거나 화살촉 모양 쿼드가 아니다
            for k in range(4):
                turn = (quad[k] - quad[k - 1]).cross(quad[(k + 1) % 4] - quad[k])
                if turn.dot(normal) <= 0:
                    return None
    return a, b, points


def _cap(bm, ring, flip=False) -> None:
    """닫힌 고리를 막는다. 짝수 고리는 쿼드 격자로(edge loop가 캡을 가로질러 이어진다), 안 되면 n각형 하나로.

    flip=False면 고리 순서대로 감긴 면(고리 순서가 바깥에서 볼 때 반시계)을 만든다."""
    grid = _cap_grid(ring)
    if grid is None:
        bm.faces.new(tuple(reversed(ring)) if flip else tuple(ring))
        return
    a, b, points = grid
    verts = [[ring[p] if isinstance(p, int) else bm.verts.new(p) for p in row] for row in points]
    for i in range(a):
        for j in range(b):
            quad = (verts[i][j], verts[i + 1][j], verts[i + 1][j + 1], verts[i][j + 1])
            bm.faces.new(tuple(reversed(quad)) if flip else quad)


def plane(name="Plane", size=(1, 1), location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """XY 평면 사각형. size=(x,y)."""
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
    bmesh.ops.scale(bm, vec=Vector((size[0], size[1], 1.0)), verts=bm.verts)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def _smooth_profile(profile, subdiv):
    """Catmull-Rom 보간: 컨트롤 포인트 사이마다 subdiv개의 중간점을 삽입한다."""
    pts = [Vector((r, z)) for r, z in profile]
    ext = [pts[0]] + pts + [pts[-1]]
    out = [(pts[0].x, pts[0].y)]
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for step in range(1, subdiv + 1):
            t = step / (subdiv + 1)
            v = 0.5 * ((2 * p1) + (-p0 + p2) * t
                       + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                       + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t)
            out.append((max(v.x, 0.0), v.y))  # 반지름은 음수 금지
        out.append((pts[i + 1].x, pts[i + 1].y))
    return out


def _bridge_rings(bm, rings, count) -> None:
    """축 순서의 고리(꼭짓점 리스트) 또는 극점(단일 꼭짓점)들을 옆면으로 잇고 양 끝을 막는다.

    고리끼리는 쿼드, 극점과는 삼각형 부채, 극점이 아닌 끝은 _cap(짝수 고리면 쿼드 격자)."""
    for lower, upper in zip(rings, rings[1:]):
        if isinstance(lower, list) and isinstance(upper, list):
            for i in range(count):
                bm.faces.new((lower[i], lower[(i + 1) % count], upper[(i + 1) % count], upper[i]))
        elif isinstance(lower, list):  # 위가 극점
            for i in range(count):
                bm.faces.new((lower[i], lower[(i + 1) % count], upper))
        elif isinstance(upper, list):  # 아래가 극점
            for i in range(count):
                bm.faces.new((lower, upper[(i + 1) % count], upper[i]))
    if isinstance(rings[0], list):
        _cap(bm, rings[0], flip=True)
    if isinstance(rings[-1], list):
        _cap(bm, rings[-1])


def lathe(name="Lathe", profile=((0.3, 0.0), (0.4, 0.5), (0.3, 1.0)), segments=8,
          ellipse=(1.0, 1.0), smooth=0,
          location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """회전체(선반 성형). profile은 아래→위 순서의 (반지름, z높이) 목록.

    배럴/항아리/병/나무줄기/탑 지붕처럼 곡률 실루엣이 필요한 형태에 최적.
    반지름 0이면 뾰족한 극점(원뿔 끝)이 된다. segments 6~12 권장.
    smooth=1~3이면 컨트롤 포인트 사이를 곡선(Catmull-Rom)으로 보간해
    적은 포인트로 매끈한 배불림/잘록함을 만든다 (포인트 사이 middle점 개수).
    ellipse=(x배율, y배율)로 단면을 타원으로 만들 수 있다 (납작한 보트·빵 등).
    예: 항아리 = profile=[(0.18,0), (0.32,0.25), (0.22,0.55), (0.14,0.7)], smooth=1"""
    if smooth > 0 and len(profile) >= 3:
        profile = _smooth_profile(profile, smooth)
    sx, sy = ellipse
    bm = bmesh.new()
    rings = []
    for r, z in profile:
        if r <= 1e-4:
            rings.append(bm.verts.new((0.0, 0.0, z)))  # 극점(단일 버텍스)
            continue
        ring = []
        for i in range(segments):
            angle = math.tau * i / segments
            ring.append(bm.verts.new((r * math.cos(angle) * sx, r * math.sin(angle) * sy, z)))
        rings.append(ring)
    _bridge_rings(bm, rings, segments)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def prism(name="Prism", outline=((-0.5, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.5), (-0.5, 1.0)),
          depth=1.0, axis='Y', location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """2D 윤곽을 axis 방향으로 depth만큼 압출한 각기둥 — 박스로 안 되는 각진 실루엣의 핵심.

    outline은 (u,v) 꼭짓점 목록(시계반대 방향). axis='Y'(기본)면 u=X, v=Z인
    정면 실루엣이 되어 앞뒤로 압출된다. 박공지붕 단면·L자 건물·화살표·계단·
    사다리꼴 몸통 등에 사용. 원점은 압출 축의 중간.
    예: 박공 집 몸통 = lp.prism(outline=[(-1,0), (1,0), (1,1.2), (0,1.9), (-1,1.2)], depth=1.6)"""
    bm = bmesh.new()
    half = depth / 2

    def to3(u, v, w):
        if axis == 'Y':
            return (u, w, v)
        if axis == 'X':
            return (w, u, v)
        return (u, v, w)  # 'Z': 윤곽이 XY 평면

    bottom = [bm.verts.new(to3(u, v, -half)) for u, v in outline]
    top = [bm.verts.new(to3(u, v, half)) for u, v in outline]
    count = len(outline)
    for i in range(count):
        bm.faces.new((bottom[i], bottom[(i + 1) % count],
                      top[(i + 1) % count], top[i]))
    bm.faces.new(bottom)
    bm.faces.new(tuple(reversed(top)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def tube(name="Tube", points=((0, 0, 0), (0, 0, 1)), radius=0.05, segments=6, closed=False,
         location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """폴리라인 경로를 따라 원형 단면을 스윕한 튜브(양 끝 캡 포함).

    손잡이·나뭇가지·파이프·난간·가로등 기둥처럼 가늘고 긴 형태에 사용.
    radius는 숫자 하나(균일) 또는 점 개수와 같은 길이의 목록(끝으로 가늘어지는 가지).
    closed=True면 마지막 점에서 첫 점으로 이어 닫힌 고리를 만든다(캡 없음) — 배 현측 테두리·
    테이블 상판 테두리·화분 테두리처럼 둘레를 한 바퀴 도는 것은 반쪽 tube를 미러하지 말고
    둘레 전체 점을 주고 closed=True로 하나만 만들어라. 반쪽을 미러하면 뱃머리처럼 모이는
    꼭짓점에서 끝이 끊겨 본체가 그 틈으로 삐져나온다. 꺾이는 점은 마이터로 이어 두께가 유지된다.
    예: 굽은 가지 = lp.tube(points=[(0,0,0), (0.1,0,0.5), (0.35,0,0.8)],
                            radius=[0.08, 0.05, 0.02], segments=6)"""
    pts = [Vector(p) for p in points]
    if closed and len(pts) > 2 and (pts[0] - pts[-1]).length < 1e-6:
        pts = pts[:-1]  # 첫 점을 끝에 반복해 준 경우
    if len(pts) < (3 if closed else 2):
        raise ValueError("tube는 경로 점이 2개(closed면 3개) 이상 필요하다")
    radii = list(radius) if hasattr(radius, "__len__") else [radius] * len(pts)
    radii = (radii + [radii[-1]] * len(pts))[:len(pts)]
    count = len(pts)
    seg_count = count if closed else count - 1
    seg_dirs = [(pts[(i + 1) % count] - pts[i]).normalized() for i in range(seg_count)]
    # 각 점의 접선과 마이터: 끝점은 세그먼트 방향, 꺾이는 점은 앞뒤 평균 + 꺾임 방향으로 늘림
    tangents, miters = [], []
    for i in range(count):
        d_in = seg_dirs[i - 1] if (closed or i > 0) else None
        d_out = seg_dirs[i] if (closed or i < count - 1) else None
        if d_in is None or d_out is None:
            tangents.append(d_out or d_in)
            miters.append(None)
            continue
        avg = d_in + d_out
        t = avg.normalized() if avg.length > 1e-6 else d_out
        tangents.append(t)
        cos_half = max(math.sqrt(max((1.0 + d_in.dot(d_out)) / 2.0, 0.0)), 0.35)
        bend = d_out - d_in
        bend -= t * bend.dot(t)
        miters.append((bend.normalized(), 1.0 / cos_half) if bend.length > 1e-6 else None)
    # 프레임 평행 이동(parallel transport)으로 단면 뒤틀림 방지
    normal = Vector((0, 0, 1)).cross(tangents[0])
    if normal.length < 1e-6:
        normal = Vector((1, 0, 0))
    normal.normalize()
    bm = bmesh.new()
    rings = []
    prev_t = tangents[0]
    for p, r, t, miter in zip(pts, radii, tangents, miters):
        normal = (prev_t.rotation_difference(t) @ normal).normalized()
        binormal = t.cross(normal).normalized()
        ring = []
        for i in range(segments):
            a = math.tau * i / segments
            offset = (normal * math.cos(a) + binormal * math.sin(a)) * r
            if miter is not None:
                axis, scale = miter
                offset += axis * (offset.dot(axis) * (scale - 1.0))
            ring.append(bm.verts.new(p + offset))
        rings.append(ring)
        prev_t = t
    pairs = list(zip(rings, rings[1:]))
    if closed:
        # 한 바퀴 돈 프레임이 시작 프레임과 어긋날 수 있어, 가장 가깝게 맞는 회전 위치로 잇는다
        last, first = rings[-1], rings[0]
        shift = min(range(segments), key=lambda k: sum(
            (last[i].co - first[(i + k) % segments].co).length for i in range(segments)))
        pairs.append((last, first[shift:] + first[:shift]))
    for lower, upper in pairs:
        for i in range(segments):
            bm.faces.new((lower[i], lower[(i + 1) % segments],
                          upper[(i + 1) % segments], upper[i]))
    if not closed:
        bm.faces.new(tuple(reversed(rings[0])))
        bm.faces.new(tuple(rings[-1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def _is_closed_mesh(mesh) -> bool:
    """모든 엣지가 정확히 2개 면을 공유하면 수밀(닫힌) 메시 — 불리언 유니온 가능."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    closed = len(bm.faces) > 0 and all(len(e.link_faces) == 2 for e in bm.edges)
    bm.free()
    return closed


def _union_solids(solids) -> bpy.types.Mesh:
    """닫힌 오브젝트들을 불리언 유니온(EXACT)으로 병합한 월드 공간 메시를 반환."""
    return _boolean_mesh(solids[0], solids[1:], 'UNION')


def _boolean_mesh(base, others, operation, world=True, use_self=None) -> bpy.types.Mesh:
    """base에 others를 차례로 불리언(EXACT) 적용한 메시를 반환. world=False면 base 로컬 공간.

    bpy.ops 없이 모디파이어 + depsgraph 평가로 굽는다. 결과가 비면 예외.
    use_self=None이면 유니온에만 자기 교차 처리를 켠다(아래 주석)."""
    mods = []
    try:
        for other in others:
            mod = base.modifiers.new(name="LP3D_Boolean", type='BOOLEAN')
            mod.operation = operation
            mod.solver = 'EXACT'
            # 미러·fast join으로 겹친 셸을 여러 개 품은 피연산자는 자기 교차 처리가 없으면 몸통이 통째로 사라진다.
            # 차집합·교집합에는 켜지 않는다 — 느린 판정 경로로 빠져 수 분씩 멈춘다
            mod.use_self = operation == 'UNION' if use_self is None else use_self
            mod.object = other
            mods.append(mod)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(
            base.evaluated_get(depsgraph),
            preserve_all_data_layers=True, depsgraph=depsgraph,
        )
    finally:
        for mod in mods:
            base.modifiers.remove(mod)
    if len(mesh.polygons) == 0:
        bpy.data.meshes.remove(mesh)
        raise RuntimeError(f"불리언 {operation} 결과가 비어 있음")
    if world:
        mesh.transform(base.matrix_world)
    return mesh


def join(objects, name="Joined", mode='fast') -> bpy.types.Object:
    """여러 오브젝트를 하나의 메시로 합친다(트랜스폼 적용됨). 합쳐진 오브젝트를 반환.

    mode='fast'(기본): 기하 변경 없이 단순 병합한다 — 각 파트가 느슨한 덩어리로
    보존되어 후편집이 쉽다. 파트가 겹쳐 완전히 가려진 면은 시스템이 마무리
    단계에서 자동 삭제하므로 자신 있게 겹쳐 파묻어라. 면을 같은 평면에 딱 맞댄 곳도
    겹친 영역을 잘라 한 면만 남긴다(Z-fighting 방지).
    생명체도 fast로 합친다 — 파트를 이웃에 파묻어 두면 이어진 한 몸으로 보이고 각 파트의 쿼드
    흐름(edge loop)이 그대로 남아 후편집이 쉽다.
    mode='union': 닫힌 메시들을 불리언 유니온으로 병합. 교차선을 따라 면이 삼각형·n각형 조각으로
    잘려 쿼드 흐름이 끊기고 트라이가 는다 — 3D 프린트처럼 수밀 단일 셸이 꼭 필요한 경우에만 써라."""
    objects = list(objects)
    # 방금 설정한 location/rotation이 matrix_world에 반영되도록 depsgraph 갱신
    bpy.context.view_layer.update()

    union_mesh = None
    plain_objects = objects
    if mode == 'union' and len(objects) >= 2:
        solids = [o for o in objects if _is_closed_mesh(o.data)]
        if len(solids) >= 2:
            try:
                union_mesh = _union_solids(solids)
                plain_objects = [o for o in objects if o not in solids]
            except Exception:
                union_mesh = None  # 폴백: 전체 단순 병합
                plain_objects = objects

    bm = bmesh.new()
    name = safe_id_name(name, "Joined")
    merged_mesh = bpy.data.meshes.new(name)
    materials = []  # 머티리얼 슬롯 병합 목록

    def _absorb(mesh_data):
        # 소스 슬롯을 병합 목록 인덱스로 리매핑 후 bm에 흡수
        index_map = []
        for mat in mesh_data.materials:
            if mat not in materials:
                materials.append(mat)
            index_map.append(materials.index(mat))
        if index_map:
            for poly in mesh_data.polygons:
                poly.material_index = index_map[poly.material_index]
        bm.from_mesh(mesh_data)

    if union_mesh is not None:
        _absorb(union_mesh)
        bpy.data.meshes.remove(union_mesh)
    for obj in plain_objects:
        mesh_copy = obj.data.copy()
        mesh_copy.transform(obj.matrix_world)
        _absorb(mesh_copy)
        bpy.data.meshes.remove(mesh_copy)

    if union_mesh is not None:
        # 유니온 교차선 잔여 동일평면 면 정리 (색 경계·노멀·심은 보존)
        bmesh.ops.dissolve_limit(
            bm, angle_limit=math.radians(1.0), use_dissolve_boundaries=False,
            verts=bm.verts, edges=bm.edges,
            delimit={'NORMAL', 'MATERIAL', 'SEAM', 'UV'},
        )
    bm.to_mesh(merged_mesh)
    bm.free()
    for mat in materials:
        merged_mesh.materials.append(mat)
    result = bpy.data.objects.new(name, merged_mesh)
    from . import link_to_root
    link_to_root(result)
    # 원본 제거
    for obj in list(objects):
        mesh = obj.data
        bpy.data.objects.remove(obj)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return result
