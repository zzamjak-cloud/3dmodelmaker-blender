# 셰이프 서버의 계약·전처리·후처리 — Modal/CUDA 없이 순수 파이썬으로 동작하는 부분
#
# 애드온(core/shapegen.py)과의 HTTP 계약:
#   GET  /status   → {"ok": true, "capabilities": {"preserve_parts": true}, ...}
#   POST /generate → body {front/back/left/right: base64, octree_resolution, num_inference_steps,
#                          guidance_scale, face_count, seed, preserve_parts} → GLB 바이트
# 모델 로드·추론(GPU)은 modal_app.py가 맡고, 이 모듈은 어떤 서버 주소·토큰도 갖지 않는다.
import base64
from io import BytesIO

import numpy as np
from PIL import Image

VIEW_KEYS = ("front", "back", "left", "right")

# 전처리 규칙 (2026-09-17 스파이크에서 확정)
#  - DINOv3 전처리는 알파를 버리므로 여백은 반드시 흰색이어야 한다(검정 여백 → 판때기 복원).
#  - 시트 라벨은 셀 상단이 아니라 하단에 그려지는 모델이 있다 → 위치 무관하게 감지·제거.
#  - 셀 경계 격자선은 마스크에서 빼야 내용 bbox가 잡힌다.
LABEL_MAX_FRAC = 0.12   # 본체와 흰 틈으로 분리된, 이 높이 미만의 덩어리를 라벨로 본다
GRID_MARGIN = 8         # 셀 경계 격자선 무시 폭(px)
PAD_RATIO = 1.15        # 정사각 캔버스 여유
WHITE_DIST = 12         # 255-min(RGB) 가 이 값 이하면 배경


def decode_image(data: str) -> Image.Image:
    """base64 → RGB. 알파가 있으면 흰 배경에 합성한다(투명 검정이 남지 않게)."""
    img = Image.open(BytesIO(base64.b64decode(data)))
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white, rgba).convert("RGB")
    return img.convert("RGB")


def _blocks(rows):
    """True 연속 구간 [(start, end)] — 행 점유 배열에서 덩어리 경계."""
    out, start = [], None
    for i, v in enumerate(rows):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(rows)))
    return out


def prepare_view(img: Image.Image) -> Image.Image:
    """라벨 제거 → 내용 크롭 → 흰 정사각 캔버스. 입력이 이미 깨끗해도 무해하다."""
    arr = np.asarray(img.convert("RGB")).astype(np.uint8)
    h = arr.shape[0]
    dist = 255 - arr.min(axis=2).astype(np.int16)
    mask = dist > WHITE_DIST
    if h > 2 * GRID_MARGIN and arr.shape[1] > 2 * GRID_MARGIN:
        mask[:GRID_MARGIN, :] = False
        mask[-GRID_MARGIN:, :] = False
        mask[:, :GRID_MARGIN] = False
        mask[:, -GRID_MARGIN:] = False
    blocks = _blocks(mask.any(axis=1))
    if blocks:
        body = max(blocks, key=lambda b: b[1] - b[0])
        for b in blocks:
            if b is not body and (b[1] - b[0]) < h * LABEL_MAX_FRAC:
                mask[b[0]:b[1], :] = False
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return img.convert("RGB")
    crop = Image.fromarray(arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1], "RGB")
    side = int(max(crop.size) * PAD_RATIO)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return canvas


