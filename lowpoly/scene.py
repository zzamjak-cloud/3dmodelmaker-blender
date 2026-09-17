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
            relief=0.4, seed=0) -> bpy.types.Object:
    """원점 중심의 XY 그리드 지형 판을 만든다(미터 단위). 생성된 오브젝트를 반환.

    size=(x,y)는 지형 전체 크기, cells=(열,행)은 분할 수로 각 축 최대 48까지만
    허용된다(면수 = 열x행). relief는 기복의 전체 높이 폭(m)이며 seed를 바꾸면
    다른 지형이 나온다. 가장자리 한 줄은 z=0으로 눌러 씬 경계가 평평해진다.
    heights를 주면 노이즈 대신 그 값을 그대로 쓴다 — heights[행][열] 순서의
    2D 리스트(크기 (행+1)x(열+1), 단위 m)이고 이때 relief/seed는 무시된다.
    비어 있거나 숫자가 아닌 heights는 무시하고 노이즈 지형을 만든다.
    배경 씬에서는 지형을 먼저 1개 만들고 그 위에 구조물·인스턴스를 올린 뒤
    lp.ground_snap으로 높이를 맞추는 순서를 권장한다.
    예: ground = lp.terrain("Ground", size=(40, 40), cells=(16, 16), relief=0.5)"""
    cx = max(1, min(int(cells[0]), _MAX_CELLS))
    cy = max(1, min(int(cells[1]), _MAX_CELLS))
    sx, sy = float(size[0]), float(size[1])

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
        for iy in range(cy + 1):
            grid.append([((raw[iy][ix] - lo) / span - 0.5) * relief
                         * _edge_falloff(ix, iy, cx, cy)
                         for ix in range(cx + 1)])

    bm = bmesh.new()
    verts = []
    for iy in range(cy + 1):
        row = []
        y = -sy / 2 + sy * iy / cy
        for ix in range(cx + 1):
            x = -sx / 2 + sx * ix / cx
            row.append(bm.verts.new((x, y, grid[iy][ix])))
        verts.append(row)
    for iy in range(cy):
        for ix in range(cx):
            bm.faces.new((verts[iy][ix], verts[iy][ix + 1],
                          verts[iy + 1][ix + 1], verts[iy + 1][ix]))
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


def place_along(src, points, spacing, align=True, rotate_offset=0.0) -> list:
    """폴리라인을 따라 src의 인스턴스를 등간격으로 배치한다. 오브젝트 리스트를 반환.

    points는 [(x,y), ...] 꺾은선(m), spacing은 인스턴스 간 거리(m)로 시작점부터
    경로 끝까지 채운다. align=True면 각 인스턴스가 진행 방향을 향해 Z 회전하며
    rotate_offset(도)으로 방향을 추가 보정한다(모델이 +X를 보지 않을 때 사용).
    울타리 기둥·가로등·성벽 망루·길가 나무처럼 선을 따르는 반복 요소에 쓴다.
    예: lp.place_along(lp.kit("lamp"), [(-10, 0), (0, 4), (10, 0)], spacing=3.0)"""
    pts = _polyline(points)
    if len(pts) < 2:
        raise ValueError("place_along은 서로 다른 점이 2개 이상 필요하다")
    step = max(float(spacing), 1e-3)
    segs = list(zip(pts, pts[1:]))
    lengths = [(b - a).length for a, b in segs]
    total = sum(lengths)
    out = []
    for k in range(int(total / step) + 1):
        target = k * step
        acc = 0.0
        pos, direction = pts[-1], (segs[-1][1] - segs[-1][0])
        for (a, b), length in zip(segs, lengths):
            if target <= acc + length + 1e-9:
                pos = a.lerp(b, min(max((target - acc) / length, 0.0), 1.0))
                direction = b - a
                break
            acc += length
        rz = rotate_offset
        if align:
            rz += math.degrees(math.atan2(direction.y, direction.x))
        out.append(instance(src, (pos.x, pos.y, 0.0), rotation_z=rz))
    return out


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
    obj.location.z에 넣는다. 오브젝트 원점이 바닥 중앙이라는 전제이며(시스템이
    마무리 단계에서 보장한다) 지형 밖이라 히트가 없으면 기존 z를 유지한다.
    objs는 오브젝트 하나 또는 리스트다 — lp.place_* 결과를 그대로 넘기면 된다.
    배치가 모두 끝난 뒤 마지막에 한 번 호출하는 것이 정석이다.
    예: lp.ground_snap(lp.place_scatter(lp.kit("tree"), 20, area=(36, 36)), ground)"""
    bpy.context.view_layer.update()  # location 변경을 matrix_world에 반영
    items = [objs] if isinstance(objs, bpy.types.Object) else list(objs)
    mw = ground.matrix_world
    inv = mw.inverted()
    zs = [(mw @ Vector(corner)).z for corner in ground.bound_box]
    top, bottom = max(zs) + 10.0, min(zs) - 10.0
    for obj in items:
        world = obj.matrix_world.translation
        start = inv @ Vector((world.x, world.y, top))
        end = inv @ Vector((world.x, world.y, bottom))
        ray = end - start
        if ray.length < 1e-6:
            continue
        hit, location, _normal, _index = ground.ray_cast(
            start, ray.normalized(), distance=ray.length)
        if hit:
            obj.location.z = (mw @ location).z


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
