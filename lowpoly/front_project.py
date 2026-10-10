# 정면 원화의 디테일을 PBR 베이스컬러에 투영한다 — 얼굴(눈·눈썹·입)·프린트·신발 끈처럼 TRELLIS 속성 볼륨이 뭉갠 것.
#
# TRELLIS.2 는 입력을 1024px 로 줄여 보고 색을 1024 격자 볼륨에 담아, 전신 캐릭터의 얼굴이 폭 100px 남짓으로 흐려진다
# (실측 2026-10-11: 텍스처 아틀라스는 얼굴 폭 280텍셀로 충분 — 병목은 볼륨). 형상은 그 원화로 만들어졌으므로 정면에서
# 보이는 텍셀은 원화의 같은 자리와 맞는다. 텍셀마다 3D 위치를 구해 정면 깊이 버퍼로 가림을 거르고, 정면을 향한 정도로
# 서서히 섞는다. 판정은 실루엣만 본다 — 메시와 원화 실루엣이 모두 덮는 안쪽에서만 투영하고 가장자리로 갈수록 뺀다.
# 흐린 색끼리 비교해 막는 방식은 TRELLIS 가 눈을 제자리에 검은 덩어리로 그려 눈·입만 정확히 막았다(실측 2026-10-11).
# 무늬를 다른 자리에 다시 그린 곳은 정면에서 원화 디자인으로 바뀌는데, 원화가 정답이므로 그쪽이 맞다.
#
# 계산은 numpy 만 쓴다(Blender 번들) — bpy 는 apply_to_object 에서만 쓴다.
import numpy as np

from . import color_match

FACING_LO, FACING_HI = 0.35, 0.75   # 정면 법선 성분(-n.y)이 LO 이하면 투영 0, HI 이상이면 1 — 옆면으로 갈수록 서서히 뺀다
DEPTH_EPS = 0.004                   # 키 대비 — 정면 깊이 버퍼보다 이만큼 뒤까지는 보이는 면으로 친다
EDGE_FEATHER = 8                    # 실루엣 가장자리에서 투영을 빼는 폭(정면 격자 픽셀) — 경계의 작은 정렬 오차가 배경색을 묻히지 않게
ROUGHNESS_FLOOR = 0.45              # 러프니스 하한 — TRELLIS 가 머리카락·피부에 낮은 값을 줘 녹은 플라스틱처럼 번들거렸다
ALIGN_STEPS = (-0.02, -0.01, 0.0, 0.01, 0.02)


# ---------- 래스터화 (numpy) ----------

def _bbox_candidates(px, py, tris, width, height, start, stop):
    """삼각형 [start, stop) 의 경계 상자 안 픽셀 후보: (삼각형 번호, x, y, 무게중심 u, v, w), 삼각형 안쪽만."""
    a, b, c = tris[start:stop, 0], tris[start:stop, 1], tris[start:stop, 2]
    ax, ay, bx, by, cx, cy = px[a], py[a], px[b], py[b], px[c], py[c]
    x0 = np.clip(np.floor(np.minimum(np.minimum(ax, bx), cx)).astype(np.int64), 0, width - 1)
    x1 = np.clip(np.ceil(np.maximum(np.maximum(ax, bx), cx)).astype(np.int64), 0, width - 1)
    y0 = np.clip(np.floor(np.minimum(np.minimum(ay, by), cy)).astype(np.int64), 0, height - 1)
    y1 = np.clip(np.ceil(np.maximum(np.maximum(ay, by), cy)).astype(np.int64), 0, height - 1)
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    counts = np.where(np.abs(area) > 1e-12, bw * bh, 0)
    total = int(counts.sum())
    if total == 0:
        return None
    idx = np.repeat(np.arange(stop - start), counts)
    local = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    tx = x0[idx] + local % bw[idx]
    ty = y0[idx] + local // bw[idx]
    fx, fy = tx + 0.5, ty + 0.5                    # 픽셀 중심
    inv = 1.0 / area[idx]
    u = ((bx[idx] - fx) * (cy[idx] - fy) - (by[idx] - fy) * (cx[idx] - fx)) * inv
    v = ((cx[idx] - fx) * (ay[idx] - fy) - (cy[idx] - fy) * (ax[idx] - fx)) * inv
    w = 1.0 - u - v
    inside = (u >= -1e-6) & (v >= -1e-6) & (w >= -1e-6)
    return idx[inside] + start, tx[inside], ty[inside], u[inside], v[inside], w[inside]


