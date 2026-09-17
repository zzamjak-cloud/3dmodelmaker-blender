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


def _clear_model_tracking(job):
    """새 실행 전에 이전 세션의 모델 추적 정보를 비운다."""
    job.phase = ""
    job.requested_model = ""
    job.effective_model = ""
    job.model_fallback = False


def add_job(props, prompt: str = ""):
    """새 잡 항목을 만들어 리스트 끝에 붙이고 선택 상태로 만든다."""
    job = props.jobs.add()
    job.uid = props.next_uid
    props.next_uid += 1
    job.prompt = prompt
    job.lane = next_lane(props)
    props.job_index = len(props.jobs) - 1
    return job


def add_child_job(props, parent_uid, prompt: str = "", style: str = None):
    """배경 잡이 플랜에 따라 스폰하는 에셋 잡을 만든다.

    부모(와 이미 만들어진 형제들) 바로 뒤에 놓아 큐에서 묶여 보이게 한다.
    에셋은 부모 씬 컬렉션 안에서 조립되므로 레인 오프셋을 적용하지 않는다(lane 0).

    style은 부모 배경 잡의 스타일을 물려주기 위한 것이다 — 물려주지 않으면 씬은
    스타일리쉬인데 그 안의 프랍만 로우폴리로 나와 한 공간에서 스타일이 섞인다."""
    parent_uid = str(parent_uid)
    selected_uid = _selected_uid(props)
    job = props.jobs.add()
    job.uid = props.next_uid
    props.next_uid += 1
    job.prompt = prompt
    job.parent_uid = parent_uid
    job.creation_mode = 'OBJECT'
    job.modeling_type = 'PALETTE'
    if style:
        job.style = style
    job.lane = 0

    source = len(props.jobs) - 1
    target = _child_insert_index(props, parent_uid, skip=source)
    if target is not None and target < source:
        props.jobs.move(source, target)
        # move는 컬렉션 데이터를 재배치하므로 앞서 얻은 항목 참조가 다른 항목을
        # 가리키게 된다 — 옮겨 놓은 자리에서 다시 집어 돌려준다
        job = props.jobs[target]
    _select_uid(props, selected_uid)
    return job


def _selected_uid(props):
    """현재 선택된 항목의 uid. 항목 이동 뒤에도 선택을 유지하기 위한 기억값."""
    index = getattr(props, "job_index", -1)
    if 0 <= index < len(props.jobs):
        return props.jobs[index].uid
    return None


def _select_uid(props, uid):
    if uid is None:
        return
    for index, job in enumerate(props.jobs):
        if job.uid == uid:
            props.job_index = index
            return


def _child_insert_index(props, parent_uid: str, skip: int):
    """부모와 그 자식들 바로 뒤 인덱스. 부모를 못 찾으면 None."""
    target = None
    for index, job in enumerate(props.jobs):
        if index == skip:
            continue
        if str(job.uid) == parent_uid or getattr(job, "parent_uid", "") == parent_uid:
            target = index + 1
    return target


def children_of(props, parent_uid) -> list:
    """해당 부모 uid를 가리키는 자식 에셋 잡 목록 (큐 순서)."""
    parent_uid = str(parent_uid)
    if not parent_uid:
        return []
    return [job for job in props.jobs
            if getattr(job, "parent_uid", "") == parent_uid]


def remove_children(props, parent_uid) -> int:
    """자식 에셋 잡만 제거한다(부모는 남긴다). 제거한 개수를 반환한다.

    실행 중인 자식은 먼저 세션을 취소한다 — 항목만 지우면 세션이 고아로 남는다."""
    from . import session

    parent_uid = str(parent_uid)
    if not parent_uid:
        return 0
    indices = [index for index, job in enumerate(props.jobs)
               if getattr(job, "parent_uid", "") == parent_uid]
    if not indices:
        return 0
    selected_uid = _selected_uid(props)
    for index in reversed(indices):
        uid = props.jobs[index].uid
        if session.is_active(uid):
            session.cancel_session(uid)
        props.jobs.remove(index)
    props.job_index = min(getattr(props, "job_index", 0), max(0, len(props.jobs) - 1))
    _select_uid(props, selected_uid)
    return len(indices)


def remove_job(context, index: int):
    """항목을 제거한다. 실행 중이면 먼저 세션을 취소한다.

    배경 잡을 지우면 그 잡이 스폰한 에셋 잡도 함께 지운다 — 부모 없이 남은
    자식은 큐에서 실행도 취소도 되지 않는 좀비가 된다."""
    from . import session

    props = context.scene.lp3d
    if not (0 <= index < len(props.jobs)):
        return
    uid = props.jobs[index].uid
    remove_children(props, uid)
    # 자식은 부모 뒤에 있으므로 부모 인덱스는 그대로지만, 방어적으로 다시 찾는다
    index = next((i for i, job in enumerate(props.jobs) if job.uid == uid), index)
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
    # parent_uid는 일부러 빼둔다 — 복제본은 자식이 아니라 최상위 잡이어야 한다
    values = {
        "ref_image_path": src.ref_image_path,
        "modeling_type": src.modeling_type,
        "creation_mode": src.creation_mode,
        "scene_size": src.scene_size,
        "style": src.style,
        "character_type": getattr(src, "character_type", 'AUTO'),
    }
    job = add_job(props, src.prompt)
    for key, value in values.items():
        setattr(job, key, value)
    return job