def background_mask(dist: "np.ndarray") -> "np.ndarray":
    """가장자리에서 이어진 배경 픽셀만 True. 흰색 임계만 쓰면 이빨·손톱·눈처럼 실루엣 안쪽의 흰색까지 지워진다.

    실측(2026-09-18): 고블린 원화의 흰 이빨·발톱이 배경과 같은 밝기라 전역 임계로는 구분되지 않는다.
    배경은 언제나 캔버스 가장자리와 이어져 있으므로, 가장자리에서 흘려 넣어(flood fill) 닿는 곳만 배경으로 본다."""
    from collections import deque
    height, width = dist.shape
    whiteish = dist <= WHITE_DIST
    seen = np.zeros_like(whiteish)
    queue = deque()
    for x in range(width):
        for y in (0, height - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True
                queue.append((y, x))
    for y in range(height):
        for x in (0, width - 1):
            if whiteish[y, x] and not seen[y, x]:
                seen[y, x] = True
                queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and whiteish[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                queue.append((ny, nx))
    return seen


def rgb_to_rgba_white(img: Image.Image, soft: int = 24) -> Image.Image:
    """흰 배경 RGB → 알파. 배경과 이어진 흰색만 투명으로 만든다(실루엣 안쪽 흰색은 보존).

    공식 파이프라인의 preprocess_image는 알파가 있으면 배경제거 모델(CC BY-NC)을 건너뛰고 알파 bbox 크롭·
    알파 곱을 하므로, 알파를 우리가 만들어 넘기는 것이 라이선스·품질 모두에 맞다."""
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    dist = 255 - arr.min(axis=2)                                   # 흰색과의 거리
    alpha = np.clip((dist - WHITE_DIST) * (255.0 / max(soft, 1)), 0, 255).astype(np.uint8)
    alpha[~background_mask(dist)] = 255                            # 배경과 떨어진 흰색(이빨·발톱·눈)은 남긴다
    return Image.fromarray(np.dstack([arr.astype(np.uint8), alpha]), "RGBA")


def collect_views(params: dict) -> dict:
    """요청 본문에서 뷰를 뽑아 전처리한다. 정면이 없으면 단일 `image`를 정면으로 쓴다."""
    views = {}
    for key in VIEW_KEYS:
        if params.get(key):
            views[key] = prepare_view(decode_image(params[key]))
    if not views and params.get("image"):
        views["front"] = prepare_view(decode_image(params["image"]))
    if not views:
        raise ValueError("front/back/left/right 또는 image 중 하나는 있어야 한다")
    return views


def map_params(params: dict) -> dict:
    """애드온 파라미터 → TRELLIS.2 파이프라인 설정.

    octree_resolution: 애드온 기본 256. 1024 이상이면 캐스케이드(HR 1024), 그 외 512 단일 패스.
    16GB 이하 GPU/시간 예산에서는 512가 기본이고, 캐스케이드는 명시적으로 요청할 때만 쓴다."""
    octree = int(params.get("octree_resolution", 256) or 256)
    steps = max(4, min(50, int(params.get("num_inference_steps", 12) or 12)))
    return {
        "seed": int(params.get("seed", 1234) or 1234),
        "steps": steps,
        "guidance_scale": float(params.get("guidance_scale", 5.0) or 5.0),
        "cascade": octree >= 1024,
        "face_count": int(params.get("face_count", 0) or 0),
        "preserve_parts": bool(params.get("preserve_parts", False)),
        "texture": bool(params.get("texture", False)),
        "texture_size": int(params.get("texture_size", 0) or 0),
        "variants": bool(params.get("variants", False)),   # 진단용 — 내보내기 설정별 통계를 로그로 남긴다
    }


# ---- 후처리: 기존 서버와 같은 클래스 이름·호출 규약 (tests/test_character_parts_backends.py가 검증) ----

class FloaterRemover:
    """떠다니는 작은 조각 제거 — 가장 큰 덩어리 대비 면수 비율이 낮은 연결 요소를 버린다."""

    def __init__(self, keep_ratio: float = 0.05):
        self.keep_ratio = keep_ratio

    def __call__(self, mesh):
        import trimesh
        parts = mesh.split(only_watertight=False)
        if len(parts) <= 1:
            return mesh
        largest = max(len(p.faces) for p in parts)
        keep = [p for p in parts if len(p.faces) >= largest * self.keep_ratio]
        return trimesh.util.concatenate(keep) if len(keep) > 1 else keep[0]


class PlaneRemover:
    """두께가 거의 0인 대형 평면 셸(바닥판) 제거 — 시트의 격자선·그림자를 모델이 바닥으로 복원한 것.

    부품 보존(preserve_parts)과 무관하게 항상 적용한다: 두께 0의 판은 어떤 부품도 아니다.
    (실측: 1.0×1.0×0.0 바닥판 18만 면, 그리고 1.0×1.0×0.021 바닥판 1.6만 면 — 두께 1% 기준으로는 후자가 통과했다)"""

    def __init__(self, thickness_ratio: float = 0.05, footprint_ratio: float = 0.25):
        self.thickness_ratio = thickness_ratio
        self.footprint_ratio = footprint_ratio

    def is_plane(self, part, whole_extent) -> bool:
        ext = part.bounds[1] - part.bounds[0]
        ext_sorted = sorted(float(e) for e in ext)
        thin = ext_sorted[0] < self.thickness_ratio * whole_extent
        wide = ext_sorted[1] * ext_sorted[2] > self.footprint_ratio * whole_extent * whole_extent
        return thin and wide

    def __call__(self, mesh):
        import trimesh
        parts = mesh.split(only_watertight=False)
        if len(parts) <= 1:
            return mesh
        whole = float(max(mesh.bounds[1] - mesh.bounds[0]))
        keep = [p for p in parts if not self.is_plane(p, whole)]
        if not keep or len(keep) == len(parts):
            return mesh
        return trimesh.util.concatenate(keep) if len(keep) > 1 else keep[0]


class DegenerateFaceRemover:
    """면적 0인 퇴화 면과 참조되지 않는 정점을 제거한다."""

    def __call__(self, mesh):
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()
        return mesh


class FaceReducer:
    """목표 면수까지 단순화. fast_simplification이 있으면 그것을, 없으면 trimesh 쿼드릭 데시메이션."""

    def __call__(self, mesh, max_facenum: int):
        if len(mesh.faces) <= max_facenum:
            return mesh
        try:
            import fast_simplification
            import trimesh
            v, f = fast_simplification.simplify(
                np.ascontiguousarray(mesh.vertices, dtype=np.float32),
                np.ascontiguousarray(mesh.faces, dtype=np.int32),
                1.0 - max_facenum / len(mesh.faces))
            return trimesh.Trimesh(vertices=v, faces=f, process=False)
        except ImportError:
            return mesh.simplify_quadric_decimation(face_count=max_facenum)


def _postprocess_mesh(mesh, params):
    """부품 보존 요청이면 파편 제거만 건너뛴다 — 작은 장비(단검·버클)가 파편으로 지워지지 않게.
    두께 0의 대형 평면(바닥판)은 부품 보존 여부와 무관하게 항상 지운다."""
    mesh = PlaneRemover()(mesh)
    if not params.get('preserve_parts', False):
        mesh = FloaterRemover()(mesh)
    mesh = DegenerateFaceRemover()(mesh)
    face_count = int(params.get('face_count', 0) or 0)
    if face_count > 0:
        mesh = FaceReducer()(mesh, max_facenum=face_count)
    return mesh


def to_gltf_frame(vertices: np.ndarray) -> np.ndarray:
    """TRELLIS(Z-up) → glTF(Y-up): (x, y, z) → (x, z, -y). Blender 임포터가 다시 Z-up으로 되돌린다.
    이 변환을 빼먹으면 모델이 90° 눕는다(스파이크 실측)."""
    out = np.asarray(vertices, dtype=np.float32).copy()
    out[:, 1], out[:, 2] = vertices[:, 2], -vertices[:, 1]
    return out


def export_glb(mesh) -> bytes:
    """GLB 바이트 — 애드온은 앞 4바이트 b'glTF'로 응답을 판별한다."""
    return mesh.export(file_type="glb")


def status_payload(loaded: bool = True) -> dict:
    return {"ok": True, "loaded": loaded, "backend": "trellis2", "multiview": True,
            "capabilities": {"preserve_parts": True, "pbr_texture": True}}