def _chunks(px, py, tris, width, height, budget=6_000_000):
    """삼각형을 후보 픽셀 수가 budget 을 넘지 않게 나눠 _bbox_candidates 결과를 낸다."""
    span = (np.ptp(px[tris], axis=1) + 2) * (np.ptp(py[tris], axis=1) + 2)
    step = max(1, int(budget / max(float(span.mean()), 1.0)))
    for start in range(0, len(tris), step):
        got = _bbox_candidates(px, py, tris, width, height, start, min(start + step, len(tris)))
        if got is not None:
            yield got


def rasterize_uv(uvs, tris, size):
    """UV 아틀라스 래스터: 텍셀마다 (삼각형 번호 또는 -1, 무게중심 (size,size,3)). uvs 는 삼각형 코너별 (F,3,2).

    행은 위에서 아래(이미지 순서) — v 를 뒤집어 넣는다."""
    flat = uvs.reshape(-1, 2)
    px = flat[:, 0] * size
    py = (1.0 - flat[:, 1]) * size
    corner = np.arange(len(flat)).reshape(-1, 3)
    tri_id = np.full((size, size), -1, np.int64)
    bary = np.zeros((size, size, 3), np.float32)
    for t, x, y, u, v, w in _chunks(px, py, corner, size, size):
        tri_id[y, x] = t
        bary[y, x] = np.stack([u, v, w], axis=1)
    return tri_id, bary


def rasterize_front(points, tris, width, height):
    """정면 깊이 래스터: points 는 (V,3) = (픽셀 x, 픽셀 y, 깊이 — 작을수록 앞). 반환 (삼각형 번호/-1, 무게중심, 깊이)."""
    px, py, pz = points[:, 0], points[:, 1], points[:, 2]
    depth = np.full(height * width, np.inf, np.float64)
    tri_id = np.full(height * width, -1, np.int64)
    bary = np.zeros((height * width, 3), np.float32)
    for t, x, y, u, v, w in _chunks(px, py, tris, width, height):
        z = u * pz[tris[t, 0]] + v * pz[tris[t, 1]] + w * pz[tris[t, 2]]
        pix = y * width + x
        order = np.lexsort((z, pix))                 # 픽셀마다 가장 앞(작은 z)이 먼저
        pix, z, t, u, v, w = pix[order], z[order], t[order], u[order], v[order], w[order]
        first = np.ones(len(pix), bool)
        first[1:] = pix[1:] != pix[:-1]
        pix, z, t, u, v, w = pix[first], z[first], t[first], u[first], v[first], w[first]
        nearer = z < depth[pix]
        pix = pix[nearer]
        depth[pix] = z[nearer]
        tri_id[pix] = t[nearer]
        bary[pix] = np.stack([u[nearer], v[nearer], w[nearer]], axis=1)
    return tri_id.reshape(height, width), bary.reshape(height, width, 3), depth.reshape(height, width)


# ---------- 샘플·판정 ----------

def sample_bilinear(img, x, y):
    """img (H,W,C) 에서 픽셀 좌표 (x, y)(픽셀 중심 = 정수 + 0.5) 를 쌍선형 보간."""
    h, w = img.shape[:2]
    fx = np.clip(x - 0.5, 0, w - 1.001)
    fy = np.clip(y - 0.5, 0, h - 1.001)
    x0, y0 = fx.astype(np.int64), fy.astype(np.int64)
    dx, dy = (fx - x0)[..., None], (fy - y0)[..., None]
    return (img[y0, x0] * (1 - dx) * (1 - dy) + img[y0, x0 + 1] * dx * (1 - dy)
            + img[y0 + 1, x0] * (1 - dx) * dy + img[y0 + 1, x0 + 1] * dx * dy)


