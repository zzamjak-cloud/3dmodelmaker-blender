# 단일 껍질 헬퍼(silhouette/loft/cut) 동작 확인 — Blender 안에서 실행한다.
#   blender --background --factory-startup --python tests/verify_hull_in_blender.py
import os
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lowpoly as lp  # noqa: E402
from lowpoly.colorsnap import cell_uv, snap_cell  # noqa: E402

failures = []


def check(label, actual, expect, cmp="eq"):
    if cmp == "eq":
        ok = actual == expect
    elif cmp == "ge":
        ok = actual >= expect
    elif cmp == "le":
        ok = actual <= expect
    else:  # "approx"
        ok = abs(actual - expect) <= 1e-3
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual} (기대 {cmp} {expect})")
    if not ok:
        failures.append(label)


def new_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    lp.set_session("LP3D_Model")


def stats(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    closed = all(len(e.link_faces) == 2 for e in bm.edges)
    volume = bm.calc_volume(signed=True)
    tris = sum(len(f.verts) - 2 for f in bm.faces)
    xs, ys, zs = ([v.co[i] for v in bm.verts] for i in range(3))
    bm.free()
    return closed, volume, tris, (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def model_objects():
    return list(lp.root().objects)


# --- silhouette: 세 윤곽 교차 차체 ---
new_scene()
car = lp.silhouette(
    "Car",
    front=[(-0.8, 0.15), (0.8, 0.15), (0.8, 0.7), (0.6, 1.25), (-0.6, 1.25), (-0.8, 0.7)],
    side=[(-1.95, 0.15), (1.95, 0.15), (1.95, 0.7), (1.6, 0.8), (1.2, 1.25), (-0.4, 1.25),
          (-1.0, 0.8), (-1.95, 0.75)],
    top=[(-0.7, -1.95), (0.7, -1.95), (0.8, -1.4), (0.8, 1.6), (0.7, 1.95), (-0.7, 1.95),
         (-0.8, 1.6), (-0.8, -1.4)])
closed, volume, tris, bx, by, bz = stats(car)
check("silhouette 수밀", closed, True)
check("silhouette 바깥 노멀(양의 부피)", volume > 0, True)
check("silhouette 커터 제거", len(model_objects()), 1)
check("silhouette x 범위", round(bx[1], 3), 0.8, "approx")
check("silhouette y 범위", round(by[0], 3), -1.95, "approx")
check("silhouette z 상단", round(bz[1], 3), 1.25, "approx")
check("silhouette 로우폴리 트라이 수", tris, 200, "le")

new_scene()
plain = lp.silhouette("Plain", front=[(-1, 0), (1, 0), (1, 1), (-1, 1)],
                      side=[(-0.5, 0), (0.5, 0), (0.5, 1), (-0.5, 1)])
closed, volume, tris, bx, by, bz = stats(plain)
check("직육면체 교차 = 박스 트라이 12", tris, 12)
check("직육면체 교차 부피", round(volume, 3), 2.0, "approx")

# --- loft ---
new_scene()
rect = lp.loft("Rect", sections=[(0, 1.0, 0.6), (2, 1.0, 0.6)], segments=4)
closed, volume, tris, bx, by, bz = stats(rect)
check("loft segments=4 = 정확한 w×d", (round(bx[1] - bx[0], 3), round(by[1] - by[0], 3)), (1.0, 0.6))
check("loft 사각 부피", round(volume, 3), 1.2, "approx")

new_scene()
fish = lp.loft("Fish", axis='Y', segments=6, smooth=1, sections=[
    (-0.5, 0.0, 0.0, 0, 0.3), (-0.3, 0.16, 0.3, 0, 0.3), (0.1, 0.2, 0.36, 0, 0.32),
    (0.45, 0.04, 0.14, 0, 0.3)])
closed, volume, tris, bx, by, bz = stats(fish)
check("loft 극점+smooth 수밀", closed, True)
check("loft 바깥 노멀", volume > 0, True)
check("loft axis=Y 길이", (round(by[0], 3), round(by[1], 3)), (-0.5, 0.45))

new_scene()
custom = lp.loft("Custom", sections=[(0, [(-1, -1), (1, -1), (1, 1), (-1, 1)]),
                                     (1, [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)])])
closed, volume, tris, bx, by, bz = stats(custom)
check("loft 직접 윤곽 절두체 부피", round(volume, 3), round((4 + 1 + 2) / 3, 3), "approx")

try:
    lp.loft("Bad", sections=[(0, [(0, 0), (1, 0), (1, 1)]), (1, [(0, 0), (1, 0), (1, 1), (0, 1)])])
    check("loft 점 개수 불일치 예외", False, True)
except ValueError:
    check("loft 점 개수 불일치 예외", True, True)

# --- cut: 관통 홈 + 커터 색 전이 ---
new_scene()
block = lp.box("Block", size=(1, 1, 1), location=(0, 0, 0.5))
lp.set_color(block, (0.9, 0.85, 0.7))
hole = lp.box("Hole", size=(0.4, 2.0, 0.4), location=(0, 0, 0.5))
lp.set_color(hole, (0.1, 0.1, 0.12))
lp.cut(block, hole)
closed, volume, tris, bx, by, bz = stats(block)
check("cut 수밀", closed, True)
check("cut 부피 1 - 0.16", round(volume, 3), 0.84, "approx")
check("cut 커터 제거", len(model_objects()), 1)
dark = cell_uv(snap_cell((0.1, 0.1, 0.12)))
uv = block.data.uv_layers.active.data
# 폴리곤 중심은 로컬 좌표 (오브젝트 원점 z=0.5)
inner = [p for p in block.data.polygons if abs(p.center.x) < 0.21 and abs(p.center.z) < 0.21
         and abs(p.center.y) < 0.49]
check("cut 안쪽 면 4개", len(inner), 4)
check("cut 안쪽 면이 커터 색", all(
    abs(uv[i].uv[0] - dark[0]) < 1e-4 and abs(uv[i].uv[1] - dark[1]) < 1e-4
    for p in inner for i in p.loop_indices), True)

new_scene()
mid = [(0, 1, 1), (1, 0, 0), (2, 1, 1)]
try:
    lp.loft("MidPole", sections=mid)
    check("loft 중간 극점 예외", False, True)
except ValueError:
    check("loft 중간 극점 예외", True, True)
block = lp.box("Block2")
try:
    lp.cut(block, lp.plane("Sheet"))
    check("cut 열린 커터 예외", False, True)
except ValueError:
    check("cut 열린 커터 예외", True, True)

# --- cut depth: 경사면에서 홈 바닥이 표면과 나란한지 ---
new_scene()
wedge = lp.prism("Wedge", outline=[(-1, 0), (1, 0), (0.4, 1.5), (-0.4, 1.5)], depth=1.0, axis='X')  # y-z 경사 벽
lp.set_color(wedge, (0.85, 0.25, 0.22))
win = lp.prism("Win", outline=[(-0.3, 0.4), (0.3, 0.4), (0.3, 1.0), (-0.3, 1.0)], depth=0.6, axis='Y',
               location=(0, -0.7, 0))                 # 경사면(-y 쪽)을 앞뒤로 관통
lp.set_color(win, (0.55, 0.75, 0.85))
before = stats(wedge)[1]
lp.cut(wedge, win, depth=0.04)
closed, volume, tris, bx, by, bz = stats(wedge)
check("cut depth 수밀", closed, True)
check("cut depth는 얕게만 판다", round(before - volume, 3), 0.03, "le")
check("cut depth 부피 감소", before - volume > 0.005, True)
bm = bmesh.new(); bm.from_mesh(wedge.data)
uvl = bm.loops.layers.uv.active
glass = cell_uv(snap_cell((0.55, 0.75, 0.85)))
slope_n = None
floors = [f for f in bm.faces if abs(f.loops[0][uvl].uv[0] - glass[0]) < 1e-4
          and abs(f.loops[0][uvl].uv[1] - glass[1]) < 1e-4 and f.normal.y < -0.3]
check("홈 바닥(유리색) 면 존재", len(floors) >= 1, True)
outer = [f for f in bm.faces if f.normal.y < -0.3 and f not in floors]
if floors and outer:
    check("홈 바닥이 경사면과 나란", round(floors[0].normal.dot(outer[0].normal), 4), 0.9999, "ge")
bm.free()

new_scene()
wall = lp.box("Wall", size=(2, 0.4, 2), location=(0, 0, 1))
lp.set_color(wall, (0.9, 0.85, 0.7))
w2 = lp.box("W2", size=(0.6, 0.5, 0.8), location=(0, -0.25, 1))  # 앞면만 관통
lp.set_color(w2, (0.55, 0.75, 0.85))
lp.cut(wall, w2, depth=0.05, frame=0.05, frame_color=(0.1, 0.1, 0.1))
closed, volume, tris, bx, by, bz = stats(wall)
check("frame cut 수밀", closed, True)
expect = 0.6 * 0.8 * 0.02 + 0.5 * 0.7 * 0.03     # 틀 단(0.4×depth) + 안쪽 단
check("frame cut 두 단 부피", round(1.6 - volume, 4), round(expect, 4), "approx")
check("frame cut 임시 오브젝트 정리", len(model_objects()), 1)
dark = cell_uv(snap_cell((0.1, 0.1, 0.1)))
uv = wall.data.uv_layers.active.data
ring = [p for p in wall.data.polygons if abs(uv[p.loop_indices[0]].uv[0] - dark[0]) < 1e-4
        and abs(uv[p.loop_indices[0]].uv[1] - dark[1]) < 1e-4]
check("창틀 색 면 존재", len(ring) >= 4, True)

# --- loft spine: 휜 꼬리 ---
new_scene()
tail = lp.loft("Tail", segments=6, smooth=1, axis='Y',
               sections=[(0, 0.2, 0.2), (0, 0.3, 0.3), (0, 0.2, 0.2), (0, 0, 0)],
               spine=[(0, 0, 0.3), (0, 0.4, 0.4), (0, 0.7, 0.8), (0, 0.75, 1.2)])
closed, volume, tris, bx, by, bz = stats(tail)
check("spine loft 수밀", closed, True)
check("spine loft 바깥 노멀", volume > 0, True)
check("spine loft 끝점 높이", round(bz[1], 3), 1.2, "approx")
try:
    lp.loft("BadSpine", sections=[(0, 1, 1), (1, 1, 1)], spine=[(0, 0, 0)])
    check("spine 개수 불일치 예외", False, True)
except ValueError:
    check("spine 개수 불일치 예외", True, True)

# --- set_color 범위 ---
new_scene()
leg = lp.loft("Leg", segments=6, sections=[(0, 0.1, 0.1), (0.25, 0.1, 0.1), (0.6, 0.12, 0.12)],
              location=(0, 0, 0.1))
lp.set_color(leg, (0.85, 0.4, 0.15))
lp.set_color(leg, (0.1, 0.08, 0.08), below=0.35)
sock = cell_uv(snap_cell((0.1, 0.08, 0.08)))
uv = leg.data.uv_layers.active.data
painted = [p for p in leg.data.polygons
           if abs(uv[p.loop_indices[0]].uv[0] - sock[0]) < 1e-4 and abs(uv[p.loop_indices[0]].uv[1] - sock[1]) < 1e-4]
check("범위 칠하기: 아래 링+바닥캡만", len(painted), 6 + 1)

# --- union: 겹친 셸을 품은(mirror된) 피연산자도 한 셸로 ---
def shells(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data); bm.verts.ensure_lookup_table()
    seen, count = set(), 0
    for v in bm.verts:
        if v.index in seen:
            continue
        count += 1
        stack = [v]
        while stack:
            x = stack.pop()
            if x.index not in seen:
                seen.add(x.index)
                stack.extend(e.other_vert(x) for e in x.link_edges)
    bm.free()
    return count


new_scene()
torso = lp.loft("Torso", sections=[(0.2, 0.5, 0.4), (0.9, 0.4, 0.3)])
leg = lp.loft("LegR", sections=[(0.0, 0.12, 0.12, 0.15, 0), (0.4, 0.14, 0.14, 0.15, 0)])
paw = lp.box("PawR", size=(0.14, 0.2, 0.08), location=(0.15, -0.05, 0.04))
limbs = lp.join([leg, paw], name="Limbs")         # 겹친 두 셸을 품은 오브젝트
lp.mirror_x(limbs)
animal = lp.join([torso, limbs], name="Animal", mode='union')
closed, volume, tris, bx, by, bz = stats(animal)
check("유기체 union 단일 셸", shells(animal), 1)
check("유기체 union 수밀", closed, True)
check("유기체 union 몸통 보존(상단 높이)", round(bz[1], 3), 0.9, "approx")

# --- emboss: 띠·둘레 ---
new_scene()
drum = lp.cylinder("Drum", radius=0.5, depth=1.0, segments=10, location=(0, 0, 0.5))
lp.set_color(drum, (0.55, 0.35, 0.18))
before = stats(drum)[1]
lp.emboss(drum, lp.box("Band", size=(2, 2, 0.1), location=(0, 0, 0.5)), height=0.03, color=(0.4, 0.4, 0.45))
closed, volume, tris, bx, by, bz = stats(drum)
check("emboss 띠 수밀", closed, True)
check("emboss 띠 부피 증가", volume > before + 0.005, True)
check("emboss 띠가 바깥으로", round(bx[1], 3), 0.5, "ge")
check("emboss 커터·임시물 정리", len(model_objects()), 1)
check("emboss 표식 레이어 제거", "LP3D_Mark" in drum.data.uv_layers, False)
check("emboss 색 레이어가 활성", drum.data.uv_layers.active.name, "UVMap")

new_scene()
wall = lp.box("Wall3", size=(2, 0.4, 2), location=(0, 0, 1))
lp.set_color(wall, (0.9, 0.85, 0.7))
lp.emboss(wall, lp.box("Ring", size=(0.8, 0.5, 0.8), location=(0, -0.25, 1)), height=0.03, ring=0.05,
          color=(0.4, 0.25, 0.15))
closed, volume, tris, bx, by, bz = stats(wall)
check("emboss ring 수밀", closed, True)
check("emboss ring 부피 = 둘레 띠만", round(volume - 1.6, 4), round((0.8 * 0.8 - 0.7 * 0.7) * 0.03, 4), "approx")
wood = cell_uv(snap_cell((0.9, 0.85, 0.7)))
uv = wall.data.uv_layers.active.data
center = [p for p in wall.data.polygons if abs(p.center.x) < 0.3 and abs(p.center.z) < 0.3
          and p.normal.y < -0.9 and p.center.y > -0.21]  # 올라오지 않은 면만
check("emboss ring 안쪽은 원래 색", bool(center) and all(
    abs(uv[i].uv[0] - wood[0]) < 1e-4 for p in center for i in p.loop_indices), True)

# --- attach ---
new_scene()
ball = lp.sphere("Head", radius=0.5, subdivisions=2, location=(0, 0, 1))
eye = lp.box("Eye", size=(0.1, 0.04, 0.1), location=(0.15, -2.0, 1.1))
lp.attach(eye, ball, direction=(0, 1, 0), sink=0.01, align=True)
d = (eye.location - ball.location).length
check("attach 곡면 근처", 0.45 < d < 0.53, True)
slab = lp.box("Slab", size=(2, 0.4, 2), location=(3, 0, 1))      # 앞면 y=-0.2
plate = lp.box("Plate", size=(0.3, 0.04, 0.2), location=(3, -1.0, 1))
lp.attach(plate, slab, direction=(0, 1, 0), sink=0.01)
check("attach 뒷면이 sink만큼만 박힘 (중심 y=-0.2-0.02+0.01)", round(plate.location.y, 4), -0.21, "approx")
n = (eye.matrix_world.to_3x3() @ Vector((0, -1, 0))).normalized()
radial = (eye.location - ball.location).normalized()
check("attach align: 정면이 노멀", round(n.dot(radial), 2), 0.97, "ge")
far = lp.attach(lp.box("Far", size=(0.1, 0.1, 0.1), location=(5, 5, 5)), ball, direction=(1, 0, 0))
check("attach 빗나가면 가장 가까운 표면에", round((far.location - ball.location).length, 1) <= 0.6, True)

# --- set_color inside ---
new_scene()
blob = lp.loft("Blob", segments=8, sections=[(0, 1.0, 1.0), (0.5, 1.0, 1.0), (1.0, 1.0, 1.0)])
lp.set_color(blob, (0.9, 0.4, 0.1))
lp.set_color(blob, (1, 0.95, 0.85), inside=lp.sphere("Spot", radius=0.3, location=(0, -0.5, 0.25)))
spot = cell_uv(snap_cell((1, 0.95, 0.85)))
uv = blob.data.uv_layers.active.data
inn = [p for p in blob.data.polygons if abs(uv[p.loop_indices[0]].uv[0] - spot[0]) < 1e-4
       and abs(uv[p.loop_indices[0]].uv[1] - spot[1]) < 1e-4]
check("inside: 영역 안 면만", 1 <= len(inn) <= 3, True)
check("inside 영역 오브젝트 삭제", "Spot" in bpy.data.objects, False)

# --- tube closed: 뱃머리처럼 뾰족한 둘레 고리 ---
new_scene()
outline = [(0, -2.3), (0.52, -1.88), (0.96, -0.35), (0.8, 2.05), (-0.8, 2.05), (-0.96, -0.35), (-0.52, -1.88)]
rail = lp.tube("Rail", points=[(x, y, 1.0) for x, y in outline], radius=0.08, segments=4, closed=True)
closed, volume, tris, bx, by, bz = stats(rail)
check("closed tube 수밀(캡 없이 고리)", closed, True)
check("closed tube 뱃머리 끝을 덮음", round(by[0], 2), -2.3, "le")
bow = [v.co for v in rail.data.vertices if v.co.y < -2.2]
check("뱃머리 마이터 두께 유지(꼭짓점 앞으로 0.08 이상)", round(min(v.y for v in bow), 3) <= -2.38, True)
straight = lp.tube("Straight", points=[(0, 0, 0), (1, 0, 0)], radius=0.05)
check("열린 tube 기존 동작 수밀", stats(straight)[0], True)

# --- mirror_x: 닫힌 반쪽의 X=0 단면이 내부 벽으로 남지 않는다 ---
new_scene()
half = lp.silhouette("Half", front=[(0, 0), (0.8, 0), (0.8, 1), (0, 1)], side=[(-1, 0), (1, 0), (1, 1), (-1, 1)])
lp.mirror_x(half)
closed, volume, tris, bx, by, bz = stats(half)
check("mirror_x 후 수밀(2-다양체)", closed, True)
check("mirror_x 후 X=0 내부 면 없음", sum(1 for p in half.data.polygons if abs(p.center.x) < 1e-4 and abs(p.normal.x) > 0.99), 0)
check("mirror_x 후 부피 = 전체", round(volume, 3), 3.2, "approx")
lp.set_color(half, (0.8, 0.2, 0.2))
win = lp.prism("Win", outline=[(-0.5, 0.3), (0.5, 0.3), (0.5, 0.8), (-0.5, 0.8)], depth=1.2, axis='Y', location=(0, -1, 0))
lp.set_color(win, (0.3, 0.5, 0.7))
lp.cut(half, win, depth=0.03, frame=0.04, frame_color=(0.1, 0.1, 0.1))
glass = cell_uv(snap_cell((0.3, 0.5, 0.7)))
uv = half.data.uv_layers.active.data
panes = [p for p in half.data.polygons if p.normal.y < -0.99 and abs(uv[p.loop_indices[0]].uv[0] - glass[0]) < 1e-4
         and abs(uv[p.loop_indices[0]].uv[1] - glass[1]) < 1e-4]
check("대칭 이음새를 가로지르는 창이 한 장", len(panes), 1)

print("\n결과:", "모두 통과" if not failures else f"실패 {len(failures)}건 — {failures}")
sys.exit(1 if failures else 0)
