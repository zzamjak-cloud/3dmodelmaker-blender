"""LOOP 가이드를 절단 링으로 바꿔 QuadriFlow 가 팔·다리에 링 격자를 깔게 하는 보조 모듈.

원통 표면은 가우스 곡률이 0 이고 앵커가 없어 QuadriFlow 방향장이 임의 각도로 굳는다(실측 2026-09-22, 구+원통
합성 입력: 팔 영역 닫힌 링 0개, 열린 나선 루프 34개). 링 위치의 얇은 띠를 지워 열린 경계를 만들고 경계 보존으로
돌리면 경계가 앵커가 되어 링이 축을 따라 전파된다(같은 입력에서 닫힌 링 10개). QuadriFlow 는 닫힌 경계 링을
평면 캡으로 막고 양쪽 링의 정점 수를 따로 정하므로(실측 20 대 16), 캡을 지우고 두 링을 DP 브리지로 이어
삼각형을 |nA-nB| 개로 줄인다. 링 정점을 축 쪽으로 눌러 골을 만드는 방식은 sharp 로 잡히지 않았다(같은 날 실측).

bmesh 를 쓰는 함수는 Blender Python 환경에서만 호출한다. 순수 계산(평면 피팅, DP)은 bpy 없이도 돈다.
3DRemesher-Blender(addon/ring_cut.py)에서 옮겨 왔다 — 상대 임포트가 없어야 유닛 테스트가 파일 단위로 불러온다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import pi, sqrt

Vector3 = tuple  # (x, y, z)

CUT_REACH = 2.5        # 가이드 반지름의 이 배수 안에서 띠 후보 면을 모은다 — 손으로 그린 원은 실제 단면보다 작거나 크다(실측 0.06 대 0.09)
STITCH_REACH = 1.1     # 접합 때 캡·경계 링 정점을 측정한 표면 링 반지름의 이 배수 + 띠 반폭×BAND_LOOSE 안에서 찾는다.
                       # QuadriFlow 경계 정점은 엣지 길이에 비례해 뜨므로(반지름과 무관) 여유는 띠 폭 기준으로 준다
CAP_DISTANCE = 2.5     # 띠 반폭의 이 배수 안에 정점이 모두 있고 법선이 절단 평면과 나란하면 QuadriFlow 캡으로 본다
CAP_SEED_RATIO = 0.5  # 캡 씨앗 면은 링 축에서 반지름의 이 비율 안에 중심이 있어야 한다
CAP_NORMAL_DOT = 0.7   # QuadriFlow 캡은 경계 정점이 떠서 기울어진다(실측: 0.85 로 좁히면 놓쳐 잘못된 접합이 생김). 벽면 오인은 CAP_RADIUS 가 막는다
RIM_LENGTH_MIN_RATIO = 0.5  # 절단 띠 양쪽 둘레 길이가 이 비율보다 어긋나면 평면이 이웃 부위를 함께 지난 것이라 자르지 않는다
FALLBACK_EDGE_SLACK = 1.5    # 일반 브리지 폴백은 정점 수가 다른 짝을 잇느라 엣지가 조금 더 길어질 수 있다
MAX_BRIDGE_EDGE_RATIO = 4.0  # 접합으로 생긴 엣지가 링 엣지 평균의 이 배수를 넘으면 잘못된 짝이다
RING_SIZE_MIN_RATIO = 0.5   # 양쪽 링 정점 수·둘레가 이 비율보다 어긋나면 한쪽이 링이 아니다. 3DRemesher 의 0.35 는 갱스터
                            # 비대칭에서 16·45정점(소매 밑단과 짝)을 억지로 접합해 삼각형 9개를 남겼다(실측 2026-09-23)
MIN_HALF_WIDTH_RATIO = 0.01  # 띠 반폭 하한 = 링 반지름 × 이 비율
BAND_LOOSE = 2.5       # QuadriFlow 경계 정점은 엣지 길이 절반 정도 평면에서 뜨므로 띠 반폭의 이 배수까지 띠로 본다
TRIANGLE_PENALTY = 4.0 # 브리지 DP 에서 삼각형 하나에 평균 엣지 길이의 이 배수를 더해 꼭 필요한 곳에만 쓰게 한다
MIN_RING_VERTICES = 3
CURVE_REACH = 0.35     # 띠 후보 면은 가이드 곡선 점에서 반지름의 이 배수(+ 띠 반폭 × BAND_LOOSE) 안에 있어야 한다.
                       # 클릭 가이드는 실제 단면 곡선이라 중심 거리만 보면 A-포즈 팔 옆의 몸통·치마까지 띠로 잡힌다
                       # (실측 2026-09-23, 고블린 팔뚝: 가이드 반지름 0.063 → 띠 반지름 0.197)
GAP_CLOSE_RATIO = 2.6  # 열린 체인의 끝점 거리가 평균 엣지의 이 배수 안이면 작은 구멍으로 끊긴 닫힌 링으로 본다
                       # (3DRemesher 는 1.8 — 갱스터 허리는 3정점 구멍 옆에서 틈이 1.84엣지라 열린 호로 판정됐다, 2026-09-23)
ARC_JOIN_RATIO = 3.5   # 같은 쪽 열린 호 조각의 끝점이 이 배수 안이면 한 호로 이어 붙인다 (대칭면 모서리 구멍은 엣지 2~3개 폭)
MIRRORED_ALONG_RATIO = 0.5  # 미러한 가이드가 반대쪽 가이드와 축 방향으로 반지름의 이 비율 안이면 같은 링이다
STRADDLE_RATIO = 0.5       # 링 중심이 대칭면에서 반지름의 이 비율 안이면 대칭면을 가로지르는 링(목·허리)으로 보고 미러하지 않는다
MIN_RING_GAP_RATIO = 3.0  # 이웃 링과의 축 방향 간격이 (띠 반폭 합)의 이 배수보다 좁으면 접합이 서로 간섭한다고 경고한다
SMALL_CYCLE_MAX = 8    # 링에 붙은 부속 사이클이 이 정점 수 이하면 QuadriFlow 구멍으로 보고 링에서 떼어낸다


@dataclass(frozen=True)
class RingCut:
    name: str
    center: Vector3
    normal: Vector3
    radius: float
    half_width: float
    points: tuple[Vector3, ...]

    def signed_distance(self, point) -> float:
        return sum((point[i] - self.center[i]) * self.normal[i] for i in range(3))

    def near_curve(self, point, slack: float) -> bool:
        """가이드 곡선 점 중 하나가 point 에서 반지름 × CURVE_REACH + slack 안에 있는가."""
        limit = self.radius * CURVE_REACH + slack
        return any(_distance(point, p) <= limit for p in self.points)

    def within(self, point, factor: float, slack: float = 0.0) -> bool:
        """링 중심에서 반지름 × factor (+ slack) 안에 있는가. 폴리라인 거리 대신 중심 거리를 쓰는 이유는
        사용자가 그린 원이 표면 링보다 크거나 작아도 표면 링 정점을 놓치지 않기 위해서다."""
        return _distance(point, self.center) <= self.radius * factor + slack


@dataclass(frozen=True)
class SeamReport:
    name: str
    ring_sizes: tuple[int, int]
    closed: bool
    triangles: int
    bridged: bool
    note: str = ""


def ring_cuts(guides, edge_length: float) -> tuple[RingCut, ...]:
    """(이름, 닫힌 점 열) 가이드마다 Newell 법선과 중심으로 절단 평면을 정한다. 띠 폭은 출력 엣지 하나 정도.
    점은 절단할 메시의 로컬 좌표여야 한다."""
    cuts = []
    for name, spline in guides:
        points = tuple(tuple(p) for p in spline)
        if len(points) >= 2 and _distance(points[0], points[-1]) < 1.0e-9:
            points = points[:-1]
        if len(points) < MIN_RING_VERTICES:
            continue
        center = tuple(sum(p[i] for p in points) / len(points) for i in range(3))
        normal = _newell_normal(points)
        if normal is None:
            continue
        radius = max(_distance(p, center) for p in points)
        if radius <= 0.0:
            continue
        cuts.append(RingCut(name, center, normal, radius, max(edge_length * 0.5, radius * MIN_HALF_WIDTH_RATIO), points))
    return tuple(cuts)


def mirror_cuts(cuts: tuple[RingCut, ...], axes: tuple[str, ...]) -> tuple[RingCut, ...]:
    """대칭 축의 음의 쪽 루프는 양의 쪽으로 미러한다 — 양의 반쪽만 깔리고 미러 용접이 반대쪽을 만든다.
    중심이 음의 쪽이고 대칭면을 제대로 가로지르지 않으면(중심이 반지름 절반 넘게 떨어짐) 미러한다 — 점이 모두 음의
    쪽일 때만 미러하면 가랑이에 닿은 허벅지 링이 빠진다. 미러한 결과가 이미 있는 루프와 겹치면 하나만 남긴다."""
    result = list(cuts)
    mirrored = set()
    for axis in axes:
        component = "XYZ".index(axis)
        for index, cut in enumerate(result):
            if cut.center[component] >= 0.0 or abs(cut.center[component]) < cut.radius * STRADDLE_RATIO:
                continue
            result[index] = RingCut(
                cut.name, _mirror_point(cut.center, component), _mirror_point(cut.normal, component), cut.radius,
                cut.half_width, tuple(_mirror_point(p, component) for p in cut.points),
            )
            mirrored.add(cut.name)
    unique: list[RingCut] = []
    for cut in result:
        # 좌우를 따로 클릭한 가이드는 따로 추정돼 미러해도 축이 20°, 위치가 0.04 어긋난다(실측 2026-09-23, 갱스터 허벅지).
        # 거의 겹친 두 절단은 서로 간섭해 대칭 반쪽이 모든 시드에서 실패하므로 미러로 넘어온 쌍은 넉넉히 합친다
        if any(_same_ring(cut, kept, loose=cut.name in mirrored or kept.name in mirrored) for kept in unique):
            continue
        unique.append(cut)
    return tuple(unique)


def crowded_pairs(cuts: tuple[RingCut, ...]) -> tuple[tuple[str, str, float], ...]:
    """축 방향 간격이 좁아 접합이 서로 간섭할 수 있는 링 쌍 (이름, 이름, 간격)."""
    pairs = []
    for i, a in enumerate(cuts):
        for b in cuts[i + 1:]:
            offset = tuple(a.center[k] - b.center[k] for k in range(3))
            along = abs(sum(offset[k] * b.normal[k] for k in range(3)))
            lateral = sqrt(max(0.0, sum(o * o for o in offset) - along * along))
            if lateral < max(a.radius, b.radius) and along < (a.half_width + b.half_width) * MIN_RING_GAP_RATIO:
                pairs.append((a.name, b.name, along))
    return tuple(pairs)


def _same_ring(a: RingCut, b: RingCut, loose: bool = False) -> bool:
    """두 루프가 같은 자리의 같은 링인가 — 축 방향 간격이 띠 폭 안이고 옆으로도 반지름 절반 안일 때만.
    목처럼 같은 축 위에 가까이 둔 링 둘은 중심 거리만 보면 중복으로 잘못 합쳐진다(실측 0.07 간격).
    loose 면(미러로 넘어온 좌우 쌍) 축 방향 간격을 반지름 × MIRRORED_ALONG_RATIO 까지 같은 링으로 본다."""
    offset = tuple(a.center[i] - b.center[i] for i in range(3))
    along = abs(sum(offset[i] * b.normal[i] for i in range(3)))
    lateral = sqrt(max(0.0, sum(o * o for o in offset) - along * along))
    reach = a.half_width + b.half_width
    if loose:
        reach = max(reach, min(a.radius, b.radius) * MIRRORED_ALONG_RATIO)
    return along <= reach and lateral < min(a.radius, b.radius) * 0.5


def _mirror_point(point, component: int):
    mirrored = list(point)
    mirrored[component] = -mirrored[component]
    return tuple(mirrored)


def cut_bands(
    mesh, cuts: tuple[RingCut, ...], scale: float, merge_distance: float = 0.0, triangulate: bool = True
) -> tuple[tuple[RingCut, ...], tuple[str, ...]]:
    """절단 링마다 평면 ±반폭으로 두 번 자르고, 가이드에 가장 가까운 연결된 띠 조각 하나만 지운다.
    (실제 표면 링 반지름을 측정해 갱신한, 실제로 자른 절단 링들, 건너뛴 루프의 사유) 를 돌려준다.
    표면과 만나지 않거나 양쪽 둘레가 크게 다른(발목 평면이 발등을 함께 지나는 경우) 루프는 자르지 않고 사유만 남긴다.
    절단이 남긴 미세 엣지는 QuadriFlow 사전 검사에 걸리므로 merge_distance 안의 정점을 병합한다.
    triangulate=False 면 n각형을 그대로 둔다 — 구멍 메우기 n각형이 섞인 메시에서는 삼각화 대각선이 기존 엣지와
    겹쳐 면 3장짜리 엣지가 생긴다(실측 2026-09-23, 고블린: 42개). QuadriFlow 는 n각형을 그대로 받는다."""
    import bmesh
    from mathutils import Vector

    bm = bmesh.new()
    bm.from_mesh(mesh)
    updated = []
    skipped = []
    for cut in cuts:
        normal = Vector(cut.normal)
        center = Vector(cut.center)
        near = _near_faces(bm, cut)
        if not near:
            skipped.append(f"링 가이드 '{cut.name}' 이 표면과 만나지 않아 절단 링으로 쓰지 않았습니다.")
            continue
        for offset in (-cut.half_width, cut.half_width):
            verts = {v for f in near for v in f.verts}
            edges = {e for f in near for e in f.edges}
            bmesh.ops.bisect_plane(
                bm, geom=list(verts) + list(edges) + near, plane_co=center + normal * offset, plane_no=normal,
                dist=max(1.0e-6, 1.0e-7 * scale),
            )
            near = _near_faces(bm, cut)
        candidates = [
            f for f in near
            if abs(cut.signed_distance(f.calc_center_median())) < cut.half_width * (1.0 - 1.0e-6)
            and cut.near_curve(f.calc_center_median(), cut.half_width * BAND_LOOSE)
        ]
        # 넓은 범위에는 옆 다리의 띠도 들어오므로, 연결된 조각 중 가이드 중심에 가장 가까운 것만 지운다
        band = _nearest_group(candidates, cut)
        if not band:
            skipped.append(f"링 가이드 '{cut.name}' 이 표면과 만나지 않아 절단 링으로 쓰지 않았습니다.")
            continue
        low, high = _rim_lengths(band, cut)
        if min(low, high) < max(low, high) * RIM_LENGTH_MIN_RATIO:
            # 절단 평면이 발등·가슴처럼 이웃 부위를 함께 지나면 한쪽 둘레만 커진다 — 링이 아니라 자르지 않는다
            skipped.append(
                f"링 가이드 '{cut.name}' 의 절단면 양쪽 둘레가 {min(low, high):.3g}·{max(low, high):.3g} 로 크게 달라 절단 링으로 쓰지 않았습니다. "
                "단면이 일정한 위치로 옮겨 주세요."
            )
            continue
        ring_radius = max(_distance(v.co, cut.center) for f in band for v in f.verts)
        updated.append(RingCut(cut.name, cut.center, cut.normal, ring_radius, cut.half_width, cut.points))
        bmesh.ops.delete(bm, geom=band, context="FACES")
        if merge_distance > 0.0:
            verts = [v for f in _near_faces(bm, cut) for v in f.verts]
            bmesh.ops.remove_doubles(bm, verts=list(set(verts)), dist=merge_distance)
    # 분할 엣지를 공유한 바깥 면은 정점이 늘어 n각형이 됐으므로 삼각형으로 되돌린다
    ngons = [f for f in bm.faces if len(f.verts) > 3] if triangulate else []
    if ngons:
        bmesh.ops.triangulate(bm, faces=ngons)
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return tuple(updated), tuple(skipped)


def _rim_lengths(band: list, cut: RingCut) -> tuple[float, float]:
    """띠 면들의 두 절단 평면 위 가장자리 길이 합 (음의 쪽, 양의 쪽)."""
    tolerance = cut.half_width * 0.5
    low = high = 0.0
    seen = set()
    for face in band:
        for edge in face.edges:
            if edge in seen:
                continue
            seen.add(edge)
            distances = [cut.signed_distance(v.co) for v in edge.verts]
            if all(abs(d + cut.half_width) <= tolerance for d in distances):
                low += edge.calc_length()
            elif all(abs(d - cut.half_width) <= tolerance for d in distances):
                high += edge.calc_length()
    return low, high


def _nearest_group(faces: list, cut: RingCut) -> list:
    """엣지로 이어진 면 그룹 중 정점이 가이드 중심에 가장 가까운 그룹."""
    remaining = set(faces)
    best: list = []
    best_distance = float("inf")
    while remaining:
        seed = remaining.pop()
        group = [seed]
        stack = [seed]
        while stack:
            current = stack.pop()
            for edge in current.edges:
                for other in edge.link_faces:
                    if other in remaining:
                        remaining.discard(other)
                        group.append(other)
                        stack.append(other)
        distance = min(_distance(v.co, cut.center) for f in group for v in f.verts)
        if distance < best_distance:
            best, best_distance = group, distance
    return best


def in_band(point, cuts: tuple[RingCut, ...]) -> bool:
    """QuadriFlow 출력 정점이 어느 절단 띠 근처에 있는가. 시드 검사에서 띠 경계 루프를 구멍으로 오인하지 않게 한다."""
    for cut in cuts:
        if abs(cut.signed_distance(point)) <= cut.half_width * BAND_LOOSE and cut.within(point, STITCH_REACH, cut.half_width * BAND_LOOSE):
            return True
    return False


def stitch_bands(mesh, cuts: tuple[RingCut, ...], plane_axes: tuple = (), plane_tolerance: float = 0.0) -> tuple[SeamReport, ...]:
    """QuadriFlow 캡을 지우고 띠 양쪽 경계 링을 브리지로 잇는다. 링별 접합 보고를 돌려준다.

    plane_axes 는 반쪽만 깐 대칭 출력의 대칭면이다. 대칭면을 가로지르는 링(허리·목)은 반쪽에서 열린 호라
    QuadriFlow 가 캡을 만들지 않으므로 캡 제거를 건너뛰고, 대칭면 위 경계 엣지(plane_tolerance 안)는 호에서
    뺀다 — 캡으로 오인한 자켓 밑단 같은 수평 면을 지우면 그 경계가 호와 엉켜 4정점 구멍만 링으로 잡혔다
    (실측 2026-09-23, 갱스터 허리)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    reports = []
    components = ["XYZ".index(axis) for axis in plane_axes]
    for cut in cuts:
        limit = cut.half_width * CAP_DISTANCE
        slack = cut.half_width * BAND_LOOSE
        crossing = [c for c in components if abs(cut.center[c]) < cut.radius * STRADDLE_RATIO]

        def on_plane(vertex) -> bool:
            return any(abs(vertex.co[c]) < plane_tolerance for c in crossing)

        # 캡은 링 안쪽을 덮는 평면 원반: 정점이 모두 띠 안·링 중심 근처에 있고 법선이 절단 축과 나란하다.
        # QuadriFlow 의 구멍 메우기는 경계 정점만 쓰지만, 중심 정점을 세우는 경우도 링 중심 거리로 잡는다.
        caps = [] if crossing else _cap_faces(bm, cut, limit, slack)
        if caps:
            bmesh.ops.delete(bm, geom=caps, context="FACES")
        edges = [
            e for e in bm.edges
            if e.is_boundary and all(abs(cut.signed_distance(v.co)) <= limit and cut.within(v.co, STITCH_REACH, slack) for v in e.verts)
            and not (crossing and all(on_plane(v) for v in e.verts))
        ]
        chains = _chains(edges)
        # 같은 쪽에 QuadriFlow 가 남긴 작은 구멍(실측 4정점)이 함께 잡히므로, 끝점이 맞닿는 열린 조각은 하나의 호로 이어 붙이고
        # (대칭면 모서리에서 호가 8·19 처럼 갈라졌다) 그중 가장 긴 체인만 링으로 쓴다. 구멍은 뒤에서 메운다.
        negative = sorted(_merge_open_chains([c for c in chains if sum(cut.signed_distance(v.co) for v in c[0]) < 0.0]), key=lambda c: -len(c[0]))
        positive = sorted(_merge_open_chains([c for c in chains if sum(cut.signed_distance(v.co) for v in c[0]) >= 0.0]), key=lambda c: -len(c[0]))
        if not negative or not positive:
            sizes = (len(negative[0][0]) if negative else 0, len(positive[0][0]) if positive else 0)
            reports.append(SeamReport(cut.name, sizes, False, 0, False, "한쪽 경계 링을 찾지 못함"))
            continue
        (a_verts, a_closed), (b_verts, b_closed) = negative[0], positive[0]
        closed = a_closed and b_closed
        a_length = _mean_step([tuple(v.co) for v in a_verts], a_closed) * (len(a_verts) if a_closed else len(a_verts) - 1)
        b_length = _mean_step([tuple(v.co) for v in b_verts], b_closed) * (len(b_verts) if b_closed else len(b_verts) - 1)
        length_ok = min(a_length, b_length) >= max(a_length, b_length) * RING_SIZE_MIN_RATIO
        typical = max(_mean_step([tuple(v.co) for v in a_verts], a_closed), _mean_step([tuple(v.co) for v in b_verts], b_closed), 1.0e-9)
        sizes = (len(a_verts), len(b_verts))
        if not length_ok:
            # 한쪽이 링의 절반도 안 되는 조각이면 캡을 못 지웠거나 구멍이 링을 끊은 것 — 잘못 이으면 팔 안쪽을 가로지른다
            reports.append(SeamReport(cut.name, sizes, False, 0, False, "양쪽 링 둘레 차이가 큼"))
            continue
        problem = ""
        if min(sizes) < max(sizes) * RING_SIZE_MIN_RATIO or a_closed != b_closed:
            problem = "한쪽 링이 열림" if a_closed != b_closed else "양쪽 링 정점 수 차이가 큼"
            faces = []
        else:
            faces = _bridge(bm, a_verts, b_verts, closed)
            triangles = [f for f in faces if f.is_valid and len(f.verts) == 3]
            if triangles:
                result = bmesh.ops.join_triangles(
                    bm, faces=triangles, cmp_seam=False, cmp_sharp=False, cmp_uvs=False, cmp_vcols=False,
                    cmp_materials=False, angle_face_threshold=pi, angle_shape_threshold=pi,
                )
                faces = [f for f in faces if f.is_valid] + [f for f in result.get("faces", []) if f.is_valid]
            # 턱 바로 아래처럼 이웃 링·구멍과 겹치는 자리에서는 브리지가 기존 면 위에 면을 얹어 비다양체가 되거나(실측: 목표
            # 3,000 에서 엣지 3개), 조각난 호와 짝을 지어 목을 가로지르는 긴 면을 만든다(실측: 엣지 0.37, 전형 0.036).
            problem = _bridge_problem([f for f in faces if f.is_valid], typical)
            if problem:
                bmesh.ops.delete(bm, geom=[f for f in faces if f.is_valid], context="FACES")
                faces = []
        note = ""
        if problem:
            # DP 브리지가 못 잇는 짝(정점 수 차이·한쪽이 작은 구멍으로 끊김·긴 면)은 Blender 일반 브리지로 잇는다.
            # 실패한 링을 빼고 QuadriFlow 를 다시 돌리면 출력이 바뀌어 이미 접합된 링까지 잃는다(실측 2026-09-23,
            # 오거: 3개 → 2개 → 1개). 일반 브리지도 겹친 면·긴 면을 만들면 되돌리고 실패로 보고한다.
            faces = []
            if min(sizes) >= max(sizes) * RING_SIZE_MIN_RATIO:
                faces = _bridge_loops_fallback(bm, a_verts, b_verts, a_closed, b_closed, typical)
            if faces and sum(1 for f in faces if len(f.verts) == 3) > abs(sizes[0] - sizes[1]) * 2 + 2:
                # 정점 수 차이보다 훨씬 많은 삼각형은 부채꼴로 뭉친 것 — 뒤의 구멍 메우기와 엉켜 비매니폴드가 된다
                # (실측 2026-09-23, 갱스터 위팔 16·33: 삼각형 49개, 비매니폴드 엣지 3)
                bmesh.ops.delete(bm, geom=faces, context="FACES")
                faces = []
            if not faces:
                reports.append(SeamReport(cut.name, sizes, False, 0, False, problem))
                continue
            note = f"일반 브리지({problem})"
            closed = True   # 양쪽 둘레가 비슷한 링을 이었으므로 닫힌 접합으로 본다
        remaining = sum(1 for f in faces if f.is_valid and len(f.verts) == 3)
        reports.append(SeamReport(cut.name, sizes, closed, remaining, bool(faces), note))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return tuple(reports)