def facing_weight(normal_y):
    """정면 축(Y)을 향한 정도 → 0~1. 서버 메시는 면 방향(감김)이 군데군데 뒤집혀 있어(실측: 얼굴 반쪽·셔츠 대부분)
    부호를 보지 않는다 — 앞면인지는 정면 깊이 버퍼가 따로 가린다."""
    return np.clip((np.abs(normal_y) - FACING_LO) / (FACING_HI - FACING_LO), 0.0, 1.0)


def gate_map(inside, feather=EDGE_FEATHER):
    """메시·원화 실루엣이 모두 덮는 칸(inside)에서 가장자리 feather 폭 동안 0 → 1 로 오르는 가중치."""
    blur = color_match.box_blur(inside.astype(np.float32)[..., None], feather)[..., 0]
    return np.where(inside, np.clip((blur - 0.5) * 2.0, 0.0, 1.0), 0.0)


def best_alignment(mesh_mask, figure_mask):
    """메시 정면 실루엣을 원화 실루엣에 맞추는 (배율, x 이동, y 이동)(격자 비율). IoU 가 가장 큰 조합."""
    h, w = mesh_mask.shape
    ys, xs = np.mgrid[0:h, 0:w]
    best = (1.0, 0.0, 0.0, -1.0)
    for s in ALIGN_STEPS:
        for dx in ALIGN_STEPS:
            for dy in ALIGN_STEPS:
                # 원화 좌표 = 중심 기준 (1+s) 배율 + 이동 — 메시 칸 (x, y) 가 원화의 어디를 보는지
                sx = ((xs + 0.5 - w / 2) * (1 + s) + w / 2 + dx * w).astype(np.int64)
                sy = ((ys + 0.5 - h / 2) * (1 + s) + h / 2 + dy * h).astype(np.int64)
                ok = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
                fig = np.zeros_like(mesh_mask)
                fig[ok] = figure_mask[sy[ok], sx[ok]]
                inter = (fig & mesh_mask).sum()
                union = (fig | mesh_mask).sum()
                iou = inter / union if union else 0.0
                if iou > best[3]:
                    best = (1 + s, dx, dy, iou)
    return best


# ---------- bpy 경계 ----------

