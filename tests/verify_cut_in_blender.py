# lp.cut 회귀 확인 — Blender 안에서 실행한다.
#   blender --background --factory-startup --python tests/verify_cut_in_blender.py
#
# 띠를 emboss 한 몸통에 틀 있는 문을 판 뒤(자기 교차가 생긴다) 얇은 홈(0.012)을 다시 파면, EXACT 차집합이
# use_self 없이 몸통을 통째로 지우거나 벽 전체를 홈 색으로 칠하던 문제를 본다. 불변식을 어기는 결과는
# 대상을 건드리지 않고 건너뛰는지도 본다.
import math
import os
import sys
import time
from collections import Counter

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lowpoly as lp  # noqa: E402
from lowpoly import boolean_check  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {label} {detail}")
    if not ok:
        failures.append(label)


PLASTER, STONE, WOOD, DARK = (0.88, 0.76, 0.52), (0.48, 0.43, 0.31), (0.30, 0.17, 0.065), (0.075, 0.15, 0.17)


def color_area(obj):
    """팔레트 칸(UV)별 면적."""
    uv = obj.data.uv_layers.active.data
    areas = Counter()
    for poly in obj.data.polygons:
        u, v = uv[poly.loop_indices[0]].uv
        areas[(round(u, 4), round(v, 4))] += poly.area
    return areas


def cell(color):
    from lowpoly.colorsnap import cell_uv, snap_cell
    return tuple(round(c, 4) for c in cell_uv(snap_cell(color)))


def arch(bottom, spring, radius):
    return [(-radius, bottom), (radius, bottom)] + [
        (radius * math.cos(i * math.pi / 8), spring + radius * math.sin(i * math.pi / 8)) for i in range(9)]


def house_body():
    body = lp.silhouette("Body", front=[(0, 0.065), (1.32, 0.065), (1.32, 2.68), (0, 4.08)],
                         side=[(-1.3, 0.065), (1.3, 0.065), (1.3, 4.1), (-1.3, 4.1)])
    lp.set_color(body, PLASTER)
    lp.mirror_x(body)
    lp.set_color(body, STONE, below=0.48)
    for z in (0.48, 2.55):
        lp.emboss(body, lp.box("Band", size=(2.9, 2.9, 0.17), location=(0, 0, z)), height=0.045, color=WOOD)
    door = lp.set_color(lp.prism("Door", outline=arch(0.32, 1.56, 0.55), depth=0.5, location=(0, -1.3, 0)), WOOD)
    lp.cut(body, door, depth=0.065, frame=0.125, frame_color=STONE)
    return body


def groove(x):
    top = 1.56 + math.sqrt(max(0.0, 0.41 ** 2 - x * x))
    return lp.set_color(lp.box("Groove", size=(0.012, 0.36, top - 0.48), location=(x, -1.31, (top + 0.48) / 2)), DARK)


def test_thin_groove_after_framed_door():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body = house_body()
    before = color_area(body)
    faces = len(body.data.polygons)
    started = time.time()
    for x in (-0.3, -0.15, 0.0, 0.15, 0.3):
        lp.cut(body, groove(x), depth=0.008)
    elapsed = time.time() - started
    after = color_area(body)
    plaster = cell(PLASTER)
    check("몸통이 남는다", len(body.data.polygons) > faces, f"faces {faces} -> {len(body.data.polygons)}")
    check("회벽 면적이 그대로다", abs(after[plaster] - before[plaster]) < 0.01 * before[plaster],
          f"{before[plaster]:.3f} -> {after[plaster]:.3f}")
    dark = after[cell(DARK)]
    check("홈 색은 홈 바닥에만", 0.0 < dark < 0.5, f"dark area {dark:.3f}")
    check("cutter가 지워진다", not [o for o in bpy.data.objects if o.name.startswith("Groove")])
    check("홈 5개가 30초 안에 끝난다", elapsed < 30.0, f"{elapsed:.1f}s")


def test_thin_groove_on_plain_box():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body = lp.set_color(lp.box("Wall", size=(2.6, 2.6, 2.6), location=(0, 0, 1.3)), PLASTER)
    lp.cut(body, lp.set_color(lp.box("Groove", size=(0.012, 0.36, 1.0), location=(0, -1.31, 1.0)), DARK), depth=0.008)
    areas = color_area(body)
    check("박스 얇은 홈", 0.0 < areas[cell(DARK)] < 0.05 and areas[cell(PLASTER)] > 40.0, str(dict(areas)))


def test_rejected_result_keeps_target():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body = lp.set_color(lp.box("Wall", size=(2.0, 2.0, 2.0), location=(0, 0, 1.0)), PLASTER)
    mesh_before = [tuple(v.co) for v in body.data.vertices]
    original = boolean_check.difference_ok
    boolean_check.difference_ok = lambda *args, **kwargs: False
    try:
        lp.cut(body, lp.box("Hole", size=(0.5, 3.0, 0.5), location=(0, 0, 1.0)))
        lp.cut(body, lp.box("Slot", size=(0.5, 3.0, 0.5), location=(0, -1.0, 1.0)), depth=0.05)
    finally:
        boolean_check.difference_ok = original
    check("거부된 cut은 대상을 그대로 둔다", [tuple(v.co) for v in body.data.vertices] == mesh_before)
    check("거부돼도 cutter는 지워진다", "Hole" not in bpy.data.objects and "Slot" not in bpy.data.objects)


for test in (test_thin_groove_after_framed_door, test_thin_groove_on_plain_box, test_rejected_result_keeps_target):
    test()

if failures:
    print(f"FAILED {len(failures)}: {failures}")
    sys.exit(1)
print("ALL PASS")
