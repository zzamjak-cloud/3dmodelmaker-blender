"""격리 Blender에서 템플릿 등록, 메시 연결, GLB 보존을 검증한다."""
import importlib
import json
import os
from pathlib import Path
import sys
import types

import bpy
import bmesh

root = Path(__file__).resolve().parents[1]
output = root / 'Generate' / 'templates-check'
output.mkdir(parents=True, exist_ok=True)
os.environ['LP3D_TEMPLATE_DIR'] = str(output / 'library')
package = types.ModuleType('template_check_addon')
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
templates = importlib.import_module('template_check_addon.core.templates')
retopo = importlib.import_module('template_check_addon.lowpoly.retopo')
bpy.ops.wm.read_factory_settings(use_empty=True)
collection = bpy.context.scene.collection
report = {}
for kind in ('HUMANOID', 'QUADRUPED'):
    obj = templates.load_template(kind, collection)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    assert all(edge.is_manifold for edge in bm.edges), kind
    assert all(face.calc_area() > 1e-9 for face in bm.faces), kind
    assert all(len(face.verts) == 4 for face in bm.faces), kind
    report[kind] = {'vertices': len(bm.verts), 'quads': len(bm.faces)}
    bm.free()
    identifier = templates.register_object(obj, '수정 가능 ' + kind, kind)
    loaded = templates.load_template(identifier, collection)
    assert loaded.data != obj.data
    assert len(loaded.data.polygons) == len(obj.data.polygons)
    loaded.data.vertices[0].co.x += .01
    assert loaded.data.vertices[0].co.x != obj.data.vertices[0].co.x
    modifier = loaded.modifiers.new('등록 거부 검증', 'SUBSURF')
    try:
        templates.register_object(loaded, 'modifier 경고', kind)
    except ValueError as error:
        assert 'modifier' in str(error)
    else:
        raise AssertionError('적용되지 않은 modifier가 조용히 제거되었습니다')
    loaded.modifiers.remove(modifier)
    external = templates.register_file(templates.template_root() / (identifier + '.blend'), '외부 ' + kind, kind)
    assert external != identifier
    bpy.data.objects.remove(loaded, do_unlink=True)
    bpy.data.objects.remove(obj, do_unlink=True)

index_path = templates.template_root() / 'index.json'
index_backup = index_path.read_text(encoding='utf-8')
try:
    index_path.write_text('{broken', encoding='utf-8')
    assert len(templates.enum_items()) == 2
    obj = templates.load_template('HUMANOID', collection)
    try:
        templates.register_object(obj, '손상 목록 보존')
    except ValueError:
        pass
    else:
        raise AssertionError('손상된 사용자 목록을 덮어썼습니다')
    assert index_path.read_text(encoding='utf-8') == '{broken'
    bpy.data.objects.remove(obj, do_unlink=True)
finally:
    index_path.write_text(index_backup, encoding='utf-8')
report['registry_error_handling'] = True

for index in range(2):
    bpy.ops.mesh.primitive_cube_add(size=.5, location=(index * 2, 0, .25))
path = output / 'two-parts.glb'
bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB')
before = set(bpy.data.objects)
result = retopo.process_glb(str(path), 'preserved', collection, method='DECIMATE',
                           preserve_parts=True, normalize_result=False)
assert len(result['obj'].data.polygons) == 24
assert len(result['obj'].data.vertices) >= 16
bpy.context.view_layer.update()
assert result['obj'].dimensions.x > 2.4
report['multi_mesh_preserved'] = True
fitted = retopo.process_glb(str(path), 'fitted', collection, method='TEMPLATE')
assert fitted['method'] == 'template'
assert fitted['quads'] == fitted['faces']
report['template_fit'] = fitted['faces']
before = set(bpy.data.objects)
before_meshes = set(bpy.data.meshes)
try:
    retopo.process_glb(str(path), 'failure', collection, method='TEMPLATE', template_id='MISSING')
except ValueError:
    pass
else:
    raise AssertionError('잘못된 템플릿이 허용되었습니다')
assert set(bpy.data.objects) == before
assert set(bpy.data.meshes) == before_meshes
report['failure_cleanup'] = True
(output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('TEMPLATE_CHECK_OK', json.dumps(report))

source_path = os.environ.get('LP3D_TEMPLATE_SOURCE')
if source_path:
    from mathutils import Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.context.scene.collection
    result = retopo.process_glb(source_path, 'Wolf_Template_Fit', collection,
                                method='TEMPLATE', target_faces=12000)
    obj = result['obj']
    obj.show_wire = True
    obj.show_all_edges = True
    destination = root / 'Generate' / 'character-validation'
    destination.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'SINGLE'
    scene.display.shading.single_color = (.45, .55, .65)
    scene.display.shading.show_cavity = True
    scene.render.resolution_x = 900
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    camera_data = bpy.data.cameras.new('ValidationCamera')
    camera = bpy.data.objects.new('ValidationCamera', camera_data)
    collection.objects.link(camera)
    scene.camera = camera
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = 2.4
    for label, position in (('front', (0, -5, .95)), ('side', (5, 0, .95))):
        camera.location = position
        camera.rotation_euler = (Vector((0, 0, .9)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = str(destination / ('wolf-template-' + label + '.png'))
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(destination / 'wolf-template-fit.blend'))
    print('WOLF_TEMPLATE_FIT_OK', result['faces'])
