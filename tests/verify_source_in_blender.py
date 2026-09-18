"""하이폴리 원본 보관과 결과 폴더 저장을 실제 Blender에서 검증한다.

실행: ./scripts/dev_run.sh --background --python tests/verify_source_in_blender.py
셰이프 서버 없이 합성 GLB로 돈다(비용 없음).
"""
import os
import sys
import tempfile

import bpy

from bl_ext.user_default.lp3d_modelmaker.core import autosave, multiview  # noqa: E402
from bl_ext.user_default.lp3d_modelmaker.lowpoly import retopo, set_session  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(("PASS " if condition else "FAIL ") + name + ("  " + detail if detail else ""), flush=True)
    if not condition:
        fails.append(name)


def make_glb(path):
    """몸통(큰 셸) + 다리(전체의 30%, 떨어진 셸) + 파편(0.5%) 3덩어리를 담은 GLB."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    parts = []
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=0.5, location=(0, 0, 1.2))
    parts.append(bpy.context.object)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=36, ring_count=18, radius=0.35, location=(0, 0, 0.4))
    parts.append(bpy.context.object)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=6, ring_count=4, radius=0.05, location=(1.5, 0, 1.2))
    parts.append(bpy.context.object)
    for obj in parts:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()
    joined = bpy.context.object
    bpy.ops.object.shade_smooth()   # 플랫이면 glTF 가 면마다 정점을 쪼개 셸 판정이 무의미해진다
    bpy.ops.export_scene.gltf(filepath=path, export_format='GLB', use_selection=True)
    counts = sorted((len(c) for c in _islands(joined)), reverse=True)
    bpy.data.objects.remove(joined, do_unlink=True)
    return counts


def _islands(obj):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    comps = retopo._face_islands(bm)
    bm.free()
    return comps


work = tempfile.mkdtemp(prefix="lp3d_source_")
glb = os.path.join(work, "shape.glb")
islands = make_glb(glb)
print(f"  입력 셸 면수: {islands} (다리 {islands[1] / islands[0]:.0%}, 파편 {islands[2] / islands[0]:.1%})", flush=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
set_session("LP3D_Model")
coll = bpy.data.collections.new("LP3D_Model")
bpy.context.scene.collection.children.link(coll)
info = retopo.process_glb(glb, "LP3D_Model", coll, height=1.8, target_faces=3000, method='QUADRIFLOW')
obj, source = info["obj"], info.get("source")

check("결과 오브젝트가 컬렉션 직계", obj.name in coll.objects)
check("하이폴리 원본 보관", source is not None)
if source is None:
    print("RESULT: FAIL -> " + ", ".join(fails))
    sys.exit(0)
check("원본은 자식 컬렉션에 (직계 목록 오염 없음)",
      source.name not in coll.objects and any(source.name in c.objects for c in coll.children),
      f"직계 {len(coll.objects)}개")
check("원본 표식", bool(source.get(retopo.SOURCE_KEY)))
check("원본 기본 숨김", source.hide_viewport and source.hide_render)
check("원본이 더 조밀(리토폴로지 전)", len(source.data.polygons) > len(obj.data.polygons),
      f"{len(source.data.polygons):,} > {len(obj.data.polygons):,}")

bpy.context.view_layer.update()
res = [obj.matrix_world @ v.co for v in obj.data.vertices]
src = [source.matrix_world @ v.co for v in source.data.vertices]


def box(points):
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    return lo, hi


rlo, rhi = box(res)
slo, shi = box(src)
check("원본 키가 결과와 같음(같은 정규화)", abs((shi[2] - slo[2]) - (rhi[2] - rlo[2])) < 0.05,
      f"원본 {shi[2] - slo[2]:.2f} vs 결과 {rhi[2] - rlo[2]:.2f}")
check("원본 발바닥 z≈0", abs(slo[2]) < 0.02, f"{slo[2]:.3f}")
center = [abs((slo[i] + shi[i]) / 2 - (rlo[i] + rhi[i]) / 2) for i in range(2)]
check("원본 중심이 결과와 겹침", max(center) < 0.05, f"x,y 차이 {center[0]:.3f}, {center[1]:.3f}")
check("다리 셸 보존(파편만 제거)", info["floaters_removed"] == 1, f"제거 {info['floaters_removed']}개")
check("결과 높이 1.8m", abs((rhi[2] - rlo[2]) - 1.8) < 0.02, f"{rhi[2] - rlo[2]:.3f}")

# 결과 폴더 저장 — 다운로드 폴더를 건드리지 않게 보관 폴더를 임시 경로로 돌린다
saved_dir = multiview.archive_dir
multiview.archive_dir = lambda: work
try:
    reference = os.path.join(work, "ref.png")
    bpy.data.images.new("ref", 4, 4).save(filepath=reference)
    folder = autosave.save_result("LP3D_Model", 'CHARACTER', {'원화': reference, '셰이프': glb})
finally:
    multiview.archive_dir = saved_dir

check("결과 폴더 생성", bool(folder) and os.path.isdir(folder), folder)
if folder:
    names = sorted(os.listdir(folder))
    check("최종 .blend 저장", "LP3D_Model.blend" in names, str(names))
    check("재료 동봉(원화·셰이프)", "원화.png" in names and "셰이프.glb" in names, str(names))
    check("모드별 폴더 아래", os.path.basename(os.path.dirname(folder)) == 'character', folder)

print("RESULT: " + ("ALL PASS" if not fails else "FAIL -> " + ", ".join(fails)), flush=True)
