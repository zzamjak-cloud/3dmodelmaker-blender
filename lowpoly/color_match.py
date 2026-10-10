# 이미지→3D(TRELLIS.2) PBR 베이스컬러를 서버에 넣은 정면 원화의 색에 맞춘다.
#
# TRELLIS.2 텍스처 모델은 공식 샘플러 설정에서도 색이 일관되게 틀어진다(실측 2026-10-10, 소녀 피규어:
# 노랑 모자·부츠 → 황토/주황, 피부 → 연어색, 원화 대비 정면 평균 오차 28.8/255). 감마 문제가 아니라
# (sRGB 로 읽으면 어둡고 G 가 크게 빠지고, 선형으로 읽으면 밝고 푸르다) 채널이 섞인 색조 이동이라
# 정면에서 보이는 텍셀과 원화의 같은 자리를 짝지어 3x3+편향(아핀) 변환을 최소제곱으로 맞추고
# 텍스처 전체(뒷면 포함)에 적용한다. 같은 모델의 위 절반으로 맞춰 아래 절반을 재면 32.6 → 17.4.
#
# 원화를 직접 투영하지 않는 이유: TRELLIS 가 무늬·눈 위치를 원화와 다르게 다시 그리므로(실루엣 IoU 0.89,
# 꽃무늬 위치 불일치) 투영하면 경계가 어긋난다. 전역 색 변환은 그런 국소 차이에 둔감하다.
#
# 판정 계산은 numpy 만 쓴다(Blender 번들) — bpy/mathutils 는 front_samples·apply_to_object 에서만 쓴다.
import numpy as np

GRID_W, GRID_H = 192, 288          # 정면 샘플 격자 (원화 비율 2:3)
WHITE_LEVEL = 0.93                 # 원화 배경(흰색) 판정 — 최소 채널이 이보다 밝으면 배경
FLAT_TOL = 0.04                    # 주변과 이만큼 이상 다르면 경계·디테일로 보고 빼낸다 (정렬 오차 회피)
MIN_SAMPLES = 1500                 # 이보다 적으면 맞추지 않는다
MIN_GAIN = 0.15                    # 오차가 이 비율 이상 줄 때만 적용한다
RIDGE = 0.02                       # 항등 변환 쪽으로 당기는 정칙화 — 적은 색 범위에 과적합하지 않게


def figure_bbox(rgb: np.ndarray):
    """흰 배경(또는 투명) 원화에서 피사체 경계 (y0, y1, x0, x1), 행은 위에서 아래. 없으면 None."""
    mask = rgb[..., :3].min(axis=-1) < WHITE_LEVEL
    if rgb.shape[-1] == 4:
        mask &= rgb[..., 3] > 0.5               # 투명 PNG 의 투명 영역은 RGB 가 대개 검정이다
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return None
    return int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1


def resample(rgb: np.ndarray, bbox, width: int, height: int) -> np.ndarray:
    """bbox 영역을 (height, width) 격자로 최근접 샘플링한다."""
    y0, y1, x0, x1 = bbox
    ys = np.clip((y0 + (np.arange(height) + 0.5) * (y1 - y0) / height).astype(int), y0, y1 - 1)
    xs = np.clip((x0 + (np.arange(width) + 0.5) * (x1 - x0) / width).astype(int), x0, x1 - 1)
    return rgb[ys][:, xs]


def box_blur(img: np.ndarray, radius: int = 2) -> np.ndarray:
    """(H, W, C) 상자 흐림 — 가장자리는 복제 채움."""
    k = 2 * radius + 1
    pad = np.pad(img, ((radius, radius), (radius, radius), (0, 0)), mode='edge')
    c = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0), (0, 0)))
    h, w = img.shape[:2]
    s = c[k:k + h, k:k + w] - c[:h, k:k + w] - c[k:k + h, :w] + c[:h, :w]
    return s / (k * k)


def flat_mask(img: np.ndarray) -> np.ndarray:
    """주변과 색이 거의 같은(평탄한) 픽셀 — 경계에서는 두 이미지의 작은 정렬 오차가 큰 색 오차가 된다."""
    return np.abs(box_blur(img) - img).max(axis=-1) < FLAT_TOL


def fit_affine(src: np.ndarray, dst: np.ndarray, ridge: float = RIDGE) -> np.ndarray:
    """dst ≈ [src, 1] @ M 인 (4, 3) M. 항등 변환 쪽 릿지 정칙화."""
    a = np.c_[src, np.ones(len(src))]
    eye = np.vstack([np.eye(3), np.zeros((1, 3))])
    reg = ridge * len(src) * np.eye(4)
    reg[3, 3] = 0.0                                      # 편향은 묶지 않는다
    return np.linalg.solve(a.T @ a + reg, a.T @ dst + reg @ eye)


def apply_affine(rgb: np.ndarray, m: np.ndarray) -> np.ndarray:
    # 4096² 텍스처에서도 float32 한 벌만 더 만들도록 편향을 따로 더한다
    flat = rgb.reshape(-1, 3).astype(np.float32, copy=False)
    out = flat @ m[:3].astype(np.float32) + m[3].astype(np.float32)
    return np.clip(out, 0.0, 1.0, out=out).reshape(rgb.shape)


