"""속이 찬 단일 표면 리메시 — 셰이프 서버가 이중 껍질 대신 속을 채운 한 겹을 내보내게 한다.

공식 내보내기(`o_voxel.postprocess.to_glb(remesh=True)`)는 원본 등위면 둘레의 **부호 없는 거리 - band** 를 등위면으로
듀얼 컨투어링하므로, 표면 양쪽 ±1복셀에 바깥·안쪽 두 겹이 생기고 속은 비어 있다(두께 4mm 안팎). 그 상태로 요청 면수까지
데시메이트하면 정강이·손가락·관절처럼 얇은 부위에서 두 겹이 서로 뚫고 지나가 누더기가 되고(실측 2026-10-02, 메카닉 캐릭터:
삼각형 2.6cm 대 껍질 4mm, 정점 병합만으로 겹친 면 1,163개), 등위면이 찢긴 자리마다 두 겹을 잇는 터널이 남는다.

여기서는 같은 듀얼 컨투어링 커널(cumesh._C)을 **부호 있는 거리장**으로 돌린다. 부호는 광선 탈출 판정으로 정한다 —
점에서 26 방향(축·면 대각·꼭짓점 대각)으로 광선을 쏴 원본 메시에 막히지 않고 빠져나가는 방향이 MIN_ESCAPES 보다
적으면 '속'이다. 몸속은 찢김 너머 0~1 방향만 트이고, 다리 사이·팔과 몸 사이는 6 방향 이상 트이므로 뚜렷이 갈린다
(애드온 lowpoly/solid_fill.py 와 같은 기준, 실측 2026-09-29). 결과는 속이 찬 워터타이트 한 겹이고, 찢김은 판정 경계를
따라 자연스럽게 막힌다(큰 찢김일수록 캡이 안쪽으로 움푹하다).

표면 격자는 공식 경로처럼 거칠게 시작해 표면 근처(중심 UDF < 0.87칸)와 **꼭짓점 부호가 섞인** 복셀만 8분할해 내려간다 —
찢김 캡은 표면에서 멀어도 부호가 바뀌는 자리라 격자에 들어온다. 최종 격자 꼭짓점의 값은 min(부호 있는 거리, UDF - 한 칸)
이다 — 한 칸보다 얇은 판(모자 챙·옷 끝단)은 광선으로는 속인 꼭짓점이 없어 사라지므로 표면 둘레 한 칸을 속에 더한다.

텍스처는 공식 경로와 같이 UV 공간 래스터 → 3D 위치 → 속성 볼륨 삼중선형 샘플이지만, 위치를 **원본 등위면**의 가장
가까운 점으로 되돌린 뒤 샘플한다(공식 경로는 to_glb 입력 메시 기준). 찢김 캡은 가장 가까운 찢김 가장자리 색을 받는다.
"""
import itertools

import numpy as np
import torch

MIN_ESCAPES = 3          # 26 방향 중 밖이 보이는 방향이 이보다 적으면 속
RAY_CHUNK = 2_000_000    # 한 번에 쏘는 광선 수 (positions 12B + face_id 8B + depth 4B)
NEAR_CELL = 0.87         # 복셀 중심 UDF 가 이 × 칸 크기 미만이면 표면이 지나는 복셀로 본다 (공식 경로와 같음, √3/2)
MIN_COMPONENT_AREA = 2e-4  # 이보다 작은 조각(도메인 한 변 1.0 기준 1.4cm² 안팎)은 지운다 — 광선 판정이 흔들린 격자 꼭짓점 하나가
                           # 만드는 몸속 방울(실측 2026-10-02: 데시메이트 뒤 조각 6,378개)은 수 mm 크기다
