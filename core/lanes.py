# 레인 배치 계산: 배치 실행한 잡들이 겹치지 않도록 Y축으로 벌리는 규칙
#
# 최종 결과(core/jobs.apply_lane_offset)와 턴 스냅샷(core/snapshots.capture_turn)은
# 반드시 같은 Y에 놓여야 한 잡의 중간 단계와 최종본이 한 줄로 보인다. 두 곳이 각자
# 계산하면 한쪽만 고쳐져 어긋나므로(실제로 그렇게 어긋났다) 계산은 여기 한 곳에 둔다.
#
# bpy에 의존하지 않는다 — 이 산술이 bpy 호출에 붙어 있어서 테스트가 볼 수 없었다.

LANE_SPACING = 4.0  # 레인 간 Y축 간격(미터)


def lane_dy(lane, spacing: float = LANE_SPACING) -> float:
    """레인 번호에 대응하는 Y 오프셋. 음수 레인은 원점으로 클램프한다.

    spacing은 레인 간격(m)이다. 배경 공간처럼 결과가 수십 미터에 걸치는 잡은
    기본 4m로는 서로 겹치므로 호출 쪽에서 씬 크기에 맞는 간격을 넘긴다."""
    return spacing * max(int(lane or 0), 0)


def lane_shift(applied, lane, spacing: float = LANE_SPACING) -> float:
    """applied 레인에 놓인 오브젝트를 lane으로 옮기는 이동량.

    오브젝트 이동은 상대 이동이라 매번 더하면 누적된다 — 개선 세션은 같은 컬렉션을
    재사용하고, 에이전트가 코드 없이 STATUS: DONE으로 끝내면 오브젝트가 다시
    만들어지지 않은 채 마무리에 도달한다. 그때 또 밀리지 않도록 차분만 낸다."""
    return lane_dy(lane, spacing) - lane_dy(applied, spacing)


# ---------- 빈자리 배치 ----------
# 레인 번호 x 고정 간격은 결과 크기를 모른다. 7m 주유소를 4m 간격에 놓으면 겹치고, 큐를 비우면
# 레인 번호가 0부터 다시 쓰여 예전 결과 위에 새 결과가 앉는다. 그래서 씬에 실제로 놓인 것들의
# 평면 영역을 보고 +X 방향으로 처음 비는 자리에 놓는다.

PLACE_MARGIN = 1.0  # 결과 사이 최소 간격(m)


def _overlaps(a, b, margin, eps=1e-6) -> bool:
    """두 평면 상자 (x0, y0, x1, y1)가 margin 안쪽으로 붙거나 겹치는가."""
    return (a[0] < b[2] + margin - eps and b[0] < a[2] + margin - eps
            and a[1] < b[3] + margin - eps and b[1] < a[3] + margin - eps)


def free_offset(box, occupied, margin: float = PLACE_MARGIN) -> float:
    """box를 X로 얼마나 밀어야 occupied 상자들과 겹치지 않는지. 제자리가 비어 있으면 0.

    후보는 제자리와 '각 장애물 오른쪽 끝 + 간격'뿐이다 — 겹침이 없는 가장 가까운 자리는
    항상 그 중 하나에서 시작한다. 왼쪽(음수)으로는 밀지 않아 결과가 한 방향 줄로 쌓인다."""
    rows = [o for o in occupied if o[1] < box[3] + margin and box[1] < o[3] + margin]
    candidates = sorted({0.0} | {o[2] + margin - box[0] for o in rows if o[2] + margin - box[0] > 0})
    for dx in candidates:
        moved = (box[0] + dx, box[1], box[2] + dx, box[3])
        if not any(_overlaps(moved, o, margin) for o in rows):
            return dx
    return candidates[-1]
