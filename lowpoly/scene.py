# 씬(배경 공간) 구성 헬퍼 — 지형·인스턴스 배치·벽/길·지면 스냅
#
# 이 모듈의 함수는 배경 모드 프롬프트에만 노출된다 (lowpoly.SCENE_API).
# 시그니처와 독스트링이 그대로 에이전트용 API 레퍼런스가 되므로 단위(m)를 명시한다.
import math
import random

import bmesh
import bpy
from mathutils import Vector

from .names import safe_id_name

from .primitives import _new_object, box, join

_MAX_CELLS = 48           # 지형 셀 수 상한 (면수 폭주 방지)
_PATH_MIN_THICKNESS = 0.03  # 지면 판 과장 금지 규칙
_PATH_MAX_THICKNESS = 0.08


# --- 내부 유틸 ---

def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _noise_lattice(seed: int, freq: int):
    """freq+1 칸 격자에 0~1 난수를 채운 값 노이즈 래티스."""
    rng = random.Random(seed)
    return [[rng.random() for _ in range(freq + 1)] for _ in range(freq + 1)]


def _sample_lattice(lat, u: float, v: float) -> float:
    """래티스를 (u,v)∈[0,1]에서 이중선형(스무스스텝) 보간한다."""
    freq = len(lat) - 1
    x, y = u * freq, v * freq
    ix = min(int(x), freq - 1)
    iy = min(int(y), freq - 1)
    tx = _smoothstep(x - ix)
    ty = _smoothstep(y - iy)
    lower = lat[iy][ix] * (1.0 - tx) + lat[iy][ix + 1] * tx
    upper = lat[iy + 1][ix] * (1.0 - tx) + lat[iy + 1][ix + 1] * tx
    return lower * (1.0 - ty) + upper * ty


def _edge_falloff(ix: int, iy: int, cx: int, cy: int) -> float:
    """가장자리를 z=0으로 눌러 씬 경계를 평평하게 만드는 감쇠 계수."""
    ramp = max(1.0, min(cx, cy) * 0.12)
    dist = min(ix, cx - ix, iy, cy - iy)
    return _smoothstep(min(dist / ramp, 1.0))


def _pair(value, default_ratio=1.0):
    """스칼라 또는 2원소 시퀀스를 (x, y) 튜플로 정규화한다."""
    if hasattr(value, "__len__"):
        return float(value[0]), float(value[1])
    return float(value), float(value) * default_ratio


