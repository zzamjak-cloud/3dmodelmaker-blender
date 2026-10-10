# PBR 임포트 실측: 셰이프 서버 GLB + 그때 넣은 정면 원화 → 열린 구멍 메움·색 보정 전후 정면 오차·소요 시간
#   ./scripts/dev_run.sh --background --python tests/verify_color_match_in_blender.py -- <shape.glb> <정면원화.png> [보정 텍스처 저장 png]
import sys
import time

import bmesh
import bpy

MODULE = "bl_ext.user_default.lp3d_modelmaker"
argv = sys.argv[sys.argv.index("--") + 1:]
glb, front = argv[0], argv[1]
color_match = __import__(f"{MODULE}.lowpoly.color_match", fromlist=["x"])
retopo = __import__(f"{MODULE}.lowpoly.retopo", fromlist=["x"])

coll = bpy.data.collections.new("Verify")
bpy.context.scene.collection.children.link(coll)
info = retopo.import_textured(glb, "Verify", coll, height=1.8)
bm = bmesh.new()
bm.from_mesh(info["obj"].data)
open_edges = sum(1 for e in bm.edges if e.is_boundary)
bm.free()
print(f"[verify] 구멍 메움 {info['holes']}개, 남은 열린 엣지 {open_edges}")
assert open_edges == 0, open_edges
started = time.time()
stats = color_match.apply_to_object(info["obj"], front)
elapsed = time.time() - started
print(f"[verify] {stats} — {elapsed:.1f}s")
assert stats.get("applied"), stats
assert stats["after"] < stats["before"]
if len(argv) > 2:
    image = color_match.base_color_image(info["obj"])
    image.filepath_raw = argv[2]
    image.file_format = 'PNG'
    image.save()
print("[verify] OK")
