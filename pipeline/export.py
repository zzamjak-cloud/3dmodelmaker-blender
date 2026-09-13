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
