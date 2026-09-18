"""외부 AI 응답만 fixture로 바꾸고 실제 Blender 부위 생성·배치를 검증한다."""
import importlib
import json
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

PACKAGE = 'bl_ext.user_default.lp3d_modelmaker'
root = Path(__file__).resolve().parents[1]
out = root / 'Generate' / 'parts-validation'
out.mkdir(parents=True, exist_ok=True)
session = importlib.import_module(PACKAGE + '.core.session')
jobs = importlib.import_module(PACKAGE + '.core.jobs')
multiview = importlib.import_module(PACKAGE + '.core.multiview')
character_parts = importlib.import_module(PACKAGE + '.core.character_parts')
shapegen = importlib.import_module(PACKAGE + '.core.shapegen')
runner = importlib.import_module(PACKAGE + '.core.runner')
scheduler = importlib.import_module(PACKAGE + '.core.scheduler')
prefs = importlib.import_module(PACKAGE + '.preferences').get_prefs()


def sheet(name, *boxes):
    """라벨 영역을 비운 동일 축척의 검증 시트를 만든다."""
    import numpy as np
    width, height = 192, 128
    pixels = np.ones((height, width, 4), dtype=np.float32)
    for row in range(2):
        for col in range(3):
            for x0, y0, x1, y1 in boxes:
                pixels[row * 64 + y0:row * 64 + y1, col * 64 + x0:col * 64 + x1, :3] = .2
    # 실제 시트처럼 칸 경계에 격자선을 그린다 — 분할기는 등분이 아니라 격자선을 찾아 자른다
    for x in (63, 64, 127, 128):
        pixels[:, x, :3] = .1
    pixels[63:65, :, :3] = .1
    image = bpy.data.images.new(name, width, height, alpha=True)
    image.pixels.foreach_set(pixels.ravel())
    image.filepath_raw = str(out / (name + '.png'))
    image.file_format = 'PNG'
    image.save()
    path = image.filepath_raw
    bpy.data.images.remove(image)
    return path


def shape(name, dimensions):
    """여러 조각을 가진 독립 GLB 응답을 실제 export로 만든다."""
    objects = []
    for offset in (-.3, .3):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(offset, 0, .5))
        obj = bpy.context.object
        obj.dimensions = dimensions
        objects.append(obj)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    path = str(out / (name + '.glb'))
    bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_selection=True)
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    return path


# 부품마다 독립 시트: 대상이 칸 중앙에 크게. 크기 관계는 시트끼리 비교하지 않는다(결합 제거)
BOXES = {'BODY': (16, 10, 48, 56), 'OUTFIT': (14, 12, 50, 54), 'WEAPON': (26, 10, 38, 56)}
sheets = {part: sheet(part.lower() + '-sheet', box) for part, box in BOXES.items()}
original = sheet('original-sheet', *BOXES.values())   # 디자인 참조(원본 턴어라운드 대역)
shapes = {part: shape(part.lower(), (.2, .15, 1.0)) for part in sheets}
request_order = []


def generated_sheet(request, workdir, timeout, on_done, **kwargs):
    """실제 요청의 부품 순서와 공통 참조 계약을 확인한다."""
    part = Path(workdir).name.upper()
    assert kwargs.get('prompt_override')
    assert kwargs['ref_image'] == original
    request_order.append('sheet:' + part)
    on_done(sheets[part], None)


def generated_shape(views, path, timeout, on_done, **kwargs):
    """분리 섬 보존 옵션을 서버 요청 경계에서 확인한다."""
    part = request_order[-1].split(':')[-1]
    assert kwargs.get('preserve_parts'), '서버 부품 보존 요청 누락'
    request_order.append('shape:' + part)
    on_done(shapes[part], None)


props = bpy.context.scene.lp3d
job = jobs.add_job(props, '부위 분리 통합 검증')
job.creation_mode = 'CHARACTER'
job.character_parts = 'SEPARATE'
job.modeling_type = 'PALETTE'
prefs.shapegen_method = 'DECIMATE'
prefs.use_library = False
runtime = session.GenerationSession(bpy.context.scene.name, job.uid, job.prompt, exe='fixture')
session._sessions[job.uid] = runtime
job.state = 'RUNNING'
runtime.multiview = original
with patch.object(character_parts, 'presence_cli', lambda: ''), \
        patch.object(multiview, 'generate', generated_sheet), \
        patch.object(multiview, 'archive', lambda path, name: path), \
        patch.object(shapegen, 'generate', generated_shape):
    runtime._start_character_parts()
    for _ in range(60):
        runner._pump()
        if not session.is_active(job.uid):
            break
assert job.state == 'DONE', job.status + '\n' + job.log
assert request_order == ['sheet:BODY', 'shape:BODY', 'sheet:OUTFIT', 'shape:OUTFIT',
                         'sheet:WEAPON', 'shape:WEAPON'], request_order
collection = bpy.data.collections[job.collection_name]
objects = {obj['lp3d_character_part']: obj for obj in collection.objects}
assert set(objects) == {'BODY', 'OUTFIT', 'WEAPON'}
assert all(len(obj.data.polygons) == 24 for obj in objects.values()), '부품 또는 몸체 면 손실'
bpy.context.view_layer.update()
assert abs(objects['BODY'].dimensions.z - 1.8) < .001
assert abs(objects['OUTFIT'].dimensions.z - 1.8 * character_parts.DEFAULT_HEIGHT_RATIO['OUTFIT']) < .001, '의상 기본 키 비율'
assert abs(objects['WEAPON'].dimensions.z - 1.8 * character_parts.DEFAULT_HEIGHT_RATIO['WEAPON']) < .001, '무기 기본 키 비율'
# 나란히 배치: 몸체 원점, 나머지는 오른쪽으로 겹치지 않게
xs = [objects[p].location.x for p in ('BODY', 'OUTFIT', 'WEAPON')]
assert abs(xs[0]) < 1e-5 and xs[0] < xs[1] < xs[2], f'나란히 배치 순서 {xs}'
for a, b in (('BODY', 'OUTFIT'), ('OUTFIT', 'WEAPON')):
    right_a = objects[a].location.x + objects[a].dimensions.x / 2
    left_b = objects[b].location.x - objects[b].dimensions.x / 2
    assert left_b - right_a > 0.25, f'{a}-{b} 겹침 또는 간격 부족'
for part in objects:
    runtime._texture_part = part
    assert runtime._mesh_objs() == [objects[part]], '다른 부품이 텍스처 대상에 섞임'
runtime._texture_part = None
report = {'external_ai': 'fixture', 'request_order': request_order,
          'part_faces': {part: len(obj.data.polygons) for part, obj in objects.items()},
          'weapon_height': objects['WEAPON'].dimensions.z, 'body_height': objects['BODY'].dimensions.z, 'row_x': xs,
          'texture_targets_isolated': True, 'scheduler': scheduler.counts()}
assert not scheduler.has_work()
(out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
bpy.ops.wm.save_as_mainfile(filepath=str(out / 'parts-flow.blend'))
print('CHARACTER_PARTS_OK', json.dumps(report), flush=True)
