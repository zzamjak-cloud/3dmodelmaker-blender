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
    """X=0 평면 기준으로 대칭 복제해 메시에 굽는다. 좌우 대칭 모델에 사용."""
    bm = _edit_bmesh(obj)
    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
    mirrored = bmesh.ops.mirror(bm, geom=geom, axis='X',
                                merge_dist=merge_threshold,
                                matrix=Matrix.Identity(4))
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


def shade_flat(obj):
    """플랫 셰이딩 적용 (로우폴리 기본). game_ready가 자동 호출하므로 보통 불필요."""
    for poly in obj.data.polygons:
        poly.use_smooth = False
    return obj
