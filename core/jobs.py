# 생성 큐 관리: 잡 항목 추가·삭제·정렬과 세션 기동
#
# 씬의 잡 리스트(properties.LP3DJobItem)와 세션 상태머신(core/session.py)을
# 잇는 얇은 레이어다. 동시성 제어 자체는 core/scheduler.py가 한다 —
# 여기서 개수를 제한하지 않고, 대기 상태가 UI에 그대로 드러나게 한다.
import logging

import bpy

from . import lanes

log = logging.getLogger(__name__)

LANE_MARK = "lp3d_lane"  # 레인 오프셋을 이미 적용한 오브젝트에 남기는 표식


def add_job(props, prompt: str = ""):
    """새 잡 항목을 만들어 리스트 끝에 붙이고 선택 상태로 만든다.

    에이전트·턴 수는 씬 기본값을 상속한다 (항목별로 나중에 바꿀 수 있다)."""
    job = props.jobs.add()
    job.uid = props.next_uid
    props.next_uid += 1
    job.prompt = prompt
    job.agent = props.agent
    job.auto_turns = props.auto_turns
    job.lane = next_lane(props)
    props.job_index = len(props.jobs) - 1
    return job


def remove_job(context, index: int):
    """항목을 제거한다. 실행 중이면 먼저 세션을 취소한다."""
    from . import session

    props = context.scene.lp3d
    if not (0 <= index < len(props.jobs)):
        return
    uid = props.jobs[index].uid
    if session.is_active(uid):
        session.cancel_session(uid)
    props.jobs.remove(index)
    props.job_index = min(index, max(0, len(props.jobs) - 1))


def duplicate_job(props, index: int):
    """항목을 복제한다 — 같은 프롬프트로 조건만 바꿔 여러 개 돌릴 때 쓴다.

    결과·상태는 물려주지 않는다 (새로 실행할 대기 항목이다)."""
    if not (0 <= index < len(props.jobs)):
        return None
    # 원본 값을 먼저 복사해둔다 — props.jobs.add()가 컬렉션을 재할당하면
    # 앞서 얻은 항목 참조(src)가 무효가 되어 접근 시 크래시할 수 있다
    src = props.jobs[index]
    # improve_feedback은 일부러 뺀다 — 원본의 완성된 결과에 대한 피드백이라
    # 결과가 없는 새 대기 항목에 붙으면 첫 [개선하기]에 엉뚱하게 반영된다
    values = {
        "ref_image_path": src.ref_image_path,
        "agent": src.agent,
        "auto_turns": src.auto_turns,
    }
    job = add_job(props, src.prompt)
    for key, value in values.items():
        setattr(job, key, value)
    return job


def move_job(props, index: int, delta: int) -> int:
    """항목 순서를 바꾼다. 새 인덱스를 반환한다."""
    new_index = index + delta
    if not (0 <= index < len(props.jobs)) or not (0 <= new_index < len(props.jobs)):
        return index
    props.jobs.move(index, new_index)
    props.job_index = new_index
    return new_index


def next_lane(props) -> int:
    """아직 쓰이지 않은 가장 작은 레인 번호를 고른다.

    리스트에 있는 모든 항목의 레인을 점유로 본다 — 대기 중 항목끼리도 레인이 겹치면
    막상 동시에 실행됐을 때 결과가 원점에 포개진다."""
    used = {job.lane for job in props.jobs}
    lane = 0
    while lane in used:
        lane += 1
    return lane


def start_one(context, job) -> str:
    """항목 하나의 생성 세션을 시작한다. 오류 메시지 또는 ""를 반환한다."""
    from . import session

    return session.start_job(context.scene.name, job.uid) or ""


def start_all(context):
    """PENDING 항목을 전부 시작한다. (시작한 개수, 첫 오류 메시지)를 반환한다.

    한 항목이 시작에 실패해도(프롬프트 없음·CLI 없음 등) 나머지는 계속 시작한다."""
    props = context.scene.lp3d
    started, first_error = 0, ""
    for job in props.jobs:
        if job.state != 'PENDING':
            continue
        error = start_one(context, job)
        if error:
            job.state = 'FAILED'
            job.status = f"실패: {error}"
            if not first_error:
                first_error = error
            continue
        started += 1
    return started, first_error


def stop_all(context) -> int:
    """실행 중인 항목을 모두 중단한다. 중단한 개수를 반환한다."""
    from . import session

    props = context.scene.lp3d
    stopped = 0
    for job in props.jobs:
        if session.is_active(job.uid):
            session.cancel_session(job.uid)
            stopped += 1
    return stopped


def retry_job(context, index: int) -> str:
    """실패·취소된 항목을 대기로 되돌리고 다시 시작한다."""
    props = context.scene.lp3d
    if not (0 <= index < len(props.jobs)):
        return "항목을 찾을 수 없습니다"
    job = props.jobs[index]
    job.state = 'PENDING'
    job.status = "대기 중"
    job.status_hint = ""
    job.log = ""
    job.iteration = 0
    error = start_one(context, job)
    if error:
        job.state = 'FAILED'
        job.status = f"실패: {error}"
    return error


def reset_stale(props) -> int:
    """살아있는 세션이 없는 RUNNING 항목을 대기로 되돌린다.

    Dev Reload나 파일 다시 열기로 세션 객체가 사라져도 항목은 RUNNING으로 남아
    실행 버튼이 영영 잠기는 교착이 생긴다. 등록 시점에 이걸 푼다."""
    from . import session

    fixed = 0
    for job in props.jobs:
        if job.state == 'RUNNING' and not session.is_active(job.uid):
            job.state = 'PENDING'
            job.status = "대기 중 (세션이 끊겨 초기화됨)"
            job.phase = ""
            fixed += 1
    return fixed


def apply_lane_offset(collection_name: str, lane: int):
    """완료된 결과를 레인 번호만큼 Y축으로 밀어 배치 결과가 겹치지 않게 한다.

    생성 코드는 항상 원점 기준으로 작성되므로, 마무리 직전에 한 번만 적용한다.

    이동은 상대 이동이라 누적된다. 그래서 이미 민 오브젝트에는 적용한 레인을 표식으로
    남기고 차분(lanes.lane_shift)만 적용해 두 번 밀지 않는다 — 코드가 다시 실행되면
    오브젝트가 새로 생겨 표식이 없으므로 정상적으로 밀린다."""
    coll = bpy.data.collections.get(collection_name)
    if not coll:
        return
    for obj in coll.objects:
        if obj.parent is not None:  # 자식은 부모를 따라 움직인다
            continue
        applied = obj.get(LANE_MARK)
        dy = lanes.lane_shift(applied, lane)
        if not dy and applied is None:
            continue  # 레인 0 — 옮길 것도, 남길 표식도 없다
        obj.location.y += dy
        obj[LANE_MARK] = lane
