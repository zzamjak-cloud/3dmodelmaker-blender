# 기하 검증기(좌우 대칭도) 동작 확인 — Blender 안에서 실행한다.
#   blender --background --factory-startup --python tests/verify_geometry_in_blender.py
#
# 순수 파이썬 테스트로는 bmesh/matrix_world를 재현할 수 없어 별도 스크립트로 둔다
# (tests/verify_palette_in_blender.py와 같은 방식).
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lowpoly import cleanup  # noqa: E402

failures = []


def check(label, actual, expect, cmp="eq"):
    ok = (actual == expect) if cmp == "eq" else (actual >= expect if cmp == "ge" else actual <= expect)
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual} (기대 {cmp} {expect})")
    if not ok:
        failures.append(label)


def new_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def box(name, size, loc):
    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= size[0]; v.co.y *= size[1]; v.co.z *= size[2]
    bm.to_mesh(mesh)
    bm.free()
    obj.location = loc
    return obj


# --- 대칭도 ---
new_scene()
body = box("Body", (2, 1, 1), (0, 0, 0.5))
left = box("MirrorL", (0.2, 0.2, 0.2), (-0.8, 0.6, 0.8))
right = box("MirrorR", (0.2, 0.2, 0.2), (0.8, 0.6, 0.8))
check("대칭 모델의 대칭도", cleanup.symmetry_score([body, left, right]), 0.99, "ge")

new_scene()
body = box("Body", (2, 1, 1), (0, 0, 0.5))
left = box("MirrorL", (0.2, 0.2, 0.2), (-0.8, 0.6, 0.8))  # 한쪽 미러만 있음
# 본체는 대칭이므로 0이 아니라 "본체 비율만큼"이어야 한다 (중심이 끌려가면 0이 된다)
score = cleanup.symmetry_score([body, left])
check("한쪽 파트 누락 시 대칭도 상한", score, 0.95, "le")
check("한쪽 파트 누락 시 대칭도 하한", score, 0.4, "ge")

new_scene()
solo = box("Solo", (1, 1, 1), (0, 0, 0.5))
check("단일 대칭 박스", cleanup.symmetry_score([solo]), 0.99, "ge")

# 좌우 대칭축은 대상마다 다르다 — 길이가 X인 차량의 좌우는 Y축이므로
# 축을 고정하면 정상 모델이 낮게 나온다 (axis=None이 X/Y 중 좋은 쪽을 고른다)
new_scene()
body = box("Body", (4, 1.8, 1), (0, 0, 0.8))          # X=길이, Y=폭
ml = box("MirrorL", (0.2, 0.3, 0.15), (1.3, 0.95, 1.5))
mr = box("MirrorR", (0.2, 0.3, 0.15), (1.3, -0.95, 1.5))
check("Y축 대칭 차량(앞뒤 비대칭)", cleanup.symmetry_score([body, ml, mr]), 0.99, "ge")

new_scene()
body = box("Body", (4, 1.8, 1), (0, 0, 0.8))
ml = box("MirrorL", (0.2, 0.3, 0.15), (1.3, 0.95, 1.5))  # 한쪽 미러 누락
check("차량 한쪽 미러 누락 감지", cleanup.symmetry_score([body, ml]), 0.95, "le")

print("GEOMETRY_VERIFY", "OK" if not failures else f"FAILED: {failures}")
if failures:
    raise SystemExit(1)
