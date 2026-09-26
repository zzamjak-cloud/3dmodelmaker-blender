# 동일평면 겹침(Z-fighting) 띄우기 확인 — Blender 안에서 실행한다.
#   blender --background --factory-startup --python tests/verify_coplanar_in_blender.py
#
# 같은 방향으로 겹친 면의 좁은 쪽 파트만 노멀 방향으로 옮겨지는지, 면 수·토폴로지가 그대로인지,
# 맞댄 면은 건드리지 않는지, 다시 돌리면 할 일이 없는지 본다.
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lowpoly import cleanup, coplanar  # noqa: E402

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


def part_min(obj, axis, pick):
    """pick(월드 좌표)을 만족하는 꼭짓점들의 axis 최솟값."""
    mw = obj.matrix_world
    return min((mw @ v.co)[axis] for v in obj.data.vertices if pick(mw @ v.co))


def topology(obj):
    return len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)


# 1) 받침 위 상자 (맞댄 면) — 아무것도 하지 않는다
new_scene()
obj = make("Stack", [((0, 0, 0), (2, 2, 1)), ((0.5, 0.5, 1), (1.5, 1.5, 2))])
before = [v.co.copy() for v in obj.data.vertices]
check("맞댄 면 불변", cleanup.resolve_coplanar_faces([obj]) == 0
      and all((v.co - b).length == 0 for v, b in zip(obj.data.vertices, before)))

# 2) 벽 앞면에 붙인 패널 + 그 위 명판 (한 오브젝트, 느슨한 파트 3개)
new_scene()
wall = make("Wall", [((0, 0, 0), (4, 0.3, 3)), ((1, 0, 1), (2, 0.1, 2)), ((1.2, 0, 1.2), (1.5, 0.05, 1.5))])
topo = topology(wall)
step = coplanar.nudge_distance(cleanup._coplanar_scale([wall]))
changed = cleanup.resolve_coplanar_faces([wall])
check("플러시 파트 2개 이동", changed == 2, f"({changed})")
check("면·변·점 수 불변", topology(wall) == topo, f"({topology(wall)} vs {topo})")
wall_y = part_min(wall, 1, lambda p: p.x < 0.5 or p.x > 2.5)
panel_y = part_min(wall, 1, lambda p: 1 <= p.x <= 2 and 1 <= p.z <= 2 and not (1.2 <= p.x <= 1.5 and 1.2 <= p.z <= 1.5))
plate_y = part_min(wall, 1, lambda p: 1.2 <= p.x <= 1.5 and 1.2 <= p.z <= 1.5 and p.y < 0.06)
check("벽 제자리", abs(wall_y) < 1e-6, f"({wall_y})")
check("패널 한 단 앞", abs(panel_y + step) < 1e-6, f"({panel_y})")
check("명판 두 단 앞", abs(plate_y + 2 * step) < 1e-6, f"({plate_y})")
check("재실행 시 할 일 없음", cleanup.resolve_coplanar_faces([wall]) == 0)

# 3) 서로 다른 오브젝트 + 회전·음수 스케일 — 월드 노멀 방향으로 밀린다
new_scene()
rot = Matrix.Rotation(0.6, 4, 'Z') @ Matrix.Rotation(0.3, 4, 'X')
a = make("A", [((-1, -1, -1), (1, 1, 1))], rot)
b = make("B", [((-0.5, -0.5, 0.5), (0.5, 0.5, 1))], rot @ Matrix.Diagonal((-1, 1, 1, 1)))
top = (rot.to_3x3() @ Vector((0, 0, 1))).normalized()
ref = (b.matrix_world @ b.data.vertices[0].co).copy()
changed = cleanup.resolve_coplanar_faces([a, b])
moved = (b.matrix_world @ b.data.vertices[0].co) - ref
check("회전·미러 오브젝트 이동", changed == 1, f"({changed})")
check("월드 윗면 노멀 방향", moved.normalized().dot(top) > 0.999, f"({moved})")

# 4) 인스턴스(공유 메시)는 움직이지 않는다
new_scene()
src = make("Src", [((1, 0, 1), (2, 0.1, 2))])
inst = src.copy()
bpy.context.scene.collection.objects.link(inst)
inst.location = (1.5, 0, 0)
slab = make("Slab", [((0, 0, 0), (4, 0.3, 3))])
bpy.context.view_layer.update()
co = [v.co.copy() for v in src.data.vertices]
cleanup.resolve_coplanar_faces([src, inst, slab])
check("인스턴스 메시 불변", all((v.co - c).length == 0 for v, c in zip(src.data.vertices, co)))
check("상대 벽이 안쪽으로 물러남", part_min(slab, 1, lambda p: True) > 1e-4)

# 5) 관통만 하는 박스는 건드리지 않는다
new_scene()
obj = make("Pierce", [((0, 0, 0), (1, 1, 1)), ((0.5, 0.2, 0.2), (1.5, 0.8, 0.8))])
check("관통 박스 불변", cleanup.resolve_coplanar_faces([obj]) == 0)

print("RESULT:", "FAIL " + ", ".join(failures) if failures else "ALL PASS")
