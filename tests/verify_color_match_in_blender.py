# PBR 임포트 실측: 셰이프 서버 GLB + 그때 넣은 정면 원화 → 열린 구멍 메움·UV 겹침 없음·색 보정·러프니스 하한
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
# 닫히지 않는 1mm 짜리 조각(끝이 열린 경계)은 루프가 아니라 메울 수 없다 — 보이지 않는 수준만 허용한다
assert open_edges <= 10, open_edges
started = time.time()
stats = color_match.apply_to_object(info["obj"], front)
elapsed = time.time() - started
print(f"[verify] {stats} — {elapsed:.1f}s")
assert stats.get("applied"), stats
assert stats["after"] < stats["before"]
# 구멍 메움이 UV 섬을 가로지르는 거대한 삼각형을 만들면 아틀라스가 겹친다(실측: UV 면적 합 198%) — 겹침 없는 아틀라스는
# 면적 합이 1 을 넘을 수 없다
mesh = info["obj"].data
mesh.calc_loop_triangles()
uv = mesh.uv_layers.active.data
uv_area = 0.0
for tri in mesh.loop_triangles:
    a_, b_, c_ = (uv[i].uv for i in tri.loops)
    uv_area += abs((b_.x - a_.x) * (c_.y - a_.y) - (c_.x - a_.x) * (b_.y - a_.y)) / 2
print(f"[verify] UV 면적 합 {uv_area:.3f}")
assert uv_area < 1.0, uv_area
color_match.floor_roughness(info["obj"])
for mat in mesh.materials:
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    node = bsdf.inputs["Roughness"].links[0].from_node
    while node.type != 'TEX_IMAGE':
        node = [l for i in node.inputs for l in i.links][0].from_node
    rough = color_match._image_rgb(node.image)[..., 1].min()
    print(f"[verify] 러프니스 최솟값 {rough:.3f}")
    assert rough >= color_match.ROUGHNESS_FLOOR - 1e-3, rough
if len(argv) > 2:
    image = color_match.base_color_image(info["obj"])
    image.filepath_raw = argv[2]
    image.file_format = 'PNG'
    image.save()
print("[verify] OK")
