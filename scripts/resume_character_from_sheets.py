"""생성된 부품 시트로 실제 Hunyuan 모델링을 재개하고 채색용 가이드를 저장한다."""
import importlib
import json
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

PACKAGE = 'bl_ext.user_default.lp3d_modelmaker'
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'Generate' / 'wolf-tool-resume'
PARTS = ('BODY', 'OUTFIT', 'WEAPON')
session = importlib.import_module(PACKAGE + '.core.session')
jobs = importlib.import_module(PACKAGE + '.core.jobs')
multiview = importlib.import_module(PACKAGE + '.core.multiview')
runner = importlib.import_module(PACKAGE + '.core.runner')
scheduler = importlib.import_module(PACKAGE + '.core.scheduler')
retopo = importlib.import_module(PACKAGE + '.lowpoly.retopo')
capture = importlib.import_module(PACKAGE + '.texturing.capture')
unwrap = importlib.import_module(PACKAGE + '.texturing.unwrap')
prefs = importlib.import_module(PACKAGE + '.preferences').get_prefs()
REPORT = {
    'image_backend': 'imagegen tool supplied local sheets',
    'shape_backend': 'actual Hunyuan HTTP; no shape fixture',
    'texture_status': 'not painted; UV and guides prepared',
    'parts': {},
}


def save_report():
    """장시간 실행 중에도 마지막 완료 단계와 실패 원인을 보존한다."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'report.json').write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')


def supply_sheet(request, workdir, timeout, on_done, **kwargs):
    """외부 이미지 요청만 이미 생성된 실제 시트 파일로 대체한다."""
    part = Path(workdir).name.upper()
    sheet = OUTPUT / part.lower() / 'sheet.png'
    REPORT['parts'].setdefault(part, {})['source_sheet'] = str(sheet)
    save_report()
    on_done(str(sheet), None)


original_process = retopo.process_glb


def record_retopology(path, name, collection, **kwargs):
    """실제 리토폴로지를 실행하고 폴백을 포함한 원본 통계를 기록한다."""
    result = original_process(path, name, collection, **kwargs)
    part = Path(path).parent.name.upper()
    record = REPORT['parts'].setdefault(part, {})
    record['shape_glb'] = str(path)
    record['retopology'] = {key: value for key, value in result.items() if key != 'obj'}
    save_report()
    return result


def prepare_guides(runtime):
    """부품별 UV와 실제 기하의 회색 6면도를 생성한다."""
    collection = bpy.data.collections[runtime.collection_name]
    for obj in collection.objects:
        if obj.type != 'MESH':
            continue
        part = obj.get('lp3d_character_part')
        if part not in PARTS:
            continue
        record = REPORT['parts'][part]
        record['uv_unwrap'] = unwrap.unwrap_objects([obj])
        record.update(object=obj.name, vertices=len(obj.data.vertices),
                      faces=len(obj.data.polygons),
                      quads=sum(len(p.vertices) == 4 for p in obj.data.polygons),
                      dimensions=list(obj.dimensions), location=list(obj.location),
                      uv_layers=[layer.name for layer in obj.data.uv_layers])
        guide_dir = OUTPUT / part.lower() / 'guide'
        materials = list(obj.data.materials)
        indices = [face.material_index for face in obj.data.polygons]
        gray = bpy.data.materials.new('LP3D_GuideGray')
        gray.diffuse_color = (.48, .48, .48, 1.0)
        try:
            obj.data.materials.clear()
            obj.data.materials.append(gray)
            for face in obj.data.polygons:
                face.material_index = 0
            with session._bake_context(runtime.scene_name) as context:
                views = capture.render_views(context, [obj], str(guide_dir), resolution=512)
            record['guide_views'] = views
            record['guide_sheet'] = capture.join_sheet(views, str(guide_dir / 'guide_sheet.png'))
            record['guide_view_order'] = list(capture.LAYOUT.views)
        finally:
            obj.data.materials.clear()
            for material in materials:
                obj.data.materials.append(material)
            for face, index in zip(obj.data.polygons, indices):
                face.material_index = index
            bpy.data.materials.remove(gray)
        save_report()


def main():
    """실제 부품 생성 세션을 끝까지 펌프하고 중간 결과도 복구 가능하게 저장한다."""
    for part in PARTS:
        sheet = OUTPUT / part.lower() / 'sheet.png'
        if not sheet.is_file():
            raise FileNotFoundError(str(sheet))
    prefs.use_library = False
    prefs.shapegen_method = 'QUADRIFLOW'
    prefs.shapegen_faces = 12000
    if hasattr(prefs, 'shapegen_adaptive'):
        prefs.shapegen_adaptive = False
    props = bpy.context.scene.lp3d
    job = jobs.add_job(props, '늑대 전사 캐릭터: 몸체, 갑옷, 무기 분리 생성')
    job.creation_mode = 'CHARACTER'
    job.character_parts = 'SEPARATE'
    job.modeling_type = 'PALETTE'
    runtime = session.GenerationSession(bpy.context.scene.name, job.uid, job.prompt, exe='local-sheet-adapter')
    runtime.workdir = str(OUTPUT / 'session')
    Path(runtime.workdir).mkdir(parents=True, exist_ok=True)
    runtime.multiview = str(OUTPUT / 'body' / 'sheet.png')
    runtime.compare_turns_left = 0
    session._sessions[job.uid] = runtime
    job.state = 'RUNNING'
    REPORT.update(job_uid=job.uid, collection=runtime.collection_name, state='RUNNING')
    save_report()
    started = time.monotonic()
    previous_status = None
    try:
        with patch.object(multiview, 'generate', supply_sheet), \
                patch.object(multiview, 'archive', lambda path, name: path), \
                patch.object(retopo, 'process_glb', record_retopology):
            runtime._start_character_parts()
            while session.is_active(job.uid):
                runner._pump()
                if job.status != previous_status:
                    previous_status = job.status
                    print('WOLF_RESUME_STATUS', job.status, flush=True)
                    REPORT.update(state=job.state, status=job.status, log=job.log)
                    save_report()
                if time.monotonic() - started > 7200:
                    runtime.cancel()
                    raise TimeoutError('실제 생성 세션이 2시간을 초과했습니다')
                time.sleep(.1)
        if job.state != 'DONE':
            raise RuntimeError(job.status + '\n' + job.log)
        REPORT.update(state='GEOMETRY_DONE', status=job.status, log=job.log)
        geometry = OUTPUT / 'wolf-separated-geometry.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(geometry))
        REPORT['geometry_blend'] = str(geometry)
        save_report()
        prepare_guides(runtime)
        prepared = OUTPUT / 'wolf-separated-uv-prepared.blend'
        bpy.ops.wm.save_as_mainfile(filepath=str(prepared))
        REPORT.update(state='UV_GUIDES_READY', prepared_blend=str(prepared),
                      elapsed_seconds=round(time.monotonic() - started, 1),
                      scheduler=scheduler.counts())
        save_report()
        print('WOLF_RESUME_READY', str(prepared), flush=True)
    except Exception:
        REPORT.update(state='FAILED', error=traceback.format_exc(), status=job.status, log=job.log)
        save_report()
        if bpy.data.collections.get(runtime.collection_name):
            bpy.ops.wm.save_as_mainfile(filepath=str(OUTPUT / 'wolf-partial-recovery.blend'))
        raise


if __name__ == '__main__':
    main()
