# 씬 구조물 헬퍼(room / fence_run) 검증 — factory Blender로 바로 돈다
#
# 실행: blender --background --factory-startup --python tests/verify_sceneprops_in_blender.py
#
# 확인하는 것:
#   1) fence_run이 속이 비치는 울타리인가 — 같은 구간 wall_run(꽉 찬 벽)보다 부피가 훨씬 작아야 한다
#   2) 기둥·가로대·살대가 실제로 생기고 바닥이 z=0인가
#   3) room이 바닥 윗면 z=0의 껍데기를 만들고 open_sides가 벽을 빼는가
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lowpoly as lp  # noqa: E402

_failures = []


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print("[%s] %s%s" % (mark, label, (" — " + detail) if detail else ""))
    if not condition:
        _failures.append(label)


def bounds(obj):
    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))


def volume(obj):
    """메시가 차지하는 실제 부피 — 속이 비쳤는지 판정하는 지표."""
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    vol = abs(bm.calc_volume(signed=True))
    bm.free()
    return vol


SQUARE = [(-6, -6), (6, -6), (6, 6), (-6, 6)]


def main():
    # 1. 울타리는 같은 구간의 벽보다 부피가 훨씬 작다 (속이 비친다)
    fence = lp.fence_run(SQUARE, height=1.4, closed=True, rails=2)
    wall = lp.wall_run(SQUARE, height=1.4, thickness=0.05, closed=True)
    fv, wv = volume(fence), volume(wall)
    check("울타리가 같은 두께 벽보다 부피가 작다", fv < wv * 0.6,
          "울타리 %.4f m3 / 벽 %.4f m3" % (fv, wv))

    # 2. 바닥이 z=0이고 높이가 지정값과 맞는다
    _, _, (zmin, zmax) = bounds(fence)
    check("울타리 바닥이 z=0", abs(zmin) < 1e-6, "%.4f" % zmin)
    check("울타리 높이가 height와 일치", abs(zmax - 1.4) < 1e-6, "%.4f" % zmax)

    # 3. 기둥 간격을 좁히면 파트가 늘어난다
    sparse = lp.fence_run(SQUARE, height=1.4, closed=True, post_spacing=6.0, rails=2)
    dense = lp.fence_run(SQUARE, height=1.4, closed=True, post_spacing=1.5, rails=2)
    check("기둥 간격이 좁으면 버텍스가 많다",
          len(dense.data.vertices) > len(sparse.data.vertices),
          "%d > %d" % (len(dense.data.vertices), len(sparse.data.vertices)))

    # 4. 살대(pickets)를 켜면 더 촘촘해진다
    picket = lp.fence_run(SQUARE, height=1.4, closed=True, rails=2, pickets=1)
    check("살대를 켜면 버텍스가 늘어난다",
          len(picket.data.vertices) > len(fence.data.vertices),
          "%d > %d" % (len(picket.data.vertices), len(fence.data.vertices)))

    # 5. rails=0은 기둥만 — 가장 가볍다
    posts_only = lp.fence_run(SQUARE, height=1.4, closed=True, rails=0)
    check("rails=0은 기둥만 남는다",
          len(posts_only.data.vertices) < len(fence.data.vertices),
          "%d < %d" % (len(posts_only.data.vertices), len(fence.data.vertices)))

    # 6. 열린 폴리라인도 동작한다
    line = lp.fence_run([(-5, 0), (0, 3), (5, 0)], height=1.0, rails=2)
    check("열린 폴리라인 울타리", len(line.data.vertices) > 0)

    # 7. room — 바닥 윗면이 z=0 (가구를 z=0에 그대로 놓을 수 있어야 한다)
    shell = lp.room("Shop", size=(10, 8), height=3.2)
    (rxmin, rxmax), (rymin, rymax), (rzmin, rzmax) = bounds(shell)
    check("방 바닥 윗면이 z=0 (아래로만 두께)", abs(rzmin + 0.2) < 1e-6, "zmin %.4f" % rzmin)
    check("방 천장고가 height와 일치", abs(rzmax - 3.2) < 1e-6, "%.4f" % rzmax)
    check("방 안목 폭이 size와 일치", abs((rxmax - rxmin) - 10.4) < 1e-6,
          "%.4f (벽 두께 0.2 x 2 포함)" % (rxmax - rxmin))

    # 8. open_sides가 벽을 실제로 뺀다
    open_shell = lp.room("Open", size=(10, 8), height=3.2, open_sides=('S',))
    check("open_sides가 벽을 뺀다",
          len(open_shell.data.vertices) < len(shell.data.vertices),
          "%d < %d" % (len(open_shell.data.vertices), len(shell.data.vertices)))
    # 바닥은 여전히 전체 면적을 덮으므로 경계는 그대로다 — 벽이 사라졌는지는
    # "열린 쪽 끝에 바닥보다 위로 솟은 지오메트리가 없는가"로 본다
    open_edge = min(v.co.y for v in open_shell.data.vertices)
    standing = [v for v in open_shell.data.vertices
                if v.co.z > 0.01 and abs(v.co.y - open_edge) < 1e-4]
    check("열린 쪽에 서 있는 벽이 없다", not standing, "%d개 남음" % len(standing))
    closed_edge = min(v.co.y for v in shell.data.vertices)
    closed_standing = [v for v in shell.data.vertices
                       if v.co.z > 0.01 and abs(v.co.y - closed_edge) < 1e-4]
    check("닫힌 쪽에는 벽이 서 있다", bool(closed_standing),
          "%d개" % len(closed_standing))

    # 9. ceiling=True는 위를 덮는다
    capped = lp.room("Capped", size=(10, 8), height=3.2, ceiling=True)
    check("ceiling=True는 천장을 덮는다",
          len(capped.data.vertices) > len(shell.data.vertices),
          "%d > %d" % (len(capped.data.vertices), len(shell.data.vertices)))

    # 10. 잘못된 입력은 오류
    for label, fn in (("점 1개 울타리", lambda: lp.fence_run([(0, 0)])),):
        try:
            fn()
            check(label + "는 오류", False, "예외가 나지 않았다")
        except ValueError:
            check(label + "는 오류", True)

    print("")
    if _failures:
        print("RESULT: %d FAILED — %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("RESULT: ALL PASS")


main()