# --- bmesh 보조 -----------------------------------------------------------------

def _cap_faces(bm, cut: RingCut, limit: float, slack: float) -> list:
    """QuadriFlow 가 링 구멍에 씌운 캡. 캡은 몸 안에서 축을 가로지르는 원반이므로 축 근처(반지름 × CAP_SEED_RATIO
    안)의 캡 조건 면에서 시작해 캡 조건을 만족하는 면으로만 번진 연결 영역만 캡으로 본다.

    조건만 보고 모으면 링 근처의 수평 면(자켓 밑단·벨트 윗면)까지 캡으로 지워 허리·목 접합이 망가졌다
    (실측 2026-09-23, 갱스터 비대칭)."""
    def is_cap(face) -> bool:
        return (abs(sum(a * b for a, b in zip(face.normal, cut.normal))) >= CAP_NORMAL_DOT
                and all(abs(cut.signed_distance(v.co)) <= limit and cut.within(v.co, STITCH_REACH, slack) for v in face.verts))

    def lateral(point) -> float:
        offset = [point[i] - cut.center[i] for i in range(3)]
        along = sum(offset[i] * cut.normal[i] for i in range(3))
        return sqrt(max(0.0, sum(o * o for o in offset) - along * along))

    seeds = [f for f in bm.faces if lateral(f.calc_center_median()) <= cut.radius * CAP_SEED_RATIO and is_cap(f)]
    caps = set(seeds)
    stack = list(seeds)
    while stack:
        face = stack.pop()
        for edge in face.edges:
            for other in edge.link_faces:
                if other not in caps and is_cap(other):
                    caps.add(other)
                    stack.append(other)
    return list(caps)


