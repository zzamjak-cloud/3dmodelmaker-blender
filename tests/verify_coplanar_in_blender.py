# 동일평면 겹침(Z-fighting) 절단 확인 — Blender 안에서 실행한다.
#   blender --background --factory-startup --python tests/verify_coplanar_in_blender.py
#
# 맞닿은 면·나란한 면이 한 평면에 하나만 남는지, 면적과 노멀·색 UV가 보존되는지, 다시 돌리면 할 일이 없는지 본다.
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lowpoly import cleanup  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {label} {detail}")
    if not ok:
        failures.append(label)


def new_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def box_bm(bm, lo, hi, uv=None):
    """lo~hi 축 정렬 박스를 bm에 더한다. uv를 주면 그 박스 모든 루프에 같은 UV(팔레트 칸)를 쓴다."""
    size = Vector(hi) - Vector(lo)
    center = (Vector(lo) + Vector(hi)) / 2
    mat = Matrix.Translation(center) @ Matrix.Diagonal((*size, 1.0))
    before = set(bm.faces)
    bmesh.ops.create_cube(bm, size=1.0, matrix=mat)
    if uv is not None:
        layer = bm.loops.layers.uv.verify()
        for face in set(bm.faces) - before:
            for loop in face.loops:
                loop[layer].uv = uv


def make(name, boxes, matrix=None):
    bm = bmesh.new()
    for spec in boxes:
        box_bm(bm, *spec)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    if matrix is not None:
        obj.matrix_world = matrix
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def area(obj):
    return sum(p.area for p in obj.data.polygons)


def facing_area(obj, axis, sign, at, tol=1e-5):
    mw = obj.matrix_world
    total = 0.0
    for p in obj.data.polygons:
        n = (mw.to_3x3() @ p.normal).normalized()
        c = mw @ p.center
        if n[axis] * sign > 0.999 and abs(c[axis] - at) < tol:
            total += p.area * abs(mw.to_3x3().determinant()) ** (2 / 3)
    return total


# 1) 받침 위 상자 (한 오브젝트, fast join 결과와 같은 느슨한 파트 2개)
new_scene()
obj = make("Stack", [((0, 0, 0), (2, 2, 1)), ((0.5, 0.5, 1), (1.5, 1.5, 2))])
changed = cleanup.resolve_coplanar_faces([obj])
check("접촉 면 2개 정리", changed == 2, f"({changed})")
check("z=1 윗면은 둘레만 남음", abs(facing_area(obj, 2, 1, 1.0) - 3.0) < 1e-4, f"({facing_area(obj, 2, 1, 1.0):.4f})")
check("z=1 아랫면 사라짐", facing_area(obj, 2, -1, 1.0) < 1e-6)
check("재실행 시 할 일 없음", cleanup.resolve_coplanar_faces([obj]) == 0)
bm = bmesh.new()
bm.from_mesh(obj.data)
check("열린 테두리 0 (수밀)", sum(1 for e in bm.edges if e.is_boundary) == 0,
      f"({sum(1 for e in bm.edges if e.is_boundary)})")
check("3면 공유 변 0", sum(1 for e in bm.edges if len(e.link_faces) > 2) == 0)
bm.free()

# 2) 벽 앞면에 붙인 패널 — 서로 다른 색 UV, 패널 색이 남아야 한다
new_scene()
wall = make("Wall", [((0, 0, 0), (4, 0.3, 3), (0.1, 0.1)), ((1, 0, 1), (2, 0.1, 2), (0.9, 0.9))])
changed = cleanup.resolve_coplanar_faces([wall])
front = facing_area(wall, 1, -1, 0.0)
check("플러시 면 정리", changed >= 1, f"({changed})")
check("앞면 총면적 보존(12)", abs(front - 12.0) < 1e-4, f"({front:.4f})")
layer = wall.data.uv_layers.active.data
panel_uv = [p for p in wall.data.polygons
            if p.normal.y < -0.999 and abs(p.center.y) < 1e-5 and 1 < p.center.x < 2 and 1 < p.center.z < 2]
check("패널 앞면 색 유지", panel_uv and all(abs(layer[i].uv[0] - 0.9) < 1e-6
                                         for p in panel_uv for i in p.loop_indices))
wall_uv = [p for p in wall.data.polygons if p.normal.y < -0.999 and abs(p.center.y) < 1e-5 and p.center.x > 2.1]
check("벽 조각 색 유지", wall_uv and all(abs(layer[i].uv[0] - 0.1) < 1e-6
                                      for p in wall_uv for i in p.loop_indices))
check("재실행 시 할 일 없음(플러시)", cleanup.resolve_coplanar_faces([wall]) == 0)

# 3) 서로 다른 오브젝트 + 회전·음수 스케일 트랜스폼
new_scene()
rot = Matrix.Rotation(0.6, 4, 'Z') @ Matrix.Rotation(0.3, 4, 'X')
a = make("A", [((-1, -1, -1), (1, 1, 1))], rot)
b = make("B", [((-1, -1, -1), (1, 1, 1))], rot @ Matrix.Translation((2, 0, 0)) @ Matrix.Diagonal((-1, 1, 1, 1)))
changed = cleanup.resolve_coplanar_faces([a, b])
check("회전·미러 오브젝트 접촉 면 정리", changed == 2, f"({changed})")
check("면 수 5+5", (len(a.data.polygons), len(b.data.polygons)) == (5, 5),
      f"({len(a.data.polygons)}, {len(b.data.polygons)})")
bm = bmesh.new()
bm.from_mesh(b.data)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
normals_before = [f.normal.copy() for f in bm.faces]
bm.free()
check("미러 오브젝트 노멀 유지", all(p.normal.dot(n) > 0.99 for p, n in zip(b.data.polygons, normals_before)))

# 4) 인스턴스(공유 메시)는 자르지 않는다
new_scene()
src = make("Src", [((0, 0, 0), (1, 1, 1))])
inst = src.copy()
bpy.context.scene.collection.objects.link(inst)
inst.location = (5, 0, 0)
slab = make("Slab", [((-1, -1, -1), (7, 2, 0))])
bpy.context.view_layer.update()
before = len(src.data.polygons)
changed = cleanup.resolve_coplanar_faces([src, inst, slab])
check("인스턴스 메시 불변", len(src.data.polygons) == before)
check("받침 윗면에서 두 자리 오림", abs(facing_area(slab, 2, 1, 0.0) - (8 * 3 - 2)) < 1e-4,
      f"({facing_area(slab, 2, 1, 0.0):.4f})")

# 5) 관통만 하는 박스는 건드리지 않는다
new_scene()
obj = make("Pierce", [((0, 0, 0), (1, 1, 1)), ((0.5, 0.2, 0.2), (1.5, 0.8, 0.8))])
count = len(obj.data.polygons)
check("관통 박스 불변", cleanup.resolve_coplanar_faces([obj]) == 0 and len(obj.data.polygons) == count)

print("RESULT:", "FAIL " + ", ".join(failures) if failures else "ALL PASS")
