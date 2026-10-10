# 결과 .blend 스튜디오: 임시 씬에만 세우고 작업 파일에는 남기지 않는지, 연 파일의 카메라·조명·색 관리·렌더
#   ./scripts/dev_run.sh --background --python tests/verify_studio_in_blender.py -- <모델.glb> <출력 폴더>
import os
import sys

import bpy

MODULE = "bl_ext.user_default.lp3d_modelmaker"
argv = sys.argv[sys.argv.index("--") + 1:]
glb, out = argv[0], os.path.abspath(argv[1])
os.makedirs(out, exist_ok=True)
autosave = __import__(f"{MODULE}.core.autosave", fromlist=["x"])
studio = __import__(f"{MODULE}.core.studio", fromlist=["x"])

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)
coll = bpy.data.collections.new("StudioVerify")
bpy.context.scene.collection.children.link(coll)
before = {name: set(getattr(bpy.data, name).keys())
          for name in ("objects", "lights", "cameras", "meshes", "materials", "worlds", "images", "collections", "scenes")}
bpy.ops.import_scene.gltf(filepath=glb)
for obj in list(bpy.context.selected_objects):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)
imported = {name: set(getattr(bpy.data, name).keys()) for name in before}

blend = autosave.write_blend(out, "StudioVerify", ["StudioVerify"], [], studio_kind='CHARACTER')
assert blend and os.path.isfile(blend), blend
# 작업 파일에 스튜디오 데이터블록이 남지 않아야 한다
for name, keys in imported.items():
    left = set(getattr(bpy.data, name).keys()) - keys
    assert not left, f"{name} 잔여: {left}"
print("[verify] 작업 파일 잔여 없음")

bpy.ops.wm.open_mainfile(filepath=blend)
scene = bpy.context.scene
assert scene.get(studio.STUDIO_MARK), "스튜디오 표시 없음"
assert scene.camera is not None, "카메라 없음"
lights = sorted(o.name for o in scene.objects if o.type == 'LIGHT')
assert len(lights) == 3, lights
assert scene.world is not None and any(n.type == 'TEX_ENVIRONMENT' and n.image and n.image.packed_file
                                       for n in scene.world.node_tree.nodes), "HDRI 가 담기지 않음"
print(f"[verify] 열린 파일: 엔진 {scene.render.engine}, 색 {scene.view_settings.view_transform}, "
      f"조명 {lights}, {scene.render.resolution_x}x{scene.render.resolution_y}")
scene.render.filepath = os.path.join(out, "studio_render.png")
bpy.ops.render.render(write_still=True)
print("[verify] OK")