def _near_faces(bm, cut: RingCut) -> list:
    """링 근처 면. 데시메이트된 팔 표면은 삼각형이 길어 중심이 띠 밖에 있어도 띠를 가로지르므로
    정점과 중심 중 하나라도 링 중심 근처면 넣고, 긴 엣지의 절반만큼 여유를 준다."""
    near = []
    for face in bm.faces:
        if not face.is_valid:
            continue
        slack = max(edge.calc_length() for edge in face.edges) * 0.5
        if any(cut.within(p, CUT_REACH, slack) for p in (face.calc_center_median(), *(v.co for v in face.verts))):
            near.append(face)
    return near


def _chains(edges) -> list[tuple[list, bool]]:
    """경계 엣지를 이어진 정점 열로 묶는다. 정점마다 엣지가 2개면 닫힘, 끝점이 둘이면 열림, 그 외는 버린다."""
    adjacency: dict = {}
    for edge in edges:
        a, b = edge.verts
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    _detach_small_cycles(adjacency)
    seen: set = set()
    chains = []
    for start in adjacency:
        if start in seen:
            continue
        component = []
        stack = [start]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(adjacency[current])
        degrees = [len(adjacency[v]) for v in component]
        if any(d > 2 for d in degrees):
            continue
        ends = [v for v in component if len(adjacency[v]) == 1]
        closed = not ends
        if not closed and len(ends) != 2:
            continue
        ordered = [ends[0] if ends else component[0]]
        previous = None
        while True:
            current = ordered[-1]
            following = [v for v in adjacency[current] if v is not previous]
            if not following:
                break
            nxt = following[0]
            if nxt is ordered[0]:
                break
            ordered.append(nxt)
            previous = current
            if len(ordered) > len(component):
                break
        if len(ordered) != len(component):
            continue
        if not closed and len(ordered) >= MIN_RING_VERTICES:
            # QuadriFlow 가 링 경계에 붙여 남긴 작은 구멍이 링을 한 군데 끊는다. 끝점 사이가 엣지 하나 남짓이면
            # 닫힌 링으로 보고 잇는다 — 브리지 면이 그 틈을 덮고 남는 구멍은 뒤의 구멍 메우기가 닫는다.
            step = _mean_step([tuple(v.co) for v in ordered], False)
            if _distance(tuple(ordered[0].co), tuple(ordered[-1].co)) <= GAP_CLOSE_RATIO * step:
                closed = True
        if len(ordered) >= (MIN_RING_VERTICES if closed else 2):
            chains.append((ordered, closed))
    return chains


