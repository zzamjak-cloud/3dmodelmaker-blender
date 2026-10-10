# 게임엔진 익스포트: FBX(Unity 세팅) / glTF
import os
import re

import bpy

from ..lowpoly.palette import save_palette_png
from ..texturing.apply import save_texture_pngs


def _select_only(coll):
    """컬렉션의 메시 오브젝트만 선택 상태로 만든다 (익스포터의 use_selection용)."""
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    selected = []
    for obj in coll.objects:
        if obj.type == 'MESH':
            obj.select_set(True)
            selected.append(obj)
    if selected:
        bpy.context.view_layer.objects.active = selected[0]
    return selected


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name)


def export_collection(coll, out_dir: str, fmt: str = 'FBX') -> str:
    """컬렉션을 FBX 또는 glTF로 내보내고 파일 경로를 반환. 팔레트 PNG를 함께 저장."""
    if not _select_only(coll):
        raise RuntimeError("내보낼 메시 오브젝트가 없습니다")
    base = _safe_name(coll.name)
    save_palette_png(out_dir)
    save_texture_pngs(out_dir=out_dir, coll=coll)  # 개별 매핑 텍스처도 동봉

    if fmt == 'FBX':
        path = os.path.join(out_dir, f"{base}.fbx")
        bpy.ops.export_scene.fbx(
            filepath=path,
            use_selection=True,
            object_types={'MESH', 'EMPTY'},
            # Unity 임포트에서 스케일 1.0이 되도록
            apply_scale_options='FBX_SCALE_ALL',
            bake_space_transform=True,
            apply_unit_scale=True,
            use_mesh_modifiers=True,
            path_mode='COPY',      # 팔레트 텍스처 동봉
            embed_textures=False,
            add_leaf_bones=False,
        )
    else:
        path = os.path.join(out_dir, f"{base}.glb")
        bpy.ops.export_scene.gltf(
            filepath=path,
            use_selection=True,
            export_format='GLB',
            export_yup=True,
        )
    return path


# ---------- 게임용 glTF (팔레트 → 셀별 단색 머티리얼) ----------

def _solid_material(name: str, rgb):
    """셀 색을 albedo로 가진 Principled 머티리얼 (같은 이름이면 재사용)."""
    from . import game_convert
    color = game_convert.linear_color(rgb)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        bsdf = mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
    for link in list(bsdf.inputs["Base Color"].links):
        mat.node_tree.links.remove(link)
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = 0.9
    bsdf.inputs["Metallic"].default_value = 0.0
    mat.diffuse_color = color   # 뷰포트 솔리드 표시도 같은 색
    return mat


def build_game_mesh(coll, name: str, height=None, footprint=None):
    """컬렉션 메시를 게임용 단일 메시 오브젝트로 만든다 (원본 컬렉션은 건드리지 않는다).

    팔레트 면은 UV 셀 색의 단색 머티리얼(C_<hex>, 같은 색은 하나로)로 바꾸고, 모두 바뀌면 UV를 지운다.
    월드 변환을 굽고 한 메시로 합친 뒤 원점을 바닥 중앙에 둔다(정면 -Y 유지). height·footprint를 주면
    목표 높이·최대 발판에 맞춰 균일 축소·확대한다. (오브젝트, 배율)을 돌려준다 — 씬 컬렉션에 링크돼 있다."""
    import bmesh
    from mathutils import Matrix, Vector

    from ..lowpoly.palette import PALETTE_MATERIAL
    from ..lowpoly.palette_data import CELLS, COLS, ROWS
    from . import game_convert

    bpy.context.view_layer.update()   # 방금 바꾼 location·scale을 matrix_world에 반영
    sources = [o for o in coll.objects if o.type == 'MESH' and o.data.polygons]
    if not sources:
        raise RuntimeError("내보낼 메시 오브젝트가 없습니다")
    materials, slot = [], {}

    def slot_of(mat):
        key = mat.name if mat else ""
        if key not in slot:
            slot[key] = len(materials)
            materials.append(mat)
        return slot[key]

    keep_uv = False
    merged = bmesh.new()
    for obj in sources:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(obj.matrix_world)
        if obj.matrix_world.determinant() < 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces)   # 음수 스케일은 굽고 나면 면이 뒤집힌다
        uv = bm.loops.layers.uv.active
        own = list(obj.data.materials)
        for face in bm.faces:
            mat = own[face.material_index] if face.material_index < len(own) else None
            if mat is not None and mat.name == PALETTE_MATERIAL and uv is not None:
                u, v = face.loops[0][uv].uv
                rgb = CELLS[game_convert.uv_cell(u, v, COLS, ROWS)]
                name_ = game_convert.material_name(rgb)
                face.material_index = slot[name_] if name_ in slot else slot_of(_solid_material(name_, rgb))
            else:
                keep_uv = keep_uv or mat is not None   # 개별 매핑 등 텍스처 머티리얼은 UV가 필요하다
                face.material_index = slot_of(mat)
            face.smooth = False
        temp = bpy.data.meshes.new("LP3D_GameTemp")
        bm.to_mesh(temp)
        bm.free()
        merged.from_mesh(temp)
        bpy.data.meshes.remove(temp)

    lo = Vector([min(v.co[i] for v in merged.verts) for i in range(3)])
    hi = Vector([max(v.co[i] for v in merged.verts) for i in range(3)])
    scale = game_convert.fit_scale(tuple(hi - lo), height, footprint)
    base = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    merged.transform(Matrix.Scale(scale, 4) @ Matrix.Translation(-base))
    if not keep_uv:
        while merged.loops.layers.uv:
            merged.loops.layers.uv.remove(merged.loops.layers.uv[0])
    mesh = bpy.data.meshes.new(name)
    merged.to_mesh(mesh)
    merged.free()
    for mat in materials:
        mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, scale


def export_game_glb(coll, path: str, height=None, footprint=None) -> dict:
    """build_game_mesh 결과 하나만 GLB(y-up)로 내보내고 임시 오브젝트를 지운다. 통계 dict를 돌려준다."""
    obj, scale = build_game_mesh(coll, _safe_name(coll.name), height, footprint)
    try:
        for other in bpy.context.view_layer.objects:
            other.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.export_scene.gltf(
            filepath=path,
            use_selection=True,
            export_format='GLB',
            export_yup=True,
            export_apply=True,
            export_materials='EXPORT',
            export_animations=False,
            export_cameras=False,
            export_lights=False,
        )
        bpy.context.view_layer.update()
        mesh = obj.data
        mesh.calc_loop_triangles()
        stats = {"path": path, "tris": len(mesh.loop_triangles), "materials": len(mesh.materials),
                 "size": tuple(obj.dimensions), "scale": scale}
    finally:
        mesh = obj.data
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
    return stats