def _point_in_polygon(x: float, y: float, poly) -> bool:
    """짝홀 교차 판정. poly는 [(x,y), ...] 3점 이상."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _dist_to_polygon_edge(x: float, y: float, poly) -> float:
    """점에서 다각형 경계까지의 최단 거리(m)."""
    best = float("inf")
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        t = 0.0 if seg < 1e-12 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / seg))
        px, py = ax + dx * t, ay + dy * t
        best = min(best, math.hypot(x - px, y - py))
    return best


def _polyline(points):
    """(x,y) 목록을 Vector 2D 목록으로 만들고 중복점을 제거한다."""
    pts = [Vector((float(p[0]), float(p[1]))) for p in points]
    if not pts:
        return []
    clean = [pts[0]]
    for p in pts[1:]:
        if (p - clean[-1]).length > 1e-6:
            clean.append(p)
    return clean


def _height_grid(heights):
    """heights를 그대로 쓸 수 있으면 2D 실수 리스트로, 아니면 None을 돌려준다.

    빈 리스트나 빈 행이 들어오면 인덱싱에서 터진다 — 에이전트가 자리만 채운 값을
    보내는 일이 흔하므로, 못 쓰는 값은 조용히 노이즈 생성으로 되돌린다."""
    if not isinstance(heights, (list, tuple)) or not heights:
        return None
    rows = []
    for row in heights:
        if not isinstance(row, (list, tuple)) or not row:
            return None
        try:
            rows.append([float(v) for v in row])
        except (TypeError, ValueError):
            return None
    return rows


def _kit_root() -> bpy.types.Collection:
    """kit() 조회 대상 컬렉션 — set_kit_collection으로 지정된 것, 없으면 세션 root."""
    from . import _kit_collection_name, root
    if _kit_collection_name:
        coll = bpy.data.collections.get(_kit_collection_name)
        if coll is not None:
            return coll
    return root()


# --- 공개 API ---

def terrain(name="Terrain", size=(40.0, 40.0), cells=(16, 16), heights=None,
            relief=0.4, seed=0, outline=None, base=0.0, elevation=0.0,
            color=None, side_color=None) -> bpy.types.Object:
    """원점 중심의 지면을 만든다(미터 단위). 생성된 오브젝트를 반환.
    지면은 격자가 아니라 **윤곽 다각형 하나**다 — outline=[(x,y), ...](6~12점, m)을 주면
    모서리를 둥글게 다듬어 해안선·절벽 끝 같은 매끈한 테두리가 되고, 없으면 size=(x,y) 직사각형이다.
    relief=0이면 윗면은 면 하나(평지)이고, relief>0(기복 폭 m)일 때만 안쪽에 성긴 정점을 흩어
    완만한 언덕을 만든다(cells는 그 정점 밀도: 긴 변을 몇 칸으로 볼지, 각 축 최대 48). 테두리 높이는 elevation이다.
    **base>0**(m)이면 테두리 아래로 거친 바위 절벽 받침(떠 있는 섬·디오라마 받침·강가 절벽)을
    그 두께만큼 만들고 바닥을 닫는다. **elevation**은 지면 전체를 들어 올린다 — 언덕 위 고지대는
    작은 outline으로 lp.terrain(..., elevation=1.5, base=1.5)를 한 번 더 호출해 본 지면에 파묻어 세운다.
    color / side_color(r,g,b 0~1)를 주면 윗면·절벽면을 각각 칠한다 — 이 둘로 칠하고 나중에 set_color로
    전체를 덮어 칠하지 마라(절벽 색이 사라진다). heights[행][열](크기 (행+1)x(열+1), m)을 주면 예전처럼
    cells 격자에 그 높이를 그대로 쓴다(outline·base 무시). 지형 위 오브젝트는 lp.ground_snap으로 높이를 맞춘다.
    예: ground = lp.terrain("Isle", outline=[(-14,-8), (-4,-12), (10,-9), (15,2), (8,11), (-6,12), (-15,4)],
                            relief=0.3, base=2.5, color=(0.45, 0.62, 0.3), side_color=(0.5, 0.5, 0.55))
    예: hill = lp.terrain("Hill", outline=[(4,4), (10,5), (11,10), (5,11)], elevation=1.5, base=1.8,
                           color=(0.45, 0.62, 0.3), side_color=(0.5, 0.5, 0.55))"""
    if heights is not None:
        obj = _grid_terrain(name, size, cells, heights, relief, seed, outline)
        if color is not None:
            from .palette import set_color
            set_color(obj, color)
        return obj
    return _island_terrain(name, size, cells, relief, seed, outline, float(base or 0.0),
                           float(elevation or 0.0), color, side_color)


def _island_terrain(name, size, cells, relief, seed, outline, base, elevation, color, side_color):
    from mathutils.geometry import delaunay_2d_cdt

    from . import terrain_shape as ts
    from .palette import set_color

    if outline is not None:
        poly = ts.ccw(outline)
        if len(poly) < 3 or abs(ts.signed_area(poly)) < 1e-4:
            raise ValueError("terrain outline은 넓이가 있는 점 3개 이상의 다각형이어야 한다")
        # 사용자가 준 6~12점 꺾은선은 도면처럼 각져 보인다 — 둥글게 다듬는다
        poly = ts.chaikin(poly, 2 if len(poly) >= 6 else 1)   # 점이 적으면 한 번만 — 원처럼 뭉개지지 않게
    else:
        sx, sy = float(size[0]) / 2, float(size[1]) / 2
        poly = [(-sx, -sy), (sx, -sy), (sx, sy), (-sx, sy)]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-3)
    relief = max(0.0, float(relief or 0.0))
    divisions = max(2, min(int(max(cells)) if cells else 16, _MAX_CELLS))
    # 격자 한 칸에 삼각형 2개였으므로 정점 간격을 두 칸으로 잡아야 예전 cells와 면수가 비슷하다 —
    # 로우폴리 지면은 그보다 촘촘할 이유가 없다
    spacing = span / max(2, divisions // 2)
    # 테두리 정점 간격: 기복이 있으면 안쪽 정점과 비슷하게, 평지면 윤곽 곡선만 살릴 만큼
    rim = ts.resample(poly, spacing if relief > 0 else span / 12)
    inner = ts.interior_points(rim, spacing, seed) if relief > 0 else []

    coarse = _noise_lattice(seed, 3)
    fine = _noise_lattice(seed + 977, 6)
    ramp = max(1.0, span * 0.12)
    x0, y0 = min(xs), min(ys)

    def height(x, y):
        if relief <= 0:
            return elevation
        u, v = (x - x0) / span, (y - y0) / span
        n = _sample_lattice(coarse, u, v) * 0.67 + _sample_lattice(fine, u, v) * 0.33
        fall = _smoothstep(min(ts.edge_distance(x, y, rim) / ramp, 1.0))
        return elevation + (n - 0.5) * relief * fall

    bm = bmesh.new()
    count = len(rim)
    if inner:
        coords = [Vector(p) for p in rim + inner]
        out_verts, _e, out_faces, _ov, _oe, _of = delaunay_2d_cdt(
            coords, [], [list(range(count))], 1, 1e-6)
        verts = [bm.verts.new((v.x, v.y, height(v.x, v.y))) for v in out_verts]
        for tri in out_faces:
            try:
                bm.faces.new([verts[i] for i in tri])
            except ValueError:
                pass
        # CDT 출력 정점 순서는 입력과 다르다 — 테두리 고리는 위치로 되찾는다
        rim_verts = []
        for x, y in rim:
            rim_verts.append(min(verts, key=lambda v: (v.co.x - x) ** 2 + (v.co.y - y) ** 2))
    else:
        rim_verts = [bm.verts.new((x, y, elevation)) for x, y in rim]
        bm.faces.new(rim_verts)
    top_faces = list(bm.faces)

    side_faces = []
    if base > 0:
        # 두 단 고리로 바위 절벽을 만든다: 중간 단은 거칠게 들쭉날쭉, 바닥은 안쪽으로 모인다
        mid = ts.offset_ring(rim, base * 0.12, jitter=base * 0.12, seed=seed + 11)
        low = ts.offset_ring(rim, base * 0.45, jitter=base * 0.15, seed=seed + 23)
        rng = random.Random(seed + 31)
        mid_verts = [bm.verts.new((x, y, elevation - base * (0.45 + (rng.random() - 0.5) * 0.2)))
                     for x, y in mid]
        low_verts = [bm.verts.new((x, y, elevation - base)) for x, y in low]
        for ring_a, ring_b in ((rim_verts, mid_verts), (mid_verts, low_verts)):
            for i in range(count):
                j = (i + 1) % count
                quad = [ring_a[i], ring_b[i], ring_b[j], ring_a[j]]
                try:
                    side_faces.append(bm.faces.new(quad))
                except ValueError:
                    pass
        try:
            side_faces.append(bm.faces.new(list(reversed(low_verts))))
        except ValueError:
            pass
    if side_faces:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)   # 받침까지 닫힌 덩어리 — 바깥 기준이 선다
    bm.normal_update()
    for face in top_faces:
        if face.normal.z < 0:   # 윗면은 반드시 위를 본다
            face.normal_flip()
    bm.faces.index_update()
    top_ids = [f.index for f in top_faces]
    side_ids = [f.index for f in side_faces]
    obj = _new_object(name, bm, (0, 0, 0), (0, 0, 0), (1, 1, 1))
    for poly_ in obj.data.polygons:
        poly_.use_smooth = False  # 로우폴리 플랫 셰이딩
    if color is not None:
        set_color(obj, color, faces=top_ids)
    if side_faces:
        set_color(obj, side_color if side_color is not None else (0.5, 0.5, 0.55), faces=side_ids)
    return obj


def _grid_terrain(name, size, cells, heights, relief, seed, outline):
    """heights를 직접 준 경우의 예전 격자 지형."""
    cx = max(1, min(int(cells[0]), _MAX_CELLS))
    cy = max(1, min(int(cells[1]), _MAX_CELLS))
    poly = None
    if outline is not None:
        poly = [(float(p[0]), float(p[1])) for p in outline]
        if len(poly) < 3:
            raise ValueError("terrain outline은 점이 3개 이상이어야 한다")
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        # 경계 상자를 격자로 덮고 원점은 상자 중심으로 옮긴다
        sx, sy = max(xs) - min(xs), max(ys) - min(ys)
        ox, oy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        poly = [(x - ox, y - oy) for x, y in poly]
    else:
        sx, sy = float(size[0]), float(size[1])
        ox, oy = 0.0, 0.0

    grid = []
    given = _height_grid(heights)
    if given is not None:
        for iy in range(cy + 1):
            row_src = given[min(iy, len(given) - 1)]
            grid.append([row_src[min(ix, len(row_src) - 1)] for ix in range(cx + 1)])
    else:
        coarse = _noise_lattice(seed, 3)          # 1옥타브: 큰 굴곡
        fine = _noise_lattice(seed + 977, 6)      # 2옥타브: 잔 기복
        raw = []
        for iy in range(cy + 1):
            v = iy / cy
            raw.append([_sample_lattice(coarse, ix / cx, v) * 0.67
                        + _sample_lattice(fine, ix / cx, v) * 0.33
                        for ix in range(cx + 1)])
        flat = [z for row in raw for z in row]
        lo, hi = min(flat), max(flat)
        span = (hi - lo) or 1.0
        ramp = max(1.0, min(sx, sy) * 0.12)  # 비정형 윤곽은 경계까지의 거리로 감쇠
        for iy in range(cy + 1):
            row = []
            for ix in range(cx + 1):
                if poly is None:
                    fall = _edge_falloff(ix, iy, cx, cy)
                else:
                    px = -sx / 2 + sx * ix / cx
                    py = -sy / 2 + sy * iy / cy
                    fall = _smoothstep(min(_dist_to_polygon_edge(px, py, poly) / ramp, 1.0))
                row.append(((raw[iy][ix] - lo) / span - 0.5) * relief * fall)
            grid.append(row)

    bm = bmesh.new()
    verts = []
    for iy in range(cy + 1):
        row = []
        y = -sy / 2 + sy * iy / cy
        for ix in range(cx + 1):
            x = -sx / 2 + sx * ix / cx
            # 윤곽 지형은 정점을 월드 좌표로 두고 오브젝트 원점은 (0,0)에 남긴다 —
            # ground_snap이 지형 오브젝트의 변환을 따로 고려하지 않아도 되게
            row.append(bm.verts.new((x + ox, y + oy, grid[iy][ix])))
        verts.append(row)
    for iy in range(cy):
        for ix in range(cx):
            if poly is not None:
                # 면 중심이 윤곽 밖이면 만들지 않는다 — 비정형 부지
                fx = -sx / 2 + sx * (ix + 0.5) / cx
                fy = -sy / 2 + sy * (iy + 0.5) / cy
                if not _point_in_polygon(fx, fy, poly):
                    continue
            bm.faces.new((verts[iy][ix], verts[iy][ix + 1],
                          verts[iy + 1][ix + 1], verts[iy + 1][ix]))
    if poly is not None:
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context='VERTS')
        if not bm.faces:
            bm.free()
            raise ValueError("terrain outline 안에 들어가는 면이 없다 — 윤곽이 너무 작거나 cells가 너무 적다")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _new_object(name, bm, (0, 0, 0), (0, 0, 0), (1, 1, 1))
    for poly in obj.data.polygons:
        poly.use_smooth = False  # 로우폴리 플랫 셰이딩
    return obj


def instance(src, location=(0, 0, 0), rotation_z=0.0, scale=1.0,
             name=None) -> bpy.types.Object:
    """src의 메시를 공유하는 인스턴스(linked duplicate)를 만든다. 새 오브젝트를 반환.

    메시 데이터를 복사하지 않으므로 같은 프랍을 수백 개 배치해도 트라이 예산과
    드로우콜이 늘지 않는다 — 배경 씬의 반복 요소는 반드시 이 함수로 배치하라.
    location은 (x,y,z) 미터, rotation_z는 Z축 회전 각도(도), scale은 균일 배율.
    원본 src는 전혀 수정하지 않는다(원본 숨김은 시스템이 처리한다).
    **인스턴스에는 lp.set_color를 쓰지 마라** — 색은 공유 메시의 UV에 저장되므로
    한 인스턴스를 칠하면 원본과 나머지 인스턴스 전부가 같이 바뀐다. 에셋의 색은
    키트 단계에서 이미 정해져 있다.
    예: lp.instance(lp.kit("barrel"), location=(3, -2, 0), rotation_z=45)"""
    from . import link_to_root
    obj = bpy.data.objects.new(safe_id_name(name or f"{src.name}_inst"), src.data)
    obj.location = Vector(location)
    obj.rotation_euler = (0.0, 0.0, math.radians(float(rotation_z)))
    obj.scale = (float(scale), float(scale), float(scale))
    # 오브젝트 레벨 머티리얼 슬롯만 복사 (메시 레벨 슬롯은 data 공유로 따라온다)
    for dst_slot, src_slot in zip(obj.material_slots, src.material_slots):
        if src_slot.link == 'OBJECT':
            dst_slot.link = 'OBJECT'
            dst_slot.material = src_slot.material
    link_to_root(obj)
    return obj


def place_grid(src, cols, rows, spacing, origin=(0.0, 0.0), jitter=0.0,
               rotate_jitter=0.0, seed=0) -> list:
    """src의 인스턴스를 격자로 배치한다. 생성된 오브젝트 리스트를 반환.

    cols x rows 개가 origin=(x,y)를 중심으로 놓이며, spacing은 (dx,dy) 또는
    스칼라(m)다. jitter는 위치 흔들기 폭(m), rotate_jitter는 Z 회전 흔들기 폭(도)로
    둘 다 0보다 크면 기계적인 반복감이 사라진다(seed로 결과 고정).
    텐트촌·막사·밭·좌석처럼 규칙적인 배열에 사용하고, 배치 후 lp.ground_snap을 호출하라.
    예: lp.place_grid(lp.kit("tent"), 4, 3, spacing=(5, 6), jitter=0.4, rotate_jitter=8)"""
    dx, dy = _pair(spacing)
    rng = random.Random(seed)
    out = []
    for iy in range(int(rows)):
        for ix in range(int(cols)):
            x = origin[0] + (ix - (int(cols) - 1) / 2.0) * dx
            y = origin[1] + (iy - (int(rows) - 1) / 2.0) * dy
            if jitter:
                x += rng.uniform(-jitter, jitter)
                y += rng.uniform(-jitter, jitter)
            rz = rng.uniform(-rotate_jitter, rotate_jitter) if rotate_jitter else 0.0
            out.append(instance(src, (x, y, 0.0), rotation_z=rz))
    return out


def place_along(src, points, spacing, align=True, rotate_offset=0.0,
                spacing_jitter=0.0, offset_jitter=0.0, rotate_jitter=0.0, seed=0) -> list:
    """폴리라인을 따라 src의 인스턴스를 배치한다. 오브젝트 리스트를 반환.

    points는 [(x,y), ...] 꺾은선(m), spacing은 인스턴스 간 거리(m)로 시작점부터
    경로 끝까지 채운다. align=True면 각 인스턴스가 진행 방향을 향해 Z 회전하며
    rotate_offset(도)으로 방향을 추가 보정한다(모델이 +X를 보지 않을 때 사용).
    **spacing_jitter**(0~0.5, 간격 비율)·**offset_jitter**(m, 선에서 좌우로 밀기)·
    **rotate_jitter**(도)를 주면 등간격의 기계적인 느낌이 사라진다 — 길가 집·나무·
    노점처럼 사람이 놓은 것에는 반드시 지터를 줘라. 가로등·성벽 망루·기둥처럼
    의도적으로 규칙적인 것만 0으로 둔다.
    예: lp.place_along(lp.kit("house"), lp.meander([(-30, -6), (0, 2), (28, -4)], 3.0),
                       spacing=7.0, offset_jitter=1.5, spacing_jitter=0.25, rotate_jitter=10)"""
    pts = _polyline(points)
    if len(pts) < 2:
        raise ValueError("place_along은 서로 다른 점이 2개 이상 필요하다")
    rng = random.Random(seed)
    step = max(float(spacing), 1e-3)
    sj = max(0.0, min(float(spacing_jitter), 0.5))
    segs = list(zip(pts, pts[1:]))
    lengths = [(b - a).length for a, b in segs]
    total = sum(lengths)
    out = []
    target = 0.0
    while target <= total + 1e-9:
        acc = 0.0
        pos, direction = pts[-1], (segs[-1][1] - segs[-1][0])
        for (a, b), length in zip(segs, lengths):
            if target <= acc + length + 1e-9:
                pos = a.lerp(b, min(max((target - acc) / length, 0.0), 1.0))
                direction = b - a
                break
            acc += length
        if offset_jitter:
            normal = Vector((-direction.y, direction.x))
            if normal.length > 1e-9:
                pos = pos + normal.normalized() * rng.uniform(-offset_jitter, offset_jitter)
        rz = rotate_offset
        if align:
            rz += math.degrees(math.atan2(direction.y, direction.x))
        if rotate_jitter:
            rz += rng.uniform(-rotate_jitter, rotate_jitter)
        out.append(instance(src, (pos.x, pos.y, 0.0), rotation_z=rz))
        target += step * (1.0 + (rng.uniform(-sj, sj) if sj else 0.0))
    return out


def place_cluster(src, count, centers, radius=6.0, min_dist=1.5, avoid=(),
                  scale_jitter=0.15, seed=0) -> list:
    """앵커 지점 주변에 src의 인스턴스를 **뭉쳐서** 배치한다. 오브젝트 리스트를 반환.

    centers는 [(x,y), ...] 군집 중심들(m), count는 전체 개수로 중심들에 나눠 뿌린다.
    radius는 군집 반경(m) — 중심 가까이 촘촘하고 멀어질수록 드물어진다(가우시안).
    min_dist·avoid·scale_jitter는 place_scatter와 같다.
    마을 집들·숲 덤불·잔해 더미·시장 노점처럼 **자연스럽게 모여 있는 것**은
    격자(place_grid)나 균일 산포(place_scatter)가 아니라 이것으로 놓아라 —
    실제 정착지는 격자로 서지 않고 우물·광장·길목 주변에 뭉친다.
    예: lp.place_cluster(lp.kit("hut"), 14, centers=[(-12, 6), (9, -8)], radius=7.0, min_dist=3.5)"""
    rng = random.Random(seed)
    anchors = [(float(c[0]), float(c[1])) for c in centers]
    if not anchors:
        raise ValueError("place_cluster는 centers가 1개 이상 필요하다")
    boxes = [(float(c[0]), float(c[1]), float(c[2]), float(c[3])) for c in avoid]
    r = max(float(radius), 0.1)
    placed, out = [], []
    target = int(count)
    attempts, limit = 0, max(1, target * 40)
    while len(out) < target and attempts < limit:
        attempts += 1
        cx, cy = anchors[attempts % len(anchors)]
        ang = rng.uniform(0.0, math.tau)
        dist = abs(rng.gauss(0.0, r * 0.5))
        x, y = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist
        if any(abs(x - bx) <= bw / 2 and abs(y - by) <= bh / 2 for bx, by, bw, bh in boxes):
            continue
        if any((x - px) ** 2 + (y - py) ** 2 < min_dist ** 2 for px, py in placed):
            continue
        placed.append((x, y))
        sc = 1.0 + rng.uniform(-scale_jitter, scale_jitter) if scale_jitter else 1.0
        # 군집 안의 집·노점은 대체로 중심을 향한다 — 완전 무작위 회전은 어색하다
        face = math.degrees(math.atan2(cy - y, cx - x)) + rng.uniform(-25.0, 25.0)
        out.append(instance(src, (x, y, 0.0), rotation_z=face, scale=max(sc, 0.05)))
    return out


def meander(points, amount=2.0, subdivisions=3, seed=0) -> list:
    """직선 폴리라인을 자연스럽게 굽은 선으로 바꾼다. [(x,y), ...]를 반환.

    points의 각 구간을 subdivisions번 나누고 중간점을 진행 방향의 수직으로 최대
    amount(m)만큼 흔든다. 끝점은 그대로 둔다.
    길(`path_strip`)·개천·울타리(`fence_run`)·성벽(`wall_run`)·`place_along` 경로에
    넣어라 — 직선과 직각으로만 그은 동선은 도면처럼 보이고 원화의 느낌이 사라진다.
    격자 계획도시를 명시적으로 요청한 경우만 예외다.
    예: road = lp.path_strip(lp.meander([(-40, -10), (0, 0), (38, 12)], amount=3.0), width=4.0)"""
    pts = _polyline(points)
    if len(pts) < 2:
        return [(p.x, p.y) for p in pts]
    rng = random.Random(seed)
    n = max(1, int(subdivisions))
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        d = b - a
        normal = Vector((-d.y, d.x))
        if normal.length > 1e-9:
            normal.normalize()
        for i in range(1, n + 1):
            t = i / (n + 1)
            # 구간 양끝에서는 흔들림을 줄여 꺾이는 지점이 어긋나지 않게 한다
            k = math.sin(math.pi * t)
            out.append(a.lerp(b, t) + normal * rng.uniform(-amount, amount) * k)
        out.append(b)
    return [(p.x, p.y) for p in out]


def place_scatter(src, count, area=(20.0, 20.0), center=(0.0, 0.0), avoid=(),
                  min_dist=1.0, scale_jitter=0.0, seed=0) -> list:
    """직사각 영역 안에 src의 인스턴스를 무작위로 흩뿌린다. 오브젝트 리스트를 반환.

    area=(폭,높이)와 center=(x,y)가 배치 영역(m), min_dist는 인스턴스 간 최소
    거리(m)다. avoid는 [(cx, cy, w, h), ...] 형태의 금지 직사각형 목록으로 건물·
    광장·길 위에 프랍이 겹치는 것을 막는다. scale_jitter는 0~1 크기 편차이고
    Z 회전은 항상 무작위다(seed로 고정). 자리를 못 찾으면 count보다 적게 반환한다.
    min_dist·avoid 제약으로 count보다 적게 반환될 수 있다(정상). 반환 개수를 assert하지 마라.
    예: lp.place_scatter(lp.kit("rock"), 24, area=(36, 36), avoid=[(0, 0, 12, 10)], min_dist=2.0)"""
    rng = random.Random(seed)
    aw, ah = float(area[0]), float(area[1])
    boxes = [(float(c[0]), float(c[1]), float(c[2]), float(c[3])) for c in avoid]
    placed, out = [], []
    target = int(count)
    attempts, limit = 0, max(1, target * 30)  # 상한으로 무한루프 방지
    while len(out) < target and attempts < limit:
        attempts += 1
        x = center[0] + rng.uniform(-aw / 2, aw / 2)
        y = center[1] + rng.uniform(-ah / 2, ah / 2)
        if any(abs(x - bx) <= bw / 2 and abs(y - by) <= bh / 2 for bx, by, bw, bh in boxes):
            continue
        if any((x - px) ** 2 + (y - py) ** 2 < min_dist ** 2 for px, py in placed):
            continue
        placed.append((x, y))
        s = 1.0 + rng.uniform(-scale_jitter, scale_jitter) if scale_jitter else 1.0
        out.append(instance(src, (x, y, 0.0), rotation_z=rng.uniform(0.0, 360.0),
                            scale=max(s, 0.05)))
    return out


def wall_run(points, height=3.0, thickness=0.4, name="Wall", closed=False,
             post_size=0.0) -> bpy.types.Object:
    """폴리라인을 따라 벽을 세워 하나의 오브젝트로 합친다. 합쳐진 오브젝트를 반환.

    points는 [(x,y), ...] 꺾은선(m), height는 벽 높이, thickness는 두께(m)이며
    바닥은 항상 z=0이다. closed=True면 마지막 점과 첫 점을 이어 둘레를 닫는다.
    post_size>0이면 각 꼭짓점에 한 변이 post_size인 정사각 기둥(높이 height의 115%)을
    세워 성벽·울타리 모서리를 강조한다.
    담장·성벽·수용소 펜스·건물 외벽처럼 이어진 벽면에 쓰고, 세그먼트마다 나누지 말고
    한 번의 호출로 만들어라(드로우콜·트라이 절감).
    예: lp.wall_run([(-12,-12), (12,-12), (12,12), (-12,12)], height=4, thickness=0.6,
                    closed=True, post_size=1.2)"""
    pts = _polyline(points)
    if len(pts) < 2:
        raise ValueError("wall_run은 서로 다른 점이 2개 이상 필요하다")
    corners = list(pts)
    if closed and len(pts) >= 3:
        pts = pts + [pts[0]]
    parts = []
    for a, b in zip(pts, pts[1:]):
        d = b - a
        length = d.length
        if length < 1e-6:
            continue
        mid = (a + b) / 2
        parts.append(box(f"{name}_seg", size=(length, float(thickness), float(height)),
                         location=(mid.x, mid.y, float(height) / 2),
                         rotation=(0.0, 0.0, math.atan2(d.y, d.x))))
    if post_size and post_size > 0:
        ph = float(height) * 1.15
        for p in corners:
            parts.append(box(f"{name}_post", size=(float(post_size), float(post_size), ph),
                             location=(p.x, p.y, ph / 2)))
    if not parts:
        raise ValueError("wall_run에 유효한 세그먼트가 없다")
    return join(parts, name=name, mode='fast')


def room(name="Room", size=(8.0, 6.0), height=3.0, thickness=0.2, ceiling=False,
         open_sides=()) -> bpy.types.Object:
    """실내 공간의 껍데기(바닥 + 벽 4면 + 선택적 천장)를 만든다. 합쳐진 오브젝트를 반환.

    **실내 씬에서는 terrain 대신 이것을 쓴다.** 실내에 지형을 깔면 기복 있는 땅 위에
    가구가 놓여 실내로 읽히지 않는다.

    size=(폭 X, 깊이 Y) 안목 치수(m), height는 천장고(m), thickness는 벽 두께(m)다.
    바닥 윗면이 z=0이라 가구·프랍을 그대로 z=0에 놓으면 된다.
    ceiling=True면 천장을 덮는다 — 위에서 내려다보는 카메라라면 False로 두어야 안이 보인다.
    open_sides에 'N'/'S'/'E'/'W'를 넣으면 그 벽을 만들지 않는다(출입구·단면 뷰).
    예: shell = lp.room("Shop", size=(10, 8), height=3.2, open_sides=('S',))"""
    sx, sy = _pair(size, 0.75)
    sx, sy = max(float(sx), 0.5), max(float(sy), 0.5)
    h = max(float(height), 0.5)
    t = max(float(thickness), 0.02)
    skip = {str(s).strip().upper()[:1] for s in (open_sides or ())}
    parts = [box(f"{name}_floor", size=(sx + t * 2, sy + t * 2, t),
                 location=(0.0, 0.0, -t / 2))]
    walls = {
        'N': ((sx + t * 2, t, h), (0.0, (sy + t) / 2, h / 2)),
        'S': ((sx + t * 2, t, h), (0.0, -(sy + t) / 2, h / 2)),
        'E': ((t, sy, h), ((sx + t) / 2, 0.0, h / 2)),
        'W': ((t, sy, h), (-(sx + t) / 2, 0.0, h / 2)),
    }
    for side, (wsize, wloc) in walls.items():
        if side in skip:
            continue
        parts.append(box(f"{name}_wall{side}", size=wsize, location=wloc))
    if ceiling:
        parts.append(box(f"{name}_ceil", size=(sx + t * 2, sy + t * 2, t),
                         location=(0.0, 0.0, h + t / 2)))
    return join(parts, name=name, mode='fast')


def fence_run(points, height=1.2, name="Fence", closed=False, post_size=0.12,
              post_spacing=2.0, rails=2, rail_height=0.08, rail_thickness=0.05,
              pickets=0, picket_width=0.10, picket_gap=0.08) -> bpy.types.Object:
    """폴리라인을 따라 속이 비치는 울타리를 세운다. 합쳐진 오브젝트를 반환.

    **울타리·난간·철조망에는 wall_run이 아니라 이 함수를 써라.** wall_run은 속이 꽉 찬
    벽면을 만들기 때문에 울타리에 쓰면 판때기로 보인다. 울타리는 기둥 사이로 배경이
    비쳐야 울타리로 읽힌다.

    points는 [(x,y), ...] 꺾은선(m), height는 기둥 높이(m), 바닥은 항상 z=0이다.
    closed=True면 마지막 점과 첫 점을 이어 둘레를 닫는다.
    post_spacing 간격마다 한 변 post_size인 기둥을 세우고, rails개의 가로대를 높이에
    나눠 건다(rails=0이면 기둥만 — 철조망 기둥줄). pickets>0이면 기둥 사이에
    picket_width 폭의 세로 살대를 picket_gap 간격으로 채운다(말뚝 울타리·난간).
    예: lp.fence_run([(-12,-12), (12,-12), (12,12), (-12,12)], height=1.4, closed=True,
                     rails=2, pickets=1)"""
    pts = _polyline(points)
    if len(pts) < 2:
        raise ValueError("fence_run은 서로 다른 점이 2개 이상 필요하다")
    if closed and len(pts) >= 3:
        pts = pts + [pts[0]]
    h = max(float(height), 0.1)
    post = max(float(post_size), 0.02)
    spacing = max(float(post_spacing), post * 2)
    parts = []
    post_at = []

    for a, b in zip(pts, pts[1:]):
        d = b - a
        length = d.length
        if length < 1e-6:
            continue
        angle = math.atan2(d.y, d.x)
        # 기둥: 구간을 spacing에 가장 가까운 정수 등분으로 나눠 간격이 튀지 않게 한다
        steps = max(1, int(round(length / spacing)))
        for i in range(steps + 1):
            p = a + d * (i / steps)
            key = (round(p.x, 4), round(p.y, 4))
            if key in post_at:
                continue  # 꺾이는 지점에서 기둥이 두 번 서지 않도록
            post_at.append(key)
            parts.append(box(f"{name}_post", size=(post, post, h),
                             location=(p.x, p.y, h / 2)))
        mid = (a + b) / 2
        # 가로대: 높이를 균등 분할하되 맨 위는 기둥 머리 살짝 아래
        for r in range(max(0, int(rails))):
            z = h * (0.85 - 0.5 * r / max(1, int(rails)))
            parts.append(box(f"{name}_rail", size=(length, float(rail_thickness),
                                                   float(rail_height)),
                             location=(mid.x, mid.y, z), rotation=(0.0, 0.0, angle)))
        # 세로 살대: 폭+간격 주기로 구간을 채운다
        if pickets and int(pickets) > 0:
            pitch = max(float(picket_width) + float(picket_gap), 0.02)
            count = max(1, int(length / pitch))
            for i in range(count):
                t = (i + 0.5) / count
                p = a + d * t
                parts.append(box(f"{name}_picket",
                                 size=(float(picket_width), float(rail_thickness) * 0.8,
                                       h * 0.92),
                                 location=(p.x, p.y, h * 0.46),
                                 rotation=(0.0, 0.0, angle)))
    if not parts:
        raise ValueError("fence_run에 유효한 세그먼트가 없다")
    return join(parts, name=name, mode='fast')


def path_strip(points, width=2.0, thickness=0.05, name="Path") -> bpy.types.Object:
    """폴리라인을 따라 일정 폭의 얇은 길 판을 만든다. 생성된 오브젝트를 반환.

    points는 [(x,y), ...] 꺾은선(m), width는 길 폭(m), thickness는 판 두께(m)로
    지면 판 과장을 막기 위해 0.03~0.08m로 자동 클램프된다. 바닥은 z=0이고
    꺾이는 지점은 마이터 보정되어 폭이 일정하게 유지된다.
    도로·오솔길·광장 바닥·다리 상판에 쓴다. 길은 지형보다 살짝 위에 놓거나
    지형을 평평하게 만든 구역 위에 두어야 Z 파이팅이 없다.
    예: road = lp.path_strip([(-18, 0), (-4, 2), (6, -3), (18, 0)], width=3.0)"""
    pts = _polyline(points)
    if len(pts) < 2:
        raise ValueError("path_strip은 서로 다른 점이 2개 이상 필요하다")
    thick = min(max(float(thickness), _PATH_MIN_THICKNESS), _PATH_MAX_THICKNESS)
    half = max(float(width), 0.05) / 2
    dirs = [(pts[i + 1] - pts[i]).normalized() for i in range(len(pts) - 1)]

    offsets = []
    for i in range(len(pts)):
        d_prev = dirs[max(i - 1, 0)]
        d_next = dirs[min(i, len(dirs) - 1)]
        n_prev = Vector((-d_prev.y, d_prev.x))
        n_next = Vector((-d_next.y, d_next.x))
        miter = n_prev + n_next
        if miter.length < 1e-6:
            miter = Vector(n_next)
        miter.normalize()
        # 급격한 꺾임에서 마이터가 폭발하지 않도록 배율 상한
        offsets.append(miter * half * (1.0 / max(miter.dot(n_next), 0.35)))

    bm = bmesh.new()
    rings = []
    for p, off in zip(pts, offsets):
        left_b = bm.verts.new((p.x + off.x, p.y + off.y, 0.0))
        right_b = bm.verts.new((p.x - off.x, p.y - off.y, 0.0))
        left_t = bm.verts.new((p.x + off.x, p.y + off.y, thick))
        right_t = bm.verts.new((p.x - off.x, p.y - off.y, thick))
        rings.append((left_b, right_b, left_t, right_t))
    for (lb0, rb0, lt0, rt0), (lb1, rb1, lt1, rt1) in zip(rings, rings[1:]):
        bm.faces.new((lt0, rt0, rt1, lt1))   # 윗면
        bm.faces.new((lb0, lb1, rb1, rb0))   # 아랫면
        bm.faces.new((lb0, lt0, lt1, lb1))   # 좌측면
        bm.faces.new((rb0, rb1, rt1, rt0))   # 우측면
    lb, rb, lt, rt = rings[0]
    bm.faces.new((lb, rb, rt, lt))           # 시작 캡
    lb, rb, lt, rt = rings[-1]
    bm.faces.new((lb, lt, rt, rb))           # 끝 캡
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    obj = _new_object(name, bm, (0, 0, 0), (0, 0, 0), (1, 1, 1))
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj


def ground_snap(objs, ground) -> None:
    """오브젝트들을 지형 표면 높이에 맞춰 Z만 내린다(단위 m). 반환값 없음.

    각 오브젝트의 (x,y)에서 위에서 아래로 레이캐스트해 ground와 만나는 높이를
    obj.location.z에 넣는다. ground는 지형 하나 또는 리스트다 — 본 지면과 고지대 언덕을 함께 넘기면
    그 자리에서 가장 높은 면에 놓는다. 오브젝트 원점이 바닥 중앙이라는 전제이며(시스템이
    마무리 단계에서 보장한다) 지형 밖이라 히트가 없으면 기존 z를 유지한다.
    objs는 오브젝트 하나 또는 리스트다 — lp.place_* 결과를 그대로 넘기면 된다.
    배치가 모두 끝난 뒤 마지막에 한 번 호출하는 것이 정석이다.
    예: lp.ground_snap(lp.place_scatter(lp.kit("tree"), 20, area=(36, 36)), [ground, hill])"""
    bpy.context.view_layer.update()  # location 변경을 matrix_world에 반영
    items = [objs] if isinstance(objs, bpy.types.Object) else list(objs)
    grounds = [ground] if isinstance(ground, bpy.types.Object) else list(ground)
    for obj in items:
        world = obj.matrix_world.translation
        best = None
        for g in grounds:
            mw = g.matrix_world
            inv = mw.inverted()
            zs = [(mw @ Vector(corner)).z for corner in g.bound_box]
            start = inv @ Vector((world.x, world.y, max(zs) + 10.0))
            end = inv @ Vector((world.x, world.y, min(zs) - 10.0))
            ray = end - start
            if ray.length < 1e-6:
                continue
            hit, location, _normal, _index = g.ray_cast(start, ray.normalized(), distance=ray.length)
            if hit:
                z = (mw @ location).z
                best = z if best is None else max(best, z)
        if best is not None:
            obj.location.z = best


def kit(name) -> bpy.types.Object:
    """미리 생성된 에셋 키트에서 원본 오브젝트를 이름으로 찾아 반환한다.

    정확히 일치하는 이름을 먼저 찾고, 없으면 name으로 시작하는 첫 오브젝트를
    반환한다(Blender가 붙이는 .001 접미어 대응). 없으면 사용 가능한 이름 목록을
    담은 KeyError를 던지므로, 오류 메시지의 목록을 보고 이름을 고쳐 다시 호출하라.
    반환된 원본은 직접 배치하지 말고 lp.instance / lp.place_* 로 복제해서 쓴다.
    예: barrel = lp.kit("barrel")"""
    coll = _kit_root()
    objects = list(coll.all_objects)
    for obj in objects:
        if obj.name == name:
            return obj
    for obj in objects:
        if obj.name.startswith(name):
            return obj
    available = sorted(obj.name for obj in objects)
    raise KeyError(f"키트에 '{name}' 오브젝트가 없다. 사용 가능한 이름: {available}")
