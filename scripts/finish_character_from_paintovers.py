"""실제 기하에 맞춘 부품별 채색 시트를 UV 아틀라스로 베이크한다."""
import importlib
import json
import time
import traceback
from pathlib import Path

import bpy
from mathutils import Vector

PACKAGE = 'bl_ext.user_default.lp3d_modelmaker'
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'Generate' / 'wolf-tool-resume'
PARTS = ('BODY', 'OUTFIT', 'WEAPON')
capture = importlib.import_module(PACKAGE + '.texturing.capture')
bake = importlib.import_module(PACKAGE + '.texturing.bake')
tex_apply = importlib.import_module(PACKAGE + '.texturing.apply')
session = importlib.import_module(PACKAGE + '.core.session')
REPORT = {'state': 'STARTING', 'parts': {}, 'visual_review': 'pending',
          'source': 'actual geometry aligned imagegen paintovers',
          'view_order': list(capture.LAYOUT.views)}


def save_report():
    """부품별 완료 통계와 실패 원인을 즉시 기록한다."""
    (OUTPUT / 'report-texture.json').write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')


def validate_sheet(path):
    """채색 시트가 정사각 셀 여섯 개로 분할되는지 검사한다."""
    if not path.is_file():
        raise FileNotFoundError(str(path))
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        if width % 3 or height % 2 or width // 3 != height // 2:
            raise ValueError(f'채색 시트는 정사각 3x2 격자여야 합니다: {path} ({width}x{height})')
        return [width, height]
    finally:
        bpy.data.images.remove(image)


def main():
    """준비된 UV를 유지하며 부품별 베이크, pack, 합성 미리보기를 생성한다."""
    started = time.monotonic()
    source = OUTPUT / 'wolf-separated-uv-prepared.blend'
    if not source.is_file():
        raise FileNotFoundError(str(source))
    sheets = {part: OUTPUT / part.lower() / 'painted_sheet.png' for part in PARTS}
    dimensions = {part: validate_sheet(path) for part, path in sheets.items()}
    bpy.ops.wm.open_mainfile(filepath=str(source))
    report_geometry = json.loads((OUTPUT / 'report.json').read_text(encoding='utf-8'))
    collection = bpy.data.collections.get(report_geometry['collection'])
    if collection is None:
        raise RuntimeError('생성 결과 컬렉션을 찾을 수 없습니다')
    targets = {part: [obj for obj in collection.objects
                      if obj.type == 'MESH' and obj.get('lp3d_character_part') == part]
               for part in PARTS}
    for part, objects in targets.items():
        if not objects or any(obj.data.uv_layers.get(tex_apply.TEXTURE_UV) is None for obj in objects):
            raise RuntimeError(f'{part} 부품 또는 준비된 UV 레이어가 없습니다')
    REPORT.update(state='BAKING', source_blend=str(source), collection=collection.name)
    save_report()
    try:
        for part, objects in targets.items():
            print('WOLF_TEXTURE_BAKE', part, flush=True)
            sheet = sheets[part]
            views = capture.split_sheet(str(sheet))
            atlas = OUTPUT / part.lower() / 'texture-atlas.png'
            record = {'painted_sheet': str(sheet), 'sheet_size': dimensions[part],
                      'views': views, 'atlas': str(atlas), 'resolution': 2048}
            REPORT['parts'][part] = record
            save_report()
            with session._bake_context(bpy.context.scene.name) as context:
                record['bake'] = bake.rasterize_to_png(
                    context, objects, views, str(atlas), 2048, padding=8,
                    uv_layer_names=[tex_apply.TEXTURE_UV] * len(objects),
                    require_all_sources=True, allow_view_substitution=False,
                    silhouette_warp=False)
            record['applied'] = tex_apply.finalize(objects, str(atlas), f'Wolf_{part}_Texture')
            record['objects'] = [{
                'name': obj.name, 'faces': len(obj.data.polygons),
                'uv_layers': [layer.name for layer in obj.data.uv_layers],
                'materials': [material.name for material in obj.data.materials],
            } for obj in objects]
            record['packed'] = bool(bpy.data.images[record['applied']['image']].packed_file)
            if not record['packed']:
                raise RuntimeError(f'{part} 텍스처 pack 확인에 실패했습니다')
            save_report()
        result = OUTPUT / 'wolf-separated-textured.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(result))
        REPORT.update(state='TEXTURES_SAVED', textured_blend=str(result))
        save_report()
        objects = [obj for part in PARTS for obj in targets[part]]
        preview_dir = OUTPUT / 'preview'
        previews = capture.render_views(bpy.context, objects, str(preview_dir),
                                        resolution=1024, views=('FRONT',))
        key = 'THREE_QUARTER'
        previous_direction = capture._CAMERA_DIRECTIONS.get(key)
        try:
            capture._CAMERA_DIRECTIONS[key] = Vector((1.0, -1.0, .35)).normalized()
            previews.update(capture.render_views(bpy.context, objects, str(preview_dir),
                                                 resolution=1024, views=(key,)))
        finally:
            if previous_direction is None:
                capture._CAMERA_DIRECTIONS.pop(key, None)
            else:
                capture._CAMERA_DIRECTIONS[key] = previous_direction
        REPORT.update(state='TEXTURES_AND_PREVIEWS_READY', previews=previews,
                      elapsed_seconds=round(time.monotonic() - started, 1))
        save_report()
        print('WOLF_TEXTURE_READY', str(result), flush=True)
    except Exception:
        REPORT.update(state='FAILED', error=traceback.format_exc())
        save_report()
        bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / 'wolf-texture-partial-recovery.blend'))
        raise


if __name__ == '__main__':
    main()
