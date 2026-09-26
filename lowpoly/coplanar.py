"""서로 다른 파트의 면이 같은 방향으로 같은 평면에 겹치는 곳(Z-fighting)을 찾아 좁은 쪽 파트를 띄우는 순수 기하 계산. bpy 없이 테스트한다.

같은 방향 면이 한 평면에 겹치면(플러시 — 벽에 붙인 패널, 받침에 파묻은 상판) 깊이 버퍼가 두 면을 가르지 못해 지글거린다.
면을 오려 내면 조각·T자 이음새·용접으로 면이 불어나 손으로 고치기 어려워지므로, 면은 그대로 두고
좁은 쪽 파트(붙인 디테일)를 통째로 노멀 방향으로 미세하게 민다 — 좁은 쪽 색이 앞에 보인다.
맞댄 면(노멀 반대 — 받침 위 상자의 밑면)은 두 솔리드 사이에 끼어 어느 쪽에서도 보이지 않으므로 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

# 띄우는 거리 = 모델 크기 x 이 비율, [NUDGE_MIN, NUDGE_MAX] m 로 자른다 — 뷰포트·엔진 깊이 정밀도를 넘기되 눈에 띄지 않게
NUDGE_RATIO = 0.002
NUDGE_MIN = 0.001
NUDGE_MAX = 0.01


@dataclass
class Face:
    """판정 입력 면. key는 호출자가 되찾을 식별자, island는 같은 파트끼리 비교하지 않기 위한 번호."""
    key: object
    island: object
    points: list          # 월드 좌표 (x, y, z) 목록, 노멀 방향으로 반시계
    mutable: bool = True  # 메시를 공유하는 인스턴스 면은 움직일 수 없다 — 상대 파트를 민다


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




def nudge_distance(scale):
    """모델 크기(m)에 맞춘 한 단 띄우는 거리."""
    return min(NUDGE_MAX, max(NUDGE_MIN, NUDGE_RATIO * scale))


def _overlaps(parts_a, parts_b, area_tol, tol):
    for pa in parts_a:
        ax0, ay0, ax1, ay1 = _bbox(pa)
        for pb in parts_b:
            bx0, by0, bx1, by1 = _bbox(pb)
            if ax1 <= bx0 + tol or bx1 <= ax0 + tol or ay1 <= by0 + tol or by1 <= ay0 + tol:
                continue
            overlap = clip(pa, pb, tol)
            if overlap and area2d(overlap) > area_tol:
                return True
    return False


def _levels(islands, area_tol, tol):
    """한 평면·한 방향에서 겹치는 파트들의 층 번호. {island: 층} — 0이 제자리, +1마다 한 단 앞으로.

    islands: {island: (면적, 움직일 수 있는가, 볼록 조각들)}. 넓은 파트부터 놓고, 좁은 파트는
    겹치는 파트들보다 한 단 앞에 둔다 — 패널 위 명판처럼 겹겹이 붙어도 층마다 서로 떨어진다.
    움직일 수 없는 파트(인스턴스)는 제자리에 두고, 같은 층에서 겹치는 상대를 한 단 뒤로 민다."""
    order = sorted(islands, key=lambda k: -islands[k][0])
    level = {}
    for key in order:
        area, mutable, parts = islands[key]
        under = [o for o in level if _overlaps(parts, islands[o][2], area_tol, tol)]
        if mutable:
            level[key] = max((level[o] for o in under), default=-1) + 1
            continue
        level[key] = 0
        for o in under:
            if level[o] == 0 and islands[o][1]:
                level[o] = -1
    return level


def resolve(faces, scale=1.0):
    """같은 방향으로 한 평면에 겹친 면을 가진 파트를 띄울 월드 이동량. {island: (dx, dy, dz)} — 움직일 파트만 담는다.

    면은 자르지도 지우지도 않는다. 한 파트가 여러 방향에서 겹치면 방향마다 이동을 더한다.
    scale은 모델 크기(m) — 허용 오차와 띄우는 거리를 크기에 비례시킨다."""
    tol = max(1e-6, 1e-5 * scale)
    plane_tol = max(1e-5, 1e-4 * scale)
    area_tol = max(1e-10, (1e-4 * scale) ** 2)
    step = nudge_distance(scale)
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
    # 고정 칸으로 반올림하면 칸 경계에 걸친 동일평면 쌍이 서로 다른 묶음으로 갈라진다.
    # 맞댄 면(부호 반대)은 보이지 않으므로 부호까지 같은 면끼리만 묶는다.
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
            base, u, v, d0 = frames[(nkey, cluster)]
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
            key = (nkey, cluster, sign)
            frames[key] = (base[0] * sign, base[1] * sign, base[2] * sign)
            group = buckets.setdefault(key, {})
            area, mutable, pieces = group.get(face.island, (0.0, True, []))
            group[face.island] = (area + area2d(loop), mutable and face.mutable, pieces + parts)

    # 파트·방향마다 가장 먼 층만 남긴다 (계단 윗면처럼 한 파트가 같은 방향 여러 평면에 걸칠 수 있다)
    shifts = {}
    for key, group in buckets.items():
        if len(group) < 2:
            continue
        for island, lv in _levels(group, area_tol, tol).items():
            if lv == 0:
                continue
            slot = shifts.setdefault(island, {})
            prev = slot.get(key[0])
            if prev is None or abs(lv) > abs(prev[0]):
                slot[key[0]] = (lv, frames[key])

    result = {}
    for island, slot in shifts.items():
        dx = dy = dz = 0.0
        for lv, n in slot.values():
            dx += n[0] * lv * step
            dy += n[1] * lv * step
            dz += n[2] * lv * step
        result[island] = (dx, dy, dz)
    return result
