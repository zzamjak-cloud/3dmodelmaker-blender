# 은면 컬링 판정 확인 — Blender 안에서 실행한다.
#   blender --background --factory-startup --python tests/verify_cull_in_blender.py
#
# 보이는 면을 지우지 않는지(미러 파트·오목 공간·테두리 노출 면)와
# 파묻힌 면은 여전히 지우는지를 함께 확인한다.
import math
import os
import random
import sys
import time

import bmesh
import bpy
from mathutils import Matrix, Vector

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


def add(name, bm, matrix=None):
    mesh = bpy.data.meshes.new(name)
    if matrix is not None:
        bm.transform(matrix)   # bmesh.transform은 감기 순서를 뒤집지 않는다 → 음수 스케일이면 노멀 반전
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def cube(size=(1, 1, 1), loc=(0, 0, 0)):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2])) + Vector(loc)
    return bm


def cyl(r=0.2, h=1.0, loc=(0, 0, 0), rot=None, segs=12, caps=True, r_top=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=caps, segments=segs, radius1=r,
                          radius2=r if r_top is None else r_top, depth=h)
    m = Matrix.Translation(loc)
    if rot:
        m = m @ rot
    bm.transform(m)
    return bm


def cup(r_bottom=0.3, r_top=0.2, h=1.2, segs=12):
    """위가 열린 테이퍼 컵 (단일 벽 + 바닥)."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=segs, radius1=r_bottom, radius2=r_top, depth=h)
    bm.faces.new([v for v in bm.verts if v.co.z < 0])
    bm.transform(Matrix.Translation((0, 0, h / 2)))
    return bm


def hidden(objs):
    return {o.name: len(v) for o, v in cleanup.find_hidden_faces(objs).items()}


def total(objs):
    return sum(hidden(objs).values())


# 1) 노멀이 뒤집힌 미러 파트는 살아야 한다
new_scene()
body = add("Body", cube((1, 1, 1), (0, 0, 0.5)))
arm = add("ArmL", cube((0.2, 0.2, 0.8), (0.8, 0, 0.6)), Matrix.Scale(-1, 4, (1, 0, 0)))
check("미러(노멀 반전) 파트 보존", total([body, arm]), 0)

# 2) 파묻힌 실린더 캡은 지워야 한다 (몸통은 보존)
new_scene()
body = add("Body", cube((1, 1, 1), (0, 0, 0.5)))
arm = add("Arm", cyl(0.15, 1.0, (0.7, 0, 0.5), Matrix.Rotation(math.radians(90), 4, 'Y')))
result = hidden([body, arm])
check("파묻힌 캡 삭제", result["Arm"], 1)
check("캡을 품은 몸통 보존", result["Body"], 0)

# 3) 깊은 테이퍼 컵 내부는 오목 공간 — 보존
new_scene()
check("깊은 테이퍼 컵 내부 보존", total([add("Cup", cup(0.3, 0.12, 2.0))]), 0)

# 4) 기울어진 긴 관 내부 — 보존
new_scene()
rot = Matrix.Rotation(math.radians(35), 4, 'Y') @ Matrix.Rotation(math.radians(20), 4, 'X')
check("기울어진 관 내부 보존", total([add("Tube", cyl(0.08, 3.0, (0, 0, 1.5), rot, caps=False))]), 0)

# 5) 작은 상자를 올린 적층: 윗상자 바닥만 은면, 아래상자 윗면은 테두리가 보이므로 보존
new_scene()
base = add("Base", cube((1, 1, 0.2), (0, 0, 0.1)))
top = add("Top", cube((0.8, 0.8, 0.5), (0, 0, 0.45)))
result = hidden([base, top])
check("받침대 윗면(테두리 노출) 보존", result["Base"], 0)
check("올린 상자 바닥 삭제", result["Top"], 1)

# 6) 같은 크기 적층(정점 일치): 접촉면 2개 삭제
new_scene()
a = add("A", cube((1, 1, 1), (0, 0, 0.5)))
b = add("B", cube((1, 1, 1), (0, 0, 1.5)))
check("동일 크기 적층 접촉면 삭제", total([a, b]), 2)

# 7) 좌우 다리 사이 좁은 틈 — 보존
new_scene()
l = add("L", cube((0.3, 0.3, 1.0), (-0.16, 0, 0.5)))
r = add("R", cube((0.3, 0.3, 1.0), (0.16, 0, 0.5)))
check("좁은 틈 마주보는 면 보존", total([l, r]), 0)

# 8) 상자 안에 완전히 든 구: 전부 삭제
new_scene()
box_obj = add("Box", cube((2, 2, 2), (0, 0, 1)))
bm = bmesh.new()
bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.4)
bm.transform(Matrix.Translation((0, 0, 1)))
ball = add("Ball", bm)
check("상자 안 구 전부 삭제", hidden([box_obj, ball])["Ball"], len(ball.data.polygons))

# 9) 열린 컵 안에 든 상자: 닫힌 볼륨 안이 아니므로 보존(보수적)
new_scene()
c = add("Cup", cup(0.5, 0.5, 1.0))
inner = add("Inner", cube((0.3, 0.3, 0.3), (0, 0, 0.15)))
check("열린 셸 안 상자 보존", total([c, inner]), 0)

# 10) 한 오브젝트에 여러 아일랜드(fast join 결과)도 동일하게 판정
new_scene()
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
for v in bm.verts:
    v.co.z += 0.5
tmp = bmesh.new()
bmesh.ops.create_cone(tmp, cap_ends=True, segments=12, radius1=0.15, radius2=0.15, depth=1.0)
tmp.transform(Matrix.Translation((0.7, 0, 0.5)) @ Matrix.Rotation(math.radians(90), 4, 'Y'))
tmp_mesh = bpy.data.meshes.new("tmp")
tmp.to_mesh(tmp_mesh)
tmp.free()
bm.from_mesh(tmp_mesh)
joined = add("Joined", bm)
check("단일 오브젝트 다중 아일랜드 캡 삭제", total([joined]), 1)

# 11) 성능: 겹치는 상자 150개
new_scene()
random.seed(7)
objs = [add(f"B{i}", cube((random.uniform(0.2, 0.6),) * 3,
                          (random.uniform(-1, 1), random.uniform(-1, 1), random.uniform(0, 1.5))))
        for i in range(150)]
started = time.perf_counter()
n = total(objs)
elapsed = time.perf_counter() - started
print(f"INFO 겹치는 상자 150개: 은면 {n}개, {elapsed:.2f}s")
check("성능 (150 상자 < 20s)", elapsed, 20.0, "le")

print("\n결과:", "모두 통과" if not failures else f"실패 {len(failures)}건: {failures}")
sys.exit(1 if failures else 0)
