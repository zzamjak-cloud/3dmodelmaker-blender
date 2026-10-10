"""cut/emboss 불리언 결과가 그럴듯한지 판정하는 순수 계산. bpy 없이 테스트한다.

EXACT 솔버는 자기 교차가 있는 대상(앞선 cut이 몸통 띠 단차를 가로질러 판 경우 등)을 use_self 없이 받으면
안팎을 잘못 판정해 대상을 통째로 지우거나(결과 면 6장) 표면 전체를 cutter 안쪽으로 분류한다(벽 전체가 홈 색).
차집합은 cutter 표면적 이상으로 표면적을 바꿀 수 없고, cutter 부피 이상으로 부피를 줄일 수 없다는
불변식으로 그런 결과를 걸러 낸다.
"""

# 면적·부피 비교 여유 — 불리언이 남긴 미세 조각과 부동소수 오차를 흡수한다
REL_TOL = 0.02
ABS_TOL = 1e-6


def _tol(*values):
    return REL_TOL * max((abs(v) for v in values), default=0.0) + ABS_TOL


def difference_ok(before_area, after_area, cutter_area, before_volume=None, after_volume=None,
                  cutter_volume=None) -> bool:
    """차집합 결과가 불변식을 지키면 True.

    표면적: |after - before| <= cutter 표면적 (잘린 표면만큼 빠지고 cutter 벽만큼 더해진다).
    부피(닫힌 메시일 때만): before - cutter <= after <= before."""
    if after_area <= ABS_TOL:
        return False
    if abs(after_area - before_area) > cutter_area + _tol(before_area, cutter_area):
        return False
    if None not in (before_volume, after_volume, cutter_volume):
        tol = _tol(before_volume, cutter_volume)
        if after_volume > before_volume + tol:
            return False
        if after_volume < before_volume - cutter_volume - tol:
            return False
    return True


def region_ok(region_area, cutter_area) -> bool:
    """cutter 안에 든 대상 표면(새김 영역) 면적이 cutter 표면적을 넘지 않으면 True.

    닫힌 cutter가 자르는 평면 단면은 cutter 표면적의 절반 이하다. 휜 표면 여유를 두고 전체 면적까지 허용한다."""
    return region_area <= cutter_area + _tol(cutter_area)
