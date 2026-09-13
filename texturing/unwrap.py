# 6방향 박스 투영 언랩 + 셸프 패킹
#
# bpy.ops(Smart UV Project)는 Edit 모드와 UI 컨텍스트가 필요해 타이머 콜백 안에서
# 불안정하다. 로우폴리 모델은 면이 축에 정렬된 경우가 대부분이고, 텍스처를 6개
# 직교 시점에서 투영 베이크하므로 면을 가장 마주보는 시점 평면에 투영하면 아일랜드
# 방향이 그 시점 그림과 그대로 일치한다. 결정적이고 bpy 없이 검증 가능하다.
import math

# 시점: (right, up, toward_camera) — bake.VIEW_SPECS와 동일한 축 배치
VIEW_AXES = {
    "FRONT": ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "RIGHT": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "BACK": ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    "LEFT": ((0, -1, 0), (0, 0, 1), (-1, 0, 0)),
    "TOP": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "BOTTOM": ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
}
TEXTURE_UV = "LP3D_Tex"   # 언랩 결과 레이어 (팔레트 UVMap과 공존하다가 적용 시 교체)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def pick_view(normal) -> str:
    """면 노멀이 가장 마주보는 시점."""
    return max(VIEW_AXES, key=lambda v: _dot(normal, VIEW_AXES[v][2]))


def project(point, view: str):
    right, up, _ = VIEW_AXES[view]
    return _dot(point, right), _dot(point, up)


class _DisjointSet:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_islands(faces):
    """faces: [(vert_keys, positions, normal)] — vert_keys는 정점 식별자(공유 변 판정용).

    같은 시점에 투영되고 변을 공유하는 면을 한 아일랜드로 묶는다.
    반환: [{"view", "faces": [face_index], "uvs": {face_index: [(u, v)...]}, "bounds"}]"""
    views = [pick_view(f[2]) for f in faces]
    ds = _DisjointSet(len(faces))
    edge_owner = {}
    for fi, (keys, _pos, _n) in enumerate(faces):
        n = len(keys)
        for i in range(n):
            a, b = keys[i], keys[(i + 1) % n]
            edge = (a, b) if a <= b else (b, a)
            other = edge_owner.get(edge)
            if other is None:
                edge_owner[edge] = fi
            elif views[other] == views[fi]:
                ds.union(other, fi)
    groups = {}
    for fi in range(len(faces)):
        groups.setdefault(ds.find(fi), []).append(fi)
    islands = []
    for members in groups.values():
        view = views[members[0]]
        uvs = {fi: [project(p, view) for p in faces[fi][1]] for fi in members}
        us = [uv[0] for fi in members for uv in uvs[fi]]
        vs = [uv[1] for fi in members for uv in uvs[fi]]
        islands.append({"view": view, "faces": members, "uvs": uvs,
                        "bounds": (min(us), min(vs), max(us), max(vs))})
    return islands


