# 베이크 결과 적용: 이미지 pack·모델 이름 머티리얼·UV 레이어 교체·익스포트용 PNG 저장
import os
import re

import bpy

from .unwrap import TEXTURE_UV

PALETTE_UV = "UVMap"
TEXTURE_MARK = "lp3d_texture"   # 이 애드온이 만든 텍스처 이미지·머티리얼 표식


def texture_name(collection_name: str, mesh_objs) -> str:
    """텍스처·머티리얼·파일 이름 — 메시가 하나면 그 오브젝트 이름, 여럿이면 컬렉션 이름."""
    base = mesh_objs[0].name if len(mesh_objs) == 1 else collection_name
    safe = re.sub(r"[^A-Za-z0-9_\-가-힣]+", "_", base).strip("_")
    return safe or "Model"


def _material(name: str, image) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = 'Linear'
    tex.location = (-400, 0)
    bsdf.inputs["Roughness"].default_value = 0.9
    links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    nodes.active = tex  # Workbench TEXTURE 셰이딩이 이 노드를 그린다
    mat[TEXTURE_MARK] = True
    return mat


def finalize(mesh_objs, png_path: str, name: str) -> dict:
    """PNG를 name 이미지로 불러와 pack하고, name 머티리얼을 만들어 메시에 적용한다.

    팔레트 UV(UVMap)는 제거하고 언랩 레이어를 UVMap으로 개명한다 — 게임엔진에는
    텍스처 UV 하나만 나가야 한다. 팔레트 머티리얼 슬롯도 걷어낸다."""
    # 머티리얼을 건드리기 전에 모든 메시에 언랩 레이어가 있는지 먼저 확인한다 —
    # 중간에 빠지면 팔레트 UV로 새 아틀라스를 읽는 깨진 조합이 성공으로 마감된다
    missing = [obj.name for obj in mesh_objs if obj.data.uv_layers.get(TEXTURE_UV) is None]
    if missing:
        raise RuntimeError(f"언랩 레이어({TEXTURE_UV})가 없는 메시: {', '.join(missing)}")
    image = bpy.data.images.load(png_path, check_existing=False)
    image.name = name
    image[TEXTURE_MARK] = True
    try:
        image.colorspace_settings.name = 'sRGB'
    except TypeError:
        pass
    image.pack()  # 세션 임시 폴더가 지워져도 .blend 안에 남는다
    mat = _material(name, image)
    for obj in mesh_objs:
        mesh = obj.data
        mesh.materials.clear()
        mesh.materials.append(mat)
        for poly in mesh.polygons:
            poly.material_index = 0
        palette = mesh.uv_layers.get(PALETTE_UV)
        if palette is not None and palette.name != TEXTURE_UV:
            mesh.uv_layers.remove(palette)
        # remove()가 레이어 배열을 재할당하므로 앞서 얻은 참조는 쓰지 않고 이름으로 다시 찾는다
        tex_layer = mesh.uv_layers.get(TEXTURE_UV)
        tex_layer.name = PALETTE_UV
        tex_layer.active = True
        tex_layer.active_render = True
    return {"image": image.name, "material": mat.name}


def discard(mesh_objs):
    """텍스처 단계 실패 시 언랩 레이어를 걷어내 팔레트 상태로 되돌린다."""
    for obj in mesh_objs:
        layer = obj.data.uv_layers.get(TEXTURE_UV)
        if layer is not None:
            obj.data.uv_layers.remove(layer)
        palette = obj.data.uv_layers.get(PALETTE_UV)
        if palette is not None:
            palette.active = True
            palette.active_render = True


def collection_textures(coll):
    """컬렉션(하위 포함) 메시의 머티리얼이 참조하는 이 애드온 텍스처 이미지.

    팔레트는 이름 접미어(.001)가 붙을 수 있어 이름 비교 대신 표식으로 가른다."""
    images = []
    for obj in coll.all_objects:
        if obj.type != 'MESH':
            continue
        for mat in obj.data.materials:
            if not mat or not mat.use_nodes or not mat.get(TEXTURE_MARK):
                continue
            for node in mat.node_tree.nodes:
                img = getattr(node, "image", None)
                if img and img.get(TEXTURE_MARK) and img not in images:
                    images.append(img)
    return images


def save_texture_pngs(coll, out_dir: str) -> list:
    """익스포트 폴더에 텍스처 PNG를 함께 저장한다 (pack된 이미지도 파일로).

    파일명은 이미지 원본 경로의 이름을 따른다 — FBX 익스포터(path_mode COPY)가
    같은 이름으로 복사하므로 한 텍스처가 두 파일로 남지 않는다."""
    saved = []
    for image in collection_textures(coll):
        base = os.path.basename(image.filepath) if image.filepath else f"{image.name}.png"
        path = os.path.join(out_dir, base)
        image.save(filepath=path)
        saved.append(path)
    return saved
