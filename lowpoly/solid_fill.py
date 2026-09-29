# 속 빈 껍질 메시를 속이 찬 덩어리로 — 리토폴로지 복셀 리메시의 입력을 만든다
#
# 셰이프 서버(TRELLIS.2 듀얼 컨투어링 리메시)의 메시는 표면 둘레 ±1복셀 띠라 **두께 4mm 안팎의 속 빈 껍질**이고,
# 원본 등위면의 찢김마다 껍질이 안쪽으로 접혀 들어가 몸속 빈 공간이 바깥과 이어져 있다. 이대로 복셀 리메시를 하면
# 껍질이 복셀보다 얇은 곳마다 몸속으로 구멍이 뚫리고(실측 2026-09-28, 좀비 GLB: 복셀 8·15·30mm 모두), Boolean 합집합도
# 두 껍질 사이 틈만 '안'으로 보므로 속을 채우지 못한다.
#
# 그래서 격자에서 직접 채운다: 표면을 벽 복셀로 찍고 → 닫힘(팽창 후 침식)으로 좁은 찢김 입구를 막고 → 바깥에서
# 흘려 넣은 빈칸이 닿지 않는 곳을 전부 속으로 본다. 결과는 복셀 경계 사각형 메시이고, 뒤따르는 복셀 리메시가
# 매끈하게 녹인다. 판정 계산은 numpy 만 쓴다(Blender 번들) — bpy 는 fill_interior 에서만 쓴다.
import numpy as np

PAD = 2                  # 격자 가장자리 여백(칸, 닫힘 칸 수에 더한다) — 팽창한 벽이 격자 테두리에 닿으면 바깥
                         # 흘려 넣기의 출발점이 막히고 침식이 테두리 쪽에서 먹어 들어오지 못한다
SAMPLE_STEP = 0.5        # 삼각형 샘플 간격 = 복셀 한 변 x 이 비율 — 벽에 틈이 새지 않게 촘촘히 찍는다
MAX_CELLS = 12_000_000   # 격자 칸 수 상한 — 넘으면 복셀을 키운다 (bool 12MB, 흘려 넣기 int32 48MB)
CHUNK_POINTS = 4_000_000  # 한 번에 좌표로 바꾸는 샘플 점 수 상한 (메모리)
MIN_ESCAPE_DIRECTIONS = 3  # 26 방향(축·면 대각·꼭짓점 대각) 중 바깥이 보이는 방향이 이보다 적은 빈칸은 속으로 채운다.
                         # 큰 찢김(셔츠 찢김 5cm)으로 몸속이 바깥과 이어져도 몸속 칸은 찢김 너머 0~1 방향만 트이고,
                         # 다리 사이(반바지 자락 아래)·팔과 몸 사이는 아래·앞뒤로 넓게 트여 6 방향 이상이다(실측 2026-09-29,
                         # 덩치큰 좀비). 6 축만 세면 앞뒤가 자락에 가린 다리 사이를 몸속과 구별하지 못해 네모난 덩어리로 메웠다
MAX_STEPS = 128          # 삼각형 한 변을 나누는 칸 수 상한 — 넘는 삼각형은 네 조각으로 쪼갠다(샘플 수가 제곱으로 늘어난다)


def _split_large(triangles, pitch: float):
    """한 변이 MAX_STEPS 칸을 넘는 삼각형을 중점 네 조각으로 쪼갠다 (배경 바닥판 같은 거대 삼각형 대비)."""
    limit = pitch * SAMPLE_STEP * MAX_STEPS
    while True:
        edges = np.linalg.norm(triangles - np.roll(triangles, 1, axis=1), axis=2).max(axis=1)
        big = edges > limit
        if not big.any():
            return triangles
        a, b, c = triangles[big, 0], triangles[big, 1], triangles[big, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        parts = [np.stack(t, axis=1) for t in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))]
        triangles = np.concatenate([triangles[~big]] + parts)


