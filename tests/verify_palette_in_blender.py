# 고정 팔레트를 실제 Blender에서 검증한다 (헤들리스)
import bpy
from bl_ext.user_default.lp3d_modelmaker import lowpoly as lp
from bl_ext.user_default.lp3d_modelmaker.lowpoly import palette, palette_data

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


# 1) 이미지 로드 + pack — 최종 리뷰가 "코드만 읽어서는 장담 불가"라고 지목한 지점
img = palette._get_image()
check("이미지 로드", tuple(img.size) == (256, 256), str(tuple(img.size)))
check("컬러스페이스 sRGB", img.colorspace_settings.name == 'sRGB', img.colorspace_settings.name)
check("pack 성공(.blend 임베드)", img.packed_file is not None)

# 2) 머티리얼 노드 고정 설정
mat = palette._get_material()
tex = [n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE'][0]
bsdf = mat.node_tree.nodes.get("Principled BSDF")
check("보간 Closest", tex.interpolation == 'Closest', tex.interpolation)
check("Roughness 0.9", abs(bsdf.inputs["Roughness"].default_value - 0.9) < 1e-6)

# 3) set_color가 요청색을 올바른 셀로 스냅하고 UV를 셀 중앙에 찍는가
lp.set_session("LP3D_Verify")
obj = lp.box("VerifyBox", (1, 1, 1))
requested = (0.55, 0.36, 0.18)   # 나무색 — 프롬프트 예시와 동일
lp.set_color(obj, requested)
uv = obj.data.uv_layers.active.data[0].uv
cell = palette.snap_cell(requested)
expect_u, expect_v = palette.cell_uv(cell)
check("UV가 셀 중앙", abs(uv[0] - expect_u) < 1e-6 and abs(uv[1] - expect_v) < 1e-6,
      "uv=(%.5f,%.5f) expect=(%.5f,%.5f)" % (uv[0], uv[1], expect_u, expect_v))

# 4) 가장 중요한 검증: 그 UV 지점의 텍셀이 실제로 스냅된 색인가
#    (PNG 행 순서 / Blender 픽셀 하단기준 / UV v 규약 3종이 모두 맞아야 통과)
px = list(img.pixels)
x = min(int(uv[0] * 256), 255)
y = min(int(uv[1] * 256), 255)
off = (y * 256 + x) * 4


def lin_to_srgb(v):
    return 12.92 * v if v <= 0.0031308 else 1.055 * (max(v, 0.0) ** (1 / 2.4)) - 0.055


sampled = tuple(round(lin_to_srgb(px[off + i]) * 255) for i in range(3))
expected = palette_data.CELLS[cell]
check("텍셀 == 스냅색 (행순서 규약 3종 일치)",
      all(abs(a - b) <= 1 for a, b in zip(sampled, expected)),
      "sampled=%s expected=%s cell=%d" % (sampled, expected, cell))

# 5) 익스포트 PNG가 리포 원본과 바이트 동일한가
import hashlib
import os
import tempfile

d = tempfile.mkdtemp()
out = palette.save_palette_png(d)
h1 = hashlib.sha256(open(out, "rb").read()).hexdigest()
h2 = hashlib.sha256(open(palette._PNG_PATH, "rb").read()).hexdigest()
check("익스포트 PNG 바이트 동일", h1 == h2, h1[:16])

# 6) 구버전 이미지 업그레이드 경로 — 시나리오 13
img["lp3d_colors"] = "{}"
img["lp3d_grid"] = 32
img.pixels.foreach_set([0.5] * (256 * 256 * 4))
img.update()
check("구버전 마커를 stale로 판정", palette._is_stale(img))
img2 = palette._get_image()
check("데이터블록 동일(참조 유지)", img2.as_pointer() == img.as_pointer())
check("구버전 마커 제거됨", "lp3d_colors" not in img2.keys() and "lp3d_grid" not in img2.keys())
px2 = list(img2.pixels)
s2 = tuple(round(lin_to_srgb(px2[off + i]) * 255) for i in range(3))
check("업그레이드 후 색 복원", all(abs(a - b) <= 1 for a, b in zip(s2, expected)),
      "sampled=%s expected=%s" % (s2, expected))
check("머티리얼 노드 연결 유지", tex.image is not None and tex.image.as_pointer() == img2.as_pointer())

print("")
print("RESULT: " + ("ALL PASS" if not fails else "FAILED -> " + ", ".join(fails)))