def _shelf_pack(sizes, width):
    """높이 내림차순 셸프 패킹. sizes: [(w, h)] → ([(x, y)], 사용 높이)."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    positions = [None] * len(sizes)
    x = y = shelf_h = 0.0
    for i in order:
        w, h = sizes[i]
        if x > 0.0 and x + w > width:
            y += shelf_h
            x, shelf_h = 0.0, 0.0
        positions[i] = (x, y)
        x += w
        shelf_h = max(shelf_h, h)
    return positions, y + shelf_h


def pack(bounds_list, margin: float = 0.004):
    """아일랜드 경계 상자들을 0~1 정사각 안에 균일 축척으로 배치한다.

    margin은 UV 단위 간격(1024px에서 약 4텍셀). 반환: (scale, [(offset_u, offset_v)]) —
    최종 uv = (u - min_u) * scale + offset."""
    if not bounds_list:
        return 1.0, []
    raw = [(max(b[2] - b[0], 1e-9), max(b[3] - b[1], 1e-9)) for b in bounds_list]
    # 1차: 간격 없이 배치해 축척을 추정하고, 그 축척으로 간격을 월드 단위로 환산해 재배치
    scale = _best_pack(raw)[0]
    pad = margin / max(scale, 1e-12)
    padded = [(w + pad, h + pad) for w, h in raw]
    scale, positions = _best_pack(padded)
    half = pad * 0.5 * scale
    offsets = [(x * scale + half, y * scale + half) for x, y in positions]
    return scale, offsets


def _best_pack(sizes):
    """여러 셸프 너비 후보 중 정사각에 가장 가깝게 담기는 배치를 고른다."""
    total = sum(w * h for w, h in sizes)
    widest = max(w for w, _h in sizes)
    best = None
    for k in (0.8, 1.0, 1.2, 1.5, 2.0):
        width = max(math.sqrt(total) * k, widest)
        positions, used_h = _shelf_pack(sizes, width)
        used_w = max(x + w for (x, _y), (w, _h) in zip(positions, sizes))
        side = max(used_w, used_h)
        if best is None or side < best[0]:
            best = (side, positions)
    side, positions = best
    return 1.0 / side, positions


def _face_data(obj):
    """오브젝트 면을 월드 좌표로 수집한다. 정점 키는 오브젝트 이름을 붙여 전역 유일."""
    mesh = obj.data
    mw = obj.matrix_world
    nm = mw.to_3x3().inverted_safe().transposed()
    world = [tuple(mw @ v.co) for v in mesh.vertices]
    faces = []
    for poly in mesh.polygons:
        keys = tuple((obj.name, vi) for vi in poly.vertices)
        positions = [world[vi] for vi in poly.vertices]
        n = nm @ poly.normal
        length = n.length or 1.0
        faces.append((keys, positions, (n.x / length, n.y / length, n.z / length)))
    return faces


def unwrap_objects(objects, uv_name: str = TEXTURE_UV, margin: float = 0.004) -> dict:
    """오브젝트들을 하나의 0~1 아틀라스에 박스 투영 언랩한다 (레이어 uv_name, 활성화).

    팔레트 레이어(UVMap)는 그대로 두므로 가이드 렌더는 여전히 팔레트 색을 보여준다.
    반환: {"islands": n, "faces": n, "scale": s}"""
    import bpy
    bpy.context.view_layer.update()  # game_ready 직후의 location 변경이 matrix_world에 반영되도록
    if len({obj.data for obj in objects}) != len(objects):
        raise ValueError("같은 메시를 공유하는 오브젝트는 개별 매핑할 수 없습니다 (Single User로 분리 필요)")
    per_object = []
    all_faces = []
    for obj in objects:
        faces = _face_data(obj)
        per_object.append((obj, len(all_faces), len(faces)))
        all_faces.extend(faces)
    if not all_faces:
        return {"islands": 0, "faces": 0, "scale": 1.0}
    islands = build_islands(all_faces)
    scale, offsets = pack([isl["bounds"] for isl in islands], margin)
    final = {}
    for isl, (ou, ov) in zip(islands, offsets):
        min_u, min_v = isl["bounds"][0], isl["bounds"][1]
        for fi, uvs in isl["uvs"].items():
            final[fi] = [((u - min_u) * scale + ou, (v - min_v) * scale + ov) for u, v in uvs]
    for obj, start, count in per_object:
        mesh = obj.data
        layer = mesh.uv_layers.get(uv_name) or mesh.uv_layers.new(name=uv_name)
        data = layer.data
        for local_index, poly in enumerate(mesh.polygons):
            uvs = final[start + local_index]
            for loop_index, uv in zip(poly.loop_indices, uvs):
                data[loop_index].uv = uv
        # 팔레트 레이어(UVMap)를 활성으로 되돌린다 — Workbench는 active_render가 아니라
        # 활성 UV 레이어로 이미지 텍스처를 그리므로, 여기서 새 레이어를 활성으로 두면
        # 가이드 렌더에 팔레트 전체가 줄무늬로 찍힌다. 베이크는 레이어 이름을 명시해 읽는다.
        palette = mesh.uv_layers.get("UVMap")
        if palette is not None and palette != layer:
            palette.active = True
            palette.active_render = True
        else:
            layer.active = True
    return {"islands": len(islands), "faces": len(all_faces), "scale": scale}
