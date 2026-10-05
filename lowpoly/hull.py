# 단일 껍질(hull) 헬퍼 — 박스를 쌓지 않고 본체를 한 메시로 정의한다
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

from .palette import set_color
from .primitives import _boolean_mesh, _is_closed_mesh, _new_object, prism


def _cleanup_coplanar(mesh):
    """불리언이 남긴 동일평면 잔여 엣지를 녹인다 (색 경계는 UV로 보존)."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.dissolve_limit(
        bm, angle_limit=math.radians(1.0), use_dissolve_boundaries=False,
        verts=bm.verts, edges=bm.edges, delimit={'NORMAL', 'MATERIAL', 'SEAM', 'UV'},
    )
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()


def _remove_object(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _replace_mesh(obj, mesh):
    old = obj.data
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)


def _extent(points, index):
    values = [p[index] for p in points]
    return min(values), max(values)


def silhouette(name="Silhouette", front=((-0.5, 0.0), (0.5, 0.0), (0.5, 1.0), (-0.5, 1.0)),
               side=((-0.5, 0.0), (0.5, 0.0), (0.5, 1.0), (-0.5, 1.0)), top=None,
               location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """정면·측면(·평면) 2D 윤곽을 교차시켜 본체를 한 덩어리로 깎아 낸다 — 박스 쌓기 대신 쓰는 본체 헬퍼.

    컨셉 시트 모델링과 같다: front=(x,z) 정면 윤곽, side=(y,z) 측면 윤곽(-y가 앞),
    top=(x,y) 평면 윤곽(선택, 위에서 본 모양 — 앞뒤로 좁아지는 차체·배 등). 각 윤곽을
    그 시선 방향으로 무한히 압출한 뒤 교집합만 남기므로, 세 방향 실루엣을 모두
    만족하는 최소 면 수의 단일 메시가 된다. 자동차 차체·동물 몸통·의자·칼·
    총·로켓처럼 박스 여러 개를 쌓던 본체에 사용하라. 윤곽은 꼭짓점 6~14개로
    실루엣의 꺾임만 그려라(꺾임 하나가 곧 면 하나다). 원점은 월드 원점 기준 그대로.
    예: 해치백 차체 = lp.silhouette(
            front=[(-0.8,0.15), (0.8,0.15), (0.8,0.7), (0.6,1.25), (-0.6,1.25), (-0.8,0.7)],
            side=[(-1.95,0.15), (1.95,0.15), (1.95,0.7), (1.6,0.8), (1.2,1.25), (-0.4,1.25), (-1.0,0.8), (-1.95,0.75)],
            top=[(-0.7,-1.95), (0.7,-1.95), (0.8,-1.4), (0.8,1.6), (0.7,1.95), (-0.7,1.95), (-0.8,1.6), (-0.8,-1.4)])"""
    front, side = list(front), list(side)
    top = list(top) if top else None
    margin = 1.0
    x_lo, x_hi = _extent(front + (top or []), 0)
    y_lo, y_hi = _extent(side, 0)
    if top:
        ty_lo, ty_hi = _extent(top, 1)
        y_lo, y_hi = min(y_lo, ty_lo), max(y_hi, ty_hi)
    z_lo, z_hi = _extent(front + side, 1)
    # 각 압출 깊이는 다른 윤곽의 범위를 넉넉히 덮어 끝면끼리 동일평면이 되지 않게 한다
    body = prism(name, outline=front, axis='Y',
                 depth=2 * (max(abs(y_lo), abs(y_hi)) + margin))
    cutters = [prism(f"{name}_Side", outline=side, axis='X',
                     depth=2 * (max(abs(x_lo), abs(x_hi)) + margin))]
    if top:
        cutters.append(prism(f"{name}_Top", outline=top, axis='Z', depth=(z_hi - z_lo) + 2 * margin,
                             location=(0, 0, (z_lo + z_hi) / 2)))
    try:
        bpy.context.view_layer.update()
        mesh = _boolean_mesh(body, cutters, 'INTERSECT', world=False)
    except Exception:
        _remove_object(body)
        raise
    finally:
        for cutter in cutters:
            _remove_object(cutter)
    _cleanup_coplanar(mesh)
    _replace_mesh(body, mesh)
    body.data.name = body.name
    body.location = location
    body.rotation_euler = rotation
    return body


def _section_points(section, segments):
    """(h, [(u,v),...]) 또는 (h, w, d[, cu, cv]) 단면 표기를 (h, 점 목록)으로 정규화."""
    h = float(section[0])
    if len(section) == 2:
        pts = [tuple(map(float, p)) for p in section[1]]
        if not pts:
            raise ValueError("loft 단면 점 목록이 비어 있다")
        return h, pts
    w, d = float(section[1]), float(section[2])
    cu, cv = (float(section[3]), float(section[4])) if len(section) >= 5 else (0.0, 0.0)
    if w <= 1e-6 or d <= 1e-6:
        return h, [(cu, cv)]  # 폭 0 = 뾰족한 극점
    # 평평한 변이 축을 향하도록 반 칸 돌리고, 변까지 거리가 w/2·d/2가 되게 외접 반지름을 키운다
    k = 1.0 / math.cos(math.pi / segments)
    pts = []
    for i in range(segments):
        a = math.tau * (i + 0.5) / segments - math.pi / 2
        pts.append((cu + math.cos(a) * w / 2 * k, cv + math.sin(a) * d / 2 * k))
    return h, pts


def _catmull_rom_rows(rows, subdiv):
    """같은 길이의 수치 행 사이마다 subdiv개 행을 Catmull-Rom으로 보간한다."""
    ext = [rows[0]] + rows + [rows[-1]]
    out = [rows[0]]
    for i in range(len(rows) - 1):
        p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
        for step in range(1, subdiv + 1):
            t = step / (subdiv + 1)
            out.append([0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t
                               + (-a + 3 * b - 3 * c + d) * t * t * t)
                        for a, b, c, d in zip(p0, p1, p2, p3)])
        out.append(rows[i + 1])
    return out


_AXIS_UV = {'Z': ((1, 0, 0), (0, 1, 0)), 'Y': ((1, 0, 0), (0, 0, 1)), 'X': ((0, 1, 0), (0, 0, 1))}


def _spine_frames(points, axis):
    """경로 점마다 (원점, u방향, v방향)을 평행 이동 프레임으로 구한다 (단면 비틀림 방지)."""
    if len(points) < 2:
        raise ValueError("loft spine은 점이 2개 이상 필요하다")
    seg = [(points[i + 1] - points[i]) for i in range(len(points) - 1)]
    if any(d.length < 1e-6 for d in seg):
        raise ValueError("loft spine에 겹친 점이 있다")
    seg = [d.normalized() for d in seg]
    tangents = [seg[0]] + [((a + b).normalized() if (a + b).length > 1e-6 else b)
                           for a, b in zip(seg, seg[1:])] + [seg[-1]]
    ref_u, ref_v = (Vector(d) for d in _AXIS_UV.get(axis, _AXIS_UV['Z']))
    t0 = tangents[0]
    u = ref_u - t0 * ref_u.dot(t0)
    if u.length < 1e-4:  # 경로가 u 기준축과 나란하면 v 기준축에서 유도
        u = ref_v.cross(t0)
    u.normalize()
    v = t0.cross(u)
    flip = v.dot(ref_v) < 0  # 시작 프레임이 axis 규칙의 v와 같은 쪽을 보게
    frames = []
    prev = t0
    for p, t in zip(points, tangents):
        u = (prev.rotation_difference(t) @ u).normalized()
        v = t.cross(u).normalized()
        frames.append((p, u, -v if flip else v))
        prev = t
    return frames


def loft(name="Loft", sections=((0.0, 0.6, 0.4), (0.5, 0.8, 0.5), (1.0, 0.3, 0.3)), segments=8,
         axis='Z', smooth=0, spine=None, location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """단면 여러 장을 축을 따라 이어 붙인 단일 껍질 — 단면 모양이 변하는 몸통의 핵심(비원형 lathe).

    sections는 축 방향 순서의 단면 목록. 각 단면은 두 표기 중 하나:
      (h, w, d) 또는 (h, w, d, cu, cv) — 폭 w·깊이 d의 segments각형(변이 축을 향함,
      segments=4면 정확한 w×d 사각형, 8이면 모서리 깎인 팔각). cu,cv는 단면 중심 이동.
      w나 d가 0이면 뾰족한 끝(극점) — 첫·마지막 단면에만 허용.
      (h, [(u,v), ...]) — 직접 그린 윤곽. 모든 단면의 점 개수와 시작점·회전 방향이 같아야 한다.
    axis='Z'면 h=z, (u,v)=(x,y). axis='Y'면 h=y, (u,v)=(x,z). axis='X'면 h=x, (u,v)=(y,z).
    smooth=1~3이면 단면 사이를 곡선 보간해 적은 단면으로 매끈한 배불림을 만든다.
    spine=[(x,y,z), ...](단면 수와 같은 개수)를 주면 각 단면을 그 점에 경로와 수직으로 세운다 —
    휘어진 꼬리·목·뿔·바나나·굽은 칼날처럼 축 자체가 구부러진 형태를 한 번에 만든다(h는 무시).
    이때 (u,v)는 경로 시작점에서 axis 규칙의 방향을 따르고 경로를 따라 비틀림 없이 이어진다.
    폭이 끝으로 줄면 그것이 곧 taper다 — 별도 변형기 없이 단면 w·d로 가늘어짐을 그려라.
    병·보트 선체·물고기·새 몸통·차체 앞뒤 단면 변화·검집에 사용. 면 수 = 단면 수 × segments.
    예: 물고기 몸통 = lp.loft(axis='Y', segments=6, smooth=1, sections=[
            (-0.5, 0.0, 0.0, 0, 0.3), (-0.3, 0.16, 0.3, 0, 0.3), (0.1, 0.2, 0.36, 0, 0.32), (0.45, 0.04, 0.14, 0, 0.3)])"""
    segments = max(3, int(segments))
    parsed = [_section_points(s, segments) for s in sections]
    if any(len(pts) == 1 for _, pts in parsed[1:-1]):
        raise ValueError("loft 극점(폭 0) 단면은 양 끝에만 둘 수 있다")
    if len(parsed) < 2:
        raise ValueError("loft는 단면이 2개 이상 필요하다")
    count = max(len(pts) for _, pts in parsed)
    for h, pts in parsed:
        if len(pts) not in (1, count):
            raise ValueError(f"loft 단면 점 개수가 다르다 (h={h}: {len(pts)}개, 기대 {count}개)")
    poles = [len(pts) == 1 for _, pts in parsed]
    rows = [[h] + [c for p in (pts * count if len(pts) == 1 else pts) for c in p] for h, pts in parsed]
    if spine is not None:
        spine = [tuple(map(float, p)) for p in spine]
        if len(spine) != len(rows):
            raise ValueError(f"loft spine 점 개수({len(spine)})가 단면 개수({len(rows)})와 같아야 한다")
        rows = [row + list(p) for row, p in zip(rows, spine)]
    if smooth > 0 and len(rows) >= 3:
        rows = _catmull_rom_rows(rows, int(smooth))
        poles = [poles[0]] + [False] * (len(rows) - 2) + [poles[-1]]

    def to3(u, v, h):
        if axis == 'Y':
            return (u, h, v)
        if axis == 'X':
            return (h, u, v)
        return (u, v, h)

    frames = _spine_frames([Vector(row[-3:]) for row in rows], axis) if spine is not None else None

    def place(row_index, u, v):
        if frames is None:
            return to3(u, v, rows[row_index][0])
        origin, u_dir, v_dir = frames[row_index]
        return origin + u_dir * u + v_dir * v

    bm = bmesh.new()
    rings = []
    for index, (row, pole) in enumerate(zip(rows, poles)):
        if pole:
            rings.append(bm.verts.new(place(index, row[1], row[2])))
            continue
        rings.append([bm.verts.new(place(index, row[1 + 2 * i], row[2 + 2 * i])) for i in range(count)])
    for lower, upper in zip(rings, rings[1:]):
        if isinstance(lower, list) and isinstance(upper, list):
            for i in range(count):
                bm.faces.new((lower[i], lower[(i + 1) % count], upper[(i + 1) % count], upper[i]))
        elif isinstance(lower, list):
            for i in range(count):
                bm.faces.new((lower[i], lower[(i + 1) % count], upper))
        elif isinstance(upper, list):
            for i in range(count):
                bm.faces.new((lower, upper[(i + 1) % count], upper[i]))
    if isinstance(rings[0], list):
        bm.faces.new(tuple(reversed(rings[0])))
    if isinstance(rings[-1], list):
        bm.faces.new(tuple(rings[-1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def _first_uv(obj):
    """오브젝트에 입혀진 팔레트 색(첫 루프 UV). 색을 안 입혔으면 None."""
    layer = obj.data.uv_layers.active
    if layer is None or not layer.data:
        return None
    return tuple(layer.data[0].uv)


def _difference(obj, cutter):
    mesh = _boolean_mesh(obj, [cutter], 'DIFFERENCE', world=False)
    _cleanup_coplanar(mesh)
    _replace_mesh(obj, mesh)


# 새김 표식은 별도 UV 레이어에 둔다 — 색(UVMap)을 지우지 않아야 돋을새김 안쪽이 원래 색을 지킨다
_MARK_LAYER = "LP3D_Mark"
_SURFACE, _REGION, _WALL = 0.0, 1.0, 2.0   # 바깥 표면 / cutter 안의 표면 / cutter에서 온 면


def _with_mark(obj, value):
    """obj 복제에 표식 레이어를 칠해 세션 컬렉션에 링크한다 (원본은 건드리지 않는다)."""
    from . import link_to_root
    copy = obj.copy()
    copy.data = obj.data.copy()
    mesh = copy.data
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    color_layer = mesh.uv_layers.active.name
    layer = mesh.uv_layers.get(_MARK_LAYER) or mesh.uv_layers.new(name=_MARK_LAYER)
    for item in layer.data:
        item.uv = (value, value)
    mesh.uv_layers.active = mesh.uv_layers[color_layer]
    link_to_root(copy)
    return copy


def _imprint(obj, cutter):
    """cutter 윤곽을 obj 표면에 새긴 (bmesh, 색 레이어, 표식 레이어)를 돌려준다.

    바깥(차집합)과 안쪽 마개(교집합)를 따로 구해 교차선에서 다시 용접한다. 표면을 오프셋한
    껍질을 쓰지 않으므로 복잡한 본체에서도 자기 교차가 생기지 않는다(EXACT 느린 경로 회피)."""
    outer_src = _with_mark(obj, _SURFACE)
    plug_src = _with_mark(obj, _REGION)
    marked = _with_mark(cutter, _WALL)
    try:
        bpy.context.view_layer.update()
        outside = _boolean_mesh(outer_src, [marked], 'DIFFERENCE', world=False)
        try:
            plug = _boolean_mesh(plug_src, [marked], 'INTERSECT', world=False)
        except RuntimeError:
            plug = None  # cutter가 표면에 닿지 않음
    finally:
        for temp in (outer_src, plug_src, marked):
            _remove_object(temp)
    bm = bmesh.new()
    bm.from_mesh(outside)
    bpy.data.meshes.remove(outside)
    if plug is not None:
        bm.from_mesh(plug)
        bpy.data.meshes.remove(plug)
    color = bm.loops.layers.uv.get("UVMap") or bm.loops.layers.uv.active
    mark = bm.loops.layers.uv.get(_MARK_LAYER)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if _mark_of(f, mark) == _WALL], context='FACES')
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    return bm, color, mark


def _mark_of(face, mark):
    if mark is None:
        return _SURFACE
    return round(face.loops[0][mark].uv[0])


def _paint_faces(faces, layer, uv):
    for face in faces:
        for loop in face.loops:
            loop[layer].uv = uv


def _write_imprint(obj, bm, mark):
    if mark is not None:
        bm.loops.layers.uv.remove(mark)
    mesh = bpy.data.meshes.new(obj.data.name)
    bm.to_mesh(mesh)
    bm.free()
    for mat in obj.data.materials:
        mesh.materials.append(mat)
    _cleanup_coplanar(mesh)
    _replace_mesh(obj, mesh)


def _even_inset_is_safe(bm, region, thickness) -> bool:
    """균일 오프셋 인셋을 복제본에 시험해 폭주하지 않는지 본다.

    균일 오프셋은 모서리마다 1/sin(반각)배로 밀어내므로, 불리언이 남긴 가는 조각 면에서는
    버텍스가 수십만 m 날아간다. 이동량이 두께의 4배를 넘으면 안전하지 않다고 본다."""
    bm.faces.ensure_lookup_table()
    trial = bm.copy()
    trial.faces.ensure_lookup_table()
    faces = [trial.faces[f.index] for f in region]
    verts = {v for f in faces for v in f.verts}
    before = {v: v.co.copy() for v in verts}
    bmesh.ops.inset_region(trial, faces=faces, thickness=thickness, depth=0.0,
                           use_even_offset=True, use_boundary=True)
    limit = thickness * 4.0 + 1e-6
    safe = all((v.co - before[v]).length <= limit for v in verts)
    trial.free()
    return safe


def _plane_offset(normals, offset):
    """인접 면들이 각자 노멀 방향으로 offset만큼 이동하도록 하는 버텍스 변위(최소제곱).

    같은 평면끼리는 정확히 n·offset이 되어 평면성이 유지되고, 모서리(평면 2~3개)에서는
    두 면 모두 offset만큼 물러나는 점을 찾는다. 날카로운 각에서 튀지 않게 3배로 자른다."""
    m = Matrix(((0.0,) * 3,) * 3)
    rhs = Vector()
    for n in normals:
        m += Matrix(((n.x * n.x, n.x * n.y, n.x * n.z),
                     (n.y * n.x, n.y * n.y, n.y * n.z),
                     (n.z * n.x, n.z * n.y, n.z * n.z)))
        rhs += n * offset
    eps = 1e-4 * len(normals)
    for i in range(3):
        m[i][i] += eps  # 평면 하나뿐이면 나머지 방향은 0으로 (특이 행렬 방지)
    d = m.inverted_safe() @ rhs
    limit = abs(offset) * 3.0
    return d if d.length <= limit else d.normalized() * limit


def _region_inset(bm, region, thickness, offset):
    """region을 표면 위에서 thickness만큼 인셋하고 offset만큼 바깥(+)/안쪽(-)으로 옮긴다. 새 면 반환.

    옮기기는 inset의 depth(셸 계수 배율) 대신 _plane_offset으로 한다 — 조각 면이 섞여도 각 면이
    자기 평면 그대로 물러나 금 간 선이 생기지 않고, 이동량이 offset의 3배를 넘지 않는다."""
    even = thickness > 0 and _even_inset_is_safe(bm, region, thickness)
    new = bmesh.ops.inset_region(bm, faces=region, thickness=thickness, depth=0.0,
                                 use_even_offset=even, use_boundary=True)["faces"]
    if offset:
        region_set = set(region)
        moves = []
        for v in {v for f in region for v in f.verts}:
            normals = [f.normal.copy() for f in v.link_faces if f in region_set and f.calc_area() > 1e-10]
            if normals:
                moves.append((v, _plane_offset(normals, offset)))
        for v, d in moves:
            v.co += d
        bm.normal_update()
    return new


def _dominant_plane(region, share=0.7, angle=20.0):
    """영역 면적의 share 이상이 한 평면이면 그 평면의 면만 남긴다.

    LLM이 그린 커터는 앞유리 면을 넘어 A필러·지붕 모서리까지 걸치기 쉽다. 여러 평면에 걸친
    영역을 인셋하면 유리 면이 접혀 금 간 선이 생기고 모서리로 조각이 삐져나온다. 원통 띠처럼
    원래 휘어 있는 영역(한 평면 비율이 낮음)은 그대로 둔다."""
    if not region:
        return region
    areas = {f: f.calc_area() for f in region}
    total = sum(areas.values()) or 1.0
    base = max(region, key=lambda f: areas[f]).normal
    limit = math.cos(math.radians(angle))
    plane = [f for f in region if f.normal.dot(base) >= limit]
    if sum(areas[f] for f in plane) / total >= share and len(plane) < len(region):
        return plane
    return region


def _merge_region(bm, region, mark):
    """새김 영역 안의 동일평면 조각 면을 녹여 합친다 — 불리언·미러 이음새가 남긴 가는 면 정리."""
    region_set = set(region)
    inner = [e for e in {e for f in region for e in f.edges}
             if len(e.link_faces) == 2 and all(g in region_set for g in e.link_faces)]
    if inner:
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(0.3), use_dissolve_boundaries=False,
                                 verts=[], edges=inner, delimit=set())
    return [f for f in bm.faces if _mark_of(f, mark) == _REGION]


def _recess(obj, cutter, depth, frame, inner_uv, wall_uv):
    """cutter가 덮은 표면을 새긴 뒤 표면 위에서 인셋·내림으로 판다.

    깊이와 틀 폭을 모두 바닥 면 위에서 재므로 경사면·곡면에서도 일정하고 좌우가 대칭이다."""
    bm, color, mark = _imprint(obj, cutter)
    region = _dominant_plane(_merge_region(bm, [f for f in bm.faces if _mark_of(f, mark) == _REGION], mark))
    if region:
        walls = []
        if frame > 0:
            walls += _region_inset(bm, region, 0.0, -depth * 0.4)    # 틀 단차
            walls += _region_inset(bm, region, frame, 0.0)           # 평평한 틀 띠
            walls += _region_inset(bm, region, 0.0, -depth * 0.6)    # 유리 단차
        else:
            walls += _region_inset(bm, region, 0.0, -depth)
        _paint_faces(region, color, inner_uv)
        _paint_faces(walls, color, wall_uv)
    _write_imprint(obj, bm, mark)


def cut(obj, cutter, depth=None, frame=0.0, frame_color=None, keep_cutter=False) -> bpy.types.Object:
    """cutter 모양만큼 obj를 파낸다(불리언 차집합). obj를 반환하고 cutter는 삭제된다.

    판을 덧붙이는 대신 파서 만들어라: 창문·문 홈, 휠 아치, 손잡이 구멍, 칼집 홈,
    계단식 파단면, 상자 뚜껑 틈. cutter는 lp.box/prism/cylinder 등 닫힌 메시로 만들어
    obj 표면을 넉넉히 관통하게 배치한다. cutter에 미리 set_color 하면 파인 안쪽 면이 그 색이 된다
    (어두운 홈·유리창 표현) — 그러려면 obj 색을 cut 전에 입혀라.
    depth=None(기본): cutter 부피 전체를 파낸다(구멍·아치·관통 홈).
    depth=0.02~0.06: cutter가 덮는 영역의 표면을 그 깊이만큼만 판다 — 홈 바닥이 표면을 따라
      기울므로 경사진 차창·곡면 선체·기울어진 벽에서도 유리면이 표면과 나란하다.
      창·문·패널은 이것을 써라. cutter는 표면 앞뒤로 충분히 길게(관통하게) 만들면 된다.
    frame=0.03~0.06: 창틀. 파인 영역 둘레에 이 폭의 띠를 frame_color로 얕게(depth의 40%) 남기고
      안쪽만 depth까지 판다 — 틀과 유리가 같은 윤곽에서 나오므로 서로 어긋나지 않는다.
      창틀을 box로 따로 둘러 붙이지 마라. frame만 주면 depth는 0.05로 잡는다.
    예: 휠 아치 = lp.cut(body, lp.cylinder("Arch", radius=0.42, depth=2.0, segments=10,
                          location=(0, 1.2, 0.3), rotation=(0, 1.5708, 0)))
        틀 있는 옆창 = lp.cut(body, glass_prism, depth=0.04, frame=0.04, frame_color=(0.1, 0.1, 0.1))"""
    if not _is_closed_mesh(cutter.data):
        raise ValueError(f"cut의 cutter는 닫힌 메시여야 한다: {cutter.name} (plane 등 열린 면 불가)")
    if frame and depth is None:
        depth = 0.05
    bpy.context.view_layer.update()
    try:
        if depth is None:
            _difference(obj, cutter)
        else:
            body_uv = _first_uv(obj) or (0.5, 0.5)
            inner_uv = _first_uv(cutter) or body_uv
            wall_uv = inner_uv
            if frame and frame > 0 and frame_color is not None:
                set_color(cutter, frame_color)
                wall_uv = _first_uv(cutter)
            _recess(obj, cutter, depth, frame or 0.0, inner_uv, wall_uv)
    finally:
        if not keep_cutter:
            _remove_object(cutter)
    return obj


def emboss(obj, cutter, height=0.03, ring=0.0, color=None) -> bpy.types.Object:
    """cutter가 덮는 obj 표면을 height만큼 도드라지게 올린다 — cut(depth)의 반대. cutter는 삭제된다.

    표면을 따라 올리므로 곡면·경사면에서도 몰딩이 표면에 딱 붙는다. box를 덧대지 말고 이것을 써라:
      띠(trim): 본체를 가로지르는 얇은 판 cutter(예: box size=(3, 5, 0.06))로 차체 몰딩·배 측면
        보호대·통의 금속 띠·벨트를 한 바퀴 두른다.
      패널: 문짝·해치·명판·근육 덩어리를 cutter 모양 그대로 올린다.
      ring=0.02~0.05: 영역 전체가 아니라 둘레 띠만 올린다 — 도드라진 창틀·액자·테두리.
    color(없으면 cutter 색, cutter도 무색이면 obj 색)가 올라온 면의 색이 된다. height 0.01~0.06 권장.
    예: 배 측면 보호대 = lp.emboss(hull, lp.box("Rail", size=(3, 6, 0.08), location=(0, 0, 0.9)),
                               height=0.05, color=(0.2, 0.3, 0.7))"""
    if not _is_closed_mesh(cutter.data):
        raise ValueError(f"emboss의 cutter는 닫힌 메시여야 한다: {cutter.name} (plane 등 열린 면 불가)")
    bpy.context.view_layer.update()
    try:
        if color is not None:
            set_color(cutter, color)
        raised_uv = _first_uv(cutter) or _first_uv(obj) or (0.5, 0.5)
        bm, layer, mark = _imprint(obj, cutter)
        region = _merge_region(bm, [f for f in bm.faces if _mark_of(f, mark) == _REGION], mark)
        if region:
            if ring and ring > 0:
                inner = list(region)
                band = _region_inset(bm, inner, ring, 0.0)        # 둘레 띠 / 안쪽은 원래 높이·색
                region = band
            raised = _region_inset(bm, region, 0.0, height) + region
            _paint_faces(raised, layer, raised_uv)
        _write_imprint(obj, bm, mark)
    finally:
        _remove_object(cutter)
    return obj


def attach(part, target, direction=(0, 1, 0), sink=0.01, align=False) -> bpy.types.Object:
    """part를 target 표면에 정확히 붙인다 — 눈·코·단추·손잡이·리벳·간판처럼 표면에 얹는 부착물용.

    part의 현재 위치에서 direction 방향으로 광선을 쏴 표면을 찾고, part의 **뒷면**(표면 쪽 끝)이
    표면에서 sink만큼 파묻히도록 옮긴다 — part 두께는 전부 표면 밖으로 나온다. part는 표면 바깥
    정면에 대충 놓고 표면을 향하는 방향을 주면 된다: 정면(-y 쪽)에서 붙이면 direction=(0,1,0),
    위에서 내려 꽂으면 (0,0,-1), +x 옆면이면 (-1,0,0). 못 맞히면 반대 방향, 그래도 없으면 가장 가까운 표면점에 붙인다.
    align=True면 part의 정면(-Y 축)이 표면 노멀을 향하도록 돌린다 — 곡면에 붙는 눈·명판·버튼.
    sink는 0.005~0.02. part를 반환한다.
    표면에 바로 붙는 것에만 써라. 팔·기둥 끝에 달린 것(백미러·가로등 갓·깃발)은 attach하지 말고
    지지 파트 끝 좌표에 직접 놓아라 — attach하면 지지 파트에서 떨어져 표면으로 끌려간다.
    좌우 한 쌍(전조등·백미러·손잡이)은 +x 쪽 하나만 attach한 뒤 mirror_x 할 목록에 넣어라.
    예: eye = lp.attach(lp.sphere("Eye", radius=0.04, location=(0.1, -0.5, 1.05)), head, align=True)"""
    bpy.context.view_layer.update()
    inv = target.matrix_world.inverted()
    origin = part.matrix_world.translation.copy()
    hit = None
    for sign in (1.0, -1.0):
        d = Vector(direction).normalized() * sign
        local_o = inv @ origin
        local_d = (inv.to_3x3() @ d).normalized()
        ok, loc, normal, _ = target.ray_cast(local_o, local_d)
        if ok:
            hit = (target.matrix_world @ loc,
                   (target.matrix_world.to_3x3().inverted().transposed() @ normal).normalized())
            break
    if hit is None:
        # 광선이 빗나가면(대충 놓은 위치가 표면 옆으로 비껴남) 가장 가까운 표면점에 붙인다
        ok, loc, normal, _ = target.closest_point_on_mesh(inv @ origin)
        if not ok:
            raise ValueError(f"attach: {target.name}에 붙일 표면이 없다")
        hit = (target.matrix_world @ loc,
               (target.matrix_world.to_3x3().inverted().transposed() @ normal).normalized())
    point, normal = hit
    if align:
        rot = Vector((0, -1, 0)).rotation_difference(normal)
        part.rotation_euler = (rot @ part.rotation_euler.to_quaternion()).to_euler()
        bpy.context.view_layer.update()
    # 원점 기준 뒷면까지 거리 — 원점을 표면에 두면 중심이 놓여 두께의 절반이 파묻힌다
    mw = part.matrix_world
    back = min(((mw @ v.co) - mw.translation).dot(normal) for v in part.data.vertices) \
        if part.data.vertices else 0.0
    part.location = point - normal * (sink + back)
    bpy.context.view_layer.update()
    return part
