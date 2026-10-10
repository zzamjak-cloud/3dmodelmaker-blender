# 게임용 glTF 익스포트(팔레트 → 셀별 단색 머티리얼) 확인 — 개발 프로필에서 실행한다.
#   ./scripts/dev_run.sh --background --python tests/verify_game_export_in_blender.py
#
# 셀 색이 sRGB→선형 한 번만 거쳐 baseColorFactor 로 나가는지(어두워지지 않는지), 같은 색이 한 머티리얼로
# 합쳐지는지, UV가 빠지고 한 메시로 합쳐지는지, 원점이 바닥 중앙이고 높이·발판 정규화가 맞는지,
# 원본 컬렉션은 그대로인지 본다.
import json
import os
import struct
import sys
import tempfile

import bpy
from bl_ext.user_default.lp3d_modelmaker import lowpoly as lp
from bl_ext.user_default.lp3d_modelmaker.lowpoly import palette, palette_data
from bl_ext.user_default.lp3d_modelmaker.pipeline import export

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


def glb_json(path):
    with open(path, "rb") as f:
        data = f.read()
    length, kind = struct.unpack_from("<II", data, 12)
    return json.loads(data[20:20 + length])


bpy.ops.wm.read_factory_settings(use_empty=True)
lp.set_session("LP3D_GameVerify")
plaster, wood = (0.88, 0.76, 0.52), (0.30, 0.17, 0.065)
wall = lp.set_color(lp.box("Wall", size=(2.0, 1.0, 1.0), location=(5.0, 3.0, 0.5)), plaster)
lp.set_color(wall, wood, below=0.2)
roof = lp.set_color(lp.box("Roof", size=(2.2, 1.2, 0.4), location=(5.0, 3.0, 1.2)), wood)
roof.scale = (1.0, -1.0, 1.0)   # 음수 스케일도 면이 뒤집히지 않아야 한다
coll = bpy.data.collections["LP3D_GameVerify"]
before = {o.name: len(o.data.polygons) for o in coll.objects}
objects_before = set(bpy.data.objects.keys())

path = os.path.join(tempfile.mkdtemp(prefix="lp3d_game_"), "house.glb")
stats = export.export_game_glb(coll, path, height=0.85, footprint=0.96)
doc = glb_json(path)
names = sorted(m["name"] for m in doc["materials"])
cell = palette_data.CELLS[palette.snap_cell(plaster)]
hex_name = "C_%02x%02x%02x" % cell
check("머티리얼은 색마다 하나 (같은 나무색 병합)", len(names) == 2, str(names))
check("머티리얼 이름이 셀 hex", hex_name in names, f"{hex_name} in {names}")
factor = next(m for m in doc["materials"] if m["name"] == hex_name)["pbrMetallicRoughness"]["baseColorFactor"]
expect = [((c / 255 + 0.055) / 1.055) ** 2.4 for c in cell]
check("baseColorFactor = 셀 sRGB의 선형값 (한 번만 변환)",
      all(abs(a - b) < 1e-3 for a, b in zip(factor[:3], expect)), f"{factor[:3]} vs {expect}")
check("텍스처·이미지 없음", not doc.get("textures") and not doc.get("images"))
check("메시 하나", len(doc["meshes"]) == 1 and len(doc["nodes"]) == 1, f"nodes={len(doc['nodes'])}")
attrs = [p["attributes"] for p in doc["meshes"][0]["primitives"]]
check("UV 없음", all("TEXCOORD_0" not in a for a in attrs))
mins = [min(doc["accessors"][a["POSITION"]]["min"][i] for a in attrs) for i in range(3)]
maxs = [max(doc["accessors"][a["POSITION"]]["max"][i] for a in attrs) for i in range(3)]
# glTF y-up: (x, z, -y) — 높이는 y, 발판은 x·z
check("바닥이 원점 높이", abs(mins[1]) < 1e-4, f"min y={mins[1]:.4f}")
check("가로 중앙", abs(mins[0] + maxs[0]) < 1e-4 and abs(mins[2] + maxs[2]) < 1e-4)
height, width = maxs[1] - mins[1], max(maxs[0] - mins[0], maxs[2] - mins[2])
check("높이·발판 정규화 (작은 배율 적용)", height <= 0.85 + 1e-4 and width <= 0.96 + 1e-4
      and (abs(height - 0.85) < 1e-4 or abs(width - 0.96) < 1e-4), f"h={height:.3f} w={width:.3f}")
check("원본 컬렉션 그대로", {o.name: len(o.data.polygons) for o in coll.objects} == before)
check("임시 오브젝트 정리", set(bpy.data.objects.keys()) == objects_before)
check("배율 = min(0.85/1.4, 0.96/2.2)", abs(stats["scale"] - 0.96 / 2.2) < 1e-4, f"{stats['scale']:.4f}")

# 뒤집힌 면 검사: 합친 메시의 부피가 양수(바깥 노멀)여야 한다
obj, _ = export.build_game_mesh(coll, "VolumeCheck")
import bmesh
bm = bmesh.new()
bm.from_mesh(obj.data)
check("음수 스케일 파트도 바깥 노멀", bm.calc_volume(signed=True) > 0, f"{bm.calc_volume(signed=True):.3f}")
bm.free()
print("stats", stats)
if fails:
    print(f"FAILED {len(fails)}: {fails}")
    sys.exit(1)
print("ALL PASS")
