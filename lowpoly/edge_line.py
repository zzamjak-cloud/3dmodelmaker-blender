"""엣지 선 가이드 — 사용자가 표면에 그린 열린 선을 따라 리토폴로지 와이어를 정렬한다.

QuadriFlow 의 sharp 제약은 입력 삼각형의 이면각 60° 초과와 경계 엣지뿐이다(원본 ComputeSharpEdges). 복셀 리메시·
데시메이트를 거친 입력은 메카닉의 하드 엣지가 뭉개져 60° 를 못 넘으므로 와이어가 모서리를 따라가지 않는다.
그래서 선을 **입력의 경계**로 바꾼다: 선을 따라 입력 메시의 엣지 경로를 찾아 그 정점을 선 위로 옮기고, 경로
엣지를 split 해 폭 0 의 틈(slit)을 낸다. 경계 보존 QuadriFlow 는 틈 양쪽을 sharp 로 받아 출력 엣지를 틈에 붙인다.
출력의 틈 경계 루프는 양 끝(팁)에서 두 사슬로 나뉘므로, 링 접합과 같은 DP 짝(ring_cut.pair_steps)으로 한쪽 정점을
다른 쪽에 용접하고 선 위에 놓는다. 링 가이드는 띠를 지워 닫힌 링을 앵커로 쓰지만 열린 선은 띠를 지우면 캡이
생기지 않아 폭 0 의 틈이 더 단순하다.

bmesh 를 쓰는 함수는 Blender Python 에서만 호출한다. 순수 계산은 bpy 없이 돈다(상대 임포트 없음 — 유닛 테스트가
파일 단위로 불러온다).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from math import sqrt

REACH_RATIO = 0.75          # 출력 틈 경계 정점이 선에서 떠도 되는 거리 = 추정 출력 엣지 × 이 비율 (QuadriFlow 경계 정점은 엣지 절반쯤 뜬다)
JOIN_RATIO = 0.5            # 선 끝점이 추정 출력 엣지 × 이 비율 안에서 맞닿으면 한 사슬로 잇는다
RESAMPLE_RATIO = 0.25       # 선을 추정 출력 엣지의 이 비율 간격으로 다시 찍는다 — 자르기·거리 계산의 해상도
MIN_LENGTH_EDGES = 2.0      # 추정 출력 엣지 이 개수보다 짧은 선(조각)은 와이어 하나도 못 정한다
PLANE_MARGIN_EDGES = 2.0    # 반쪽 리토폴로지에서 틈은 대칭면에서 출력 엣지 이만큼 떨어진 곳까지만 — 틈이 대칭면 경계에 닿으면 반쪽 루프가 깨진다
SNAP_PLANE_MARGIN_EDGES = 0.5   # 사슬 펴기(입력 불변)는 대칭면 경계 정점만 피하면 된다
BAND_MARGIN_EDGES = 2.0     # 링 절단 띠에서도 이만큼 떨어진 곳까지만 — 틈이 띠 경계에 닿으면 링 접합 루프가 깨진다
DUPLICATE_RATIO = 0.7       # 선 점의 이 비율 이상이 앞선 선의 reach×2 안이면 같은 선(대칭 반사본·좌우를 따로 그린 선)으로 본다
CORRIDOR_RATIO = 1.0        # 입력 엣지 경로는 선에서 입력 평균 엣지 × 이 비율 안의 정점만 지난다
CORRIDOR_MIN_RATIO = 0.6    # 통로 반경 하한 = 추정 출력 엣지 × 이 비율. 선은 원본 표면 위, 입력은 속 채운 복셀 표면이라
                            # 1cm 넘게 떨어진 곳이 있다(실측 메카닉 허벅지: 통로 1.2cm 에서 경로가 끊김)
PATH_PENALTY = 4.0          # 경로 가중: 엣지 길이 × (1 + 이 값 × (선까지 거리 / 통로 반경)²) — 선에 붙어 가게 한다
PATH_DETOUR_MAX = 1.8       # 경로 길이가 선 길이의 이 배수를 넘으면 통로가 끊겨 돌아간 것이다
SLIT_WIDTH_RATIO = 0.3      # 틈을 출력 엣지 × 이 비율 폭으로 벌린다(양 끝은 뾰족). 폭 0 의 틈이 짧으면 QuadriFlow 가 넓이 0 캡으로
                            # 메운 뒤 퇴화 면을 접어 양쪽을 하나로 지퍼처럼 닫았다(실측 메카닉 허벅지: 8엣지 틈, 6시드 모두 닫힘)
SLIT_LOOPS_MAX = 8          # 한 선에서 용접할 틈 루프 수 상한
CHAIN_SNAP_RATIO = 0.5      # 출력 엣지 사슬 정점을 선 위로 옮길 때 이웃 엣지 최단 길이의 이 비율까지만
ALIGN_PENALTY = 3.0         # 출력 사슬 경로 가중에 (1 - |선 방향과의 cos|) × 이 값을 더한다 — 선을 가로지르는 엣지를 피한다
SLIT_KEPT_RATIO = 0.8       # 시드 고르기: 선 점의 이 비율 이상 근처에 출력 정점이 있어야 틈을 지킨 출력이다
PIN_ATTRIBUTE = "lp3d_edge_line"   # 용접한 선 정점 표식(정점 int 속성) — 뒤의 슈링크랩이 이 정점을 움직이지 않는다
SNAP_LIMIT = 0.45           # 경로 정점을 선 위로 옮길 때 이웃 엣지 최단 길이의 이 비율까지만 — 더 옮기면 면이 접힌다
LOOP_NEAR_RATIO = 0.8       # 출력 경계 루프 정점의 이 비율 이상이 선 reach 안이면 그 선의 틈이다
DENSIFY_RATIO = 0.35        # 선 근처 입력 엣지를 추정 출력 엣지 × 이 비율 이하가 되도록 쪼갠다 — 입력이 출력보다 성기면
                            # 경로가 선 끝에 못 닿고 지그재그가 남는다(실측 메카닉: 입력 4.7cm · 출력 3.8cm 에서 커버리지 0.4~0.5)
DENSIFY_ROUNDS = 4
TINY_EDGE = 1.0e-4          # Blender QuadriFlow 사전 검사가 길이 0 으로 보는 축별 차이
CAP_REACH_RATIO = 0.5       # 정점이 모두 선에서 출력 엣지 × 이 비율 안인 면은 QuadriFlow 가 틈을 덮은 캡이다 — 틈이 있으면 표면 면은
                            # 선을 가로지르지 못하고, 선 끝 근처 표면 면도 정점 넷이 지름 출력 엣지 원 안에 모일 수 없다


@dataclass(frozen=True)
class EdgeLine:
    name: str
    points: tuple            # 다시 찍은 로컬 좌표 점 열. 닫힌 선은 마지막 점이 첫 점과 같다
    reach: float             # 출력 틈 경계 정점 허용 거리
    edge: float              # 추정 출력 엣지 길이
    closed: bool = False     # 판 외곽처럼 한 바퀴 도는 선


# --- 순수 계산 -------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _distance(a, b) -> float:
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def length(points) -> float:
    return sum(_distance(points[i], points[i + 1]) for i in range(len(points) - 1))


def resample(points, step: float) -> tuple:
    """열린 점 열을 step 간격(마지막 구간은 짧을 수 있다)으로 다시 찍는다. 양 끝점은 그대로 둔다."""
    points = [tuple(p) for p in points]
    if len(points) < 2 or step <= 0.0:
        return tuple(points)
    total = length(points)
    if total <= 0.0:
        return (points[0],)
    count = max(1, int(round(total / step)))
    targets = [total * k / count for k in range(count + 1)]
    result = []
    walked = 0.0
    index = 0
    for target in targets:
        while index < len(points) - 2 and walked + _distance(points[index], points[index + 1]) < target:
            walked += _distance(points[index], points[index + 1])
            index += 1
        a, b = points[index], points[index + 1]
        span = _distance(a, b)
        t = 0.0 if span <= 0.0 else min(1.0, max(0.0, (target - walked) / span))
        result.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
    result[0], result[-1] = points[0], points[-1]
    return tuple(result)


def closest(points, point):
    """열린 점 열 위에서 point 에 가장 가까운 점. (거리, 시작점부터의 호 길이, 점)."""
    best = None
    walked = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        ab = _sub(b, a)
        span2 = _dot(ab, ab)
        t = 0.0 if span2 <= 0.0 else min(1.0, max(0.0, _dot(_sub(point, a), ab) / span2))
        foot = (a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t)
        distance = _distance(point, foot)
        if best is None or distance < best[0]:
            best = (distance, walked + sqrt(span2) * t, foot)
        walked += sqrt(span2)
    if best is None:
        only = tuple(points[0])
        return _distance(point, only), 0.0, only
    return best


def edge_lines(guides, edge: float) -> tuple:
    """(이름, 로컬 점 열) 가이드들을 EdgeLine 으로. 끝점이 맞닿은 선은 사슬·닫힌 고리로 합친다(merge_chains).
    너무 짧은 선은 뺀다. 선을 3D 로 평활하지 않는다 — 골을 지나는 선이 표면에서 떠 커버리지가 0.9 → 0.3 으로 떨어졌다."""
    lines = []
    for name, points, closed in merge_chains(guides, edge * JOIN_RATIO):
        if len(points) < 2:
            continue
        if closed:
            points = list(points) + [points[0]]
        sampled = resample(points, edge * RESAMPLE_RATIO)
        if length(sampled) < edge * MIN_LENGTH_EDGES:
            continue
        lines.append(EdgeLine(name, sampled, edge * REACH_RATIO, edge, closed))
    return tuple(lines)


def merge_chains(guides, tolerance: float) -> list:
    """끝점이 tolerance 안에서 맞닿은 선들을 하나로 잇는다. [(이름, 점 열, 닫힘)].

    판 외곽을 클릭으로 그리면 한 변씩 선이 나뉘고 모서리에서 끝점이 맞닿는다(실측 사용자 21선). 따로 다루면 모서리마다
    틈 끝(팁)이 생겨 용접이 깨졌다. 한 점에 끝이 정확히 둘 모이는 곳만 잇는다 — 셋 이상(T·X 갈림)은 그대로 둔다.
    한 바퀴 돌아 처음 선의 시작에 닿으면 닫힌 고리다."""
    items = [(name, [tuple(p) for p in points]) for name, points in guides if len(points) >= 2]
    ends = [(i, side) for i in range(len(items)) for side in (0, 1)]

    def at(end):
        i, side = end
        return items[i][1][0] if side == 0 else items[i][1][-1]

    partner = {}
    for end in ends:
        near = [other for other in ends if other != end and _distance(at(end), at(other)) <= tolerance]
        if len(near) == 1:
            other = near[0]
            mutual = [e for e in ends if e != other and _distance(at(other), at(e)) <= tolerance]
            if mutual == [end]:
                partner[end] = other
    result, used = [], set()
    for start in range(len(items)):
        if start in used:
            continue
        # 사슬의 한쪽 끝까지 거슬러 올라간다 (닫힌 고리면 시작으로 돌아온다)
        i, side = start, 0
        visited = {start}
        while (i, side) in partner:
            j, other_side = partner[(i, side)]
            if j in visited:
                break
            visited.add(j)
            i, side = j, 1 - other_side
        head = (i, side)
        chain_points, names, closed = [], [], False
        i, enter = head
        while True:
            used.add(i)
            names.append(items[i][0])
            pts = items[i][1] if enter == 0 else items[i][1][::-1]
            chain_points.extend(pts if not chain_points else pts[1:])
            exit_end = (i, 1 - enter)
            nxt = partner.get(exit_end)
            if nxt is None:
                break
            j, side = nxt
            if j in used:
                closed = (j, side) == head
                break
            i, enter = j, side
        result.append(("+".join(names), chain_points, closed))
    return result


def reflect_lines(lines, component: int = 0) -> tuple:
    """축 성분을 뒤집은 선들(합치지 않는다)."""
    def flip(p):
        q = list(p)
        q[component] = -q[component]
        return tuple(q)
    return tuple(EdgeLine(line.name, tuple(flip(p) for p in line.points), line.reach, line.edge, line.closed)
                 for line in lines)


def trim_lines(lines, keep) -> tuple:
    """keep(점) 이 거짓인 구간을 잘라 낸 조각들. 짧은 조각은 버린다. 조각이 여럿이면 이름 뒤에 번호를 붙인다."""
    result = []
    for line in lines:
        pieces, current = [], []
        for point in line.points:
            if keep(point):
                current.append(point)
            elif current:
                pieces.append(current)
                current = []
        if current:
            pieces.append(current)
        if line.closed and len(pieces) == 1 and len(pieces[0]) == len(line.points):
            result.append(line)   # 잘린 데 없는 닫힌 고리는 그대로
            continue
        if line.closed and len(pieces) >= 2 and keep(line.points[0]) and keep(line.points[-1]):
            # 닫힌 고리가 잘리면 마지막 조각과 첫 조각은 이음매를 사이에 둔 한 조각이다
            pieces = [pieces[-1] + pieces[0][1:]] + pieces[1:-1]
        pieces = [piece for piece in pieces if len(piece) >= 2 and length(piece) >= line.edge * MIN_LENGTH_EDGES]
        for index, piece in enumerate(pieces):
            name = line.name if len(pieces) == 1 else f"{line.name}#{index + 1}"
            result.append(EdgeLine(name, tuple(piece), line.reach, line.edge))
    return tuple(result)


def dedupe_lines(lines) -> tuple:
    """앞선 선과 거의 겹치는 선을 뺀다 — 대칭 반쪽에서 원본 선과 반사본, 좌우를 따로 그린 선이 겹친다."""
    kept = []
    for line in lines:
        duplicate = False
        for other in kept:
            near = sum(1 for p in line.points if closest(other.points, p)[0] <= max(line.reach, other.reach) * 2.0)
            if near >= len(line.points) * DUPLICATE_RATIO:
                duplicate = True
                break
        if not duplicate:
            kept.append(line)
    return tuple(kept)


def plane_keep(margin: float, component: int = 0):
    """반쪽(양의 쪽) 리토폴로지에서 쓸 수 있는 점 — 대칭면에서 margin 넘게 양의 쪽."""
    return lambda point: point[component] >= margin


def is_slit_loop(coords, lines) -> bool:
    """출력 경계 루프(정점 좌표들)가 어느 엣지 선의 틈인가."""
    coords = list(coords)
    if not coords or not lines:
        return False
    for line in lines:
        near = sum(1 for c in coords if closest(line.points, c)[0] <= line.reach)
        if near >= len(coords) * LOOP_NEAR_RATIO:
            return True
    return False


def split_at_tips(ordered, params):
    """닫힌 순서 정점 열을 호 길이가 가장 작은/큰 두 팁에서 나눈 두 사슬 (둘 다 첫 팁 → 둘째 팁). 팁이 같으면 None."""
    n = len(ordered)
    first = min(range(n), key=lambda i: params[i])
    second = max(range(n), key=lambda i: params[i])
    if first == second:
        return None
    forward, backward = [], []
    i = first
    while True:
        forward.append(ordered[i])
        if i == second:
            break
        i = (i + 1) % n
    i = first
    while True:
        backward.append(ordered[i])
        if i == second:
            break
        i = (i - 1) % n
    return forward, backward


# --- bmesh (Blender 전용) ------------------------------------------------------------

def _distances(coords, points):
    """정점 좌표들에서 열린 점 열까지의 거리 목록. numpy 가 있으면 벡터화한다."""
    try:
        import numpy as np
    except ImportError:
        return [closest(points, c)[0] for c in coords]
    if not coords:
        return []
    p = np.asarray(coords, dtype=float)
    a = np.asarray(points[:-1], dtype=float)
    b = np.asarray(points[1:], dtype=float)
    ab = b - a
    span2 = np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-24)
    best = np.full(len(p), np.inf)
    for k in range(len(a)):   # 선분 수(수십~수백)만큼만 돈다 — 정점 수 × 선분 수 행렬은 메모리가 크다
        t = np.clip(((p - a[k]) @ ab[k]) / span2[k], 0.0, 1.0)
        foot = a[k] + t[:, None] * ab[k]
        np.minimum(best, np.linalg.norm(p - foot, axis=1), out=best)
    return best.tolist()


def snap_lines(bm, lines) -> tuple:
    """QuadriFlow 출력에서 선을 따라가는 엣지 사슬을 선(원본 표면) 위로 펴고 고정 표식을 남긴다. (편 선, 실패 사유)."""
    layer = bm.verts.layers.int.get(PIN_ATTRIBUTE) or bm.verts.layers.int.new(PIN_ATTRIBUTE)
    done, failed = [], []
    for line in lines:
        if snap_chain(bm, line, layer):
            snap_chain(bm, line, layer)   # 한 번에 이웃 엣지 절반까지만 옮기므로 한 번 더
            done.append(line.name)
        else:
            failed.append(f"엣지 선 '{line.name}' 을 따라가는 출력 엣지를 찾지 못했습니다")
    return tuple(done), tuple(failed)


def cut_slits(bm, lines) -> tuple:
    """선마다 입력 메시 엣지 경로를 찾아 선 위로 정점을 옮기고 split 해 틈을 낸다. (낸 선, 건너뛴 사유) 를 돌려준다.

    경로는 선 근처 통로 안의 정점만 지나는 최단 경로(선에서 멀수록 비싸다)다. 경계 정점(대칭면·링 띠)과 앞선 선이
    쓴 정점은 지나지 않는다 — 두 선이 교차하면 뒤의 선은 건너뛴다."""
    import bmesh

    if not lines:
        return (), ()
    for line in lines:
        _densify(bm, line)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    coords = [tuple(v.co) for v in bm.verts]
    used = set()
    cut, skipped, split_edges, opened = [], [], [], []
    for line in lines:
        distances = _distances(coords, line.points)
        near = [e.calc_length() for e in bm.edges if min(distances[v.index] for v in e.verts) <= line.edge]
        mean_edge = sum(near) / len(near) if near else line.edge * DENSIFY_RATIO
        radius = max(mean_edge * CORRIDOR_RATIO, line.edge * CORRIDOR_MIN_RATIO, 1e-9)
        corridor = {v for v in bm.verts if distances[v.index] <= radius and not v.is_boundary and v not in used}
        if len(corridor) < 2:
            skipped.append(f"엣지 선 '{line.name}' 근처에 입력 메시 정점이 없어 쓰지 않았습니다")
            continue
        if line.closed:
            path = _closed_path(line, corridor, coords, radius)
            if path is None:
                skipped.append(f"엣지 선 '{line.name}' (닫힌 고리)을 따라 입력 메시 경로를 찾지 못했습니다")
                continue
            for vertex in path:
                _snap_to_line(vertex, line)
            used.update(path)
            for vertex in path:
                used.update(edge.other_vert(vertex) for edge in vertex.link_edges)
            ring = path + [path[0]]
            split_edges.extend(bm.edges.get((ring[i], ring[i + 1])) for i in range(len(path)))
            opened.append((line, [tuple(v.co) for v in path]))   # 팁이 없다 — 모든 정점이 둘로 갈린다
            cut.append(line)
            continue
        start = min(corridor, key=lambda v: _distance(tuple(v.co), line.points[0]))
        end = min(corridor, key=lambda v: _distance(tuple(v.co), line.points[-1]))
        if start is end:
            skipped.append(f"엣지 선 '{line.name}' 이 너무 짧습니다")
            continue
        path = _corridor_path(start, end, corridor, distances, radius)
        if path is None or length([tuple(v.co) for v in path]) > length(line.points) * PATH_DETOUR_MAX + 2.0 * radius:
            skipped.append(f"엣지 선 '{line.name}' 을 따라 입력 메시 경로를 찾지 못했습니다(다른 선과 교차하거나 "
                           "링 띠·대칭면에 너무 가깝습니다)")
            continue
        for vertex in path:
            _snap_to_line(vertex, line)
        used.update(path)
        for vertex in path:
            used.update(edge.other_vert(vertex) for edge in vertex.link_edges)   # 이웃 선끼리 한 면을 사이에 두지 않게
        split_edges.extend(bm.edges.get((path[i], path[i + 1])) for i in range(len(path) - 1))
        opened.append((line, [tuple(v.co) for v in path[1:-1]]))
        cut.append(line)
    split_edges = [e for e in split_edges if e is not None]
    if split_edges:
        bmesh.ops.split_edges(bm, edges=split_edges)
        bm.normal_update()   # 조밀화·poke·정점 옮김 뒤 법선이 낡았거나 0 이면 벌릴 방향을 못 잡는다
        _open_slits(bm, opened)
    return tuple(cut), tuple(skipped)


def _closed_path(line, corridor, coords, radius):
    """닫힌 고리 선을 따라가는 입력 메시 정점 고리(첫 정점 반복 없음). 두 반쪽을 따로 찾아 잇는다. 없으면 None."""
    half = len(line.points) // 2
    first = line.points[:half + 1]
    second = line.points[half:]
    d_first = _distances(coords, first)
    d_second = _distances(coords, second)
    near_first = {v for v in corridor if d_first[v.index] <= radius}
    near_second = {v for v in corridor if d_second[v.index] <= radius}
    if len(near_first) < 2 or len(near_second) < 2:
        return None
    start = min(near_first, key=lambda v: _distance(tuple(v.co), first[0]))
    middle = min(near_first, key=lambda v: _distance(tuple(v.co), first[-1]))
    if start is middle:
        return None
    one = _corridor_path(start, middle, near_first, d_first, radius)
    if one is None:
        return None
    blocked = set(one[1:-1])
    two = _corridor_path(middle, start, (near_second | {start, middle}) - blocked, d_second, radius)
    if two is None or len(one) + len(two) < 5:
        return None
    cycle = one + two[1:-1]
    if len(set(cycle)) != len(cycle):
        return None
    if length([tuple(v.co) for v in cycle + [cycle[0]]]) > length(line.points) * PATH_DETOUR_MAX + 2.0 * radius:
        return None
    return cycle


def _open_slits(bm, opened) -> None:
    """split 로 둘이 된 경로 내부 정점을 표면을 따라 선 양쪽으로 벌린다. 양 끝 팁은 그대로라 틈은 렌즈 모양이다."""
    by_position = {}
    for vertex in bm.verts:
        by_position.setdefault(tuple(vertex.co), []).append(vertex)
    for line, positions in opened:
        half = line.edge * SLIT_WIDTH_RATIO * 0.5
        for position in positions:
            copies = by_position.get(position, [])
            if len(copies) != 2:
                continue
            _distance_, s, _foot = closest(line.points, position)
            tangent = _tangent_at(line.points, s)
            for vertex in copies:
                if not vertex.link_faces:
                    continue
                normal = vertex.normal
                side = normal.cross(tangent)
                if side.length < 1e-9:
                    continue
                side.normalize()
                centre = sum((f.calc_center_median() for f in vertex.link_faces), vertex.co * 0.0) / len(vertex.link_faces)
                if (centre - vertex.co).dot(side) < 0.0:
                    side = -side
                shortest = min((e.calc_length() for e in vertex.link_edges), default=0.0)
                vertex.co += side * min(half, shortest * SNAP_LIMIT)


def _tangent_at(points, s):
    """호 길이 s 위치의 선 접선(mathutils.Vector)."""
    from mathutils import Vector

    walked = 0.0
    for i in range(len(points) - 1):
        span = _distance(points[i], points[i + 1])
        if walked + span >= s or i == len(points) - 2:
            tangent = Vector(points[i + 1]) - Vector(points[i])
            return tangent.normalized() if tangent.length > 0 else Vector((1.0, 0.0, 0.0))
        walked += span
    return Vector((1.0, 0.0, 0.0))


def _densify(bm, line) -> int:
    """선에서 band 안의 긴 입력 엣지를 반으로 쪼개기를 반복한다. 쪼갠 엣지 수.

    경계 엣지(대칭면·링 띠)는 쪼개지 않는다. 선은 링 띠에서 출력 엣지 두 개 넘게 떨어져 있어 띠 절단 n각형까지는
    닿지 않는다."""
    import bmesh

    target = line.edge * DENSIFY_RATIO
    total = 0
    for _ in range(DENSIFY_ROUNDS):
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        distances = _distances([tuple(v.co) for v in bm.verts], line.points)
        longest = max((e.calc_length() for e in bm.edges
                       if min(distances[v.index] for v in e.verts) <= line.edge), default=0.0)
        band = max(longest, target) * 1.5
        long_edges = [e for e in bm.edges
                      if e.calc_length() > target and not e.is_boundary
                      and min(distances[v.index] for v in e.verts) <= band]
        if not long_edges:
            break
        result = bmesh.ops.subdivide_edges(bm, edges=long_edges, cuts=1, use_grid_fill=True)
        # 쪼개며 생긴 n각형은 삼각형으로 — 그대로 두면 QuadriFlow 가 "Remeshing failed" 로 거절했다(실측 메카닉 왼허벅지).
        # triangulate 는 대각선이 이웃 면의 기존 엣지와 겹쳐 면 3장 엣지를 만들었다(같은 날, 배 선) — 가운데 정점을 세워
        # 부채로 나누는 poke 는 기존 정점끼리 잇지 않아 그럴 일이 없다
        ngons = list({f for f in result["geom"] if isinstance(f, bmesh.types.BMFace) and len(f.verts) > 3})
        if ngons:
            bmesh.ops.poke(bm, faces=ngons)
        # 얇은 삼각형을 쪼개면 정점이 한 점에 뭉쳐 축별 1e-4 미만 엣지(사전 검사 거절 사유)가 생긴다(실측 선 70개: 6~30개)
        tiny = {v for e in bm.edges if all(abs(e.verts[0].co[i] - e.verts[1].co[i]) < TINY_EDGE for i in range(3))
                for v in e.verts}
        if tiny:
            bmesh.ops.remove_doubles(bm, verts=list(tiny), dist=TINY_EDGE * 3.0)
        total += len(long_edges)
    return total


def _corridor_path(start, end, corridor, distances, radius, weight=None):
    """통로 정점만 지나는 start → end 가중 최단 경로(정점 열). 없으면 None. weight(엣지, 출발, 도착) 로 가중을 바꾼다."""
    best = {start: 0.0}
    previous = {}
    heap = [(0.0, start.index, start)]
    while heap:
        cost, _key, vertex = heapq.heappop(heap)
        if vertex is end:
            path = [end]
            while path[-1] is not start:
                path.append(previous[path[-1]])
            return path[::-1]
        if cost > best.get(vertex, float("inf")):
            continue
        for edge in vertex.link_edges:
            other = edge.other_vert(vertex)
            if other not in corridor:
                continue
            if weight is not None:
                step = weight(edge, vertex, other)
            else:
                middle = (distances[vertex.index] + distances[other.index]) * 0.5 / radius
                step = edge.calc_length() * (1.0 + PATH_PENALTY * middle * middle)
            if cost + step < best.get(other, float("inf")):
                best[other] = cost + step
                previous[other] = vertex
                heapq.heappush(heap, (cost + step, other.index, other))
    return None


def _snap_to_line(vertex, line) -> None:
    """정점을 선 위 가장 가까운 점 쪽으로 옮기되 이웃 엣지 최단 길이의 SNAP_LIMIT 까지만."""
    from mathutils import Vector

    _distance_, _s, foot = closest(line.points, tuple(vertex.co))
    move = Vector(foot) - vertex.co
    shortest = min((edge.calc_length() for edge in vertex.link_edges), default=0.0)
    limit = shortest * SNAP_LIMIT
    if move.length > limit > 0.0:
        move = move * (limit / move.length)
    vertex.co += move


def weld_slits(bm, lines, pair_steps) -> tuple:
    """QuadriFlow 출력의 틈 경계 루프를 두 사슬로 나눠 용접하고 선 위에 놓는다. (용접한 선 이름들, 실패 사유들).

    pair_steps 는 ring_cut.pair_steps (b 사슬 정점 번호 → a 사슬 정점 번호). 사슬 정점 수 차이만큼 b 정점이 한 a 로
    모이거나 a 정점이 짝 없이 남아 작은 구멍이 생기고, 뒤의 출력 수리가 메운다. 루프가 단순하지 않아 나눌 수 없으면
    삼각형으로 메워 구멍은 남기지 않는다."""
    import bmesh

    welded, failed = [], []
    # 층을 더하면 bmesh 요소 참조가 무효가 된다 — 정점을 모으기 전에 만든다
    layer = bm.verts.layers.int.get(PIN_ATTRIBUTE) or bm.verts.layers.int.new(PIN_ATTRIBUTE)
    for line in lines:
        _remove_caps(bm, line)
        done = _weld_line(bm, line, pair_steps, layer, failed)
        if done:
            welded.append(line.name)
    return tuple(welded), tuple(failed)


def _weld_line(bm, line, pair_steps, layer, failed) -> bool:
    """한 선의 틈 루프를 모두 용접한다. 틈이 일부 닫히면 한 선이 루프 여럿으로 쪼개진다 — 용접마다 bmesh 참조가
    무효가 되므로 루프를 매번 다시 찾는다. 하나라도 용접하거나 사슬을 폈으면 참."""
    import bmesh
    from mathutils import Vector

    if line.closed:
        if _weld_closed(bm, line, pair_steps, layer):
            snap_chain(bm, line, layer)
            return True
        if snap_chain(bm, line, layer):
            return True
        failed.append(f"엣지 선 '{line.name}' (닫힌 고리)의 틈 안·밖 루프를 찾지 못했습니다")
        return False
    any_welded = False
    for _attempt in range(SLIT_LOOPS_MAX):
        loop = _slit_loop(bm, line)
        if loop is None:
            break
        ordered = _walk(loop)
        chains = None
        if ordered is not None and len(ordered) >= 4:
            params = [closest(line.points, tuple(v.co))[1] for v in ordered]
            chains = split_at_tips(ordered, params)
        if chains is None:
            bmesh.ops.triangle_fill(bm, edges=loop, use_beauty=True, use_dissolve=False)
            failed.append(f"엣지 선 '{line.name}' 의 틈 일부를 두 줄로 나누지 못해 메웠습니다")
            continue
        a_verts, b_verts = chains
        pairs = pair_steps([tuple(v.co) for v in a_verts], [tuple(v.co) for v in b_verts], False)
        targetmap = {}
        keep = set(a_verts)   # 꼬집힌 정점은 양쪽 사슬에 다 있다 — 용접 대상(값)이면서 지워질 정점(키)이 되면 안 된다
        for j, i in pairs.items():
            if b_verts[j] not in keep:
                targetmap[b_verts[j]] = a_verts[i]
        if not targetmap:
            bmesh.ops.triangle_fill(bm, edges=loop, use_beauty=True, use_dissolve=False)
            failed.append(f"엣지 선 '{line.name}' 의 틈 양쪽 짝을 찾지 못해 메웠습니다")
            continue
        for vertex in a_verts:
            vertex.co = Vector(closest(line.points, tuple(vertex.co))[2])
            vertex[layer] = 1
        bmesh.ops.weld_verts(bm, targetmap=targetmap)
        any_welded = True
    # 용접이 덮지 못한 구간(틈이 닫힌 자리)까지 선을 따라가는 출력 사슬을 편다. 한 번에 이웃 엣지 절반까지만
    # 옮기므로 덜 닿은 정점이 남는다 — 한 번 더 편다
    chained = snap_chain(bm, line, layer)
    if chained:
        snap_chain(bm, line, layer)
        return True
    if not any_welded:
        failed.append(f"엣지 선 '{line.name}' 의 틈을 출력에서 찾지 못했습니다")
    return any_welded


def _weld_closed(bm, line, pair_steps, layer) -> bool:
    """닫힌 고리 틈의 안쪽·바깥쪽 경계 루프를 닫힌 DP 짝으로 용접한다(링 접합과 같은 짝). 둘을 못 찾으면 거짓."""
    import bmesh
    from mathutils import Vector

    loops = []
    for loop in _boundary_loops(bm):
        vertices = {v for e in loop for v in e.verts}
        near = sum(1 for v in vertices if closest(line.points, tuple(v.co))[0] <= line.reach)
        if near >= len(vertices) * LOOP_NEAR_RATIO and near >= 3:
            loops.append((len(loop), loop))
    if len(loops) < 2:
        return False
    loops.sort(key=lambda item: -item[0])
    a_verts, b_verts = _walk(loops[0][1]), _walk(loops[1][1])
    if a_verts is None or b_verts is None:
        return False
    # 두 루프가 고리 둘레를 거의 다 덮어야 같은 고리의 안·밖이다
    total = length(line.points)
    for verts in (a_verts, b_verts):
        if length([tuple(v.co) for v in verts] + [tuple(verts[0].co)]) < total * 0.6:
            return False
    pairs = pair_steps([tuple(v.co) for v in a_verts], [tuple(v.co) for v in b_verts], True)
    keep = set(a_verts)
    targetmap = {b_verts[j]: a_verts[i] for j, i in pairs.items() if b_verts[j] not in keep}
    if not targetmap:
        return False
    for vertex in a_verts:
        vertex.co = Vector(closest(line.points, tuple(vertex.co))[2])
        vertex[layer] = 1
    bmesh.ops.weld_verts(bm, targetmap=targetmap)
    return True


def snap_chain(bm, line, layer=None) -> bool:
    """출력 메시에서 선을 따라가는 엣지 사슬을 찾아 선 위로 옮긴다. 찾으면 참.

    틈이 짧으면 QuadriFlow 가 일부를 닫아(캡을 접어) 용접할 경계가 남지 않지만, 출력 엣지는 틈을 따라 이미 놓여
    있다(실측 메카닉 허벅지: 경계 없음, 선 근처 엣지 커버리지 0.6~0.9). 선 근처 통로에서 선과 나란한 엣지를 우선하는
    최단 경로를 찾아 그 정점을 선 위로 옮기고 표식을 남긴다. 닫힌 고리는 두 반으로 나눠 편다."""
    if line.closed:
        half = len(line.points) // 2
        parts = (line.points[:half + 1], line.points[half:])
        done = [snap_chain(bm, EdgeLine(line.name, part, line.reach, line.edge), layer) for part in parts]
        return any(done)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    distances = _distances([tuple(v.co) for v in bm.verts], line.points)
    radius = max(line.reach, 1e-9)
    corridor = {v for v in bm.verts if distances[v.index] <= radius and v.link_faces}
    if len(corridor) < 2:
        return False
    start = min(corridor, key=lambda v: _distance(tuple(v.co), line.points[0]))
    end = min(corridor, key=lambda v: _distance(tuple(v.co), line.points[-1]))
    if start is end:
        return False
    params = {v: closest(line.points, tuple(v.co))[1] for v in corridor}

    def weight(edge, a, b):
        middle = (distances[a.index] + distances[b.index]) * 0.5 / radius
        span = edge.calc_length()
        along = abs(params[b] - params[a]) / span if span > 0 else 0.0
        return span * (1.0 + PATH_PENALTY * middle * middle + ALIGN_PENALTY * (1.0 - min(1.0, along)))

    path = _corridor_path(start, end, corridor, distances, radius, weight)
    if path is None or len(path) < 2:
        return False
    from mathutils import Vector
    for vertex in path:
        _distance_, _s, foot = closest(line.points, tuple(vertex.co))
        move = Vector(foot) - vertex.co
        shortest = min((e.calc_length() for e in vertex.link_edges), default=0.0)
        limit = shortest * CHAIN_SNAP_RATIO
        reached = move.length <= limit or limit <= 0.0
        if not reached:
            move = move * (limit / move.length)
        vertex.co += move
        if layer is not None and reached:
            vertex[layer] = 1   # 선(원본 표면) 위에 닿은 정점만 — 덜 옮긴 정점을 고정하면 표면에서 뜬 채 남는다
    return True


def slit_score(mesh, lines) -> int:
    """QuadriFlow 출력이 틈을 지킨 선 수 — 선 점의 SLIT_KEPT_RATIO 이상에서 출력 엣지 절반 안에 출력 정점이 있으면 지킨 것.

    시드에 따라 틈을 통째로 무시한 출력이 나온다(실측 메카닉 허벅지: 같은 입력 3회 중 1회, 선 근처 정점 0) — 시드 고르기용."""
    from mathutils.kdtree import KDTree

    vertices = mesh.vertices
    tree = KDTree(len(vertices))
    for index, vertex in enumerate(vertices):
        tree.insert(vertex.co, index)
    tree.balance()
    kept = 0
    for line in lines:
        near = sum(1 for p in line.points if tree.find(p)[2] <= line.edge * 0.5)
        if near >= len(line.points) * SLIT_KEPT_RATIO:
            kept += 1
    return kept


def _remove_caps(bm, line) -> int:
    """QuadriFlow 가 틈을 덮은 캡 면을 지운다. 지운 면 수.

    QuadriFlow 는 출력 경계 루프 중 정점 25개 미만을 루프 정점만으로 쿼드를 짜 메운다(원본 FixHoles). 짧은 선의 틈은
    여기에 걸려 덮이고, 겹치는 엣지 때문에 못 짠 자리만 세 변 구멍으로 남는다(실측 2026-10-03 메카닉 허벅지 선:
    틈 경계 12엣지 → 출력에 3엣지 구멍 하나). 캡은 틈 정점만으로 짜서 정점이 모두 선 위에 있다. 넓이로 거르면
    틈 양쪽이 조금 벌어진 자리의 캡을 놓쳐 틈 전체가 닫힌 채 남았다."""
    import bmesh

    limit = line.edge * CAP_REACH_RATIO
    bm.verts.index_update()
    distances = _distances([tuple(v.co) for v in bm.verts], line.points)
    caps = [face for face in bm.faces if all(distances[v.index] <= limit for v in face.verts)]
    if not caps:
        return 0
    bmesh.ops.delete(bm, geom=caps, context='FACES_ONLY')
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
    return len(caps)


def _boundary_loops(bm) -> list:
    border = [e for e in bm.edges if len(e.link_faces) == 1]
    remaining = set(border)
    loops = []
    for edge in border:
        if edge not in remaining:
            continue
        remaining.discard(edge)
        loop, stack = [edge], [edge]
        while stack:
            current = stack.pop()
            for vertex in current.verts:
                for other in vertex.link_edges:
                    if other in remaining:
                        remaining.discard(other)
                        loop.append(other)
                        stack.append(other)
        loops.append(loop)
    return loops


def _slit_loop(bm, line):
    """line 의 틈으로 보이는 경계 루프(엣지 목록) — 선 근처 정점 비율이 가장 높은 것. 없으면 None."""
    best = None
    for loop in _boundary_loops(bm):
        vertices = {v for e in loop for v in e.verts}
        near = sum(1 for v in vertices if closest(line.points, tuple(v.co))[0] <= line.reach)
        ratio = near / len(vertices)
        if ratio >= LOOP_NEAR_RATIO and (best is None or near > best[0]):
            best = (near, loop)
    return best[1] if best is not None else None


def _walk(loop):
    """경계 루프를 면 루프 방향으로 한 바퀴 걸은 정점 열. 틈 양쪽이 한 정점에서 맞닿아(꼬집혀) 차수 4 정점이 있어도
    면 쪽으로 돌아 나가므로 한 바퀴가 된다 — 같은 정점이 두 번 나올 수 있다. 한 바퀴로 다 못 돌면 None."""
    edges = set(loop)
    start = next(iter(loop))
    if len(start.link_loops) != 1:
        return None
    first = start.link_loops[0]
    current, ordered, steps = first, [], 0
    while True:
        ordered.append(current.vert)
        following = current.link_loop_next
        guard = 0
        while following.edge not in edges or len(following.edge.link_loops) != 1:
            following = following.link_loop_radial_next.link_loop_next
            guard += 1
            if guard > 64:
                return None
        current = following
        steps += 1
        if current is first:
            break
        if steps > len(edges):
            return None
    return ordered if steps == len(edges) else None
