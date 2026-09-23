"""클릭 지점에서 팔·다리 단면 링을 추정하는 순수 기하 계산. bpy 없이 테스트한다.

축은 접평면 안의 방향 후보 중 단면 둘레가 가장 짧은 방향으로 고른다 — 관을 비스듬히 자르면 둘레가 길어지므로
최소 둘레 단면이 축에 수직이다. PCA 는 목처럼 길이가 폭보다 짧은 구간에서 폭 방향을 축으로 잡아 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from math import cos, pi, sin, sqrt

Vector3 = tuple  # (x, y, z)

HEMISPHERE_SAMPLES = 96    # 축 후보를 반구에 고르게 이만큼 뿌린다 (간격 약 11°)
REFINE_STEPS = (pi / 18, pi / 45, pi / 120)  # 최적 축 주변을 10°, 4°, 1.5° 로 좁혀 다듬는다
REFINE_AZIMUTHS = 8        # 다듬을 때 축 주위로 보는 방향 수
REFINE_MAX_ROUNDS = 12     # 단계마다 이동 반복 상한 — 점수는 단조 감소하지만 UI 정지 시간을 묶어 둔다
PLANE_NUDGE = 3.0          # 정점이 평면 위에 있을 때 평면을 PLANE_EPSILON 의 이 배수만큼 민다
HIT_OFFSET_MIN_RATIO = 0.5 # 클릭 점이 루프 중심에서 평균 반지름의 이 비율보다 가까우면 접선 조각 루프로 본다
MAX_NORMAL_ALIGNMENT = 0.7 # 루프를 따라 면 법선이 절단 축과 이보다 나란하면(≈45° 안) 관 단면이 아니라 돌출부 조각이다 (목은 어깨·턱으로 벌어져 0.5 까지 나온다)
STABILITY_SHIFT_RATIO = 0.15  # 축 점수를 매길 때 평면을 평균 반지름의 이 비율만큼 앞뒤로 옮겨 둘레 변화를 본다
STABILITY_SCALE_RATIO = 0.03  # 이동 폭 하한 = 모델 크기 × 이 비율 (작은 돌출부 조각은 이 폭 안에서 사라진다)
STABILITY_MIN_RATIO = 0.5  # 세 위치 둘레의 min/max 가 이보다 작으면 관 단면이 아니다
AXIS_ROUNDS = 6            # 법선 공분산 축 보정 반복 상한
AXIS_BAND_RATIO = 0.35     # 단면 둘레에서 평균 반지름의 이 배수 안의 면으로 축을 잰다 — 넓히면 목 위아래로 벌어지는 면이 들어와 축이 뒤집힌다
AXIS_SAMPLES = 48          # 둘레를 이만큼 다시 찍어 근처 면을 모은다
AXIS_MIN_FACES = 12
AXIS_PERIMETER_GROWTH = 1.6  # 보정한 단면 둘레가 최소 둘레 단면의 이 배수를 넘으면 이웃 부위까지 벤 것이다
AXIS_CONVERGED = math.cos(math.radians(0.5))
RESAMPLE_COUNT = 32
RIM_OK_RATIO = 0.5         # ring_cut.RIM_LENGTH_MIN_RATIO 와 같은 기준 (단독 임포트를 위해 복제)
PLANE_EPSILON = 1.0e-9


@dataclass(frozen=True)
class RingEstimate:
    center: Vector3
    axis: Vector3
    radius: float
    thickness: float
    loop: tuple[Vector3, ...]


class MeshData:
    """정점 좌표 열과 면(정점 인덱스 열) 열. bpy 메시를 이 모양으로 넘기면 된다."""

    def __init__(self, vertices, faces):
        self.vertices = tuple(tuple(v) for v in vertices)
        self.faces = tuple(tuple(f) for f in faces)


class SectionMesh:
    """단면 계산용으로 미리 삼각화하고(가능하면 numpy 배열로) 준비한 메시. 클릭 한 번에 수백 번 자르므로 한 번만 만든다."""

    def __init__(self, mesh):
        self.vertices = mesh.vertices
        self.triangles = [tri for face in mesh.faces for tri in _fan(face)]
        self.np = None
        try:
            import numpy as np
        except ImportError:  # 순수 파이썬 폴백 — 느리지만 같은 결과
            return
        self.np = np
        self.V = np.asarray(mesh.vertices, dtype=float)
        self.T = np.asarray(self.triangles, dtype=int)
        e1 = self.V[self.T[:, 1]] - self.V[self.T[:, 0]]
        e2 = self.V[self.T[:, 2]] - self.V[self.T[:, 0]]
        normals = np.cross(e1, e2)
        lengths = np.linalg.norm(normals, axis=1)
        lengths[lengths < 1.0e-30] = 1.0
        self.N = normals / lengths[:, None]
        self.A = lengths * 0.5                                   # 삼각형 넓이 — 법선 공분산 가중치
        self.C = (self.V[self.T[:, 0]] + self.V[self.T[:, 1]] + self.V[self.T[:, 2]]) / 3.0


def section_loops(mesh, center, normal) -> list[tuple[list, bool, float]]:
    """평면과 메시의 교차 폴리라인들. (점 열, 닫힘, 면 법선과 평면 법선의 평균 |내적|) 목록.
    교차점은 엣지 키로 이어 붙여 좌표 오차에 흔들리지 않는다. 세 번째 값은 관을 수직으로 자른 단면이면 0 에 가깝고,
    턱·발등처럼 돌출부를 얇게 베어 낸 조각 루프면 1 에 가깝다. mesh 는 MeshData 나 SectionMesh."""
    section = mesh if isinstance(mesh, SectionMesh) else SectionMesh(mesh)
    points: dict = {}
    links: dict = {}
    alignment: dict = {}
    vertices = section.vertices
    if section.np is not None:
        np = section.np
        signed = (section.V - np.asarray(center, dtype=float)) @ np.asarray(normal, dtype=float)
        if np.any(np.abs(signed) < PLANE_EPSILON):
            signed = signed + PLANE_EPSILON * PLANE_NUDGE
        negative = signed < 0.0
        tri_negative = negative[section.T]
        crossing_index = np.nonzero(tri_negative.any(axis=1) & ~tri_negative.all(axis=1))[0]
        tri_alignment = section.N[crossing_index] @ np.asarray(normal, dtype=float)
        candidates = ((section.triangles[i], float(tri_alignment[k])) for k, i in enumerate(crossing_index))
        signed = signed.tolist()
    else:
        signed = [(v[0] - center[0]) * normal[0] + (v[1] - center[1]) * normal[1] + (v[2] - center[2]) * normal[2] for v in vertices]
        if any(abs(d) < PLANE_EPSILON for d in signed):
            signed = [d + PLANE_EPSILON * PLANE_NUDGE for d in signed]
        candidates = ((tri, None) for tri in section.triangles)
    for tri, tri_align in candidates:
        crossing = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            da, db = signed[a], signed[b]
            if (da < 0.0) == (db < 0.0):
                continue
            key = (a, b) if a < b else (b, a)
            if key not in points:
                t = da / (da - db)
                va, vb = vertices[a], vertices[b]
                points[key] = (va[0] + (vb[0] - va[0]) * t, va[1] + (vb[1] - va[1]) * t, va[2] + (vb[2] - va[2]) * t)
            crossing.append(key)
        if len(crossing) == 2 and crossing[0] != crossing[1]:
            links.setdefault(crossing[0], []).append(crossing[1])
            links.setdefault(crossing[1], []).append(crossing[0])
            if tri_align is None:
                face_normal = _unit(_cross(
                    tuple(vertices[tri[1]][i] - vertices[tri[0]][i] for i in range(3)),
                    tuple(vertices[tri[2]][i] - vertices[tri[0]][i] for i in range(3)),
                ))
                tri_align = _dot(face_normal, normal)
            alignment[frozenset(crossing)] = tri_align
    loops = []
    seen: set = set()
    for start in points:
        if start in seen or start not in links:
            continue
        component = _component(links, start)
        ends = [k for k in component if len(links[k]) == 1]
        origin = ends[0] if ends else start
        ordered = [origin]
        seen.add(origin)
        previous = None
        current = origin
        while True:
            following = [k for k in links[current] if k != previous and k not in seen]
            if not following:
                break
            previous, current = current, following[0]
            ordered.append(current)
            seen.add(current)
        seen |= component
        # 면 3장이 공유하는 엣지(비매니폴드)는 차수 4 접합점을 만들어 끝점이 없어도 되돌아오지 않는다 — 실제 폐합만 닫힘으로 본다
        closed = not ends and len(ordered) >= 3 and len(ordered) == len(component) and origin in links[ordered[-1]]
        pairs = list(zip(ordered, ordered[1:] + ([ordered[0]] if closed else [])))
        dots = [alignment.get(frozenset(pair), 0.0) for pair in pairs]
        mean_alignment = sum(abs(d) for d in dots) / max(1, len(dots))
        loops.append(([points[k] for k in ordered], closed, mean_alignment, _weighted_spread(pairs, dots, points)))
    return loops


def _weighted_spread(pairs, dots, points) -> float:
    """단면을 따라 (면 법선 · 절단 축) 의 호 길이 가중 표준편차. 수직 단면이면 테이퍼가 있어도 0 에 가깝다."""
    total = mean = 0.0
    weights = []
    for (a, b), dot in zip(pairs, dots):
        length = _distance(points[a], points[b])
        weights.append(length)
        total += length
        mean += dot * length
    if total <= 0.0:
        return 0.0
    mean /= total
    return sqrt(sum(w * (d - mean) ** 2 for w, d in zip(weights, dots)) / total)


def nearest_loop(loops, point):
    """정점이 point 에 가장 가까운 루프."""
    best = None
    best_distance = float("inf")
    for loop in loops:
        distance = min(_distance(p, point) for p in loop[0])
        if distance < best_distance:
            best, best_distance = loop, distance
    return best


def perimeter(points, closed: bool) -> float:
    count = len(points)
    if count < 2:
        return 0.0
    pairs = count if closed else count - 1
    return sum(_distance(points[i], points[(i + 1) % count]) for i in range(pairs))


def estimate_ring(mesh, hit, normal, scale: float) -> RingEstimate | None:
    """클릭 점을 지나는 평면들 중 단면 루프 둘레가 가장 짧은 방향을 축으로 고른다.

    중심을 먼저 추정하지 않는다 — 표면 점에서 잰 현은 지름 방향이 가장 길고 축 방향으로는 무한히 길어져
    두께로 중심을 잡는 방식은 턱 아래처럼 법선이 기울어진 곳에서 머리 속으로 들어갔다(실측). 대신 평면이 클릭 점을
    지나게 하면 그 점을 포함하는 루프가 곧 단면이고, 루프 중심이 관 중심이다."""
    normal = _unit(normal)
    section = mesh if isinstance(mesh, SectionMesh) else SectionMesh(mesh)

    def measure(axis):
        loop = nearest_loop(section_loops(section, hit, axis), hit)
        if loop is None or not loop[1]:
            return float("inf"), None
        points = loop[0]
        centroid = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
        distances = [_distance(p, centroid) for p in points]
        mean_radius = sum(distances) / len(points)
        # 표면에 거의 접하는 평면은 클릭 점 둘레의 작은 조각 루프를 만든다 — 클릭 점은 관 단면 위에 있으므로
        # 루프 중심에서 최소 반지름만큼은 떨어져 있어야 한다 (평균 반지름 기준은 납작한 팔뚝 단면을 기각했다)
        if mean_radius <= 0.0 or _distance(centroid, hit) < min(distances) * HIT_OFFSET_MIN_RATIO:
            return float("inf"), None
        # 턱·발등 같은 돌출부를 베어 낸 조각은 둘레가 짧아도 면 법선이 절단 축과 나란하다 — 관 단면은 법선이 축에 수직이다
        if loop[2] > MAX_NORMAL_ALIGNMENT:
            return float("inf"), None
        # 관 단면은 평면을 조금 옮겨도 둘레가 비슷하지만 턱 같은 돌출부 조각은 한쪽으로는 사라지고 다른 쪽으로는
        # 머리를 크게 벤다(실측: 0.45 → 0.02 / 열림). 세 위치가 모두 닫혀 있고 둘레가 절반 이상 비슷해야 하며,
        # 점수는 그중 최대 둘레다. 이동 폭은 반지름 비율과 모델 크기 비율 중 큰 쪽이다
        lengths = [perimeter(points, True)]
        shift = max(mean_radius * STABILITY_SHIFT_RATIO, scale * STABILITY_SCALE_RATIO)
        for sign in (-1.0, 1.0):
            shifted_center = tuple(hit[i] + axis[i] * shift * sign for i in range(3))
            shifted = nearest_loop(section_loops(section, shifted_center, axis), shifted_center)
            if shifted is None or not shifted[1]:
                return float("inf"), None
            lengths.append(perimeter(shifted[0], True))
        if min(lengths) < max(lengths) * STABILITY_MIN_RATIO:
            return float("inf"), None
        return max(lengths) * (1.0 + loop[2]), (loop, centroid, mean_radius)

    found = _search_axis(measure, normal)
    if found is None:
        return None
    axis, (loop, centroid, mean_radius) = found
    axis, loop, centroid, mean_radius = _refine_axis(section, hit, axis, loop, centroid, mean_radius)
    points = loop[0]
    radius = max(_distance(p, centroid) for p in points)
    return RingEstimate(centroid, axis, radius, mean_radius * 2.0, tuple(points))


def _refine_axis(section, hit, axis, loop, centroid, mean_radius):
    """최소 둘레 축을 관의 실제 축으로 바로잡는다 — 단면 주변 면 법선의 공분산에서 가장 작은 고유벡터.

    최소 둘레 기준은 굵기가 변하는 관(팔뚝→손목, 반바지 통, 목→어깨)에서 평면을 가는 쪽으로 기울여 둘레를
    줄이고, 옆 부위와 붙은 자리(반바지 두 통)에서는 붙은 큰 루프를 피해 비스듬한 단면으로 달아난다(실측
    2026-09-23, 갱스터 허벅지가 수직에서 40°). 관 표면의 법선은 축에 수직이라 Σ n nᵀ 의 최소 고유벡터가 축이다
    (원뿔은 반각 35° 까지 성립). 단면 위상과 무관하므로 붙은 부위·테이퍼에 흔들리지 않는다. 단면 둘레를 따라
    반지름 × AXIS_BAND_RATIO 안의 면만 모으고, 새 축으로 클릭 점에서 다시 잘라 반복한다. 새 단면은 닫혀 있고
    둘레가 크게 늘지 않으면 받는다 — 축 탐색의 안정성 검사(평면을 옮긴 단면도 닫힐 것)는 붙은 부위 옆에서
    올바른 축까지 기각했다. (둘레 법선 편차 최소화·중심선 추적은 잡음과 테이퍼에 발산해 폐기했다.)"""
    start_perimeter = perimeter(loop[0], True)
    for _ in range(AXIS_ROUNDS):
        direction = _normal_axis(section, loop[0], mean_radius * AXIS_BAND_RATIO)
        if direction is None:
            break
        if _dot(direction, axis) < 0.0:
            direction = tuple(-d for d in direction)
        change = _dot(direction, axis)
        candidate = nearest_loop(section_loops(section, hit, direction), hit)
        if candidate is None or not candidate[1] or perimeter(candidate[0], True) > start_perimeter * AXIS_PERIMETER_GROWTH:
            break   # 단면이 열리거나 이웃 부위까지 크게 베면 앞 축을 쓴다
        new_centroid = _arc_centroid(candidate[0])
        distances = [_distance(p, new_centroid) for p in candidate[0]]
        if _distance(new_centroid, hit) < min(distances) * HIT_OFFSET_MIN_RATIO:
            break   # 클릭 점 둘레의 조각 루프
        # 안정성 검사(평면을 옮긴 단면 둘레 비)로 거르면 갱스터 허벅지의 올바른 보정까지 되돌린다 — 이웃 부위를
        # 함께 베는 위치는 패널의 둘레 비율(단면 급변)로 알린다
        axis, loop, centroid = direction, candidate, new_centroid
        mean_radius = sum(distances) / len(distances)
        if change >= AXIS_CONVERGED:
            break
    return axis, loop, centroid, mean_radius


def _normal_axis(section, points, reach: float):
    """points(단면 둘레) 에서 reach 안에 중심이 있는 삼각형들의 넓이 가중 법선 공분산 최소 고유벡터."""
    ring = resample_loop(points, AXIS_SAMPLES)
    if section.np is not None:
        np = section.np
        ring_array = np.asarray(ring, dtype=float)
        near = np.zeros(len(section.C), dtype=bool)
        for point in ring_array:
            near |= np.einsum("ij,ij->i", section.C - point, section.C - point) <= reach * reach
        if np.count_nonzero(near) < AXIS_MIN_FACES:
            return None
        normals = section.N[near]
        weights = section.A[near]
        matrix = (normals * weights[:, None]).T @ normals
        values, vectors = np.linalg.eigh(matrix)
        return _unit(tuple(float(v) for v in vectors[:, 0]))
    matrix = [[0.0] * 3 for _ in range(3)]
    count = 0
    for tri in section.triangles:
        a, b, c = (section.vertices[i] for i in tri)
        center = tuple((a[i] + b[i] + c[i]) / 3.0 for i in range(3))
        if not any(_distance(center, p) <= reach for p in ring):
            continue
        cross = _cross(tuple(b[i] - a[i] for i in range(3)), tuple(c[i] - a[i] for i in range(3)))
        area = sqrt(_dot(cross, cross)) * 0.5
        if area <= 0.0:
            continue
        n = _unit(cross)
        count += 1
        for i in range(3):
            for j in range(3):
                matrix[i][j] += area * n[i] * n[j]
    if count < AXIS_MIN_FACES:
        return None
    return _smallest_eigenvector(matrix)


def _smallest_eigenvector(matrix):
    """대칭 3x3 행렬의 가장 작은 고유값 고유벡터 — 야코비 회전 (numpy 없는 폴백)."""
    a = [row[:] for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(50):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda pq: abs(a[pq[0]][pq[1]]))
        if abs(a[p][q]) < 1.0e-15:
            break
        theta = 0.5 * math.atan2(2.0 * a[p][q], a[q][q] - a[p][p])
        c, s_ = cos(theta), sin(theta)
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p], a[k][q] = c * akp - s_ * akq, s_ * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k], a[q][k] = c * apk - s_ * aqk, s_ * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p], v[k][q] = c * vkp - s_ * vkq, s_ * vkp + c * vkq
    smallest = min(range(3), key=lambda i: a[i][i])
    return _unit(tuple(v[k][smallest] for k in range(3)))


def _arc_centroid(points):
    """호 길이로 가중한 폐곡선 중심 — 정점 밀도가 고르지 않은 단면에서도 치우치지 않는다."""
    total = 0.0
    acc = [0.0, 0.0, 0.0]
    count = len(points)
    for index in range(count):
        a, b = points[index], points[(index + 1) % count]
        length = _distance(a, b)
        total += length
        for i in range(3):
            acc[i] += (a[i] + b[i]) * 0.5 * length
    if total <= 0.0:
        return tuple(sum(p[i] for p in points) / count for i in range(3))
    return tuple(value / total for value in acc)


def _search_axis(measure, normal):
    """반구 전체의 방향을 고르게 훑어 점수가 가장 낮은 축을 찾고, 그 주변을 단계적으로 좁혀 다듬는다.

    접평면 후보만으로 시작하면 턱 아래처럼 후보가 전부 기각되는 곳에서 탐색이 끊긴다(실측). 축은 부호가 없으므로
    반구만 본다."""
    best_axis, best_value, best_loop = None, float("inf"), None
    for axis in _hemisphere_directions(normal, HEMISPHERE_SAMPLES):
        value, loop = measure(axis)
        if value < best_value:
            best_axis, best_value, best_loop = axis, value, loop
    if best_loop is None:
        return None
    for step in REFINE_STEPS:
        improved = True
        rounds = 0
        while improved and rounds < REFINE_MAX_ROUNDS:
            improved = False
            rounds += 1
            t, b = _tangent_frame(best_axis)
            for k in range(REFINE_AZIMUTHS):
                angle = 2 * pi * k / REFINE_AZIMUTHS
                direction = _unit(tuple(
                    best_axis[i] * cos(step) + (t[i] * cos(angle) + b[i] * sin(angle)) * sin(step) for i in range(3)
                ))
                value, loop = measure(direction)
                if value < best_value:
                    best_axis, best_value, best_loop = direction, value, loop
                    improved = True
    return best_axis, best_loop


def _hemisphere_directions(pole, count: int):
    """pole 을 기준으로 한 반구를 피보나치 격자로 고르게 덮는 단위 벡터들."""
    t, b = _tangent_frame(pole)
    golden = pi * (3.0 - sqrt(5.0))
    directions = []
    for k in range(count):
        z = 1.0 - (k + 0.5) / count          # (0, 1]: pole 쪽 반구
        r = sqrt(max(0.0, 1.0 - z * z))
        angle = k * golden
        directions.append(_unit(tuple(pole[i] * z + (t[i] * cos(angle) + b[i] * sin(angle)) * r for i in range(3))))
    return directions


def slice_ring(mesh, center, axis, hit, radius: float = 0.0):
    """center 를 지나 axis 에 수직인 단면 중 hit 에 가장 가까운 닫힌 루프. 없으면 None. mesh 는 MeshData 나 SectionMesh."""
    del radius  # 예전 절단 반경 인자 — 전체 면을 자르므로 쓰지 않는다
    loop = nearest_loop(section_loops(mesh, center, axis), hit)
    if loop is None or not loop[1]:
        return None
    return tuple(loop[0])


def rim_ratio(mesh, center, axis, hit, radius: float, half_width: float) -> float:
    """center ± half_width 두 단면 둘레의 min/max. 발등·가슴을 함께 지나면 낮아진다."""
    mesh = mesh if isinstance(mesh, SectionMesh) else SectionMesh(mesh)
    lengths = []
    for sign in (-1.0, 1.0):
        shifted_center = tuple(center[i] + axis[i] * half_width * sign for i in range(3))
        shifted_hit = tuple(hit[i] + axis[i] * half_width * sign for i in range(3))
        loop = slice_ring(mesh, shifted_center, axis, shifted_hit, radius)
        if loop is None:
            return 0.0
        lengths.append(perimeter(loop, True))
    if max(lengths) <= 0.0:
        return 0.0
    return min(lengths) / max(lengths)


def resample_loop(points, count: int = RESAMPLE_COUNT) -> tuple[Vector3, ...]:
    """닫힌 폴리라인을 호 길이 기준 count 개로 다시 찍는다."""
    total = perimeter(points, True)
    if total <= 0.0 or len(points) < 3:
        return tuple(points)
    step = total / count
    result = []
    index = 0
    travelled = 0.0
    segment_start = points[0]
    segment_end = points[1 % len(points)]
    segment_length = _distance(segment_start, segment_end)
    target = 0.0
    while len(result) < count:
        while travelled + segment_length < target - 1.0e-12:
            travelled += segment_length
            index += 1
            segment_start = points[index % len(points)]
            segment_end = points[(index + 1) % len(points)]
            segment_length = _distance(segment_start, segment_end)
        ratio = 0.0 if segment_length <= 0.0 else (target - travelled) / segment_length
        result.append(tuple(segment_start[i] + (segment_end[i] - segment_start[i]) * ratio for i in range(3)))
        target += step
    return tuple(result)


# --- 보조 ------------------------------------------------------------------------

def _fan(face):
    if len(face) == 3:
        yield face
        return
    for index in range(1, len(face) - 1):
        yield (face[0], face[index], face[index + 1])


def _component(links: dict, start):
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for neighbor in links.get(current, ()):
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen


def _tangent_frame(normal):
    axis = min(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), key=lambda a: abs(_dot(a, normal)))
    t = _unit(tuple(axis[i] - normal[i] * _dot(axis, normal) for i in range(3)))
    return t, _cross(normal, t)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    length = sqrt(_dot(v, v))
    return v if length < 1.0e-15 else (v[0] / length, v[1] / length, v[2] / length)


def _distance(a, b) -> float:
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)
