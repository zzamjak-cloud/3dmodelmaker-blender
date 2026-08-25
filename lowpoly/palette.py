# 팔레트 머티리얼: 단일 32x32 팔레트 텍스처 + UV 셀 매핑
#
# 모든 생성 모델이 하나의 팔레트 텍스처/머티리얼을 공유 → Unity에서 드로우콜 1개.
# 페이스의 UV를 색상 셀 중앙 한 점으로 모으는 방식이라 텍스처 필터링 번짐이 없다.
#
# 그리드 크기를 키울 때는 반드시 무손실로 이전한다. 기존 색 매핑을 그대로 옮기고
# 팔레트 머티리얼을 쓰는 모든 메시의 UV를 새 셀 중심으로 재매핑하므로,
# 이미 만들어 둔 모델의 색이 바뀌거나 팔레트가 초기화되지 않는다.
import json

import bpy

_PALETTE_IMAGE = "LP3D_Palette"
_PALETTE_MATERIAL = "LP3D_Palette"
_GRID = 32         # 32x32 = 1024색 (모델당 8~10색이므로 100개 이상 생성 가능)
_CELL_PX = 8       # 셀당 픽셀 (총 256x256 텍스처). 밉맵 번짐을 막기 위해 넉넉히 잡는다
_COLORS_PROP = "lp3d_colors"  # 이미지 커스텀 프로퍼티에 색→셀 매핑 저장
_GRID_PROP = "lp3d_grid"      # 이 이미지가 만들어질 때의 그리드 크기


# ---------- 픽셀 버퍼 ----------

def _read_pixels(img) -> list:
    """이미지 픽셀을 평면 리스트로 읽는다 (foreach_get은 list(img.pixels)보다 훨씬 빠르다)."""
    buf = [0.0] * (len(img.pixels))
    img.pixels.foreach_get(buf)
    return buf


def _write_pixels(img, buf: list):
    img.pixels.foreach_set(buf)
    img.update()


