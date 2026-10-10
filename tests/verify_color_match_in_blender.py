# PBR 임포트 실측: 셰이프 서버 GLB + 그때 넣은 정면 원화 → 열린 구멍 메움·UV 겹침 없음·색 보정·정면 디테일 투영
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
front_project = __import__(f"{MODULE}.lowpoly.front_project", fromlist=["x"])

coll = bpy.data.collections.new("Verify")
bpy.context.scene.collection.children.link(coll)
info = retopo.import_textured(glb, "Verify", coll, height=1.8)
bm = bmesh.new()
bm.from_mesh(info["obj"].data)
open_edges = sum(1 for e in bm.edges if e.is_boundary)
bm.free()
print(f"[verify] 구멍 메움 {info['holes']}개, 남은 열린 엣지 {open_edges}")
# 닫히지 않는 1mm 짜리 조각(끝이 열린 경계)은 루프가 아니라 메울 수 없다 — 보이지 않는 수준만 허용한다
assert open_edges <= 10, open_edges
started = time.time()
stats = color_match.apply_to_object(info["obj"], front)
elapsed = time.time() - started
print(f"[verify] {stats} — {elapsed:.1f}s")
assert stats.get("applied"), stats
assert stats["after"] < stats["before"]
# 구멍 메움이 UV 섬을 가로지르는 거대한 삼각형을 만들면 아틀라스가 겹친다(실측 46%) — 겹침이 없어야 한다
_co, tris, uvs = front_project._mesh_arrays(info["obj"])
size = 512
flat = uvs.reshape(-1, 2)
hits = __import__("numpy").zeros(size * size, int)
for _t, x, y, _u, _v, _w in front_project._chunks(flat[:, 0] * size, (1 - flat[:, 1]) * size,
                                                   __import__("numpy").arange(len(flat)).reshape(-1, 3), size, size):
    __import__("numpy").add.at(hits, y * size + x, 1)
overlap = float((hits > 1).mean())
print(f"[verify] UV 겹침 {overlap:.4f}")
assert overlap < 0.002, overlap
started = time.time()
fp_stats = front_project.apply_to_object(info["obj"], front)
print(f"[verify] 정면 디테일 {fp_stats} — {time.time() - started:.1f}s")
assert fp_stats.get("applied") and fp_stats["iou"] > 0.8, fp_stats
if len(argv) > 2:
    image = color_match.base_color_image(info["obj"])
    image.filepath_raw = argv[2]
    image.file_format = 'PNG'
    image.save()
print("[verify] OK")
