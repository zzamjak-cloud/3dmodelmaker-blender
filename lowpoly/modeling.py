# 모델링 가공 헬퍼 — bevel, 대칭, 배열, 스캐터, 테이퍼
import math

import bmesh
import bpy
from mathutils import Matrix, Vector


def _edit_bmesh(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    return bm


def _write_back(bm, obj):
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def bevel(obj, width=0.02, segments=1, angle_limit=60.0):
    """(주의: 기본 사용 금지) 모서리를 깎는다. 면이 쪼개져 후편집이 어려워지므로
    사용자가 '베벨/모서리 둥글게'를 명시적으로 요청한 경우에만 사용하라.

    width는 미터 단위(0.01~0.05 권장), segments=1이면 폴리 증가 최소."""
    bm = _edit_bmesh(obj)
    limit = math.radians(angle_limit)
    edges = [e for e in bm.edges
             if len(e.link_faces) == 2 and e.calc_face_angle(0.0) >= limit]
    if edges:
        bmesh.ops.bevel(bm, geom=edges, offset=width, segments=segments,
                        profile=0.7, affect='EDGES')
    _write_back(bm, obj)
    return obj


def mirror_x(obj, merge_threshold=0.001):
    """월드 X=0 평면 기준으로 대칭 복제해 메시에 굽는다. 좌우 대칭 모델에 사용.

    오브젝트가 x≠0에 배치되어 있어도 반대편에 짝이 생긴다 —
    예: `lp.box("Mirror", location=(0.95, 1.3, 1.5))` 후 `lp.mirror_x(mirror)`이면
    x=-0.95에도 백미러가 생긴다. (로컬 원점 기준으로 미러하면 제자리에 겹쳐
    병합돼 버려 한쪽만 남는다.)"""
    bpy.context.view_layer.update()  # location 변경을 matrix_world에 반영
    mw = obj.matrix_world.copy()
    bm = _edit_bmesh(obj)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    dup = bmesh.ops.duplicate(bm, geom=geom)
    verts = [e for e in dup["geom"] if isinstance(e, bmesh.types.BMVert)]
    # 로컬 → 월드 → X 반전 → 로컬 (오브젝트 트랜스폼을 존중한 월드 기준 미러)
    xform = mw.inverted() @ Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)) @ mw
    bmesh.ops.transform(bm, matrix=xform, verts=verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_threshold)
    # 미러된 페이스는 노멀이 뒤집히므로 재계산
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    _write_back(bm, obj)
    return obj


def array(obj, count=3, offset=(1, 0, 0)):
    """오브젝트를 offset 간격으로 count개 복제한다. 복제본 리스트(원본 포함)를 반환."""
    from . import link_to_root
    result = [obj]
    for i in range(1, count):
        dup = obj.copy()
        dup.data = obj.data  # 메시 공유(인스턴스)로 메모리 절약
        dup.location = Vector(obj.location) + Vector(offset) * i
        link_to_root(dup)
        result.append(dup)
    return result


def scatter(obj, count=5, area=(4, 4), seed=0, scale_jitter=0.3, rotate_z=True):
    """오브젝트를 XY 영역 안에 무작위 배치 복제한다. 나무/바위 군집에 사용.

    area=(x,y) 영역 크기, scale_jitter는 0~1 스케일 편차. 복제본 리스트 반환."""
    import random as _random
    from . import link_to_root
    rng = _random.Random(seed)
    result = [obj]
    for _ in range(count - 1):
        dup = obj.copy()
        dup.data = obj.data
        dup.location = (
            obj.location.x + rng.uniform(-area[0] / 2, area[0] / 2),
            obj.location.y + rng.uniform(-area[1] / 2, area[1] / 2),
            obj.location.z,
        )
        s = 1.0 + rng.uniform(-scale_jitter, scale_jitter)
        dup.scale = (obj.scale.x * s, obj.scale.y * s, obj.scale.z * s)
        if rotate_z:
            dup.rotation_euler = (obj.rotation_euler.x, obj.rotation_euler.y,
                                  rng.uniform(0, math.tau))
        link_to_root(dup)
        result.append(dup)
    return result


def taper(obj, factor=0.5, axis='Z'):
    """축 방향으로 갈수록 좁아지게 변형한다. factor=0.5면 꼭대기가 절반 폭.

    통나무, 탑, 나무 기둥 등에 사용."""
    bm = _edit_bmesh(obj)
    idx = {'X': 0, 'Y': 1, 'Z': 2}[axis]
    coords = [v.co[idx] for v in bm.verts]
    lo, hi = min(coords), max(coords)
    span = (hi - lo) or 1.0
    for v in bm.verts:
        t = (v.co[idx] - lo) / span  # 0(아래) → 1(위)
        s = 1.0 + (factor - 1.0) * t
        for other in range(3):
            if other != idx:
                v.co[other] *= s
    _write_back(bm, obj)
    return obj