def rasterize(triangles, lo, pitch: float, shape) -> np.ndarray:
    """삼각형 (N, 3, 3) 이 지나가는 칸을 True 로 찍은 격자."""
    wall = np.zeros(tuple(int(s) for s in shape), bool)
    triangles = np.asarray(triangles, dtype=np.float64)
    if not len(triangles):
        return wall
    lo = np.asarray(lo, dtype=np.float64)
    triangles = _split_large(triangles, pitch)
    edges = np.linalg.norm(triangles - np.roll(triangles, 1, axis=1), axis=2).max(axis=1)
    steps = np.maximum(np.ceil(edges / (pitch * SAMPLE_STEP)).astype(np.int64), 1)
    limit = np.asarray(wall.shape) - 1
    for k in np.unique(steps):
        chosen = triangles[steps == k]
        i, j = np.meshgrid(np.arange(k + 1), np.arange(k + 1), indexing='ij')
        keep = i + j <= k
        a = (i[keep] / k)[None, :, None]
        b = (j[keep] / k)[None, :, None]
        per = max(1, CHUNK_POINTS // a.shape[1])
        for start in range(0, len(chosen), per):
            part = chosen[start:start + per]
            points = part[:, None, 0] * (1 - a - b) + part[:, None, 1] * a + part[:, None, 2] * b
            cells = np.clip(np.floor((points.reshape(-1, 3) - lo) / pitch).astype(np.int64), 0, limit)
            wall[cells[:, 0], cells[:, 1], cells[:, 2]] = True
    return wall


def _spread_along(empty, reached, axis: int) -> np.ndarray:
    """축 방향 빈칸 구간마다, 구간의 한 칸이라도 닿았으면 구간 전체를 닿은 것으로 만든다."""
    e = np.moveaxis(empty, axis, -1)
    r = np.moveaxis(reached, axis, -1)
    lead, length = e.shape[:-1], e.shape[-1]
    e = e.reshape(-1, length)
    r = r.reshape(-1, length)
    # 벽 칸마다 구간 번호가 하나씩 오른다 — 줄마다 (length + 1) 씩 띄워 줄 사이 번호가 겹치지 않게 한다
    segment = np.cumsum(~e, axis=1, dtype=np.int32)
    segment += (np.arange(e.shape[0], dtype=np.int32) * (length + 1))[:, None]
    hit = np.zeros(e.shape[0] * (length + 1) + 1, bool)
    hit[segment[r]] = True
    out = e & hit[segment]
    return np.moveaxis(out.reshape(*lead, length), -1, axis)


def exterior(empty) -> np.ndarray:
    """격자 테두리의 빈칸에서 빈칸끼리(6방향) 이어진 영역 — 바깥 공간."""
    reached = np.zeros_like(empty)
    for axis in range(3):
        for index in (0, -1):
            face = [slice(None)] * 3
            face[axis] = index
            reached[tuple(face)] = empty[tuple(face)]
    count = -1
    # 축마다 구간 단위로 번지므로 반복 횟수는 경로의 꺾임 수 정도다 (칸 수가 아니다)
    while True:
        for axis in range(3):
            reached = _spread_along(empty, reached, axis)
        now = int(reached.sum())
        if now == count:
            return reached
        count = now


def _dilate(mask, radius: int) -> np.ndarray:
    """6방향 팽창을 radius 번 — 반지름 radius 칸 마름모."""
    out = mask.copy()
    for _ in range(radius):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        grown[:, :, 1:] |= out[:, :, :-1]
        grown[:, :, :-1] |= out[:, :, 1:]
        out = grown
    return out


def solidify(wall, close: int) -> np.ndarray:
    """벽 복셀 격자를 속이 찬 덩어리로. close 칸 이하 반폭의 틈·입구는 막힌다.

    팽창으로 입구를 막은 뒤 속을 채우고, 같은 만큼 침식해 바깥 윤곽을 되돌린다. 원래 벽은 그대로 더해
    얇은 천 자락이 침식으로 사라지지 않게 한다."""
    close = max(int(close), 0)
    grown = _dilate(wall, close) if close else wall
    filled = ~exterior(~grown)
    if close:
        filled = ~_dilate(~filled, close)
    # 닫힘보다 넓은 찢김으로 몸속이 바깥과 이어지면 흘려 넣기가 몸속까지 바깥으로 본다(실측 2026-09-29, 덩치큰 좀비:
    # 닫힘 1~3cm 모두 몸통·팔 속이 비어 리토폴로지가 몸속 벽까지 깔았고, 5cm 는 팔과 몸 사이까지 메웠다).
    # 흘려 넣기와 별개로 여러 방향에서 바깥이 보이는지로 몸속을 가려 채운다
    return fill_enclosed(~exterior(~(filled | wall)))


DIRECTIONS = tuple(d for d in __import__("itertools").product((-1, 0, 1), repeat=3) if d != (0, 0, 0))


def escape_directions(blocked) -> np.ndarray:
    """막히지 않은 칸마다 26 방향 중 격자 밖까지 막힌 칸 없이 뚫린 방향 수 (0~26, 막힌 칸은 0).

    방향마다 주축을 따라 한 장씩 훑으며 '다음 칸이 막혔나'를 옮겨 온다 — 칸 수에 비례하는 선형 시간이다."""
    count = np.zeros(blocked.shape, np.uint8)
    for d in DIRECTIONS:
        a = next(i for i in range(3) if d[i] != 0)
        b, c = [i for i in range(3) if i != a]
        grid = np.moveaxis(blocked, a, 0)
        hit = np.empty_like(grid)
        db, dc = d[b], d[c]
        rows, cols = grid.shape[1], grid.shape[2]
        # 다음 장의 (j+db, i+dc) 칸을 현재 장의 (j, i) 로 가져오는 슬라이스 — 격자 밖은 막히지 않은 것으로 둔다
        dst = (slice(max(0, -db), rows - max(0, db)), slice(max(0, -dc), cols - max(0, dc)))
        src = (slice(max(0, db), rows - max(0, -db)), slice(max(0, dc), cols - max(0, -dc)))
        order = range(grid.shape[0] - 1, -1, -1) if d[a] > 0 else range(grid.shape[0])
        prev = None
        for k in order:
            cur = grid[k].copy()
            if prev is not None:
                cur[dst] |= prev[src]
            hit[k] = cur
            prev = cur
        count += ~np.moveaxis(hit, 0, a)
    count[blocked] = 0
    return count


def fill_enclosed(solid, min_escapes: int = MIN_ESCAPE_DIRECTIONS) -> np.ndarray:
    """바깥이 보이는 방향이 min_escapes 보다 적은 빈칸을 채운 덩어리.

    대각 광선이 계단 모양 벽의 꼭짓점 틈으로 새지 않도록 판정은 한 칸 두껍게 한 덩어리에서 하고, 채울 칸을 한 칸 되
    넓혀 벽에 붙은 띠까지 채운다. 끝으로 바깥과 이어지지 않은 빈칸(갇힌 방울)을 모두 채운다.
    서로 마주 보는 찢김(앞·뒤) 사이 관통 굴처럼 여러 방향으로 트인 몸속은 남는다."""
    thick = _dilate(solid, 1)
    enclosed = ~thick & (escape_directions(thick) < min_escapes)
    enclosed = _dilate(enclosed, 1) & ~solid
    return ~exterior(~(solid | enclosed))


def boundary_quads(solid, lo, pitch: float):
    """속 칸과 빈칸 사이 면마다 사각형 하나 — (정점 (V, 3), 사각형 (F, 4)). 노멀은 바깥(빈칸) 쪽이다."""
    solid = np.asarray(solid, bool)
    corners = np.asarray(solid.shape) + 1
    quads = []
    for axis in range(3):
        b, c = (axis + 1) % 3, (axis + 2) % 3
        low = [slice(None)] * 3
        high = [slice(None)] * 3
        low[axis], high[axis] = slice(None, -1), slice(1, None)
        a, z = solid[tuple(low)], solid[tuple(high)]
        for sign, where in ((1, a & ~z), (-1, ~a & z)):
            cells = np.argwhere(where)
            if not len(cells):
                continue
            base = cells.copy()
            base[:, axis] += 1                    # 두 칸 사이 평면
            steps = [(0, 0), (1, 0), (1, 1), (0, 1)]
            if sign < 0:
                steps = steps[::-1]
            corner_ids = []
            for db, dc in steps:
                p = base.copy()
                p[:, b] += db
                p[:, c] += dc
                corner_ids.append((p[:, 0] * corners[1] + p[:, 1]) * corners[2] + p[:, 2])
            quads.append(np.stack(corner_ids, axis=1))
    if not quads:
        return np.zeros((0, 3)), np.zeros((0, 4), np.int64)
    quads = np.concatenate(quads)
    used, inverse = np.unique(quads.ravel(), return_inverse=True)
    grid = np.stack(np.unravel_index(used, tuple(corners)), axis=1).astype(np.float64)
    ids = inverse.reshape(-1, 4)
    return grid * pitch + np.asarray(lo, dtype=np.float64), ids


def plan_grid(lo, hi, pitch: float, close_distance: float = 0.0, max_cells: int = MAX_CELLS):
    """여백을 두른 격자의 (원점, 칸 수, 복셀 한 변, 닫힘 칸 수). 칸 수가 상한을 넘으면 복셀을 키운다."""
    lo = np.asarray(lo, dtype=np.float64)
    hi = np.asarray(hi, dtype=np.float64)
    while True:
        close = max(1, int(round(close_distance / pitch))) if close_distance > 0 else 0
        margin = pitch * (close + PAD)
        origin = lo - margin
        shape = np.ceil((hi + margin - origin) / pitch).astype(np.int64) + 1
        if int(np.prod(shape)) <= max_cells:
            return origin, shape, pitch, close
        pitch *= (np.prod(shape) / max_cells) ** (1 / 3) * 1.01


def build_solid(triangles, pitch: float, close_distance: float):
    """삼각형 수프 → (속 찬 격자, 격자 원점, 실제 복셀 한 변, 닫힘 칸 수)."""
    triangles = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
    flat = triangles.reshape(-1, 3)
    origin, shape, pitch, close = plan_grid(flat.min(axis=0), flat.max(axis=0), pitch, close_distance)
    wall = rasterize(triangles, origin, pitch, shape)
    return solidify(wall, close), origin, pitch, close


def solid_from_triangles(triangles, pitch: float, close_distance: float):
    """삼각형 수프 → 속 찬 덩어리의 (정점, 사각형, 실제 복셀 한 변, 닫힘 칸 수)."""
    solid, origin, pitch, close = build_solid(triangles, pitch, close_distance)
    verts, quads = boundary_quads(solid, origin, pitch)
    return verts, quads, pitch, close


PROBE_STEPS = (0.75, 1.5)   # 면 앞 탐침 거리(복셀 한 변 배수) — 한 칸 두께 천 자락의 벽 칸은 넘기되, 멀리 보면 손가락·귀
                            # 같은 얇은 부위에서 안쪽 껍질의 탐침이 반대편 바깥까지 뚫고 나가 바깥 면으로 오판한다


def facing_outside(solid, origin, pitch: float, centers, normals) -> np.ndarray:
    """면마다 앞(+노멀)쪽이 덩어리 바깥인지. 이중 껍질의 안쪽 껍질은 채워진 몸속을 향하므로 False 다.

    탐침 중 하나라도 빈칸(또는 격자 밖)이면 바깥 면이다. 닫힘이 막은 좁은 틈을 향한 면도 False 가 된다."""
    centers = np.asarray(centers, dtype=np.float64).reshape(-1, 3)
    normals = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
    shape = np.asarray(solid.shape)
    outside = np.zeros(len(centers), bool)
    for step in PROBE_STEPS:
        cells = np.floor((centers + normals * (pitch * step) - origin) / pitch).astype(np.int64)
        inside_grid = ((cells >= 0) & (cells < shape)).all(axis=1)
        clipped = np.clip(cells, 0, shape - 1)
        filled = solid[clipped[:, 0], clipped[:, 1], clipped[:, 2]] & inside_grid
        outside |= ~filled
    return outside


def fill_interior(obj, pitch: float, close_distance: float) -> dict:
    """오브젝트 메시를 속이 찬 복셀 덩어리 메시로 바꾼다 (로컬 좌표, 머티리얼 슬롯 유지).

    pitch 는 격자 한 변, close_distance 는 막을 틈의 반폭(둘 다 로컬 단위)이다. 결과의 "outer" 는 바꾸기 전
    폴리곤마다 덩어리 바깥을 향하는지(False 면 안쪽 껍질) — 슈링크랩 대상에서 안쪽 껍질을 빼는 데 쓴다.
    "outer_area" 는 그 바깥 면의 넓이 합 — 채운 뒤의 계단 면적 대신 엣지 길이 추정에 쓴다.
    메시가 비었거나 크기가 0 이면 손대지 않고 faces 0 을 돌려준다."""
    mesh = obj.data
    if not len(mesh.polygons) or not pitch > 0 or not close_distance >= 0:
        return {"faces": 0, "pitch": pitch, "close": 0, "outer": None, "outer_area": 0.0}
    mesh.calc_loop_triangles()
    count = len(mesh.vertices)
    coords = np.empty(count * 3, np.float64)
    mesh.vertices.foreach_get("co", coords)
    tri_index = np.empty(len(mesh.loop_triangles) * 3, np.int64)
    mesh.loop_triangles.foreach_get("vertices", tri_index)
    triangles = coords.reshape(-1, 3)[tri_index.reshape(-1, 3)]
    centers = np.empty(len(mesh.polygons) * 3, np.float64)
    normals = np.empty(len(mesh.polygons) * 3, np.float64)
    mesh.polygons.foreach_get("center", centers)
    mesh.polygons.foreach_get("normal", normals)
    areas = np.empty(len(mesh.polygons), np.float64)
    mesh.polygons.foreach_get("area", areas)
    solid, origin, used_pitch, close = build_solid(triangles, pitch, close_distance)
    outer = facing_outside(solid, origin, used_pitch, centers, normals)
    outer_area = float(areas[outer].sum())
    verts, quads = boundary_quads(solid, origin, used_pitch)
    del solid
    if not len(quads):
        return {"faces": 0, "pitch": used_pitch, "close": close, "outer": outer, "outer_area": outer_area}
    mesh.clear_geometry()
    mesh.vertices.add(len(verts))
    mesh.vertices.foreach_set("co", verts.astype(np.float32).ravel())
    mesh.loops.add(quads.size)
    mesh.loops.foreach_set("vertex_index", quads.astype(np.int32).ravel())
    mesh.polygons.add(len(quads))
    mesh.polygons.foreach_set("loop_start", (np.arange(len(quads), dtype=np.int32) * 4))
    mesh.update(calc_edges=True)
    mesh.validate()
    return {"faces": len(mesh.polygons), "pitch": used_pitch, "close": close, "outer": outer,
            "outer_area": outer_area}
