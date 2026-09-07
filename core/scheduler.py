# AI/Blender 작업 스케줄러: AI는 N개 동시, Blender는 항상 1개씩 순차
#
# 이 모듈은 bpy에 의존하지 않는 순수 로직만 담는다 (Blender 없이 테스트 가능).
# 타이머 연동은 core/runner.py의 펌프가 pump()를 주기적으로 호출하는 방식이다.
#
# 배경: Codex CLI 서브프로세스는 서로 독립이라 여러 개를 동시에 띄워도
# 되지만, bpy 조작은 단일 스레드·전역 상태(lowpoly.set_session, 씬 렌더 설정,
# executor의 bpy.data 스냅샷/롤백)라 반드시 하나씩 원자적으로 실행해야 한다.
import logging
from collections import deque

log = logging.getLogger(__name__)

_ai_limit = 3            # 동시에 실행할 AI CLI 개수 (환경설정에서 갱신)
_ai_running = set()      # AI 슬롯을 점유 중인 job_key 집합
_ai_waiting = deque()    # 대기 중인 (job_key, fn) — FIFO
_blender_waiting = deque()  # 대기 중인 (job_key, fn) — FIFO


def reset():
    """전체 상태를 초기화한다 (Dev Reload·테스트용)."""
    _ai_running.clear()
    _ai_waiting.clear()
    _blender_waiting.clear()


def set_ai_limit(n: int):
    """동시 AI 실행 수를 설정한다. 한도를 올리면 대기 작업이 바로 시작된다."""
    global _ai_limit
    _ai_limit = max(1, int(n))
    _drain_ai()


def submit_ai(job_key, fn):
    """AI 작업을 제출한다. 슬롯이 있으면 즉시 실행, 없으면 대기열에 넣는다.

    한 잡은 AI 슬롯을 최대 1개만 점유한다 — 상태머신이 한 번에 한 요청만 보내므로
    같은 잡의 두 번째 제출은 항상 대기열로 간다."""
    _ai_waiting.append((job_key, fn))
    _drain_ai()


def release_ai(job_key):
    """AI 슬롯을 반환한다. 이미 반환된 잡의 중복 호출은 무시한다."""
    _ai_running.discard(job_key)
    _drain_ai()


def submit_blender(job_key, fn):
    """Blender 작업을 FIFO 대기열에 넣는다. 실제 실행은 pump()에서 한다.

    제출 즉시 실행하지 않는 이유: 실행 중인 작업 안에서 다음 작업이 제출될 때
    호출 스택이 무한히 깊어지는 것을 막고, 작업 사이에 UI 리드로우를 넣기 위함이다."""
    _blender_waiting.append((job_key, fn))


def cancel_job(job_key) -> int:
    """해당 잡의 대기 항목을 모두 제거하고 AI 슬롯을 반환한다. 제거 개수를 반환."""
    removed = 0
    for queue in (_ai_waiting, _blender_waiting):
        kept = [item for item in queue if item[0] != job_key]
        removed += len(queue) - len(kept)
        queue.clear()
        queue.extend(kept)
    if job_key in _ai_running:
        _ai_running.discard(job_key)
        removed += 1
    _drain_ai()
    return removed


def counts() -> dict:
    """UI 표시용 현황."""
    return {
        "ai_running": len(_ai_running),
        "ai_waiting": len(_ai_waiting),
        "blender_waiting": len(_blender_waiting),
    }


def has_work() -> bool:
    return bool(_ai_running or _ai_waiting or _blender_waiting)


def pump():
    """타이머가 주기적으로 호출한다. Blender 작업은 틱당 최대 1개만 실행한다."""
    _drain_ai()
    if _blender_waiting:
        _job_key, fn = _blender_waiting.popleft()
        _safe_call(fn)


def _drain_ai():
    """슬롯이 남는 동안 대기 중 AI 작업을 시작한다.

    같은 잡이 이미 슬롯을 점유 중이면 건너뛰고 그 뒤 항목을 본다 —
    한 잡이 앞을 막아 다른 잡을 굶기지 않도록."""
    while len(_ai_running) < _ai_limit:
        picked = None
        for i, (job_key, fn) in enumerate(_ai_waiting):
            if job_key not in _ai_running:
                picked = i
                break
        if picked is None:
            return
        job_key, fn = _ai_waiting[picked]
        del _ai_waiting[picked]
        _ai_running.add(job_key)
        _safe_call(fn)


def _safe_call(fn):
    """작업 하나가 터져도 큐 전체가 멈추지 않게 한다."""
    try:
        fn()
    except Exception:
        log.exception("LP3D 스케줄러 작업 오류")