SMOOTH_ROUNDS = 2          # 안/밖 판정의 6 이웃 다수결 반복 횟수 — 광선이 틈을 스치는 자리의 점 단위 흔들림을 지운다
THIN_BAND = 1.0          # 표면에서 이 × 칸 크기 안의 꼭짓점도 속으로 친다 — 모자 챙·옷 끝단처럼 한 칸보다 얇은 판은 광선 판정으로는
                         # 속인 꼭짓점이 하나도 없어 통째로 사라졌다(실측 2026-10-11: 공식 경로엔 있는 모자 챙이 속 채움에선 전멸,
                         # 소매·바짓단 톱니). 얇은 판은 ±1칸 두께의 판(공식 band=1 과 같은 폭)으로, 두꺼운 곳은 그대로 속이 찬다
HOLE_PERIMETER = 0.30    # 데시메이트·비매니폴드 복구가 낸 구멍을 메우는 둘레 상한(도메인 한 변 1.0 기준, 키 1m 캐릭터에서 30cm).
                         # 속이 찬 한 겹에는 옷과 몸 사이 공동 입구 같은 '메우면 안 되는 구멍'이 없다 — 눈·입은 오목한 면이지 구멍이 아니다

# 26 방향 단위 벡터 — 애드온 solid_fill.DIRECTIONS 와 같은 집합
DIRECTIONS = torch.tensor([d for d in itertools.product((-1, 0, 1), repeat=3) if d != (0, 0, 0)],
                          dtype=torch.float32)
DIRECTIONS = DIRECTIONS / DIRECTIONS.norm(dim=1, keepdim=True)

_OFFSETS = torch.tensor([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0],
                         [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1]], dtype=torch.int32)
# 듀얼 컨투어링 위상 — 공식 remesh_narrow_band_dc 와 같은 표
_EDGE_NEIGHBOR = torch.tensor([
    [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]],
    [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
    [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]],
], dtype=torch.int32)
_SPLIT_1_N = torch.tensor([0, 1, 2, 0, 2, 3], dtype=torch.long)
_SPLIT_1_P = torch.tensor([0, 2, 1, 0, 3, 2], dtype=torch.long)
_SPLIT_2_N = torch.tensor([0, 1, 3, 3, 1, 2], dtype=torch.long)
_SPLIT_2_P = torch.tensor([0, 3, 1, 3, 2, 1], dtype=torch.long)


def escape_counts(bvh, points: torch.Tensor, directions: torch.Tensor = None, chunk: int = RAY_CHUNK) -> torch.Tensor:
    """점마다 26 방향 중 원본 메시에 막히지 않고 빠져나가는 광선 수 (uint8, 0~26)."""
    directions = (DIRECTIONS if directions is None else directions).to(points.device)
    counts = torch.zeros(points.shape[0], dtype=torch.uint8, device=points.device)
    for start in range(0, points.shape[0], chunk):
        pts = points[start:start + chunk].contiguous()
        part = torch.zeros(pts.shape[0], dtype=torch.uint8, device=points.device)
        for d in directions:
            _pos, face_id, _depth = bvh.ray_trace(pts, d.expand_as(pts).contiguous())
            part += (face_id < 0).to(torch.uint8)      # 맞은 삼각형이 없으면 -1 — 밖으로 빠져나간 광선
        counts[start:start + chunk] = part
    return counts


def inside_mask(bvh, points: torch.Tensor, min_escapes: int = MIN_ESCAPES) -> torch.Tensor:
    """속(True)/밖(False) 판정 — 탈출 방향이 min_escapes 미만이면 속."""
    return escape_counts(bvh, points) < min_escapes


def mixed_sign_voxels(inside_corner: torch.Tensor, inverse: torch.Tensor) -> torch.Tensor:
    """복셀 8 꼭짓점의 안/밖이 섞였는지 — inside_corner 는 고유 꼭짓점별 판정, inverse 는 (N*8,) 꼭짓점 → 고유 색인."""
    flags = inside_corner[inverse].reshape(-1, 8)
    return flags.any(dim=1) & ~flags.all(dim=1)


def corner_keys(corners: torch.Tensor, resolution: int) -> torch.Tensor:
    """격자 꼭짓점 정수 좌표 (N, 3) → 유일한 int64 키. 격자 꼭짓점은 0..resolution 이라 한 변이 resolution+1 이다."""
    side = resolution + 1
    c = corners.to(torch.int64)
    return (c[:, 0] * side + c[:, 1]) * side + c[:, 2]


