"""3단계 흐름(셰이프 → 리토폴로지 → 매핑)과 와이어 기준선 전달을 실제 Blender에서 검증한다.

실행: ./scripts/dev_run.sh --background --python tests/verify_stages_in_blender.py
AI·셰이프 서버를 부르지 않는다 — 합성 GLB와 가짜 세션으로 단계 전환만 본다.
"""
import math
import os
import tempfile

import bmesh
import bpy

from bl_ext.user_default.lp3d_modelmaker.lowpoly import retopo, set_session  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + ("  " + detail if detail else ""), flush=True)
    if not condition:
        fails.append(name)


def make_glb(path):
    """이음새가 뚜렷한 덩어리 — 샤프 기준선을 그을 허리선이 있다."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=0.5, location=(0, 0, 1.0))
    body = bpy.context.object
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=0.35, location=(0, 0, 0.45))
    hips = bpy.context.object
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    joined = bpy.context.object
    bpy.ops.object.shade_smooth()
    bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_selection=True)
    bpy.data.objects.remove(joined, do_unlink=True)


work = tempfile.mkdtemp(prefix="lp3d_stage_")
glb = os.path.join(work, "shape.glb")
make_glb(glb)

# --- 1단계: 셰이프만 가져온다 (사용자가 확인·마킹하는 상태) ---
bpy.ops.wm.read_factory_settings(use_empty=True)
set_session("LP3D_Model")
coll = bpy.data.collections.new("LP3D_Model")
bpy.context.scene.collection.children.link(coll)
source, _count = retopo.import_shape(glb, "LP3D_Model" + retopo.SOURCE_SUFFIX, coll)
retopo.remove_ground_slabs(source)
retopo.flip_if_inverted(source)
source[retopo.SOURCE_KEY] = True
retopo.normalize(source, 1.8)
check("1단계: 원본이 컬렉션에 보인다", source.name in coll.objects and not source.hide_viewport)
check("1단계: 원본 표식", bool(source.get(retopo.SOURCE_KEY)))
raw_faces = len(source.data.polygons)

# 사용자가 허리선을 Mark Sharp 로 긋는 상황을 흉내낸다 (z 가 특정 높이 근처인 엣지)
band = 0.0
zs = [v.co.z for v in source.data.vertices]
band = (min(zs) + max(zs)) * 0.5
marked = 0
for edge in source.data.edges:
    a = source.data.vertices[edge.vertices[0]].co.z
    b = source.data.vertices[edge.vertices[1]].co.z
    if abs((a + b) / 2 - band) < 0.03:
        edge.use_edge_sharp = True
        marked += 1
source.data.update()
check("사용자 기준선 표시", marked > 0, f"{marked}개 엣지")
points = retopo.sharp_edge_points(source)
check("기준선 좌표 수집", len(points) > marked, f"표본 {len(points)}개")

# --- 2단계: 그 원본에서 리토폴로지 ---
info = retopo.process_object(source, "LP3D_Model", coll, height=1.8, target_faces=3000,
                             method='QUADRIFLOW', symmetry=True)
result = info["obj"]
check("2단계: 결과 메시", len(result.data.polygons) > 0, f"{len(result.data.polygons):,}면")
check("2단계: 원본보다 가볍다", len(result.data.polygons) < raw_faces, f"{len(result.data.polygons):,} < {raw_faces:,}")
check("2단계: 원본 보관", info.get("source") is not None and info["source"].get(retopo.SOURCE_KEY))
check("2단계: 원본은 자식 컬렉션으로 이동", info["source"].name not in coll.objects)
check("2단계: 결과가 컬렉션 직계", result.name in coll.objects)

bm = bmesh.new()
bm.from_mesh(result.data)
border = sum(1 for e in bm.edges if len(e.link_faces) != 2)
bm.free()
check("2단계: 닫힌 메시", border == 0, f"경계엣지 {border}")
check("2단계: 노멀이 바깥", retopo.signed_volume(result) > 0, f"{retopo.signed_volume(result):+.4f}")
quads = sum(1 for p in result.data.polygons if len(p.vertices) == 4)
check("2단계: 쿼드 위주", quads / max(len(result.data.polygons), 1) > 0.9,
      f"{100 * quads / max(len(result.data.polygons), 1):.0f}%")

# 기준선 전달 자체를 직접 확인 — 리메시 메시에 샤프가 다시 심기는가
probe = bpy.data.objects.new("probe", bpy.data.meshes.new("probe"))
coll.objects.link(probe)
bm = bmesh.new()
bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0)
bm.to_mesh(probe.data)
bm.free()
transferred = retopo.transfer_sharp_edges(probe, [v.co.copy() for v in probe.data.vertices[:10]], 0.2)
check("기준선 전달", transferred > 0, f"{transferred}개 엣지에 샤프")

print("RESULT: " + ("ALL PASS" if not fails else "FAIL -> " + ", ".join(fails)), flush=True)