def _merge_open_chains(chains: list) -> list:
    """열린 체인들 중 끝점이 평균 엣지의 GAP_CLOSE_RATIO 배 안에서 맞닿는 것들을 하나로 이어 붙인다. 닫힌 체인은 그대로."""
    closed = [c for c in chains if c[1]]
    open_chains = [list(c[0]) for c in chains if not c[1]]
    merged: list = []
    while open_chains:
        current = open_chains.pop(0)
        step = _mean_step([tuple(v.co) for v in current], False) or 1.0e-9
        tolerance = step * ARC_JOIN_RATIO
        joined = True
        while joined:
            joined = False
            for other in list(open_chains):
                ends = {
                    (0, 0): _distance(tuple(current[0].co), tuple(other[0].co)),
                    (0, -1): _distance(tuple(current[0].co), tuple(other[-1].co)),
                    (-1, 0): _distance(tuple(current[-1].co), tuple(other[0].co)),
                    (-1, -1): _distance(tuple(current[-1].co), tuple(other[-1].co)),
                }
                key = min(ends, key=ends.get)
                if ends[key] > tolerance:
                    continue
                if key == (-1, 0):
                    current = current + other
                elif key == (-1, -1):
                    current = current + other[::-1]
                elif key == (0, -1):
                    current = other + current
                else:
                    current = other[::-1] + current
                open_chains.remove(other)
                joined = True
        merged.append((current, False))
    return closed + merged