def _z_range(bm):
    zs = [v.co.z for v in bm.verts]
    lo = min(zs)
    return lo, (max(zs) - lo) or 1.0


def bend(obj, angle=30.0, axis='X'):
    """Z 높이에 따라 점진적으로 axis 축 둘레로 회전시켜 활처럼 구부린다.

    휘어진 나무줄기·기울어진 굴뚝·바람에 눕는 풀 같은 만화적 동세의 핵심.
    angle은 도 단위로 꼭대기가 받는 회전량(바닥은 0도). axis='X'면 Y방향으로 눕는다.
    예: lp.bend(trunk, angle=20)  # 살짝 휜 나무줄기"""
    bm = _edit_bmesh(obj)
    lo, span = _z_range(bm)
    pivot = Vector((0.0, 0.0, lo))
    for v in bm.verts:
        t = (v.co.z - lo) / span
        rot = Matrix.Rotation(math.radians(angle) * t, 4, axis)
        v.co = rot @ (v.co - pivot) + pivot
    _write_back(bm, obj)
    return obj


def bulge(obj, amount=0.3, center=0.5, width=0.5):
    """Z 정규화 높이(0=바닥, 1=꼭대기)의 center 주변 폭(XY)을 불룩하게/잘록하게 한다.

    amount=0.3이면 최대 지점 폭이 130%(음수면 잘록). width는 영향 범위(정규화).
    배럴의 배, 항아리 허리, 통통한 몸통 등 곡률 실루엣을 한 줄로 만든다.
    예: lp.bulge(body, amount=0.35, center=0.45)  # 배가 불룩한 몸통"""
    bm = _edit_bmesh(obj)
    lo, span = _z_range(bm)
    for v in bm.verts:
        t = (v.co.z - lo) / span
        d = abs(t - center) / max(width, 1e-4)
        if d < 1.0:
            s = 1.0 + amount * 0.5 * (1.0 + math.cos(math.pi * d))
            v.co.x *= s
            v.co.y *= s
    _write_back(bm, obj)
    return obj


def shear(obj, offset=0.3, axis='X'):
    """꼭대기로 갈수록 axis 방향으로 밀어 기울인다(전단 변형). offset은 꼭대기 이동량(m).

    바람 맞은 나무·비스듬한 텐트·달리는 듯한 동세 등 만화적 기울기에 사용.
    예: lp.shear(tree_top, offset=0.4)  # 바람에 쏠린 잎덩어리"""
    bm = _edit_bmesh(obj)
    lo, span = _z_range(bm)
    idx = {'X': 0, 'Y': 1}[axis]
    for v in bm.verts:
        v.co[idx] += offset * (v.co.z - lo) / span
    _write_back(bm, obj)
    return obj


def stretch_at(obj, z_min, z_max, scale=1.3):
    """z_min~z_max(m) 높이 구간의 폭(XY)만 scale배 한다 — 부분 과장의 핵심 도구.

    높이는 오브젝트 로컬 좌표 기준이다 (location으로 배치하기 전의 형상 기준).
    "지붕만 넓게", "밑동만 굵게", "머리만 크게" 같은 데포르메를 한 줄로.
    구간 경계에 단차가 생기지 않도록 한 파트 전체를 감싸는 구간을 주는 것이 좋다.
    예: lp.stretch_at(house, z_min=1.2, z_max=2.2, scale=1.25)  # 지붕부만 과장"""
    bm = _edit_bmesh(obj)
    for v in bm.verts:
        if z_min <= v.co.z <= z_max:
            v.co.x *= scale
            v.co.y *= scale
    _write_back(bm, obj)
    return obj


def jitter(obj, amount=0.05, seed=0):
    """정점을 무작위로 흔들어 유기적인 울퉁불퉁함을 만든다(시드 고정, 재현 가능).

    바위·통나무·흙더미·빵 등 유기물 표면에 사용. amount는 최대 변위(m) —
    아이코스피어 바위는 반지름의 15~25% 권장.
    예: rock = lp.jitter(lp.sphere("Rock", radius=0.4), amount=0.08, seed=3)"""
    import random as _random
    rng = _random.Random(seed)
    bm = _edit_bmesh(obj)
    for v in bm.verts:
        v.co += Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))) * amount
    _write_back(bm, obj)
    return obj


def shade_flat(obj):
    """플랫 셰이딩 적용 (로우폴리 기본). game_ready가 자동 호출하므로 보통 불필요."""
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj
