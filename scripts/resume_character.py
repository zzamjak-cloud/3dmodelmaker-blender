"""격리 Blender에서 보관된 턴어라운드로 실제 캐릭터 생성을 재개한다.

LP3D_RESUME_SHEET, LP3D_RESUME_OUT, LP3D_SOURCE_SETTINGS 환경변수를 지정한다.
API 인증 정보는 기존 설정에서 메모리로만 읽고 출력·저장하지 않는다.
"""
import importlib
import json
import logging
import os
from pathlib import Path
import tempfile
import time

import bpy

PACKAGE = 'bl_ext.user_default.lp3d_modelmaker'
session = importlib.import_module(PACKAGE + '.core.session')
jobs = importlib.import_module(PACKAGE + '.core.jobs')
multiview = importlib.import_module(PACKAGE + '.core.multiview')
shapegen = importlib.import_module(PACKAGE + '.core.shapegen')
runner = importlib.import_module(PACKAGE + '.core.runner')
preferences = importlib.import_module(PACKAGE + '.preferences')
persist = importlib.import_module(PACKAGE + '.core.persist')

sheet = Path(os.environ['LP3D_RESUME_SHEET']).resolve()
out = Path(os.environ['LP3D_RESUME_OUT']).resolve()
out.mkdir(parents=True, exist_ok=True)
archive = out / 'images'
archive.mkdir(exist_ok=True)
work = out / 'work'
work.mkdir(exist_ok=True)
tempfile.tempdir = str(work)
multiview.archive_dir = lambda: str(archive)
logging.basicConfig(level=logging.INFO)

prefs = preferences.get_prefs()
persist._suspended = True
stored = json.loads(Path(os.environ['LP3D_SOURCE_SETTINGS']).read_text(encoding='utf-8')).get('prefs', {})
for key in ('image_backend', 'image_model', 'image_quality', 'openrouter_api_key', 'codex_path'):
    if key in stored:
        setattr(prefs, key, stored[key])
stored.clear()
prefs.use_shapegen = True
prefs.shapegen_url = 'http://127.0.0.1:8081'
prefs.shapegen_method = 'QUADRIFLOW'
prefs.shapegen_faces = 12000
prefs.use_library = False
prefs.timeout = 600
prefs.character_height = 1.8
prefs.texture_resolution = '2048'
error = shapegen.parts_support_error()
if error:
    raise RuntimeError(error)
if not sheet.is_file():
    raise RuntimeError('보관된 턴어라운드 파일을 찾을 수 없습니다')

props = bpy.context.scene.lp3d
job = jobs.add_job(props, '늑대 머리를 한 수인 전사, 가죽 갑옷과 어깨 털 망토, 원화의 양손 도끼')
job.creation_mode = 'CHARACTER'
job.character_type = 'CREATURE'
job.character_parts = 'SEPARATE'
job.style = 'STYLIZED'
job.modeling_type = 'TEXTURE'
job.ref_image_path = str(sheet)
runtime = session.GenerationSession(bpy.context.scene.name, job.uid, job.prompt,
                                    exe=preferences.resolve_cli_path('CODEX') or 'codex')
session._sessions[job.uid] = runtime
runtime._begin()
runtime.multiview = str(sheet)
job.multiview_path = str(sheet)
runtime._start_character_parts()
deadline = time.monotonic() + 3600
previous = ''
while session.is_active(job.uid):
    runner._pump()
    if job.status != previous:
        previous = job.status
        print('PROGRESS', previous, flush=True)
        (out / 'status.json').write_text(json.dumps({'state': job.state, 'status': previous,
                                                     'log': job.log}, ensure_ascii=False, indent=2), encoding='utf-8')
    if time.monotonic() > deadline:
        runtime.cancel()
        runner._pump()
        raise TimeoutError('실제 생성 검증이 60분을 초과했습니다')
    time.sleep(.2)

collection = bpy.data.collections.get(runtime.collection_name)
parts = {}
if collection:
    for obj in collection.objects:
        if obj.type == 'MESH':
            parts[obj.get('lp3d_character_part', obj.name)] = {
                'name': obj.name, 'faces': len(obj.data.polygons),
                'quads': sum(len(face.vertices) == 4 for face in obj.data.polygons),
                'materials': [material.name for material in obj.data.materials if material],
            }
report = {'state': job.state, 'status': job.status, 'log': job.log,
          'collection': runtime.collection_name, 'parts': parts, 'workdir': runtime.workdir}
(out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
bpy.ops.wm.save_as_mainfile(filepath=str(out / 'wolf-separated.blend'))
print('LIVE_CHARACTER_RESULT', json.dumps(report, ensure_ascii=True), flush=True)
prefs.openrouter_api_key = ''
if job.state != 'DONE':
    raise RuntimeError(job.status)