def _detach_small_cycles(adjacency: dict) -> None:
    """QuadriFlow 가 링 경계에 붙여 남긴 작은 구멍은 링과 정점을 공유해 8자 모양이 된다. 차수 3 이상 정점마다
    그 정점을 지나는 가장 짧은 사이클을 찾아 SMALL_CYCLE_MAX 이하면 그 엣지를 떼어낸다 — 구멍은 뒤의 구멍 메우기가 닫는다."""
    changed = True
    while changed:
        changed = False
        for vertex in list(adjacency):
            if len(adjacency.get(vertex, ())) <= 2:
                continue
            cycle = _shortest_cycle(adjacency, vertex, SMALL_CYCLE_MAX)
            if cycle is None:
                continue
            for a, b in zip(cycle, (*cycle[1:], cycle[0])):
                if b in adjacency.get(a, ()):
                    adjacency[a].remove(b)
                if a in adjacency.get(b, ()):
                    adjacency[b].remove(a)
            for v in cycle:
                if not adjacency.get(v):
                    adjacency.pop(v, None)
            changed = True
            break


def _shortest_cycle(adjacency: dict, start, limit: int):
    """start 를 지나는 가장 짧은 사이클(정점 열). 길이가 limit 를 넘으면 None."""
    best = None
    for first in list(adjacency[start]):
        # start→first 엣지를 제외하고 first 에서 start 로 돌아오는 최단 경로를 찾는다
        previous = {first: None}
        queue = [first]
        found = None
        while queue and found is None:
            following = []
            for current in queue:
                for neighbor in adjacency.get(current, ()):
                    if current is first and neighbor is start:
                        continue
                    if neighbor is start:
                        found = current
                        break
                    if neighbor not in previous:
                        previous[neighbor] = current
                        following.append(neighbor)
                if found is not None:
                    break
            queue = following
            if len(previous) > limit:
                break
        if found is None:
            continue
        path = [found]
        while path[-1] is not first:
            path.append(previous[path[-1]])
        cycle = [start, *reversed(path)]
        if len(cycle) <= limit and (best is None or len(cycle) < len(best)):
            best = cycle
    return best


