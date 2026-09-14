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
    """원기둥. 로우폴리는 segments 6~12 권장. depth는 Z축 높이, 원점은 중심."""
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius, radius2=radius, depth=depth,
    )
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def cone(name="Cone", radius_bottom=0.5, radius_top=0.0, depth=1.0, segments=8,
         location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """원뿔/절두체. radius_top>0이면 절두체(테이퍼 기둥)가 된다."""
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius_bottom, radius2=radius_top, depth=depth,
    )
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def sphere(name="Sphere", radius=0.5, subdivisions=1,
           location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """아이코스피어. 로우폴리는 subdivisions 1~2만 사용할 것 (1=80트라이)."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


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
    for lower, upper in zip(rings, rings[1:]):
        if isinstance(lower, list) and isinstance(upper, list):
            for i in range(segments):
                bm.faces.new((lower[i], lower[(i + 1) % segments],
                              upper[(i + 1) % segments], upper[i]))
        elif isinstance(lower, list):  # 위가 극점
            for i in range(segments):
                bm.faces.new((lower[i], lower[(i + 1) % segments], upper))
        elif isinstance(upper, list):  # 아래가 극점
            for i in range(segments):
                bm.faces.new((lower, upper[(i + 1) % segments], upper[i]))
    # 끝이 극점이 아니면 n각형으로 캡
    if isinstance(rings[0], list):
        bm.faces.new(tuple(reversed(rings[0])))
    if isinstance(rings[-1], list):
        bm.faces.new(tuple(rings[-1]))
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


def tube(name="Tube", points=((0, 0, 0), (0, 0, 1)), radius=0.05, segments=6,
         location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """폴리라인 경로를 따라 원형 단면을 스윕한 튜브(양 끝 캡 포함).

    손잡이·나뭇가지·파이프·난간·가로등 기둥처럼 가늘고 긴 형태에 사용.
    radius는 숫자 하나(균일) 또는 점 개수와 같은 길이의 목록(끝으로 가늘어지는 가지).
    예: 굽은 가지 = lp.tube(points=[(0,0,0), (0.1,0,0.5), (0.35,0,0.8)],
                            radius=[0.08, 0.05, 0.02], segments=6)"""
    pts = [Vector(p) for p in points]
    if len(pts) < 2:
        raise ValueError("tube는 경로 점이 2개 이상 필요하다")
    radii = list(radius) if hasattr(radius, "__len__") else [radius] * len(pts)
    # 각 점의 접선: 끝점은 세그먼트 방향, 중간점은 앞뒤 평균(마이터 조인트)
    seg_dirs = [(pts[i + 1] - pts[i]).normalized() for i in range(len(pts) - 1)]
    tangents = [seg_dirs[0]]
    for i in range(1, len(pts) - 1):
        avg = seg_dirs[i - 1] + seg_dirs[i]
        tangents.append(avg.normalized() if avg.length > 1e-6 else seg_dirs[i])
    tangents.append(seg_dirs[-1])
    # 프레임 평행 이동(parallel transport)으로 단면 뒤틀림 방지
    normal = Vector((0, 0, 1)).cross(tangents[0])
    if normal.length < 1e-6:
        normal = Vector((1, 0, 0))
    normal.normalize()
    bm = bmesh.new()
    rings = []
    prev_t = tangents[0]
    for p, r, t in zip(pts, radii, tangents):
        normal = (prev_t.rotation_difference(t) @ normal).normalized()
        binormal = t.cross(normal).normalized()
        ring = []
        for i in range(segments):
            a = math.tau * i / segments
            ring.append(bm.verts.new(p + (normal * math.cos(a) + binormal * math.sin(a)) * r))
        rings.append(ring)
        prev_t = t
    for lower, upper in zip(rings, rings[1:]):
        for i in range(segments):
            bm.faces.new((lower[i], lower[(i + 1) % segments],
                          upper[(i + 1) % segments], upper[i]))
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
    """닫힌 오브젝트들을 불리언 유니온(EXACT)으로 병합한 월드 공간 메시를 반환.

    bpy.ops 없이 모디파이어 + depsgraph 평가로 굽는다. 결과가 비면 예외."""
    base = solids[0]
    mods = []
    try:
        for other in solids[1:]:
            mod = base.modifiers.new(name="LP3D_Union", type='BOOLEAN')
            mod.operation = 'UNION'
            mod.solver = 'EXACT'
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
        raise RuntimeError("불리언 유니온 결과가 비어 있음")
    mesh.transform(base.matrix_world)
    return mesh


def join(objects, name="Joined", mode='fast') -> bpy.types.Object:
    """여러 오브젝트를 하나의 메시로 합친다(트랜스폼 적용됨). 합쳐진 오브젝트를 반환.

    mode='fast'(기본): 기하 변경 없이 단순 병합한다 — 각 파트가 느슨한 덩어리로
    보존되어 후편집이 쉽다. 파트가 겹쳐 완전히 가려진 면은 시스템이 마무리
    단계에서 자동 삭제하므로 자신 있게 겹쳐 파묻어라.
    mode='union': 닫힌 메시들을 불리언 유니온으로 병합(교차선에서 면이 잘려
    면 수가 늘 수 있음 — 수밀 단일 셸이 꼭 필요한 경우에만)."""
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
