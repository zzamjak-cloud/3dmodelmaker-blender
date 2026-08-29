# 자동 반복 루프 정책: 턴 수 계산과 종료 판정
#
# 이 모듈은 bpy에 의존하지 않는 순수 함수만 담는다 (Blender 없이 테스트 가능).
#
# UI의 "자동 반복" 값 = 내부 턴 수 (1:1 동기화). 과거 "1회 = 3턴" 환산은
# 값이 곱으로 부풀어(3 설정 시 9턴) 위험해서 폐기했다.
#
# 배경: 1턴은 에이전트가 렌더를 한 장도 보지 않은 맹목 생성 턴이다. 거기서
# 나오는 DONE 선언은 근거가 없는데도 세션을 조기 종료시켜 품질이 급락했다
# (Codex가 1턴에 DONE을 내는 경향이 강하다). 실측 결과 3턴은 돌아야 품질이
# 보장되므로 기본 턴 수는 3이고, 최소 턴 전의 DONE은 무시한다.

MIN_TURNS = 3         # 이 턴 전에는 에이전트의 DONE 선언을 무시한다


def total_turns(turns: int) -> int:
    """UI의 자동 반복 값을 내부 턴 수로 정규화한다 (1:1, 최소 1)."""
    return max(1, turns)


def allow_done(iteration: int, max_turns: int) -> bool:
    """이번 턴의 DONE 선언을 인정할지 판정한다.

    최소 턴(MIN_TURNS) 이후에만 인정한다. 총 턴이 그보다 짧은 세션
    (개선하기 = 1턴)은 마지막 턴에서 곧바로 인정한다."""
    return iteration >= min(MIN_TURNS, max_turns)


def should_finalize(status: str, iteration: int, max_turns: int) -> bool:
    """턴 실행 후 세션을 종료할지 판정한다.

    최대 턴에 도달했으면 무조건 종료, 그 전에는 인정되는 DONE만 종료한다."""
    if iteration >= max_turns:
        return True
    return status == 'DONE' and allow_done(iteration, max_turns)
