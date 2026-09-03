# 레인 배치 계산: 배치 실행한 잡들이 겹치지 않도록 Y축으로 벌리는 규칙
#
# 최종 결과(core/jobs.apply_lane_offset)와 턴 스냅샷(core/snapshots.capture_turn)은
# 반드시 같은 Y에 놓여야 한 잡의 중간 단계와 최종본이 한 줄로 보인다. 두 곳이 각자
# 계산하면 한쪽만 고쳐져 어긋나므로(실제로 그렇게 어긋났다) 계산은 여기 한 곳에 둔다.
#
# bpy에 의존하지 않는다 — 이 산술이 bpy 호출에 붙어 있어서 테스트가 볼 수 없었다.

LANE_SPACING = 4.0  # 레인 간 Y축 간격(미터)


def lane_dy(lane) -> float:
    """레인 번호에 대응하는 Y 오프셋. 음수 레인은 원점으로 클램프한다."""
    return LANE_SPACING * max(int(lane or 0), 0)


def lane_shift(applied, lane) -> float:
    """applied 레인에 놓인 오브젝트를 lane으로 옮기는 이동량.

    오브젝트 이동은 상대 이동이라 매번 더하면 누적된다 — 개선 세션은 같은 컬렉션을
    재사용하고, 에이전트가 코드 없이 STATUS: DONE으로 끝내면 오브젝트가 다시
    만들어지지 않은 채 마무리에 도달한다. 그때 또 밀리지 않도록 차분만 낸다."""
    return lane_dy(lane) - lane_dy(applied)