def _bridge_problem(faces: list, typical: float) -> str:
    """접합 면이 비다양체를 만들거나 지나치게 길면 사유, 아니면 빈 문자열."""
    if any(len(e.link_faces) > 2 for f in faces for e in f.edges):
        return "접합이 겹친 면을 만듦"
    if any(e.calc_length() > typical * MAX_BRIDGE_EDGE_RATIO for f in faces for e in f.edges):
        return "접합 면이 지나치게 김"
    return ""


def _chain_edges(bm, verts: list, closed: bool) -> list:
    pairs = list(zip(verts, verts[1:] + ([verts[0]] if closed else [])))
    edges = [bm.edges.get((a, b)) for a, b in pairs]
    return [e for e in edges if e is not None]


def _bridge_loops_fallback(bm, a_verts: list, b_verts: list, a_closed: bool, b_closed: bool, typical: float) -> list:
    """Blender 일반 브리지(bmesh.ops.bridge_loops)로 두 경계 열을 잇는다. 검증에 걸리면 되돌리고 빈 목록."""
    import bmesh

    edges = _chain_edges(bm, a_verts, a_closed) + _chain_edges(bm, b_verts, b_closed)
    if len(edges) < 4:
        return []
    try:
        result = bmesh.ops.bridge_loops(bm, edges=edges, use_pairs=False, use_cyclic=False, use_merge=False)
    except (RuntimeError, ValueError):
        return []
    faces = [f for f in result.get("faces", []) if f.is_valid]
    if not faces or _bridge_problem(faces, typical * FALLBACK_EDGE_SLACK):
        if faces:
            bmesh.ops.delete(bm, geom=faces, context="FACES")
        return []
    return faces


