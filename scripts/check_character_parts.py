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


# 실루엣은 격자 여백(GRID_MARGIN 8px) 밖에 둔다 — 실제 시트도 여백을 유지한다. 무기 키 = 몸체 키의 절반
BOXES = {'BODY': (20, 10, 44, 54), 'OUTFIT': (18, 20, 46, 45), 'WEAPON': (48, 12, 54, 34)}
sheets = {part: sheet(part.lower() + '-sheet', box) for part, box in BOXES.items()}
# 원본 턴어라운드는 몸체·의상·무기를 모두 담는다 — 환각 게이트는 부품 실루엣이 원본 밖에 있는 비율로 판정한다
original = sheet('original-sheet', *BOXES.values())
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
assert abs(objects['WEAPON'].dimensions.z - .9) < .001, '무기 키가 몸체 키로 확대됨'
assert objects['WEAPON'].location.x > .6, '무기 상대 위치 손실'
for part in objects:
    runtime._texture_part = part
    assert runtime._mesh_objs() == [objects[part]], '다른 부품이 텍스처 대상에 섞임'
runtime._texture_part = None
report = {'external_ai': 'fixture', 'request_order': request_order,
          'part_faces': {part: len(obj.data.polygons) for part, obj in objects.items()},
          'weapon_height': objects['WEAPON'].dimensions.z, 'body_height': objects['BODY'].dimensions.z,
          'texture_targets_isolated': True, 'scheduler': scheduler.counts()}
assert not scheduler.has_work()
(out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
bpy.ops.wm.save_as_mainfile(filepath=str(out / 'parts-flow.blend'))
print('CHARACTER_PARTS_OK', json.dumps(report), flush=True)