def solve(rendered: np.ndarray, covered: np.ndarray, reference: np.ndarray):
    """정면 렌더(텍스처 색)와 같은 격자로 맞춘 원화를 비교해 변환을 구한다.

    rendered/reference: (H, W, 3) 0~1, covered: (H, W) 메시가 맞은 칸. 반환 (M 또는 None, 통계 dict)."""
    use = covered & (reference.min(axis=-1) < WHITE_LEVEL) & flat_mask(reference) & flat_mask(rendered)
    stats = {"samples": int(use.sum())}
    if stats["samples"] < MIN_SAMPLES:
        stats["reason"] = "샘플 부족"
        return None, stats
    src, dst = box_blur(rendered)[use], box_blur(reference)[use]
    m = fit_affine(src, dst)
    before = float(np.abs(src - dst).mean() * 255)
    after = float(np.abs(apply_affine(src, m) - dst).mean() * 255)
    stats.update(before=before, after=after)
    if after > before * (1.0 - MIN_GAIN):
        stats["reason"] = "개선 미미"
        return None, stats
    return m, stats


# ---------- bpy 경계 ----------

def _image_rgb(image) -> np.ndarray:
    """bpy 이미지 → (H, W, 4) float, 행은 위에서 아래. 바이트 이미지는 저장된 색공간 값 그대로다."""
    w, h = image.size
    px = np.empty(w * h * 4, np.float32)
    image.pixels.foreach_get(px)
    return px.reshape(h, w, 4)[::-1]


def base_color_image(obj):
    """Principled 베이스컬러에 연결된 이미지 텍스처."""
    for mat in obj.data.materials:
        if not (mat and mat.use_nodes):
            continue
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None or not bsdf.inputs["Base Color"].links:
            continue
        node = bsdf.inputs["Base Color"].links[0].from_node
        if node.type == 'TEX_IMAGE' and node.image:
            return node.image
    return None


def front_samples(obj, texture: np.ndarray, width: int = GRID_W, height: int = GRID_H):
    """-Y 정면에서 정사영 광선을 쏴 맞은 텍셀 색을 격자로 모은다. (rgb (H,W,3), covered (H,W))

    격자는 메시의 정면 투영 경계(X·Z)에 맞춘다 — 원화도 피사체 경계로 잘라 같은 격자에 놓는다."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.interpolate import poly_3d_calc

    mesh = obj.data
    mesh.calc_loop_triangles()
    mw = obj.matrix_world
    verts = [mw @ v.co for v in mesh.vertices]
    tris = [tuple(t.vertices) for t in mesh.loop_triangles]
    bvh = BVHTree.FromPolygons(verts, tris)
    uv_layer = mesh.uv_layers.active.data
    tri_loops = [tuple(t.loops) for t in mesh.loop_triangles]

    xs = [v.x for v in verts]
    zs = [v.z for v in verts]
    y_min = min(v.y for v in verts) - 1.0
    x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    th, tw = texture.shape[:2]
    rgb = np.zeros((height, width, 3), np.float32)
    covered = np.zeros((height, width), bool)
    direction = Vector((0.0, 1.0, 0.0))
    for row in range(height):
        z = z1 - (row + 0.5) * (z1 - z0) / height
        for col in range(width):
            x = x0 + (col + 0.5) * (x1 - x0) / width
            loc, _normal, index, _d = bvh.ray_cast(Vector((x, y_min, z)), direction)
            if index is None:
                continue
            corners = [verts[i] for i in tris[index]]
            weights = poly_3d_calc(corners, loc)
            u = v = 0.0
            for w, loop in zip(weights, tri_loops[index]):
                uv = uv_layer[loop].uv
                u += w * uv.x
                v += w * uv.y
            ty = min(th - 1, max(0, int((1.0 - v) * th)))
            tx = min(tw - 1, max(0, int(u * tw)))
            rgb[row, col] = texture[ty, tx, :3]
            covered[row, col] = True
    return rgb, covered


def apply_to_object(obj, reference_path: str) -> dict:
    """원화 색에 맞춰 obj 베이스컬러 텍스처를 고친다. 적용하지 않으면 stats['reason'] 에 이유."""
    import bpy

    if len([m for m in obj.data.materials if m]) != 1:
        return {"reason": "재질이 하나가 아님"}       # 광선이 맞은 면의 재질을 가려 샘플하지 않는다
    image = base_color_image(obj)
    if image is None:
        return {"reason": "베이스컬러 텍스처 없음"}
    ref_image = bpy.data.images.load(reference_path, check_existing=False)
    try:
        ref = _image_rgb(ref_image)
    finally:
        bpy.data.images.remove(ref_image)
    bbox = figure_bbox(ref)
    if bbox is None:
        return {"reason": "원화에 피사체 없음"}
    texture = _image_rgb(image)
    rendered, covered = front_samples(obj, texture)
    reference = resample(ref[..., :3], bbox, GRID_W, GRID_H)
    m, stats = solve(rendered, covered, reference)
    if m is None:
        return stats
    out = texture.copy()
    out[..., :3] = apply_affine(texture[..., :3], m)
    image.pixels.foreach_set(out[::-1].ravel())
    image.update()
    if image.packed_file is not None:
        image.pack()                                  # 고친 픽셀을 .blend 에 다시 담는다
    stats["applied"] = True
    return stats
