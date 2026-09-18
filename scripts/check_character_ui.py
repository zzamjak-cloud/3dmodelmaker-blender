"""격리 Blender에서 캐릭터 템플릿 UI와 저장·재로드 계약을 검증한다."""
import importlib
import json
import os
from pathlib import Path

import bpy
import addon_utils

PACKAGE = 'bl_ext.user_default.lp3d_modelmaker'
root = Path(__file__).resolve().parents[1]
out = root / 'Generate' / 'character-validation'
out.mkdir(parents=True, exist_ok=True)
os.environ['LP3D_TEMPLATE_DIR'] = str(out / 'templates')

addon = importlib.import_module(PACKAGE)
templates = importlib.import_module(PACKAGE + '.core.templates')
jobs = importlib.import_module(PACKAGE + '.core.jobs')
template_ui = importlib.import_module(PACKAGE + '.ui.template_operators')
prefs = importlib.import_module(PACKAGE + '.preferences').get_prefs()
assert hasattr(bpy.ops.lp3d, 'template_load'), '템플릿 로드 연산자 미등록'

props = bpy.context.scene.lp3d
job = jobs.add_job(props, '템플릿 검증')
job.creation_mode = 'CHARACTER'
assert job.character_template == 'AUTO'
job.character_type = 'ANIMAL'
assert template_ui.selected_id(job) == 'QUADRUPED'
prefs.shapegen_method = 'TEMPLATE'
assert bpy.ops.lp3d.template_choose(template_id='HUMANOID') == {'FINISHED'}
assert job.character_template == 'HUMANOID'
assert bpy.ops.lp3d.template_load() == {'FINISHED'}
human = bpy.context.active_object
assert human.type == 'MESH' and len(human.data.polygons) > 100
assert all(len(face.vertices) == 4 for face in human.data.polygons)

# 편집한 정점을 저장·재로드했을 때 자체 제작 원본과 분리되어 보존되는지 확인한다.
original = human.data.vertices[0].co.copy()
human.data.vertices[0].co.x += 0.017
modified = human.data.vertices[0].co.copy()
assert bpy.ops.lp3d.template_register(template_name='수정한 인간형', kind='HUMANOID') == {'FINISHED'}
custom_id = job.character_template
assert custom_id not in {'AUTO', 'HUMANOID', 'QUADRUPED'}
assert bpy.ops.lp3d.template_load() == {'FINISHED'}
custom = bpy.context.active_object
assert (custom.data.vertices[0].co - modified).length < 1e-5
assert bpy.ops.lp3d.template_choose(template_id='HUMANOID') == {'FINISHED'}
assert bpy.ops.lp3d.template_load() == {'FINISHED'}
fresh = bpy.context.active_object
assert (fresh.data.vertices[0].co - original).length < 1e-5

# 외부 파일 선택을 제외한 실제 import 연산자 경로를 실행한다.
source = out / 'external-template.blend'
bpy.data.libraries.write(str(source), {custom}, fake_user=True)
assert bpy.ops.lp3d.template_import(filepath=str(source), template_name='외부 등록',
                                  kind='HUMANOID') == {'FINISHED'}
source_id = job.character_template
duplicate = jobs.duplicate_job(props, props.job_index)
assert duplicate.character_template == source_id

report = {'blender': bpy.app.version_string, 'profile': bpy.utils.resource_path('USER'),
          'human_faces': len(human.data.polygons), 'user_templates': len(templates.enum_items()) - 2,
          'edit_roundtrip': True, 'import_operator': True, 'duplicate_settings': True}
bpy.ops.wm.save_as_mainfile(filepath=str(out / 'template-workflow.blend'))
addon.dev_reload()
assert bpy.ops.lp3d.template_load() == {'FINISHED'}
addon_utils.disable(PACKAGE, default_set=True)
assert not hasattr(bpy.types.Scene, 'lp3d')
report['reload_unregister'] = True
(out / 'ui-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('CHARACTER_UI_OK', json.dumps(report, ensure_ascii=True), flush=True)
