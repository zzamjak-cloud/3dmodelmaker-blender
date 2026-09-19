"""UV 공간 래스터라이저 — nvdiffrast(비상업 라이선스) 없이 텍스처를 굽기 위한 대체 구현.

TRELLIS.2 의 공식 내보내기(`o_voxel.postprocess.to_glb`)는 정리·리메시·데시메이트·UV 언랩·PBR 텍스처 굽기를
모두 하는데, 그중 nvdiffrast 를 쓰는 곳은 **UV 공간 래스터화 두 함수**뿐이다(rasterize / interpolate).
이 모듈이 같은 규약으로 그 둘을 대신하므로, 나머지 공식 경로(MIT: cumesh·flex_gemm)를 그대로 쓸 수 있다.

nvdiffrast 규약:
  rasterize(ctx, pos, tri, resolution) -> (rast, None)
      pos  : (B, V, 4) 클립 좌표. UV 굽기에서는 xy 가 [-1, 1] 로 들어온다.
      rast : (B, H, W, 4) = (u, v, z/w, triangle_id). triangle_id 는 1부터(0 은 빈 픽셀).
             u, v 는 삼각형의 첫 두 정점에 대한 무게중심 좌표.
  interpolate(attr, rast, tri) -> (out, None)
      out  : (B, H, W, C) = u*a + v*b + (1-u-v)*c

UV 아틀라스는 삼각형이 겹치지 않으므로 깊이 비교 없이 마지막에 쓴 값을 남긴다.
"""


class RasterizeCudaContext:
    """nvdiffrast 컨텍스트 자리 — 이 구현은 상태가 필요 없다."""

    def __init__(self, device=None):
        self.device = device


class RasterizeGLContext(RasterizeCudaContext):
    pass


def _import_torch():
    import torch
    return torch


def rasterize(ctx, pos, tri, resolution, ranges=None, grad_db=False):
    """UV 공간 삼각형을 텍셀 격자에 채운다. (rast, None) 반환."""
    torch = _import_torch()
    height, width = int(resolution[0]), int(resolution[1])
    device = pos.device
    verts = pos[0, :, :2].float()                       # (V, 2), [-1, 1]
    faces = tri.long()                                   # (F, 3)
    # 클립 좌표 → 텍셀 중심 좌표계. nvdiffrast 와 같이 y 는 아래에서 위로 증가한다.
    px = (verts[:, 0] * 0.5 + 0.5) * width - 0.5
    py = (verts[:, 1] * 0.5 + 0.5) * height - 0.5
    ax, ay = px[faces[:, 0]], py[faces[:, 0]]
    bx, by = px[faces[:, 1]], py[faces[:, 1]]
    cx, cy = px[faces[:, 2]], py[faces[:, 2]]

    rast = torch.zeros((1, height, width, 4), device=device, dtype=torch.float32)
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    ok = area.abs() > 1e-12
    if not bool(ok.any()):
        return rast, None

    x0 = torch.clamp(torch.floor(torch.minimum(torch.minimum(ax, bx), cx)).long(), 0, width - 1)
    x1 = torch.clamp(torch.ceil(torch.maximum(torch.maximum(ax, bx), cx)).long(), 0, width - 1)
    y0 = torch.clamp(torch.floor(torch.minimum(torch.minimum(ay, by), cy)).long(), 0, height - 1)
    y1 = torch.clamp(torch.ceil(torch.maximum(torch.maximum(ay, by), cy)).long(), 0, height - 1)
    box_w = (x1 - x0 + 1).clamp(min=0)
    box_h = (y1 - y0 + 1).clamp(min=0)
    counts = torch.where(ok, box_w * box_h, torch.zeros_like(box_w))

    # 삼각형마다 자기 경계상자 안의 텍셀만 검사한다 — 전체 텍셀을 모든 삼각형과 비교하지 않는다.
    chunk_faces = _chunk_size(counts)
    for start in range(0, faces.shape[0], chunk_faces):
        stop = min(start + chunk_faces, faces.shape[0])
        _rasterize_chunk(torch, rast, start, stop, counts, x0, y0, box_w,
                         ax, ay, bx, by, cx, cy, area, width)
    return rast, None


def _chunk_size(counts) -> int:
    """한 번에 다룰 삼각형 수 — 텍셀 후보가 800만 개를 넘지 않게 나눈다."""
    total = int(counts.sum().item())
    faces = max(int(counts.numel()), 1)
    per_face = max(total / faces, 1.0)
    return max(1, min(faces, int(8_000_000 / per_face)))


def _rasterize_chunk(torch, rast, start, stop, counts, x0, y0, box_w,
                     ax, ay, bx, by, cx, cy, area, width):
    part = counts[start:stop]
    total = int(part.sum().item())
    if total == 0:
        return
    index = torch.repeat_interleave(torch.arange(start, stop, device=part.device), part)
    offsets = torch.cumsum(part, 0) - part
    local = torch.arange(total, device=part.device) - torch.repeat_interleave(offsets, part)
    w = box_w[index]
    tx = x0[index] + local % w
    ty = y0[index] + local // w

    fx, fy = tx.float(), ty.float()
    a_x, a_y = ax[index], ay[index]
    b_x, b_y = bx[index], by[index]
    c_x, c_y = cx[index], cy[index]
    inv = 1.0 / area[index]
    # 무게중심: u 는 첫 정점, v 는 두 번째 정점 가중치 (nvdiffrast 와 같은 정의)
    u = ((b_x - fx) * (c_y - fy) - (b_y - fy) * (c_x - fx)) * inv
    v = ((c_x - fx) * (a_y - fy) - (c_y - fy) * (a_x - fx)) * inv
    wgt = 1.0 - u - v
    eps = -1e-6
    inside = (u >= eps) & (v >= eps) & (wgt >= eps)
    if not bool(inside.any()):
        return
    tx, ty, u, v, index = tx[inside], ty[inside], u[inside], v[inside], index[inside]
    flat = ty * width + tx
    view = rast.view(-1, 4)
    view[flat, 0] = u
    view[flat, 1] = v
    view[flat, 2] = 0.0
    view[flat, 3] = (index + 1).float()      # nvdiffrast 는 삼각형 번호를 1부터 센다


def interpolate(attr, rast, tri, rast_db=None, diff_attrs=None):
    """rast 의 무게중심으로 정점 속성을 보간한다. (out, None) 반환."""
    torch = _import_torch()
    faces = tri.long()
    values = attr[0] if attr.dim() == 3 else attr        # (V, C)
    ids = rast[0, ..., 3].long() - 1                      # (H, W), 빈 픽셀은 -1
    mask = ids >= 0
    out = torch.zeros((1, rast.shape[1], rast.shape[2], values.shape[-1]),
                      device=values.device, dtype=values.dtype)
    if not bool(mask.any()):
        return out, None
    picked = faces[ids[mask]]                             # (P, 3)
    u = rast[0, ..., 0][mask].unsqueeze(-1)
    v = rast[0, ..., 1][mask].unsqueeze(-1)
    w = 1.0 - u - v
    out[0][mask] = (values[picked[:, 0]] * u + values[picked[:, 1]] * v + values[picked[:, 2]] * w)
    return out, None


def antialias(color, rast, pos, tri, *args, **kwargs):
    """UV 굽기 경로에서는 안티에일리어싱이 필요 없다 — 입력을 그대로 돌려준다."""
    return color


def texture(*args, **kwargs):
    raise RuntimeError("uv_raster 는 UV 굽기용 rasterize/interpolate 만 제공한다")
