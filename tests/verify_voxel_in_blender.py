# 복셀 헬퍼 검증 — 애드온 설치 없이 factory Blender로 바로 돈다
#
# 실행: blender --background --factory-startup --python tests/verify_voxel_in_blender.py
#
# bpy가 필요해 일반 유닛 테스트로는 닿지 않는다. 확인하는 것:
#   1) 격자 좌표가 정확히 origin + (i,j,k)*size 에 놓이는가
#   2) 맞닿은 안쪽 면이 실제로 빠지는가 (트라이가 셀 수에 비례해 터지지 않는가)
#   3) 메시가 닫혀 있는가 (은면 컬링·베이크가 닫힌 메시를 전제한다)
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


def tri_count(obj) -> int:
    obj.data.calc_loop_triangles()
    return len(obj.data.loop_triangles)


def is_closed(obj) -> bool:
    """모든 에지가 정확히 두 면에 물려 있으면 닫힌 메시다."""
    counts = {}
    for poly in obj.data.polygons:
        for key in poly.edge_keys:
            counts[key] = counts.get(key, 0) + 1
    return bool(counts) and all(n == 2 for n in counts.values())


def main():
    # 1. 단일 셀 — 정육면체 하나 (6면 = 12 트라이)
    one = lp.voxel("One", [(0, 0, 0)], size=0.5)
    check("단일 셀은 정육면체 12트라이", tri_count(one) == 12, "%d" % tri_count(one))
    check("단일 셀은 닫힌 메시", is_closed(one))
    xs = [v.co.x for v in one.data.vertices]
    zs = [v.co.z for v in one.data.vertices]
    check("셀 크기가 size와 일치", abs((max(xs) - min(xs)) - 0.5) < 1e-6,
          "%.4f" % (max(xs) - min(xs)))
    check("바닥이 z=0", abs(min(zs)) < 1e-6, "%.4f" % min(zs))

    # 2. 두 셀을 붙이면 맞닿은 면이 빠져 10면(20트라이)
    pair = lp.voxel("Pair", [(0, 0, 0), (1, 0, 0)], size=0.5)
    check("맞닿은 안쪽 면이 빠진다", tri_count(pair) == 20,
          "%d (면을 안 빼면 24)" % tri_count(pair))
    check("두 셀도 닫힌 메시", is_closed(pair))

    # 3. 속이 찬 큐브 — 표면적만 남아야 한다 (4x4x4 → 6면 x 16셀 x 2 = 192 트라이)
    solid = lp.voxel_box("Solid", dims=(4, 4, 4), size=0.25)
    check("4x4x4 속찬 큐브는 표면만 192트라이", tri_count(solid) == 192,
          "%d (안쪽 면을 다 만들면 768)" % tri_count(solid))

    # 4. hollow=True는 껍데기만 남긴다 — 겉면은 같고 안쪽 면이 추가된다
    hollow = lp.voxel_box("Hollow", dims=(4, 4, 4), size=0.25, hollow=True)
    check("hollow는 겉면을 유지한다", tri_count(hollow) >= 192, "%d" % tri_count(hollow))
    check("hollow도 닫힌 메시", is_closed(hollow))

    # 5. origin 오프셋이 정확히 반영되는가
    moved = lp.voxel("Moved", [(0, 0, 0)], size=0.2, origin=(1.0, -0.4, 0.6))
    mxs = [v.co.x for v in moved.data.vertices]
    mzs = [v.co.z for v in moved.data.vertices]
    check("origin X가 반영된다", abs(min(mxs) - 1.0) < 1e-6, "%.4f" % min(mxs))
    check("origin Z가 반영된다", abs(min(mzs) - 0.6) < 1e-6, "%.4f" % min(mzs))

    # 6. 기둥 헬퍼
    column = lp.voxel_column("Col", height=5, size=0.2)
    czs = [v.co.z for v in column.data.vertices]
    check("기둥 높이 = height x size", abs((max(czs) - min(czs)) - 1.0) < 1e-6,
          "%.4f" % (max(czs) - min(czs)))
    check("기둥은 옆면만 이어진 닫힌 메시", is_closed(column))

    # 7. 계단식 원형 근사 — 스타일 지침의 예시 패턴이 실제로 도는가
    disc = lp.voxel("Disc", [(i, j, 0) for i in range(-3, 4) for j in range(-3, 4)
                             if i * i + j * j <= 9], size=0.25)
    check("원형 근사 셀이 만들어진다", tri_count(disc) > 0, "%d 트라이" % tri_count(disc))
    check("원형 근사도 닫힌 메시", is_closed(disc))

    # 8. 빈 입력은 조용히 넘어가지 않고 오류여야 한다
    try:
        lp.voxel("Empty", [], size=0.2)
        check("빈 셀 목록은 오류", False, "예외가 나지 않았다")
    except ValueError:
        check("빈 셀 목록은 오류", True)

    print("")
    if _failures:
        print("RESULT: %d FAILED — %s" % (len(_failures), ", ".join(_failures)))
        sys.exit(1)
    print("RESULT: ALL PASS")


main()