def _mesh_arrays(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    nv, nt = len(mesh.vertices), len(mesh.loop_triangles)
    co = np.empty(nv * 3, np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(obj.matrix_world, np.float64)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    tri_v = np.empty(nt * 3, np.int64)
    mesh.loop_triangles.foreach_get("vertices", tri_v)
    tri_l = np.empty(nt * 3, np.int64)
    mesh.loop_triangles.foreach_get("loops", tri_l)
    uv_all = np.empty(len(mesh.loops) * 2, np.float64)
    mesh.uv_layers.active.data.foreach_get("uv", uv_all)
    uvs = uv_all.reshape(-1, 2)[tri_l].reshape(-1, 3, 2)
    return co, tri_v.reshape(-1, 3), uvs


def _floor_roughness(obj, floor):
    """metallicRoughness(G=러프니스) 이미지의 하한을 올린다. 바꾼 이미지 수."""
    changed = 0
    for mat in obj.data.materials:
        if not (mat and mat.use_nodes):
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None or not bsdf.inputs["Roughness"].links:
            continue
        node = bsdf.inputs["Roughness"].links[0].from_node
        while node is not None and node.type != 'TEX_IMAGE':      # glTF 임포트: 이미지 → 색 분리 → 러프니스
            links = [l for i in node.inputs for l in i.links]
            node = links[0].from_node if links else None
        if node is None or node.image is None:
            continue
        px = color_match._image_rgb(node.image)
        if px[..., 1].min() >= floor:
            continue
        px[..., 1] = np.maximum(px[..., 1], floor)
        node.image.pixels.foreach_set(px[::-1].ravel())
        node.image.update()
        if node.image.packed_file is not None:
            node.image.pack()
        changed += 1
    return changed


def apply_to_object(obj, reference_path: str, grid_height: int = 1536) -> dict:
    """원화 정면 디테일을 obj 베이스컬러에 투영하고 러프니스 하한을 올린다. 통계 dict."""
    import bpy

    stats = {"roughness": _floor_roughness(obj, ROUGHNESS_FLOOR)}
    if len([m for m in obj.data.materials if m]) != 1:
        stats["reason"] = "재질이 하나가 아님"
        return stats
    image = color_match.base_color_image(obj)
    if image is None:
        stats["reason"] = "베이스컬러 텍스처 없음"
        return stats
    ref_image = bpy.data.images.load(reference_path, check_existing=False)
    try:
        ref = color_match._image_rgb(ref_image)
    finally:
        bpy.data.images.remove(ref_image)
    bbox = color_match.figure_bbox(ref)
    if bbox is None:
        stats["reason"] = "원화에 피사체 없음"
        return stats
    texture = color_match._image_rgb(image)
    co, tris, uvs = _mesh_arrays(obj)

    # 정면 격자 = 원화 피사체 경계 상자를 그대로 확대한 격자. 메시는 X·Z 경계 상자를 그 격자에 맞춘다
    y0, y1, x0, x1 = bbox
    gh = grid_height
    gw = max(8, int(round(gh * (x1 - x0) / (y1 - y0))))
    lo, hi = co.min(axis=0), co.max(axis=0)
    height = float(hi[2] - lo[2])
    gx = (co[:, 0] - lo[0]) / max(hi[0] - lo[0], 1e-9) * gw
    gy = (hi[2] - co[:, 2]) / max(hi[2] - lo[2], 1e-9) * gh
    front_tri, front_bary, depth = rasterize_front(np.stack([gx, gy, co[:, 1]], axis=1), tris, gw, gh)
    covered = front_tri >= 0
    reference = color_match.resample(ref[..., :3], bbox, gw, gh)
    figure = ~color_match.background_mask(reference)

    # 실루엣 IoU 로 미세 정렬 — 원화가 피사체 경계 상자 계산에서 그림자·안티에일리어싱만큼 어긋난다
    scale, dx, dy, iou = best_alignment(covered[::4, ::4], figure[::4, ::4])
    stats["iou"] = round(float(iou), 3)

    def to_ref(x, y):
        return ((x - gw / 2) * scale + gw / 2 + dx * gw, (y - gh / 2) * scale + gh / 2 + dy * gh)

    yy, xx = np.mgrid[0:gh, 0:gw]
    rx, ry = to_ref(xx + 0.5, yy + 0.5)
    gate = gate_map(covered & figure[np.clip(ry.astype(int), 0, gh - 1), np.clip(rx.astype(int), 0, gw - 1)])
    th = texture.shape[0]

    # 텍셀마다 3D 위치·법선 → 정면 격자 좌표 → 가림·정면도·일치 판정
    tex_tri, tex_bary = rasterize_uv(uvs, tris, th)
    texel = tex_tri >= 0
    t = tex_tri[texel]
    b = tex_bary[texel]
    pos = (co[tris[t]] * b[:, :, None]).sum(axis=1)
    e1 = co[tris[t, 1]] - co[tris[t, 0]]
    e2 = co[tris[t, 2]] - co[tris[t, 0]]
    normal = np.cross(e1, e2)
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
    px = (pos[:, 0] - lo[0]) / max(hi[0] - lo[0], 1e-9) * gw
    py = (hi[2] - pos[:, 2]) / max(hi[2] - lo[2], 1e-9) * gh
    ix = np.clip(px.astype(np.int64), 0, gw - 1)
    iy = np.clip(py.astype(np.int64), 0, gh - 1)
    visible = pos[:, 1] <= depth[iy, ix] + DEPTH_EPS * height
    weight = facing_weight(normal[:, 1]) * gate[iy, ix] * visible
    rx, ry = to_ref(px, py)
    projected = sample_bilinear(reference, rx, ry)

    rows, cols = np.nonzero(texel)
    out = texture.copy()
    current = out[rows, cols, :3]
    out[rows, cols, :3] = current + (projected - current) * weight[:, None]
    stats["texels"] = int((weight > 0.5).sum())
    if stats["texels"] == 0:
        stats["reason"] = "투영할 텍셀 없음"
        return stats
    image.pixels.foreach_set(out[::-1].astype(np.float32).ravel())
    image.update()
    if image.packed_file is not None:
        image.pack()
    stats["applied"] = True
    return stats
