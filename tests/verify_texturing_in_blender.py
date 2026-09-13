# 개별 매핑 파이프라인의 Blender 의존 구간 검증 — AI 호출 없이 factory Blender로 돈다.
#   blender --background --factory-startup --python tests/verify_texturing_in_blender.py
#
# 언랩 → 6면도 가이드 렌더 → 시트 합성 → 크롭 → CPU 베이크 → 머티리얼/UV 교체까지
# 실제 bpy로 돌린다. AI 결과 대신 가이드 시트(팔레트 색 렌더) 자체를 되먹여
# 투영 좌표계가 맞으면 면 색이 텍스처에 그대로 옮겨지는지 확인한다.
import os
import sys
import tempfile
import time
import types

import bmesh
import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 상대 임포트(..lowpoly)가 풀리도록 리포 루트를 가짜 패키지로 얹는다
_pkg = types.ModuleType("lp3d_v")
_pkg.__path__ = [ROOT]
sys.modules["lp3d_v"] = _pkg
from lp3d_v.lowpoly import palette  # noqa: E402
from lp3d_v.texturing import apply as tex_apply  # noqa: E402
from lp3d_v.texturing import bake as tex_bake  # noqa: E402
from lp3d_v.texturing import capture as tex_capture  # noqa: E402
from lp3d_v.texturing import unwrap as tex_unwrap  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {label} {detail}")
    if not ok:
        failures.append(label)


