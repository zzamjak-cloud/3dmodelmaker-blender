"""서로 다른 파트의 면이 같은 평면에서 겹치는 곳(Z-fighting)을 찾아 잘라내는 순수 기하 계산. bpy 없이 테스트한다.

박스 두 개가 면을 정확히 맞대거나(접촉) 같은 방향 면이 한 평면에 나란히 놓이면(플러시) 깊이 버퍼가
두 면을 가르지 못해 지글거린다. 겹친 영역을 면에서 오려 내 한 평면에 면이 하나만 남게 한다.
  접촉(노멀 반대): 겹친 영역은 두 파트 사이에 끼어 보이지 않는다 — 양쪽 면에서 모두 오린다.
  플러시(노멀 같음): 넓은 면에서 오린다 — 좁은 면이 붙인 디테일(패널·띠)이라 그 색이 남아야 한다.
절단은 볼록 다각형 연산이다. 오목 면(프리즘 뚜껑 등)은 삼각형으로 쪼개 같은 방식으로 다룬다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

# 한 평면에서 반복 절단 상한 (조각끼리 다시 겹칠 수 있어 수렴을 보장한다)
MAX_PASSES = 400


@dataclass
class Face:
    """판정 입력 면. key는 호출자가 되찾을 식별자, island는 같은 파트끼리 비교하지 않기 위한 번호."""
    key: object
    island: object
    points: list          # 월드 좌표 (x, y, z) 목록, 노멀 방향으로 반시계
    mutable: bool = True  # 메시를 공유하는 인스턴스 면은 자를 수 없다 — 상대 면만 자른다


@dataclass
class _Poly:
    face: Face
    sign: int             # 면 노멀이 평면 기준 노멀과 같으면 +1
    loop: list            # 평면 2D 좌표, 기준 노멀 기준 반시계
    parts: list = field(default_factory=list)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    n = math.sqrt(_dot(v, v))
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else None


def newell_normal(points):
    """다각형 노멀 (Newell). 퇴화 면이면 None."""
    nx = ny = nz = 0.0
    count = len(points)
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return _unit((nx, ny, nz))


def _canonical(normal):
    """노멀과 그 반대를 같은 평면 묶음으로 보기 위한 기준 방향과 부호."""
    for c in normal:
        if abs(c) > 1e-9:
            return (normal, 1) if c > 0 else ((-normal[0], -normal[1], -normal[2]), -1)
    return normal, 1


def _basis(n):
    helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = _unit(_cross(helper, n))
    v = _cross(n, u)
    return u, v


def area2d(loop):
    s = 0.0
    for i in range(len(loop)):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % len(loop)]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def _is_convex(loop, tol):
    count = len(loop)
    for i in range(count):
        a, b, c = loop[i], loop[(i + 1) % count], loop[(i + 2) % count]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) < -tol:
            return False
    return True


def _side(a, b, p):
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _split(loop, a, b, tol):
    """볼록 다각형을 직선 a→b로 가른다 → (왼쪽 조각, 오른쪽 조각)."""
    left, right = [], []
    count = len(loop)
    for i in range(count):
        p, q = loop[i], loop[(i + 1) % count]
        sp, sq = _side(a, b, p), _side(a, b, q)
        if sp >= -tol:
            left.append(p)
        if sp <= tol:
            right.append(p)
        if (sp > tol and sq < -tol) or (sp < -tol and sq > tol):
            t = sp / (sp - sq)
            x = (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
            left.append(x)
            right.append(x)
    return _clean(left, tol), _clean(right, tol)


def _clean(loop, tol):
    """겹친 점과 일직선 중간점을 걷어 낸다."""
    out = []
    for p in loop:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    changed = True
    while changed and len(out) >= 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            if abs(_side(a, c, b)) <= tol * max(1.0, math.hypot(c[0] - a[0], c[1] - a[1])):
                out.pop(i)
                changed = True
                break
    return out if len(out) >= 3 else []


def clip(subject, cutter, tol):
    """볼록 다각형 교집합 (Sutherland–Hodgman)."""
    out = subject
    for i in range(len(cutter)):
        if not out:
            return []
        out, _ = _split(out, cutter[i], cutter[(i + 1) % len(cutter)], tol)
    return out


def subtract(subject, hole, tol):
    """볼록 subject − 볼록 hole → 볼록 조각 목록. hole의 변마다 바깥쪽을 떼어 낸다."""
    pieces, rest = [], subject
    for i in range(len(hole)):
        if not rest:
            break
        inside, outside = _split(rest, hole[i], hole[(i + 1) % len(hole)], tol)
        if outside:
            pieces.append(outside)
        rest = inside
    return pieces


def _conform(loops, tol):
    """조각 변 위에 다른 조각의 꼭짓점이 놓이면 그 변에 끼워 넣는다 — T자 이음새로 생기는 틈을 막는다."""
    points = [p for loop in loops for p in loop]
    out = []
    for loop in loops:
        new = []
        for i in range(len(loop)):
            a, b = loop[i], loop[(i + 1) % len(loop)]
            new.append(a)
            dx, dy = b[0] - a[0], b[1] - a[1]
            length2 = dx * dx + dy * dy
            if length2 <= tol * tol:
                continue
            on = []
            for p in points:
                t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2
                if t <= 1e-9 or t >= 1 - 1e-9:
                    continue
                if abs(_side(a, b, p)) / math.sqrt(length2) <= tol:
                    on.append((t, p))
            for _t, p in sorted(on):
                if not any(abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol for q in new[-1:]):
                    new.append(p)
        out.append(_clean_dupes(new, tol))
    return out


def _clean_dupes(loop, tol):
    out = []
    for p in loop:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def _ear_clip(loop, tol):
    """반시계 단순 다각형 → 삼각형 목록. 실패하면 빈 목록."""
    rest = list(loop)
    tris = []
    guard = len(rest) * len(rest)
    while len(rest) > 3 and guard > 0:
        guard -= 1
        for i in range(len(rest)):
            a, b, c = rest[i - 1], rest[i], rest[(i + 1) % len(rest)]
            if _side(a, b, c) <= tol:
                continue
            if any(p not in (a, b, c) and _side(a, b, p) > -tol and _side(b, c, p) > -tol
                   and _side(c, a, p) > -tol for p in rest):
                continue
            tris.append([a, b, c])
            rest.pop(i)
            break
        else:
            return []
    if len(rest) == 3:
        tris.append(rest)
    return tris


def _normal_key(normal):
    """노멀 방향 묶음 키 (1e-3 격자)."""
    return (round(normal[0] / 1e-3), round(normal[1] / 1e-3), round(normal[2] / 1e-3))


def _bbox(loop):
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return min(xs), min(ys), max(xs), max(ys)


def _find_hit(items, skip, area_tol, tol):
    boxes = {id(p): _bbox(p) for _poly, parts in items for p in parts}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i][0].face.island == items[j][0].face.island:
                continue
            for pa in items[i][1]:
                ax0, ay0, ax1, ay1 = boxes[id(pa)]
                for pb in items[j][1]:
                    bx0, by0, bx1, by1 = boxes[id(pb)]
                    if ax1 <= bx0 + tol or bx1 <= ax0 + tol or ay1 <= by0 + tol or by1 <= ay0 + tol:
                        continue
                    if (id(pa), id(pb)) in skip:
                        continue
                    overlap = clip(pa, pb, tol)
                    if overlap and area2d(overlap) > area_tol:
                        return i, j, pa, pb, overlap
    return None


def _resolve_bucket(polys, area_tol, tol):
    """한 평면 안에서 겹침이 없어질 때까지 자른다. 바뀐 면 key → 최종 2D 조각 목록."""
    items = [(p, list(p.parts)) for p in polys]  # (원본, 현재 조각들)
    changed, skip = set(), set()
    for _ in range(MAX_PASSES):
        hit = _find_hit(items, skip, area_tol, tol)
        if hit is None:
            break
        i, j, pa, pb, overlap = hit
        a, b = items[i][0], items[j][0]
        if a.sign != b.sign:
            # 접촉 — 겹친 영역은 두 파트 사이라 어느 쪽에서도 보이지 않는다
            targets = [k for k in (i, j) if items[k][0].face.mutable]
        else:
            # 플러시 — 넓은 쪽을 오린다. 넓은 쪽이 인스턴스면 좁은 쪽이라도 오려 겹침을 없앤다
            order = sorted(((abs(area2d(pa)), i), (abs(area2d(pb)), j)), reverse=True)
            targets = [k for _area, k in order if items[k][0].face.mutable][:1]
        if not targets:
            skip.add((id(pa), id(pb)))  # 둘 다 자를 수 없다 — 이 쌍만 포기한다
            continue
        for k in targets:
            src = pa if k == i else pb
            parts = [p for p in items[k][1] if p is not src]
            parts += [piece for piece in subtract(src, overlap, tol) if area2d(piece) > area_tol]
            items[k] = (items[k][0], parts)
            changed.add(k)
    return {items[k][0].face.key: _conform(items[k][1], tol) for k in changed}


def resolve(faces, scale=1.0):
    """겹친 동일평면 면을 오린 결과. {face.key: [조각 월드 좌표 목록, ...]} — 바뀐 면만 담는다.

    조각이 빈 목록이면 그 면은 통째로 지운다. 조각 꼭짓점 순서는 원래 면 노멀 방향 반시계다.
    scale은 모델 크기(m) — 허용 오차를 크기에 비례시켜 큰 배경에서도 판정이 흔들리지 않게 한다."""
    tol = max(1e-6, 1e-5 * scale)
    plane_tol = max(1e-5, 1e-4 * scale)
    area_tol = max(1e-10, (1e-4 * scale) ** 2)
    by_normal = {}
    for face in faces:
        if len(face.points) < 3:
            continue
        normal = newell_normal(face.points)
        if normal is None:
            continue
        base, sign = _canonical(normal)
        by_normal.setdefault(_normal_key(base), []).append((_dot(base, face.points[0]), base, sign, face))

    # 같은 노멀 안에서 d(원점 거리) 순으로 늘어놓고 틈이 plane_tol을 넘을 때만 끊는다 —
    # 고정 칸으로 반올림하면 칸 경계에 걸친 동일평면 쌍이 서로 다른 묶음으로 갈라진다
    buckets, frames = {}, {}
    for nkey, entries in by_normal.items():
        entries.sort(key=lambda e: e[0])
        cluster = -1
        last = None
        for d, _base, sign, face in entries:
            if last is None or d - last > plane_tol:
                cluster += 1
                u, v = _basis(entries[0][1])
                frames[(nkey, cluster)] = (entries[0][1], u, v, d)
            last = d
            key = (nkey, cluster)
            base, u, v, d0 = frames[key]
            # 평면에서 벗어난 면(비평면 쿼드)은 투영하면 모양이 달라진다 — 다루지 않는다
            if any(abs(_dot(base, p) - d0) > plane_tol * 2 for p in face.points):
                continue
            loop = [(_dot(p, u), _dot(p, v)) for p in face.points]
            if sign < 0:
                loop.reverse()
            loop = _clean(loop, tol)
            if not loop or area2d(loop) <= area_tol:
                continue
            parts = [loop] if _is_convex(loop, tol * 10) else _ear_clip(loop, tol)
            if not parts:
                continue
            buckets.setdefault(key, []).append(_Poly(face=face, sign=sign, loop=loop, parts=parts))

    result = {}
    for key, polys in buckets.items():
        if len({p.face.island for p in polys}) < 2:
            continue
        base, u, v, d = frames[key]
        origin = (base[0] * d, base[1] * d, base[2] * d)
        changed = _resolve_bucket(polys, area_tol, tol)
        sign_of = {p.face.key: p.sign for p in polys}
        for face_key, parts in changed.items():
            out = []
            for loop in parts:
                if len(loop) < 3:
                    continue
                pts = [(origin[0] + x * u[0] + y * v[0],
                        origin[1] + x * u[1] + y * v[1],
                        origin[2] + x * u[2] + y * v[2]) for x, y in loop]
                if sign_of[face_key] < 0:
                    pts.reverse()
                out.append(pts)
            result[face_key] = out
    return result
