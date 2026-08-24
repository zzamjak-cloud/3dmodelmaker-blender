# 팔레트 머티리얼: 단일 8x8 팔레트 텍스처 + UV 셀 매핑
#
# 모든 생성 모델이 하나의 팔레트 텍스처/머티리얼을 공유 → Unity에서 드로우콜 1개.
# 페이스의 UV를 색상 셀 중앙 한 점으로 모으는 방식이라 텍스처 필터링 번짐이 없다.
import json

import bpy

_PALETTE_IMAGE = "LP3D_Palette"
_PALETTE_MATERIAL = "LP3D_Palette"
_GRID = 8          # 8x8 = 64색
_CELL_PX = 8       # 셀당 픽셀 (총 64x64 텍스처)
_COLORS_PROP = "lp3d_colors"  # 이미지 커스텀 프로퍼티에 색→셀 매핑 저장


def _get_image() -> bpy.types.Image:
    img = bpy.data.images.get(_PALETTE_IMAGE)
    if img is None:
        size = _GRID * _CELL_PX
        img = bpy.data.images.new(_PALETTE_IMAGE, size, size, alpha=False)
        img.pixels[:] = [0.5, 0.5, 0.5, 1.0] * (size * size)  # 회색으로 초기화
        img[_COLORS_PROP] = "{}"
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


def _assign_cell(color) -> int:
    """색상에 셀 인덱스를 할당(기존 색이면 재사용)하고 픽셀을 칠한다."""
    img = _get_image()
    mapping = json.loads(img.get(_COLORS_PROP, "{}"))
    key = ",".join(f"{c:.3f}" for c in color[:3])
    if key in mapping:
        return mapping[key]
    cell = len(mapping)
    if cell >= _GRID * _GRID:
        raise RuntimeError("팔레트 셀(64색)이 가득 찼습니다")
    mapping[key] = cell
    img[_COLORS_PROP] = json.dumps(mapping)
    # 셀 픽셀 채우기
    size = _GRID * _CELL_PX
    cx, cy = (cell % _GRID) * _CELL_PX, (cell // _GRID) * _CELL_PX
    pixels = list(img.pixels)
    r, g, b = color[:3]
    for y in range(cy, cy + _CELL_PX):
        for x in range(cx, cx + _CELL_PX):
            offset = (y * size + x) * 4
            pixels[offset:offset + 4] = [r, g, b, 1.0]
    img.pixels[:] = pixels
    img.update()
    return cell


def _cell_uv(cell: int):
    u = (cell % _GRID + 0.5) / _GRID
    v = (cell // _GRID + 0.5) / _GRID
    return u, v


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