def add_box(name, size, location, color):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= size[0]
        v.co.y *= size[1]
        v.co.z *= size[2]
        v.co.x += location[0]
        v.co.y += location[1]
        v.co.z += location[2]
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    palette.set_color(obj, color)
    return obj


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    coll = bpy.data.collections.new("LP3D_Verify")
    scene.collection.children.link(coll)
    body = add_box("Crate", (1.0, 1.0, 1.0), (0, 0, 0.5), (0.75, 0.45, 0.2))    # 갈색 상자
    lid = add_box("Lid", (1.1, 1.1, 0.15), (0, 0, 1.07), (0.2, 0.5, 0.8))        # 파란 뚜껑
    for obj in (body, lid):
        scene.collection.objects.unlink(obj)
        coll.objects.link(obj)
    bpy.context.view_layer.update()
    objs = [body, lid]

    # 1) 언랩
    info = tex_unwrap.unwrap_objects(objs)
    check("언랩 아일랜드 생성", info["islands"] >= 12, f"islands={info['islands']} faces={info['faces']}")
    for obj in objs:
        layer = obj.data.uv_layers.get(tex_unwrap.TEXTURE_UV)
        check(f"{obj.name} 텍스처 UV 레이어 존재", layer is not None)
        uvs = [tuple(d.uv) for d in layer.data]
        check(f"{obj.name} UV가 0~1 안", all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in uvs))
        check(f"{obj.name} 면 UV 면적 > 0", all(
            abs(sum(uvs[p.loop_indices[i]][0] * uvs[p.loop_indices[(i + 1) % len(p.loop_indices)]][1]
                    - uvs[p.loop_indices[(i + 1) % len(p.loop_indices)]][0] * uvs[p.loop_indices[i]][1]
                    for i in range(len(p.loop_indices)))) > 1e-6 for p in obj.data.polygons))
        check(f"{obj.name} 팔레트 UV가 활성(가이드 렌더용)", obj.data.uv_layers.active.name == "UVMap")

    # 2) 가이드 렌더 + 시트
    work = tempfile.mkdtemp(prefix="lp3d_verify_tex_")
    t0 = time.time()
    views = tex_capture.render_views(bpy.context, objs, work, resolution=512)
    check("6시점 렌더", len(views) == 6 and all(os.path.isfile(p) for p in views.values()),
          f"{time.time() - t0:.1f}s")
    sheet = tex_capture.join_sheet(views, os.path.join(work, "guide_sheet.png"))
    img = bpy.data.images.load(sheet)
    check("시트 크기 3x2", tuple(img.size) == (1536, 1024), str(tuple(img.size)))
    bpy.data.images.remove(img)

    # 3) 크롭 → 베이크 (AI 결과 대신 가이드 시트를 그대로 사용)
    crops = tex_capture.split_sheet(sheet)
    check("6장 크롭", sorted(crops) == sorted(views))
    png = os.path.join(work, "Crate.png")
    t0 = time.time()
    stats = tex_bake.rasterize_to_png(bpy.context, objs, crops, png, 512, padding=8,
                                      uv_layer_names=[tex_unwrap.TEXTURE_UV] * len(objs))
    elapsed = time.time() - t0
    check("베이크 PNG 생성", os.path.isfile(png), f"{elapsed:.1f}s filled={stats.get('filled_pixels')}")
    check("채움 픽셀 존재", stats.get("filled_pixels", 0) > 1000)

    # 4) 적용: 이미지 pack, 머티리얼, UV 레이어 교체
    result = tex_apply.finalize(objs, png, "Crate")
    check("이미지 이름", result["image"] == "Crate", result["image"])
    image = bpy.data.images["Crate"]
    check("이미지 pack", image.packed_file is not None)
    for obj in objs:
        mesh = obj.data
        check(f"{obj.name} UV 레이어 1개(UVMap)", [l.name for l in mesh.uv_layers] == ["UVMap"],
              str([l.name for l in mesh.uv_layers]))
        check(f"{obj.name} 머티리얼 1개", len(mesh.materials) == 1 and mesh.materials[0].name == "Crate")
        check(f"{obj.name} 팔레트 재질 제거", all(m.name != palette.PALETTE_MATERIAL for m in mesh.materials))

    # 5) 텍스처 색이 원래 팔레트 색 계열인지 — 상자 정면 중앙 텍셀은 갈색(R>B)이어야 한다
    front_poly = max(body.data.polygons, key=lambda p: -p.normal.y)
    layer = body.data.uv_layers["UVMap"].data
    cu = sum(layer[i].uv.x for i in front_poly.loop_indices) / len(front_poly.loop_indices)
    cv = sum(layer[i].uv.y for i in front_poly.loop_indices) / len(front_poly.loop_indices)
    w, h = image.size
    px = (int(cv * h) * w + int(cu * w)) * 4
    r, g, b = image.pixels[px], image.pixels[px + 1], image.pixels[px + 2]
    check("정면 텍셀이 갈색 계열", r > b and r > 0.2, f"rgb=({r:.2f},{g:.2f},{b:.2f})")
    top_poly = max(lid.data.polygons, key=lambda p: p.normal.z)
    layer = lid.data.uv_layers["UVMap"].data
    cu = sum(layer[i].uv.x for i in top_poly.loop_indices) / len(top_poly.loop_indices)
    cv = sum(layer[i].uv.y for i in top_poly.loop_indices) / len(top_poly.loop_indices)
    px = (int(cv * h) * w + int(cu * w)) * 4
    r, g, b = image.pixels[px], image.pixels[px + 1], image.pixels[px + 2]
    check("뚜껑 윗면 텍셀이 파란 계열", b > r, f"rgb=({r:.2f},{g:.2f},{b:.2f})")

    # 6) 익스포트용 PNG 저장
    filepath_before = image.filepath
    os.makedirs(os.path.join(work, "export"), exist_ok=True)
    saved = tex_apply.save_texture_pngs(coll, os.path.join(work, "export"))
    check("익스포트 PNG 저장", len(saved) == 1 and os.path.isfile(saved[0]), str(saved))
    check("익스포트 후에도 pack 유지", image.packed_file is not None)
    check("익스포트 후 filepath 불변", image.filepath == filepath_before, f"{filepath_before} → {image.filepath}")

    print(f"\n작업 폴더: {work}")
    if failures:
        print(f"RESULT: FAIL ({len(failures)}): {failures}")
        sys.exit(1)
    print("RESULT: ALL PASS")


main()
