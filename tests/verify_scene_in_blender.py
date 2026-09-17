# 씬 헬퍼(lowpoly/scene.py) 동작 확인 — Blender 안에서 실행한다.
#   blender --background --factory-startup --python tests/verify_scene_in_blender.py
#   /Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
#       --python tests/verify_scene_in_blender.py
#
# bmesh/matrix_world/ray_cast는 순수 파이썬 테스트로 재현할 수 없어 별도 스크립트로 둔다
# (tests/verify_geometry_in_blender.py와 같은 방식). 성공하면 "RESULT: ALL PASS"를 출력한다.
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lowpoly as lp  # noqa: E402

failures = []
_scene_index = [0]


def check(label, ok):
    print(f"{'PASS' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(label)


def close(label, actual, expect, tol):
    ok = abs(actual - expect) <= tol
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual:.4f} (기대 {expect:.4f} ±{tol})")
    if not ok:
        failures.append(label)


def new_scene():
    """빈 씬 + 매번 새로운 세션 컬렉션 (이전 테스트의 오브젝트 격리)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _scene_index[0] += 1
    lp.set_session(f"VERIFY_{_scene_index[0]}")
    lp.set_kit_collection(None)


def bbox_world(obj):
    """오브젝트 바운딩 박스를 월드 좌표 (minx, miny, minz, maxx, maxy, maxz)로 반환."""
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return (min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts),
            max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))


# --- terrain: 면수 상한, 가장자리 z=0, 셀 클램프 ---
new_scene()
ground = lp.terrain("Ground", size=(40.0, 40.0), cells=(16, 16), relief=0.6, seed=3)
check("terrain 면수 == 셀 곱", len(ground.data.polygons) == 16 * 16)
edge_z = [v.co.z for v in ground.data.vertices
          if abs(abs(v.co.x) - 20.0) < 1e-4 or abs(abs(v.co.y) - 20.0) < 1e-4]
check("terrain 가장자리 z≈0", edge_z and max(abs(z) for z in edge_z) < 1e-4)
inner_z = [v.co.z for v in ground.data.vertices]
check("terrain 릴리프가 relief 범위 안", (max(inner_z) - min(inner_z)) <= 0.6 + 1e-4)
check("terrain 릴리프가 평평하지 않음", (max(inner_z) - min(inner_z)) > 0.1)
check("terrain 플랫 셰이딩", all(not p.use_smooth for p in ground.data.polygons))
big = lp.terrain("Big", size=(80.0, 80.0), cells=(200, 200))
check("terrain 셀 수 48 클램프", len(big.data.polygons) == 48 * 48)
# 에이전트가 heights 자리만 채워 보내는 일이 흔하다 — 터지지 말고 노이즈로 되돌아가야 한다
for bad in ([], [[]], [[None]], "높이", 3):
    try:
        junk = lp.terrain("Junk", size=(4.0, 4.0), cells=(2, 2), heights=bad, relief=0.3)
        ok = len(junk.data.polygons) == 4
    except Exception:
        ok = False
    check(f"terrain 불량 heights 무시 ({bad!r})", ok)

# --- instance: 메시 공유 ---
new_scene()
src = lp.box("Barrel", size=(1, 1, 1))
inst = lp.instance(src, location=(3.0, -2.0, 0.0), rotation_z=45.0, scale=2.0)
check("instance 메시 공유(data.users>1)", src.data.users > 1)
check("instance는 원본과 다른 오브젝트", inst is not src and inst.data is src.data)
close("instance 회전(도→라디안)", inst.rotation_euler.z, math.radians(45.0), 1e-5)
close("instance 스케일", inst.scale.x, 2.0, 1e-6)
check("instance 세션 컬렉션 링크", lp.root() in list(inst.users_collection))

# --- place_grid ---
new_scene()
src = lp.box("Tent", size=(1, 1, 1))
grid = lp.place_grid(src, 4, 3, spacing=(5.0, 6.0), origin=(2.0, 0.0),
                     jitter=0.0, rotate_jitter=0.0, seed=1)
check("place_grid 개수 == cols*rows", len(grid) == 12)
xs = sorted({round(o.location.x, 4) for o in grid})
check("place_grid 간격 dx", len(xs) == 4 and abs((xs[1] - xs[0]) - 5.0) < 1e-4)
close("place_grid 중심 = origin", sum(o.location.x for o in grid) / 12, 2.0, 1e-4)
check("place_grid 메시 공유", src.data.users == 13)
jittered = lp.place_grid(src, 3, 3, spacing=4.0, jitter=0.5, rotate_jitter=10.0, seed=7)
check("place_grid 지터가 위치를 흔든다",
      any(abs(o.location.x % 4.0) > 1e-6 for o in jittered))
check("place_grid 회전 지터 범위",
      all(abs(math.degrees(o.rotation_euler.z)) <= 10.0 + 1e-6 for o in jittered))

# --- place_along ---
new_scene()
src = lp.box("Lamp", size=(0.3, 0.3, 2.0))
line = lp.place_along(src, [(0.0, 0.0), (10.0, 0.0)], spacing=2.5)
check("place_along 개수(길이 10 / 간격 2.5)", len(line) == 5)
close("place_along 시작점", line[0].location.x, 0.0, 1e-6)
close("place_along 끝점", line[-1].location.x, 10.0, 1e-6)
check("place_along 직선 정렬 0도",
      all(abs(o.rotation_euler.z) < 1e-6 for o in line))
corner = lp.place_along(src, [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], spacing=2.0)
check("place_along 꺾인 뒤 90도 정렬",
      abs(math.degrees(corner[-1].rotation_euler.z) - 90.0) < 1e-4)
offset = lp.place_along(src, [(0.0, 0.0), (10.0, 0.0)], spacing=5.0, rotate_offset=90.0)
check("place_along rotate_offset 반영",
      abs(math.degrees(offset[0].rotation_euler.z) - 90.0) < 1e-4)
check("place_along align=False면 회전 없음",
      all(abs(o.rotation_euler.z) < 1e-6
          for o in lp.place_along(src, [(0.0, 0.0), (0.0, 8.0)], spacing=4.0, align=False)))

# --- place_scatter: avoid 영역 회피 + min_dist ---
new_scene()
src = lp.box("Rock", size=(0.6, 0.6, 0.6))
avoid = [(0.0, 0.0, 12.0, 10.0)]
rocks = lp.place_scatter(src, 24, area=(36.0, 36.0), center=(0.0, 0.0),
                         avoid=avoid, min_dist=2.0, scale_jitter=0.3, seed=11)
check("place_scatter 개수 상한", 0 < len(rocks) <= 24)
check("place_scatter 영역 안",
      all(abs(o.location.x) <= 18.0 + 1e-6 and abs(o.location.y) <= 18.0 + 1e-6
          for o in rocks))
check("place_scatter avoid 직사각형 회피",
      all(not (abs(o.location.x) <= 6.0 and abs(o.location.y) <= 5.0) for o in rocks))
pairs_ok = True
for i, a in enumerate(rocks):
    for b in rocks[i + 1:]:
        if (a.location.xy - b.location.xy).length < 2.0 - 1e-6:
            pairs_ok = False
check("place_scatter min_dist 준수", pairs_ok)
check("place_scatter 스케일 지터 범위",
      all(0.7 - 1e-6 <= o.scale.x <= 1.3 + 1e-6 for o in rocks))
crowded = lp.place_scatter(src, 500, area=(4.0, 4.0), min_dist=3.0, seed=2)
check("place_scatter 시도 상한으로 종료(무한루프 없음)", len(crowded) < 500)

# --- wall_run ---
new_scene()
wall = lp.wall_run([(-12.0, -12.0), (12.0, -12.0), (12.0, 12.0), (-12.0, 12.0)],
                   height=4.0, thickness=0.6, name="Rampart",
                   closed=True, post_size=1.2)
check("wall_run 단일 오브젝트 반환", isinstance(wall, bpy.types.Object))
check("wall_run 결과가 컬렉션에 1개만", len(lp.root().objects) == 1)
wmin_x, wmin_y, wmin_z, wmax_x, wmax_y, wmax_z = bbox_world(wall)
close("wall_run 바닥 z=0", wmin_z, 0.0, 1e-5)
close("wall_run 꼭대기 = height*1.15 (기둥)", wmax_z, 4.0 * 1.15, 1e-5)
# 닫힌 사각 둘레 4세그먼트 + 꼭짓점 기둥 4개 = 박스 8개 = 정점 64개
check("wall_run 세그먼트+기둥 병합 정점 수", len(wall.data.vertices) == 8 * 8)
open_wall = lp.wall_run([(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)], height=3.0, thickness=0.4)
check("wall_run 열린 폴리라인 = 2세그먼트", len(open_wall.data.vertices) == 2 * 8)

# --- path_strip ---
new_scene()
road = lp.path_strip([(-18.0, 0.0), (-4.0, 2.0), (6.0, -3.0), (18.0, 0.0)],
                     width=3.0, thickness=0.05, name="Road")
pmin_x, pmin_y, pmin_z, pmax_x, pmax_y, pmax_z = bbox_world(road)
close("path_strip 두께", pmax_z - pmin_z, 0.05, 1e-5)
close("path_strip 바닥 z=0", pmin_z, 0.0, 1e-5)
thick = lp.path_strip([(0.0, 0.0), (10.0, 0.0)], width=2.0, thickness=0.5)
tmin_z, tmax_z = bbox_world(thick)[2], bbox_world(thick)[5]
close("path_strip 두께 상한 클램프(0.08)", tmax_z - tmin_z, 0.08, 1e-5)
straight = lp.path_strip([(0.0, 0.0), (10.0, 0.0)], width=3.0)
smin_y, smax_y = bbox_world(straight)[1], bbox_world(straight)[4]
close("path_strip 폭", smax_y - smin_y, 3.0, 1e-5)
check("path_strip 플랫 셰이딩", all(not p.use_smooth for p in road.data.polygons))

# --- ground_snap ---
new_scene()
# x = -5, 0, 5 위치에 각각 높이 0, 1, 2인 경사 지형 (heights[행][열])
slope = lp.terrain("Slope", size=(10.0, 10.0), cells=(2, 2),
                   heights=[[0.0, 1.0, 2.0]] * 3)
src = lp.box("Crate", size=(0.5, 0.5, 0.5))
a = lp.instance(src, location=(0.0, 0.0, 9.0))
b = lp.instance(src, location=(2.5, 1.0, -4.0))
outside = lp.instance(src, location=(40.0, 0.0, 7.0))
lp.ground_snap([a, b, outside], slope)
close("ground_snap 지형 중앙 높이", a.location.z, 1.0, 0.01)
close("ground_snap 보간 지점 높이", b.location.z, 1.5, 0.01)
close("ground_snap 히트 없으면 z 유지", outside.location.z, 7.0, 1e-6)
single = lp.instance(src, location=(-5.0, 0.0, 12.0))
lp.ground_snap(single, slope)  # 단일 오브젝트도 허용
close("ground_snap 단일 오브젝트 인자", single.location.z, 0.0, 0.01)

# --- kit ---
new_scene()
lp.box("barrel", size=(1, 1, 1))
lp.box("tent_large", size=(2, 2, 2))
check("kit 정확 이름 일치", lp.kit("barrel").name == "barrel")
check("kit 접두어 일치", lp.kit("tent").name == "tent_large")
try:
    lp.kit("dragon")
    check("kit 없는 이름은 KeyError", False)
except KeyError as exc:
    message = str(exc)
    check("kit KeyError에 사용 가능한 이름 목록 포함",
          "barrel" in message and "tent_large" in message)

# 지정한 키트 컬렉션에서 조회
kit_coll = bpy.data.collections.new("VERIFY_Kit")
bpy.context.scene.collection.children.link(kit_coll)
kit_obj = bpy.data.objects.new("anvil", bpy.data.meshes.new("anvil"))
kit_coll.objects.link(kit_obj)
lp.set_kit_collection("VERIFY_Kit")
check("set_kit_collection 조회", lp.kit("anvil") is kit_obj)
try:
    lp.kit("barrel")  # 세션 컬렉션에만 있으므로 키트에서는 못 찾아야 한다
    check("키트 컬렉션 지정 시 세션 오브젝트 미노출", False)
except KeyError:
    check("키트 컬렉션 지정 시 세션 오브젝트 미노출", True)
lp.set_kit_collection(None)

# --- SCENE_API 노출 계약 ---
check("SCENE_API 11개 함수", len(lp.SCENE_API) == 11)   # + room, fence_run
check("SCENE_API 전부 접근 가능", all(callable(getattr(lp, n)) for n in lp.SCENE_API))
check("씬 헬퍼는 __all__(오브젝트 모드)에 미노출",
      not any(n in lp.__all__ for n in lp.SCENE_API))

if failures:
    print(f"RESULT: FAIL ({len(failures)}): {failures}")
    raise SystemExit(1)
print("RESULT: ALL PASS")