def _paint_cell(buf: list, size: int, cell: int, color):
    """픽셀 버퍼의 셀 영역을 단색으로 채운다."""
    r, g, b = color[:3]
    cx, cy = (cell % _GRID) * _CELL_PX, (cell // _GRID) * _CELL_PX
    for y in range(cy, cy + _CELL_PX):
        row = y * size
        for x in range(cx, cx + _CELL_PX):
            offset = (row + x) * 4
            buf[offset:offset + 4] = [r, g, b, 1.0]


# ---------- 이미지/머티리얼 ----------

def _new_image() -> bpy.types.Image:
    size = _GRID * _CELL_PX
    img = bpy.data.images.new(_PALETTE_IMAGE, size, size, alpha=False)
    img.pixels.foreach_set([0.5, 0.5, 0.5, 1.0] * (size * size))  # 회색으로 초기화
    img.update()
    img[_COLORS_PROP] = "{}"
    img[_GRID_PROP] = _GRID
    return img


def _repoint_material(img):
    """머티리얼의 텍스처 노드를 새 팔레트 이미지로 다시 연결한다."""
    mat = bpy.data.materials.get(_PALETTE_MATERIAL)
    if not mat or not mat.use_nodes:
        return
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE':
            node.image = img


def _cell_uv_in(cell: int, grid: int):
    """주어진 그리드 기준 셀 중앙 UV."""
    return (cell % grid + 0.5) / grid, (cell // grid + 0.5) / grid


def _remap_uvs(old_grid: int, new_grid: int):
    """팔레트 머티리얼을 쓰는 모든 메시의 UV를 새 그리드의 셀 중심으로 옮긴다.

    셀 인덱스는 그대로 유지하므로 기존 모델의 색이 보존된다."""
    mat = bpy.data.materials.get(_PALETTE_MATERIAL)
    if mat is None:
        return 0
    remapped = 0
    for mesh in bpy.data.meshes:
        if mat.name not in [m.name for m in mesh.materials if m]:
            continue
        uv_layer = mesh.uv_layers.active
        if uv_layer is None:
            continue
        for loop in uv_layer.data:
            u, v = loop.uv
            cx = min(max(int(u * old_grid), 0), old_grid - 1)
            cy = min(max(int(v * old_grid), 0), old_grid - 1)
            loop.uv = _cell_uv_in(cy * old_grid + cx, new_grid)
        remapped += 1
    return remapped


def _sample_cell(buf: list, size: int, grid: int, cell: int):
    """픽셀 버퍼에서 셀 중앙 텍셀의 색을 읽는다."""
    cell_px = max(size // grid, 1)
    x = (cell % grid) * cell_px + cell_px // 2
    y = (cell // grid) * cell_px + cell_px // 2
    offset = (min(y, size - 1) * size + min(x, size - 1)) * 4
    return buf[offset:offset + 3]


def _migrate(img) -> bpy.types.Image:
    """구버전 그리드의 팔레트를 색 손실 없이 현재 그리드로 이전한다."""
    old_grid = int(img.get(_GRID_PROP, 0)) or max(img.size[0] // _CELL_PX, 1)
    old_size = img.size[0]
    old_buf = _read_pixels(img)
    mapping = json.loads(img.get(_COLORS_PROP, "{}"))
    # 그리드를 줄이는 경우에만 발생 — 들어가지 않는 색은 버릴 수밖에 없다
    mapping = {k: c for k, c in mapping.items() if c < _GRID * _GRID}

    bpy.data.images.remove(img)
    new_img = _new_image()
    new_img[_COLORS_PROP] = json.dumps(mapping)

    # 매핑 키(소수점 3자리)로 다시 칠하면 8비트 양자화에서 1/255 오차가 생긴다.
    # 원본 텍셀을 그대로 복사해 비트 단위로 동일한 색을 유지한다.
    size = _GRID * _CELL_PX
    buf = _read_pixels(new_img)
    for cell in mapping.values():
        _paint_cell(buf, size, cell, _sample_cell(old_buf, old_size, old_grid, cell))
    _write_pixels(new_img, buf)

    _repoint_material(new_img)
    _remap_uvs(old_grid, _GRID)
    return new_img


def _get_image() -> bpy.types.Image:
    img = bpy.data.images.get(_PALETTE_IMAGE)
    if img is None:
        return _new_image()
    size = _GRID * _CELL_PX
    if tuple(img.size) != (size, size):
        return _migrate(img)
    return img


def _get_material() -> bpy.types.Material:
    mat = bpy.data.materials.get(_PALETTE_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(_PALETTE_MATERIAL)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        bsdf = nodes.get("Principled BSDF")
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = _get_image()
        tex.interpolation = 'Closest'  # 셀 경계 번짐 방지
        tex.location = (-300, 300)
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.9  # 캐주얼 톤: 무광
    return mat


# ---------- 롤백용 상태 ----------

def snapshot_state():
    """롤백용 팔레트 상태(색 매핑 + 픽셀) 스냅샷. 이미지가 없으면 None."""
    img = bpy.data.images.get(_PALETTE_IMAGE)
    if img is None:
        return None
    return {"colors": img.get(_COLORS_PROP, "{}"), "pixels": _read_pixels(img)}


def restore_state(state):
    """실패한 실행이 소비한 팔레트 셀을 되돌린다 (셀 누수 방지)."""
    if state is None:
        return
    img = bpy.data.images.get(_PALETTE_IMAGE)
    if img is None or len(img.pixels) != len(state["pixels"]):
        return
    img[_COLORS_PROP] = state["colors"]
    _write_pixels(img, state["pixels"])


# ---------- 색 할당 ----------

def _nearest_cell(mapping: dict, color) -> int:
    """팔레트가 가득 찼을 때 RGB 거리가 가장 가까운 기존 셀을 고른다."""
    r, g, b = color[:3]
    best, best_dist = 0, None
    for key, cell in mapping.items():
        kr, kg, kb = (float(v) for v in key.split(","))
        dist = (kr - r) ** 2 + (kg - g) ** 2 + (kb - b) ** 2
        if best_dist is None or dist < best_dist:
            best, best_dist = cell, dist
    return best


def _assign_cell(color) -> int:
    """색상에 셀 인덱스를 할당(기존 색이면 재사용)하고 픽셀을 칠한다."""
    img = _get_image()
    mapping = json.loads(img.get(_COLORS_PROP, "{}"))
    key = ",".join(f"{c:.3f}" for c in color[:3])
    if key in mapping:
        return mapping[key]
    cell = len(mapping)
    if cell >= _GRID * _GRID:
        # 가득 찬 경우 실행을 죽이는 대신 가장 가까운 기존 색으로 대체한다
        return _nearest_cell(mapping, color)
    mapping[key] = cell
    img[_COLORS_PROP] = json.dumps(mapping)
    # 셀 픽셀 채우기
    size = _GRID * _CELL_PX
    buf = _read_pixels(img)
    _paint_cell(buf, size, cell, color)
    _write_pixels(img, buf)
    return cell


def _cell_uv(cell: int):
    return _cell_uv_in(cell, _GRID)


def set_color(obj, color, faces=None):
    """오브젝트(또는 일부 페이스)에 팔레트 색을 입힌다.

    color=(r,g,b) 0~1 범위. faces=None이면 전체, 아니면 페이스 인덱스 리스트.
    예: lp.set_color(barrel, (0.55, 0.35, 0.18))  # 나무색
        lp.set_color(barrel, (0.4, 0.4, 0.45), faces=band_faces)  # 금속 밴드만"""
    mesh = obj.data
    mat = _get_material()
    if mat.name not in [m.name for m in mesh.materials if m]:
        mesh.materials.clear()
        mesh.materials.append(mat)
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    uv_layer = mesh.uv_layers.active.data
    cell = _assign_cell(color)
    u, v = _cell_uv(cell)
    target = set(faces) if faces is not None else None
    for poly in mesh.polygons:
        if target is not None and poly.index not in target:
            continue
        for loop_idx in poly.loop_indices:
            uv_layer[loop_idx].uv = (u, v)
    return obj


def save_palette_png(directory: str) -> str:
    """익스포트 시 팔레트 텍스처를 PNG로 저장하고 경로를 반환."""
    import os
    img = _get_image()
    path = os.path.join(directory, f"{_PALETTE_IMAGE}.png")
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    return path