def _bridge(bm, a_verts: list, b_verts: list, closed: bool) -> list:
    """두 정점 열을 쿼드 위주로 잇는다. 닫힌 링은 시작 위상과 방향을 바꿔 가장 싼 배치를 고른다."""
    a_points = [tuple(v.co) for v in a_verts]
    b_points = [tuple(v.co) for v in b_verts]
    mean_edge = _mean_step(a_points, closed) + _mean_step(b_points, closed)
    penalty = TRIANGLE_PENALTY * max(mean_edge * 0.5, 1.0e-9)
    candidates = []
    if closed:
        nearest = min(range(len(b_points)), key=lambda j: _distance(a_points[0], b_points[j]))
        for orientation in (1, -1):
            for shift in (-1, 0, 1):
                start = (nearest + shift) % len(b_points)
                order = [(start + orientation * k) % len(b_points) for k in range(len(b_points))]
                candidates.append(order)
    else:
        forward = list(range(len(b_points)))
        if _distance(a_points[0], b_points[0]) + _distance(a_points[-1], b_points[-1]) <= _distance(a_points[0], b_points[-1]) + _distance(a_points[-1], b_points[0]):
            candidates.append(forward)
        else:
            candidates.append(forward[::-1])
    best = None
    for order in candidates:
        cost, steps = bridge_steps(a_points, [b_points[j] for j in order], closed, penalty)
        if best is None or cost < best[0]:
            best = (cost, steps, order)
    if best is None:
        return []
    _cost, steps, order = best
    b_ordered = [b_verts[j] for j in order]
    faces = []
    n, m = len(a_verts), len(b_ordered)
    for i, j, kind in steps:
        a0, b0 = a_verts[i % n], b_ordered[j % m]
        if kind == "quad":
            corners = (a0, a_verts[(i + 1) % n], b_ordered[(j + 1) % m], b0)
        elif kind == "a":
            corners = (a0, a_verts[(i + 1) % n], b0)
        else:
            corners = (a0, b_ordered[(j + 1) % m], b0)
        if len(set(corners)) != len(corners):
            continue
        if any(_full_edge(bm, a, b) for a, b in zip(corners, (*corners[1:], corners[0]))):
            continue
        try:
            faces.append(bm.faces.new(corners))
        except ValueError:
            continue
    return faces