def move_job(props, index: int, delta: int) -> int:
    """항목 순서를 바꾼다. 새 인덱스를 반환한다.

    에셋 자식 잡은 움직이지 않는다 — 부모 바로 뒤라는 자리가 곧 소속 표시이고,
    부모 위로 올라가면 리스트에서 어느 배경에 속한 에셋인지 읽을 수 없게 된다."""
    new_index = index + delta
    if not (0 <= index < len(props.jobs)) or not (0 <= new_index < len(props.jobs)):
        return index
    if getattr(props.jobs[index], "parent_uid", ""):
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
        if getattr(job, "parent_uid", ""):
            continue  # 에셋 잡은 부모 배경 세션이 직접 구동한다
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
    """실패·취소된 항목을 대기로 되돌리고 다시 시작한다.

    에셋 자식 잡은 혼자 다시 돌릴 수 없다 — 부모 배경 세션만이 자식을 구동하고
    결과를 키트로 거둬간다. 혼자 실행하면 결과가 어디에도 합쳐지지 않는다."""
    props = context.scene.lp3d
    if not (0 <= index < len(props.jobs)):
        return "항목을 찾을 수 없습니다"
    job = props.jobs[index]
    if getattr(job, "parent_uid", ""):
        return "에셋 항목은 단독 재시도할 수 없습니다 — 부모 배경 항목을 재시도하세요"
    # 배경 잡을 다시 돌리면 플랜부터 새로 세우므로 지난 에셋 잡은 의미가 없다
    remove_children(props, job.uid)
    index = next((i for i, item in enumerate(props.jobs) if item.uid == job.uid), index)
    job = props.jobs[index]
    job.state = 'PENDING'
    job.status = "대기 중"
    job.status_hint = ""
    job.log = ""
    job.iteration = 0
    _clear_model_tracking(job)
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
    orphaned = []
    for job in props.jobs:
        if getattr(job, "parent_uid", ""):
            continue  # 에셋 잡은 스스로 시작하지 못한다 — 아래에서 따로 처리한다
        if job.state == 'RUNNING' and not session.is_active(job.uid):
            job.state = 'PENDING'
            job.status = "대기 중 (세션이 끊겨 초기화됨)"
            _clear_model_tracking(job)
            orphaned.append(str(job.uid))
            fixed += 1
    # 에셋 잡은 부모 배경 세션만이 구동한다. 부모가 초기화됐거나 자기 세션이 끊겼으면
    # 대기로 되돌려도 영영 실행되지 않으므로, 실패로 못박아 부모 재시도 경로를 열어준다
    for job in props.jobs:
        parent_uid = getattr(job, "parent_uid", "")
        if not parent_uid or job.state not in ('RUNNING', 'PENDING'):
            continue
        dead = job.state == 'RUNNING' and not session.is_active(job.uid)
        if parent_uid in orphaned or dead:
            job.state = 'FAILED'
            job.status = "실패: 부모 배경 세션이 끊겼습니다"
            _clear_model_tracking(job)
            fixed += 1
    return fixed


def apply_lane_offset(collection_name: str, lane: int, spacing: float = None):
    """완료된 결과를 레인 번호만큼 Y축으로 밀어 배치 결과가 겹치지 않게 한다.

    생성 코드는 항상 원점 기준으로 작성되므로, 마무리 직전에 한 번만 적용한다.

    이동은 상대 이동이라 누적된다. 그래서 이미 민 오브젝트에는 적용한 레인을 표식으로
    남기고 차분(lanes.lane_shift)만 적용해 두 번 밀지 않는다 — 코드가 다시 실행되면
    오브젝트가 새로 생겨 표식이 없으므로 정상적으로 밀린다.

    spacing을 주면 레인 간격을 바꾼다 — 배경 공간처럼 결과가 넓게 퍼지는 잡은
    기본 간격으로는 옆 레인과 겹친다."""
    coll = bpy.data.collections.get(collection_name)
    if not coll:
        return
    gap = lanes.LANE_SPACING if spacing is None else spacing
    for obj in coll.objects:
        if obj.parent is not None:  # 자식은 부모를 따라 움직인다
            continue
        applied = obj.get(LANE_MARK)
        dy = lanes.lane_shift(applied, lane, gap)
        if not dy and applied is None:
            continue  # 레인 0 — 옮길 것도, 남길 표식도 없다
        obj.location.y += dy
        obj[LANE_MARK] = lane