def lookup_flags(keys_sorted: torch.Tensor, flags_sorted: torch.Tensor, query_keys: torch.Tensor) -> torch.Tensor:
    """정렬된 키 목록에서 질의 키의 플래그를 찾는다. 모든 질의 키가 목록에 있어야 한다(없으면 ValueError)."""
    idx = torch.searchsorted(keys_sorted, query_keys)
    idx = idx.clamp(max=keys_sorted.shape[0] - 1)
    if not bool((keys_sorted[idx] == query_keys).all()):
        raise ValueError("격자 꼭짓점이 꼭짓점 판정 목록에 없다")
    return flags_sorted[idx]


class SignCache:
    """한 해상도의 격자 꼭짓점 안/밖 판정 캐시 — 처음 보는 꼭짓점만 광선을 쏜다."""

    def __init__(self, bvh, resolution: int, scale: float, center: torch.Tensor, min_escapes: int, device,
                 band: float = THIN_BAND):
        self.bvh, self.resolution, self.scale, self.center, self.min_escapes = bvh, resolution, scale, center, min_escapes
        self.band = band * scale / resolution       # 절대 거리 — 해상도마다 한 칸 크기가 다르다
        self.keys = torch.empty(0, dtype=torch.int64, device=device)
        self.flags = torch.empty(0, dtype=torch.bool, device=device)
        self.queries = 0

    def _coords(self, keys: torch.Tensor) -> torch.Tensor:
        side = self.resolution + 1
        return torch.stack([keys // (side * side), (keys // side) % side, keys % side], dim=1)

    def flags_for(self, corners: torch.Tensor) -> torch.Tensor:
        """꼭짓점 정수 좌표 (N, 3) → 속 여부 (N,)."""
        keys = corner_keys(corners, self.resolution)
        uniq, inverse = torch.unique(keys, return_inverse=True)
        if self.keys.shape[0]:
            idx = torch.searchsorted(self.keys, uniq).clamp(max=self.keys.shape[0] - 1)
            known = self.keys[idx] == uniq
        else:
            known = torch.zeros(uniq.shape[0], dtype=torch.bool, device=uniq.device)
        missing = uniq[~known]
        if missing.shape[0]:
            pts = (self._coords(missing).float() / self.resolution - 0.5) * self.scale + self.center
            new_flags = inside_mask(self.bvh, pts, self.min_escapes) | (self.bvh.unsigned_distance(pts)[0] < self.band)
            self.queries += int(missing.shape[0])
            self.keys = torch.cat([self.keys, missing])
            self.flags = torch.cat([self.flags, new_flags])
            order = torch.argsort(self.keys)
            self.keys, self.flags = self.keys[order], self.flags[order]
        return lookup_flags(self.keys, self.flags, uniq)[inverse]

    def smooth(self, rounds: int = SMOOTH_ROUNDS) -> int:
        """캐시된 꼭짓점의 판정을 6 이웃(축 방향)과의 다수결(7표 중 4표)로 다듬는다. 모르는 이웃은 자기 값으로 친다.

        광선 탈출 수가 문턱(3) 언저리에서 흔들리는 자리(좁은 틈 안, 찢김 가장자리)는 점마다 안/밖이 뒤바뀌어 듀얼 컨투어링이
        방울·지느러미를 쏟아 냈다(실측 2026-10-02: 닫기 6회에도 섞인 복셀이 계속 늘고, 데시메이트 뒤 조각 7,894 · 비매니폴드 2,643).
        두께 한 칸짜리 벽은 같은 면 이웃 4표로 살아남고, 한 칸짜리 점·실은 지워진다. 바뀐 꼭짓점 수를 돌려준다."""
        if self.keys.shape[0] == 0:
            return 0
        side = self.resolution + 1
        steps = (1, side, side * side)
        changed = 0
        for _ in range(rounds):
            votes = self.flags.to(torch.int8).clone()
            for step in steps:
                for sign in (-1, 1):
                    nb = self.keys + sign * step
                    idx = torch.searchsorted(self.keys, nb).clamp(max=self.keys.shape[0] - 1)
                    known = self.keys[idx] == nb
                    votes += torch.where(known, self.flags[idx], self.flags).to(torch.int8)
            new_flags = votes >= 4
            changed += int((new_flags != self.flags).sum())
            self.flags = new_flags
        return changed


_NEIGHBORS_26 = torch.tensor([d for d in itertools.product((-1, 0, 1), repeat=3) if d != (0, 0, 0)], dtype=torch.int32)


def close_active_voxels(coords: torch.Tensor, cache: "SignCache", resolution: int, verbose: bool = False,
                        max_rounds: int = 6) -> torch.Tensor:
    """부호가 섞인 복셀 집합을 닫는다 — 부호가 바뀌는 격자 엣지를 둘러싼 복셀 4개가 모두 들어와야 듀얼 컨투어링이 닫힌 면을 낸다.

    후보는 거친 격자에서 내려오므로, 광선 판정이 흔들린 꼭짓점 하나가 만드는 방울이나 캡 가장자리처럼 부모 칸이 버려진 자리에서는
    엣지를 둘러싼 복셀 일부가 빠져 열린 조각이 남았다(실측 2026-10-02: 데시메이트 뒤 조각 6,272 · 열린 엣지 5,557).
    섞인 복셀의 26 이웃을 후보로 더하고 그중 섞인 것을 다시 넣기를 더 늘지 않을 때까지 반복한다."""
    offsets = _OFFSETS.to(coords.device)
    neighbors = _NEIGHBORS_26.to(coords.device)

    def mixed_of(vox):
        corners = (vox.unsqueeze(1) + offsets.unsqueeze(0)).reshape(-1, 3)
        flags = cache.flags_for(corners).reshape(-1, 8)
        return flags.any(dim=1) & ~flags.all(dim=1)

    active = coords[mixed_of(coords)]
    for round_no in range(max_rounds):
        active_keys = corner_keys(active, resolution)        # 복셀 좌표도 0..resolution-1 이라 같은 키 함수를 쓴다
        active_keys, _ = torch.sort(active_keys)
        grown = (active.unsqueeze(1) + neighbors.unsqueeze(0)).reshape(-1, 3)
        grown = grown[((grown >= 0) & (grown < resolution)).all(dim=1)]
        grown = torch.unique(grown, dim=0)
        idx = torch.searchsorted(active_keys, corner_keys(grown, resolution)).clamp(max=active_keys.shape[0] - 1)
        fresh = grown[active_keys[idx] != corner_keys(grown, resolution)]
        if fresh.shape[0] == 0:
            break
        fresh = fresh[mixed_of(fresh)]
        if verbose:
            print(f"닫기 {round_no + 1}: 활성 {active.shape[0]:,} + 새 섞임 {fresh.shape[0]:,}", flush=True)
        if fresh.shape[0] == 0:
            break
        active = torch.cat([active, fresh])
    return active.contiguous()


def remesh_solid_dc(vertices: torch.Tensor, faces: torch.Tensor, center: torch.Tensor, scale: float,
                    resolution: int, bvh=None, min_escapes: int = MIN_ESCAPES, verbose: bool = False):
    """원본(찢긴) 등위면 → 속이 찬 한 겹 삼각 메시 (V (N,3) float32, F (M,3) int32).

    center·scale·resolution 은 공식 to_glb 의 remesh 분기와 같은 의미다(도메인 중심·한 변·격자 해상도)."""
    import cumesh
    from cumesh import _C

    assert vertices.dtype == torch.float32 and faces.dtype == torch.int32
    device = vertices.device
    center = center.to(device)
    if bvh is None:
        bvh = cumesh.cuBVH(vertices, faces)
    offsets = _OFFSETS.to(device)

    base = resolution
    while base > 32:
        assert base % 2 == 0, "해상도는 32 × 2^n 이어야 한다"
        base //= 2
    coords = torch.stack(torch.meshgrid(torch.arange(base, device=device), torch.arange(base, device=device),
                                        torch.arange(base, device=device), indexing='ij'), dim=-1).int().reshape(-1, 3)
    cache = None
    while True:
        cell = scale / base
        cache = SignCache(bvh, base, scale, center, min_escapes, device)
        centers = ((coords.float() + 0.5) / base - 0.5) * scale + center
        near = bvh.unsigned_distance(centers)[0] < NEAR_CELL * cell
        corners = (coords.unsqueeze(1) + offsets.unsqueeze(0)).reshape(-1, 3)
        flags = cache.flags_for(corners).reshape(-1, 8)
        mixed = flags.any(dim=1) & ~flags.all(dim=1)
        keep = near | mixed
        if verbose:
            print(f"격자 {base}: 후보 {keep.shape[0]:,} → 표면 {int(near.sum()):,} · 부호 섞임 {int(mixed.sum()):,} "
                  f"· 유지 {int(keep.sum()):,} · 광선 꼭짓점 {cache.queries:,}", flush=True)
        if base >= resolution:
            # 최종 격자: 판정을 다수결로 다듬은 뒤, 교차가 있는(부호 섞인) 복셀만 쓰되 부호가 바뀌는 엣지 둘레
            # 복셀 4개가 모두 들어오도록 닫는다
            changed = cache.smooth()
            if verbose:
                print(f"판정 다듬기: 꼭짓점 {changed:,}개 뒤집음", flush=True)
            coords = close_active_voxels(coords[keep], cache, resolution, verbose=verbose)
            break
        coords = coords[keep]
        base *= 2
        coords = (coords * 2).unsqueeze(1) + offsets.unsqueeze(0)
        coords = coords.reshape(-1, 3)

    if coords.shape[0] == 0:
        raise RuntimeError("활성 복셀이 없다 — 속 채움 리메시 실패")

    # ---- 듀얼 컨투어링 (공식 remesh_narrow_band_dc 와 같은 커널·같은 위상 생성) ----
    n_vox = coords.shape[0]
    coords = coords.contiguous()
    hashmap_vox = _init_hashmap(resolution, 2 * n_vox, device)
    _C.hashmap_insert_3d_idx_as_val_cuda(*hashmap_vox, torch.cat([torch.zeros_like(coords[:, :1]), coords], dim=1),
                                         resolution, resolution, resolution)
    grid_verts = _C.get_sparse_voxel_grid_active_vertices(*hashmap_vox, coords, resolution, resolution, resolution)
    n_vert = grid_verts.shape[0]
    pts_vert = (grid_verts.float() / resolution - 0.5) * scale + center
    udf = bvh.unsigned_distance(pts_vert)[0]
    inside_vert = cache.flags_for(grid_verts)
    # 장은 min(부호 있는 거리, UDF - 띠) — 두꺼운 곳의 속은 -UDF, 얇은 판 둘레는 UDF - 띠 로 판 양쪽에 면이 선다.
    # 다수결로 뒤집힌 꼭짓점도 부호는 판정을 따르도록 크기만 쓰고, 0 에 걸리지 않게 아주 작은 값으로 민다
    band = cache.band
    values = torch.where(inside_vert, torch.minimum(-udf, udf - band).clamp(max=-1e-7), (udf - band).clamp(min=1e-7))

    hashmap_vert = _init_hashmap(resolution + 1, 2 * n_vert, device)
    _C.hashmap_insert_3d_idx_as_val_cuda(*hashmap_vert, torch.cat([torch.zeros_like(grid_verts[:, :1]), grid_verts], dim=1),
                                         resolution + 1, resolution + 1, resolution + 1)
    dual_verts, intersected = _C.simple_dual_contour(*hashmap_vert, coords, values,
                                                     resolution + 1, resolution + 1, resolution + 1)

    edge_neighbor = _EDGE_NEIGHBOR.to(device).unsqueeze(0)
    neighbor = coords.reshape(n_vox, 1, 1, 3) + edge_neighbor                 # (N, 3, 4, 3)
    connected = neighbor[intersected != 0]                                      # (M, 4, 3)
    intersected = intersected[intersected != 0]
    m = connected.shape[0]
    key = torch.cat([torch.zeros((m * 4, 1), dtype=torch.int, device=device), connected.reshape(-1, 3)], dim=1)
    idx = _C.hashmap_lookup_3d_cuda(*hashmap_vox, key, resolution, resolution, resolution).reshape(m, 4).int()
    valid = (idx != 0xffffffff).all(dim=1)
    quads = idx[valid].int()
    direction = intersected[valid].int()

    used = torch.unique(quads.reshape(-1))
    dual_verts = dual_verts[used]
    remap = torch.zeros((n_vox,), dtype=torch.int32, device=device)
    remap[used] = torch.arange(used.shape[0], dtype=torch.int32, device=device)
    quads = remap[quads]
    mesh_vertices = (dual_verts / resolution - 0.5) * scale + center

    def _split(pos, neg):
        tris = torch.where((direction == 1).unsqueeze(1), quads[:, pos.to(device)], quads[:, neg.to(device)])
        n0 = torch.cross(mesh_vertices[tris[:, 1]] - mesh_vertices[tris[:, 0]],
                         mesh_vertices[tris[:, 2]] - mesh_vertices[tris[:, 0]], dim=1)
        n1 = torch.cross(mesh_vertices[tris[:, 2]] - mesh_vertices[tris[:, 1]],
                         mesh_vertices[tris[:, 3]] - mesh_vertices[tris[:, 1]], dim=1)
        return tris, (n0 * n1).sum(dim=1).abs()

    tris0, align0 = _split(_SPLIT_1_P, _SPLIT_1_N)
    tris1, align1 = _split(_SPLIT_2_P, _SPLIT_2_N)
    triangles = torch.where((align0 > align1).unsqueeze(1), tris0, tris1).reshape(-1, 3)
    if verbose:
        print(f"속 채움 DC: 복셀 {n_vox:,} · 꼭짓점 {n_vert:,} · 정점 {mesh_vertices.shape[0]:,} · 면 {triangles.shape[0]:,}",
              flush=True)
    return mesh_vertices.contiguous(), triangles.int().contiguous()


def weld_stats(vertices, faces) -> str:
    """용접(소수 6자리) 기준 열린 엣지·비매니폴드 엣지(면 3개 이상)·겹친 면 수 — 서버 로그와 애드온 검증이 같은 지표를 본다."""
    v = np.asarray(vertices, dtype=np.float64)
    f = np.asarray(faces, dtype=np.int64)
    _u, inverse = np.unique(np.round(v, 6), axis=0, return_inverse=True)
    w = inverse.reshape(-1)[f]
    tri = np.sort(w, axis=1)
    _t, tri_counts = np.unique(tri, axis=0, return_counts=True)
    edges = np.sort(np.concatenate([w[:, [0, 1]], w[:, [1, 2]], w[:, [2, 0]]]), axis=1)
    _e, counts = np.unique(edges, axis=0, return_counts=True)
    return (f"열린엣지 {int((counts == 1).sum()):,} 비매니폴드엣지 {int((counts > 2).sum()):,} "
            f"겹친면 {int((tri_counts > 1).sum()):,}")


def _init_hashmap(resolution: int, capacity: int, device):
    vol = resolution ** 3
    if vol < 2 ** 32:
        keys = torch.full((capacity,), torch.iinfo(torch.uint32).max, dtype=torch.uint32, device=device)
    else:
        keys = torch.full((capacity,), torch.iinfo(torch.uint64).max, dtype=torch.uint64, device=device)
    return keys, torch.empty((capacity,), dtype=torch.uint32, device=device)


def to_glb_solid(vertices: torch.Tensor, faces: torch.Tensor, attr_volume: torch.Tensor, coords: torch.Tensor,
                 attr_layout: dict, aabb, voxel_size: float, decimation_target: int, texture_size: int = 2048,
                 min_escapes: int = MIN_ESCAPES, verbose: bool = False):
    """원본 등위면 + 속성 볼륨 → 속이 찬 한 겹의 PBR GLB(trimesh.Trimesh). 공식 to_glb 와 같은 출력 규약(glTF 축·UV V 뒤집기)."""
    import cv2
    import cumesh
    import trimesh
    import trimesh.visual
    from PIL import Image
    from flex_gemm.ops.grid_sample import grid_sample_3d
    import nvdiffrast.torch as dr        # 이미지에서 uv_raster(토치) 가 이 이름으로 얹혀 있다

    device = coords.device
    aabb = torch.as_tensor(np.asarray(aabb), dtype=torch.float32, device=device)
    vs = torch.tensor([voxel_size] * 3, dtype=torch.float32, device=device)
    grid_size = ((aabb[1] - aabb[0]) / vs).round().int()
    vertices = vertices.cuda().float().contiguous()
    faces = faces.cuda().int().contiguous()

    # 원본 등위면의 BVH — 안/밖 판정·거리장·텍스처 샘플 위치 복원에 모두 쓴다
    bvh = cumesh.cuBVH(vertices, faces)
    center = aabb.mean(dim=0)
    scale = float((aabb[1] - aabb[0]).max().item())
    resolution = int(grid_size.max().item())
    dc_v, dc_f = remesh_solid_dc(vertices, faces, center, scale, resolution, bvh=bvh,
                                 min_escapes=min_escapes, verbose=verbose)

    mesh = cumesh.CuMesh()
    mesh.init(dc_v, dc_f)

    def _say(stage, weld=True):
        if not verbose:
            return
        line = f"속 채움 {stage}: 정점 {mesh.num_vertices:,} 면 {mesh.num_faces:,} 조각 {mesh.num_conneted_components:,}"
        if weld:   # 용접 기준 열린·비매니폴드 엣지 — 수백만 면에서는 몇 초 걸리므로 데시메이트 뒤에만 센다
            v, f = mesh.read()
            line += " · " + weld_stats(v.cpu().numpy(), f.cpu().numpy())
        print(line, flush=True)

    _say("DC 직후", weld=False)
    mesh.remove_small_connected_components(MIN_COMPONENT_AREA)
    mesh.simplify(int(decimation_target), verbose=verbose)
    _say("데시메이트 후")
    # 수백만 면에서는 cumesh 연결 요소 계산이 깨져(조각 수가 수억으로 나온다) 작은 조각 제거가 듣지 않는다 — 데시메이트 뒤에 한다
    mesh.remove_small_connected_components(MIN_COMPONENT_AREA)
    _say("작은 조각 제거 후")
    # 데시메이트가 남긴 겹친 면·퇴화 면·지느러미(한 엣지에 면 3개 이상)를 걷어 내고, 그 자리에 난 구멍을 메운다.
    # 공식 경로의 repair_non_manifold_edges 는 정점을 쪼개 위상만 매니폴드로 만들므로 같은 자리에 정점이 겹쳐 남고,
    # 애드온이 심을 용접하면 도로 비매니폴드가 된다(실측: 용접 후 비매니폴드 엣지 4,717) — 면을 지우는 쪽을 쓴다
    mesh.remove_duplicate_faces()
    mesh.remove_degenerate_faces()
    mesh.remove_non_manifold_faces()
    _say("비매니폴드 정리 후")
    mesh.remove_small_connected_components(MIN_COMPONENT_AREA)
    mesh.fill_holes(max_hole_perimeter=HOLE_PERIMETER)
    mesh.remove_duplicate_faces()
    mesh.remove_small_connected_components(MIN_COMPONENT_AREA)
    _say("구멍 메운 후")

    # ---- UV 언랩 (공식 기본 인자) ----
    out_vertices, out_faces, out_uvs, out_vmaps = mesh.uv_unwrap(
        compute_charts_kwargs={"threshold_cone_half_angle_rad": np.radians(90.0), "refine_iterations": 0,
                               "global_iterations": 1, "smooth_strength": 1},
        return_vmaps=True, verbose=verbose)
    out_vertices = out_vertices.cuda()
    out_faces = out_faces.cuda()
    out_uvs = out_uvs.cuda()
    out_vmaps = out_vmaps.cuda()
    mesh.compute_vertex_normals()
    out_normals = mesh.read_vertex_normals()[out_vmaps]

    # ---- 텍스처 굽기: UV 래스터 → 3D 위치 → 원본 등위면의 가장 가까운 점 → 속성 볼륨 ----
    ctx = dr.RasterizeCudaContext()
    uvs_rast = torch.cat([out_uvs * 2 - 1, torch.zeros_like(out_uvs[:, :1]), torch.ones_like(out_uvs[:, :1])],
                         dim=-1).unsqueeze(0)
    rast = torch.zeros((1, texture_size, texture_size, 4), device='cuda', dtype=torch.float32)
    for i in range(0, out_faces.shape[0], 100000):
        rast_chunk, _ = dr.rasterize(ctx, uvs_rast, out_faces[i:i + 100000], resolution=[texture_size, texture_size])
        mask_chunk = rast_chunk[..., 3:4] > 0
        rast_chunk[..., 3:4] += i
        rast = torch.where(mask_chunk, rast_chunk, rast)
    mask = rast[0, ..., 3] > 0
    pos = dr.interpolate(out_vertices.unsqueeze(0), rast, out_faces)[0][0]
    valid_pos = pos[mask]
    _d, face_id, uvw = bvh.unsigned_distance(valid_pos, return_uvw=True)
    tri = vertices[faces[face_id.long()]]
    valid_pos = (tri * uvw.unsqueeze(-1)).sum(dim=1)
    attrs = torch.zeros(texture_size, texture_size, attr_volume.shape[1], device='cuda')
    attrs[mask] = grid_sample_3d(
        attr_volume, torch.cat([torch.zeros_like(coords[:, :1]), coords], dim=-1),
        shape=torch.Size([1, attr_volume.shape[1], *grid_size.tolist()]),
        grid=((valid_pos - aabb[0]) / vs).reshape(1, -1, 3), mode='trilinear')

    mask_np = mask.cpu().numpy()
    base_color = np.clip(attrs[..., attr_layout['base_color']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    metallic = np.clip(attrs[..., attr_layout['metallic']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    roughness = np.clip(attrs[..., attr_layout['roughness']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    alpha = np.clip(attrs[..., attr_layout['alpha']].cpu().numpy() * 255, 0, 255).astype(np.uint8)
    mask_inv = (~mask_np).astype(np.uint8)
    base_color = cv2.inpaint(base_color, mask_inv, 3, cv2.INPAINT_TELEA)
    metallic = cv2.inpaint(metallic, mask_inv, 1, cv2.INPAINT_TELEA)[..., None]
    roughness = cv2.inpaint(roughness, mask_inv, 1, cv2.INPAINT_TELEA)[..., None]
    alpha = cv2.inpaint(alpha, mask_inv, 1, cv2.INPAINT_TELEA)[..., None]
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=Image.fromarray(np.concatenate([base_color, alpha], axis=-1)),
        baseColorFactor=np.array([255, 255, 255, 255], dtype=np.uint8),
        metallicRoughnessTexture=Image.fromarray(np.concatenate([np.zeros_like(metallic), roughness, metallic], axis=-1)),
        metallicFactor=1.0, roughnessFactor=1.0, alphaMode='OPAQUE', doubleSided=False)

    vertices_np = out_vertices.cpu().numpy()
    faces_np = out_faces.cpu().numpy()
    uvs_np = out_uvs.cpu().numpy()
    normals_np = out_normals.cpu().numpy()
    vertices_np[:, 1], vertices_np[:, 2] = vertices_np[:, 2], -vertices_np[:, 1]
    normals_np[:, 1], normals_np[:, 2] = normals_np[:, 2], -normals_np[:, 1]
    uvs_np[:, 1] = 1 - uvs_np[:, 1]
    return trimesh.Trimesh(vertices=vertices_np, faces=faces_np, vertex_normals=normals_np, process=False,
                           visual=trimesh.visual.TextureVisuals(uv=uvs_np, material=material))
