# 팔레트 머티리얼: 고정 256x256 팔레트 텍스처 + UV 셀 매핑
#
# 모든 생성 모델이 하나의 팔레트 텍스처/머티리얼을 공유 → Unity에서 드로우콜 1개.
# 페이스의 UV를 색상 셀 중앙 한 점으로 모으는 방식이라 텍스처 필터링 번짐이 없다.
#
# 팔레트는 읽기 전용이다. 색→셀 매핑이 공식으로 결정되므로 생성 순서와 무관하게
# 항상 같은 텍스처가 나오고, 사용자는 에셋 라이브러리에서 머티리얼만 교체해도
# 색이 그대로 유지된다. 그리드/셀 크기는 영구 고정이며 변경하지 않는다.
# 설계 근거: docs/superpowers/specs/2026-08-28-fixed-palette-design.md
import os
import shutil

import bpy

from .colorsnap import cell_uv, snap_cell
from .palette_data import CELLS, SIZE

PALETTE_IMAGE = "LP3D_Palette"     # executor의 롤백 예외 처리에서 참조한다
PALETTE_MATERIAL = "LP3D_Palette"

_PNG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LP3D_Palette.png")
# 구버전(순서 기반 팔레트)이 이미지에 남긴 커스텀 프로퍼티 — 있으면 교체 대상이다
_LEGACY_PROPS = ("lp3d_colors", "lp3d_grid")


# ---------- 이미지 ----------

def _expected_pixels() -> list:
    """고정 팔레트의 픽셀 버퍼(선형 float RGBA 평면 리스트)를 만든다.

    Blender의 이미지 픽셀은 선형이지만, 이미지 컬러스페이스를 sRGB로 두면
    파일에서 로드한 것과 동일하게 해석된다. 이 함수는 PNG를 로드할 수 없는
    예외 상황의 대비책으로만 쓴다."""
    from .colorsnap import _srgb_to_linear
    grid = SIZE // 8
    buffer = [0.0] * (SIZE * SIZE * 4)
    for py in range(SIZE):
        cell_y = py // 8
        for px in range(SIZE):
            red, green, blue = CELLS[cell_y * grid + (px // 8)]
            offset = (py * SIZE + px) * 4
            buffer[offset:offset + 4] = [_srgb_to_linear(red / 255),
                                         _srgb_to_linear(green / 255),
                                         _srgb_to_linear(blue / 255), 1.0]
    return buffer


def _is_stale(img) -> bool:
    """기존 이미지가 고정 팔레트와 다른지 판정한다."""
    if tuple(img.size) != (SIZE, SIZE):
        return True
    if any(prop in img.keys() for prop in _LEGACY_PROPS):
        return True
    grid = SIZE // 8
    buffer = [0.0] * len(img.pixels)
    img.pixels.foreach_get(buffer)
    # 대표 셀 몇 개만 대조한다 (전체 대조는 불필요하게 비싸다)
    for cell in (0, grid + 1, len(CELLS) - 1):
        col, row = cell % grid, cell // grid
        x, y = col * 8 + 4, row * 8 + 4
        offset = (y * SIZE + x) * 4
        expected = CELLS[cell]
        for channel in range(3):
            actual = round(_linear_to_srgb(buffer[offset + channel]) * 255)
            if abs(actual - expected[channel]) > 1:   # 8비트 왕복 오차 1 허용
                return True
    return False


def _linear_to_srgb(value: float) -> float:
    """선형 RGB 성분을 sRGB 감마로 변환한다."""
    if value <= 0.0031308:
        return 12.92 * value
    return 1.055 * (max(value, 0.0) ** (1 / 2.4)) - 0.055


def _get_image() -> bpy.types.Image:
    """고정 팔레트 이미지를 반환한다. 없거나 낡았으면 번들 PNG로 채운다.

    구버전 이미지를 교체할 때 데이터블록을 제거하지 않고 픽셀만 덮어쓴다.
    머티리얼 노드의 이미지 참조가 끊기는 것을 막기 위함이다."""
    img = bpy.data.images.get(PALETTE_IMAGE)
    if img is None:
        img = bpy.data.images.load(_PNG_PATH)
        img.name = PALETTE_IMAGE
        img.colorspace_settings.name = 'sRGB'
        img.pack()   # .blend를 옮겨도 텍스처가 살아 있도록 임베드한다
        return img
    if _is_stale(img):
        for prop in _LEGACY_PROPS:
            if prop in img.keys():
                del img[prop]
        if tuple(img.size) != (SIZE, SIZE):
            img.scale(SIZE, SIZE)
        img.colorspace_settings.name = 'sRGB'
        img.pixels.foreach_set(_expected_pixels())
        img.update()
        img.pack()
    return img


# ---------- 머티리얼 ----------

def _get_material() -> bpy.types.Material:
    """팔레트 머티리얼을 반환한다(없으면 생성).

    노드 설정(Closest 보간 / sRGB / Roughness 0.9)은 에셋 라이브러리 머티리얼과
    맞춰야 하는 값이다. 어긋나면 머티리얼 교체 시 색이 미묘하게 달라진다."""
    mat = bpy.data.materials.get(PALETTE_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(PALETTE_MATERIAL)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        bsdf = nodes.get("Principled BSDF")
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = _get_image()
        tex.interpolation = 'Closest'   # 셀 경계 번짐 방지
        tex.location = (-300, 300)
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.9   # 캐주얼 톤: 무광
    else:
        # 기존 머티리얼이 낡은 이미지를 가리키고 있을 수 있으므로 다시 연결한다
        img = _get_image()
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                node.image = img
                node.interpolation = 'Closest'
    return mat


# ---------- 공개 API ----------

def set_color(obj, color, faces=None):
    """오브젝트(또는 일부 페이스)에 팔레트 색을 입힌다.

    color=(r,g,b) 0~1 범위. faces=None이면 전체, 아니면 페이스 인덱스 리스트.
    요청한 색은 고정 팔레트에서 지각적으로 가장 가까운 스와치로 스냅된다.
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
    u, v = cell_uv(snap_cell(color))
    target = set(faces) if faces is not None else None
    for poly in mesh.polygons:
        if target is not None and poly.index not in target:
            continue
        for loop_idx in poly.loop_indices:
            uv_layer[loop_idx].uv = (u, v)
    return obj


def save_palette_png(directory: str) -> str:
    """익스포트 시 팔레트 텍스처를 PNG로 저장하고 경로를 반환.

    Blender의 저장 경로를 거치지 않고 번들 PNG를 그대로 복사하므로,
    내보낸 파일은 항상 리포의 원본과 바이트 단위로 동일하다."""
    path = os.path.join(directory, "%s.png" % PALETTE_IMAGE)
    shutil.copyfile(_PNG_PATH, path)
    return path
