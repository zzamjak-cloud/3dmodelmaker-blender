# 비정형 배치 헬퍼 검증 — factory Blender로 바로 돈다
#
# 실행: blender --background --factory-startup --python tests/verify_organic_in_blender.py
#
# 대도시 생성 결과가 "너무 규칙적이고 항상 정사각형"이라는 지적에서 나온 헬퍼들이
# 실제로 비정형을 만드는지 확인한다:
#   1) terrain(outline=...)이 다각형 밖 면을 지우고 원점은 (0,0)에 남기는가
#   2) meander가 끝점을 고정한 채 중간을 굽히는가
#   3) place_cluster가 중심 주변에 뭉치는가 (균일 산포보다 중심 가까이 밀도가 높다)
#   4) place_along 지터가 등간격·직선 정렬을 실제로 깨는가
import math
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lowpoly as lp  # noqa: E402

_failures = []


def check(label, condition, detail=""):
    print("[%s] %s%s" % ("PASS" if condition else "FAIL", label, (" — " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


def main():
    # 1. 비정형 지형 — 격자가 아니라 윤곽 다각형 지면
    import bmesh
    hexagon = [(-30, -18), (-8, -26), (24, -20), (34, 4), (18, 22), (-12, 26), (-32, 8)]
    flat = lp.terrain("FlatIsle", outline=hexagon, relief=0.0)
    check("평지는 윗면 하나", len(flat.data.polygons) == 1, "%d" % len(flat.data.polygons))
    check("평지 윗면은 위를 본다", flat.data.polygons[0].normal.z > 0.99)
    ground = lp.terrain("Isle", outline=hexagon, cells=(24, 20), relief=0.4, seed=3)
    full = lp.terrain("Rect", size=(66, 52), cells=(24, 20), relief=0.4, seed=3)
    check("기복 지형은 성긴 삼각형", 30 < len(ground.data.polygons) < 24 * 20 * 2,
          "%d" % len(ground.data.polygons))
    check("기복 지형 윗면은 모두 위를 본다", all(p.normal.z > 0.2 for p in ground.data.polygons))
    check("outline 지형 원점은 (0,0)", ground.location.length < 1e-6, str(tuple(ground.location)))
    corner_faces = [p for p in ground.data.polygons if p.center.x < -28 and p.center.y > 20]
    check("경계 상자 구석(다각형 밖)에 면이 없다", not corner_faces, "%d개" % len(corner_faces))
    bm = bmesh.new()
    bm.from_mesh(ground.data)
    loose = [v for v in bm.verts if not v.link_faces]
    rim = [e for e in bm.edges if e.is_boundary]
    # 계단 테두리였다면 축에 나란한 짧은 변이 대부분이다 — 매끈한 윤곽은 그렇지 않다
    axis = [e for e in rim if min(abs((e.verts[0].co - e.verts[1].co).x),
                                   abs((e.verts[0].co - e.verts[1].co).y)) < 1e-4]
    bm.free()
    check("outline 지형에 고립 정점 없음", not loose, "%d개" % len(loose))
    check("테두리가 계단이 아니다", len(axis) < len(rim) * 0.2, "%d/%d" % (len(axis), len(rim)))
    xs = [v.co.x for v in ground.data.vertices]
    check("outline 지형 정점이 다각형 X 범위 안", min(xs) >= -32 - 1e-4 and max(xs) <= 34 + 1e-4,
          "%.1f ~ %.1f" % (min(xs), max(xs)))
    try:
        lp.terrain("Zero", outline=[(0, 0), (10, 0), (20, 0)], cells=(4, 4))  # 면적 0 다각형
        check("면적 없는 outline은 오류", False, "예외가 나지 않았다")
    except ValueError:
        check("면적 없는 outline은 오류", True)

    # 절벽 받침 + 색
    isle = lp.terrain("Floating", outline=hexagon, relief=0.3, base=3.0, seed=2,
                      color=(0.4, 0.65, 0.3), side_color=(0.5, 0.5, 0.55))
    bm = bmesh.new()
    bm.from_mesh(isle.data)
    open_edges = sum(1 for e in bm.edges if e.is_boundary)
    bm.free()
    zs = [v.co.z for v in isle.data.vertices]
    check("받침 지형은 닫힌 덩어리", open_edges == 0, "%d" % open_edges)
    check("받침 깊이 반영", abs(min(zs) + 3.0) < 1e-4, "%.2f" % min(zs))
    uv = isle.data.uv_layers.active.data
    tops = {tuple(round(c, 4) for c in uv[p.loop_indices[0]].uv) for p in isle.data.polygons if p.normal.z > 0.5}
    sides = {tuple(round(c, 4) for c in uv[p.loop_indices[0]].uv) for p in isle.data.polygons if p.normal.z < 0.3}
    check("윗면·절벽 색이 다르다", tops and sides and not (tops & sides), "%s / %s" % (tops, sides))
    hill = lp.terrain("Hill", outline=[(0, 0), (8, 0), (8, 8), (0, 8)], relief=0.0, elevation=1.5, base=2.0)
    check("고지대 윗면 높이", abs(max(v.co.z for v in hill.data.vertices) - 1.5) < 1e-4)

    # 비정사각 지형
    wide = lp.terrain("Wide", size=(64, 40), cells=(24, 15))
    wx = [v.co.x for v in wide.data.vertices]
    wy = [v.co.y for v in wide.data.vertices]
    check("비정사각 size가 그대로 반영", abs((max(wx) - min(wx)) - 64) < 1e-4
          and abs((max(wy) - min(wy)) - 40) < 1e-4)

    # 2. meander
    line = [(-40.0, 0.0), (40.0, 0.0)]
    bent = lp.meander(line, amount=3.0, subdivisions=4, seed=1)
    check("meander는 점을 늘린다", len(bent) == 2 + 4, "%d" % len(bent))
    check("meander 시작점 고정", bent[0] == (-40.0, 0.0), str(bent[0]))
    check("meander 끝점 고정", bent[-1] == (40.0, 0.0), str(bent[-1]))
    check("meander 중간이 굽는다", any(abs(p[1]) > 0.3 for p in bent[1:-1]),
          "|y|max %.2f" % max(abs(p[1]) for p in bent[1:-1]))
    check("meander 흔들림이 amount 안", all(abs(p[1]) <= 3.0 + 1e-6 for p in bent))
    check("meander seed가 결과를 고정", lp.meander(line, 3.0, 4, seed=1) == bent)
    check("meander seed를 바꾸면 달라진다", lp.meander(line, 3.0, 4, seed=2) != bent)

    # 3. place_cluster — 중심 가까이 촘촘
    src = lp.box("Hut", size=(2, 2, 2))
    huts = lp.place_cluster(src, 30, centers=[(-15, 0), (15, 0)], radius=6.0, min_dist=1.0, seed=4)
    check("place_cluster 개수", 20 <= len(huts) <= 30, "%d" % len(huts))
    near = sum(1 for o in huts if min(abs(o.location.x + 15), abs(o.location.x - 15)) < 4.0)
    check("place_cluster 대부분이 중심 반경 4m 안", near >= len(huts) * 0.6,
          "%d / %d" % (near, len(huts)))
    check("place_cluster 두 중심에 모두 뿌린다",
          any(o.location.x < 0 for o in huts) and any(o.location.x > 0 for o in huts))
    check("place_cluster 메시 공유", src.data.users == len(huts) + 1)
    try:
        lp.place_cluster(src, 5, centers=[])
        check("centers 비면 오류", False, "예외가 나지 않았다")
    except ValueError:
        check("centers 비면 오류", True)

    # 4. place_along 지터
    lamp = lp.box("Lamp", size=(0.3, 0.3, 2.0))
    plain = lp.place_along(lamp, [(0, 0), (40, 0)], spacing=5.0)
    check("지터 없는 place_along은 예전과 같다 (9개 등간격)", len(plain) == 9, "%d" % len(plain))
    check("지터 없는 정렬 0도", all(abs(o.rotation_euler.z) < 1e-6 for o in plain))
    jit = lp.place_along(lamp, [(0, 0), (40, 0)], spacing=5.0, spacing_jitter=0.3,
                         offset_jitter=1.5, rotate_jitter=12.0, seed=5)
    gaps = [b.location.x - a.location.x for a, b in zip(jit, jit[1:])]
    check("spacing_jitter가 간격을 흔든다", max(gaps) - min(gaps) > 0.5,
          "%.2f ~ %.2f" % (min(gaps), max(gaps)))
    check("offset_jitter가 선에서 밀어낸다", any(abs(o.location.y) > 0.3 for o in jit),
          "|y|max %.2f" % max(abs(o.location.y) for o in jit))
    check("offset_jitter 범위 안", all(abs(o.location.y) <= 1.5 + 1e-6 for o in jit))
    check("rotate_jitter가 회전을 흔든다", any(abs(o.rotation_euler.z) > 0.02 for o in jit))
    check("rotate_jitter 범위 안",
          all(abs(math.degrees(o.rotation_euler.z)) <= 12.0 + 1e-6 for o in jit))
    check("지터 place_along 시작점 고정", abs(jit[0].location.x) < 1e-6)

    print("")
    if _failures:
        print("RESULT: %d FAILED — %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("RESULT: ALL PASS")


main()