def _full_edge(bm, a, b) -> bool:
    """두 정점 사이 엣지가 이미 면 두 개를 가지면 참 — 그 위에 면을 더 만들면 비다양체가 된다."""
    edge = bm.edges.get((a, b))
    return edge is not None and len(edge.link_faces) >= 2


def bridge_steps(a_points, b_points, closed: bool, penalty: float) -> tuple[float, list[tuple[int, int, str]]]:
    """(i, j) 격자 위 최소 비용 경로. 쿼드는 대각선 길이, 삼각형은 새 엣지 길이 + 벌점. 순수 계산이라 테스트 가능."""
    n, m = len(a_points), len(b_points)
    if closed:
        rows, cols = n, m
    else:
        rows, cols = n - 1, m - 1
    if rows <= 0 or cols <= 0:
        return float("inf"), []
    inf = float("inf")
    cost = [[inf] * (cols + 1) for _ in range(rows + 1)]
    parent: list[list[str | None]] = [[None] * (cols + 1) for _ in range(rows + 1)]
    cost[0][0] = 0.0

    def a_at(i):
        return a_points[i % n]

    def b_at(j):
        return b_points[j % m]

    for i in range(rows + 1):
        for j in range(cols + 1):
            current = cost[i][j]
            if current == inf:
                continue
            if i < rows and j < cols:
                candidate = current + _distance(a_at(i + 1), b_at(j + 1))
                if candidate < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1] = candidate
                    parent[i + 1][j + 1] = "quad"
            if i < rows:
                candidate = current + _distance(a_at(i + 1), b_at(j)) + penalty
                if candidate < cost[i + 1][j]:
                    cost[i + 1][j] = candidate
                    parent[i + 1][j] = "a"
            if j < cols:
                candidate = current + _distance(a_at(i), b_at(j + 1)) + penalty
                if candidate < cost[i][j + 1]:
                    cost[i][j + 1] = candidate
                    parent[i][j + 1] = "b"
    steps = []
    i, j = rows, cols
    while i > 0 or j > 0:
        kind = parent[i][j]
        if kind == "quad":
            i, j = i - 1, j - 1
        elif kind == "a":
            i -= 1
        else:
            j -= 1
        steps.append((i, j, kind))
    steps.reverse()
    return cost[rows][cols], steps


# --- 순수 계산 -------------------------------------------------------------------

def _newell_normal(points) -> Vector3 | None:
    nx = ny = nz = 0.0
    count = len(points)
    for index in range(count):
        p, q = points[index], points[(index + 1) % count]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    length = sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1.0e-12:
        return None
    return (nx / length, ny / length, nz / length)


def _mean_step(points, closed: bool) -> float:
    count = len(points)
    if count < 2:
        return 0.0
    pairs = count if closed else count - 1
    return sum(_distance(points[i], points[(i + 1) % count]) for i in range(pairs)) / pairs


def _distance(a, b) -> float:
    return sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)
