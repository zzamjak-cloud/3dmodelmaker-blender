# 생성 큐(병렬 AI + 직렬 Blender)를 실제 Blender에서 검증한다 (헤들리스)
#
# 단위 테스트는 bpy 비의존 모듈(scheduler·lanes·snapshots 산술)만 덮는다.
# 잡 리스트·레인 배치·세션 수명처럼 bpy에 붙은 부분은 여기서만 확인할 수 있다.
# AI CLI는 부르지 않는다 — 돈·시간이 들고 네트워크에 의존한다. 대신 CLI 없이
# 도달 가능한 경로(자료구조, 레인 배치, 시작 거부, 종료 정리)만 훑는다.
#
# 실행:
#   .\scripts\dev_run.ps1 -Background -PythonFile tests\verify_queue_in_blender.py
#   ./scripts/dev_link.sh && blender -b --python tests/verify_queue_in_blender.py
import bpy
from bl_ext.user_default.lp3d_modelmaker.core import jobs, lanes, scheduler, session

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


props = bpy.context.scene.lp3d

# 1) 빈 큐 — 구버전 .blend를 열었을 때와 같은 상태다. 여기서 터지면 패널 전체가 죽는다
check("빈 큐에서 active_job=None", props.active_job() is None)
check("uid 조회 실패는 None", props.job_by_uid(999) is None)

# 2) 잡 추가와 레인 배정 — 레인이 겹치면 배치 결과가 원점에 포개진다
a = jobs.add_job(props, "나무 상자")
b = jobs.add_job(props, "돌 항아리")
c = jobs.add_job(props, "철제 랜턴")
check("잡 3개 추가", len(props.jobs) == 3, str(len(props.jobs)))
check("레인 충돌 없음", len({j.lane for j in props.jobs}) == 3,
      str(sorted(j.lane for j in props.jobs)))
check("uid 순차 발급", {a.uid, b.uid, c.uid} == {1, 2, 3})
check("uid 조회", props.job_by_uid(b.uid).prompt == "돌 항아리")

# 3) 복제 — props.jobs.add()가 컬렉션을 재할당해도 원본 참조로 크래시하지 않아야 한다
dup = jobs.duplicate_job(props, 0)
check("복제 성공", dup is not None and len(props.jobs) == 4)
check("복제본 프롬프트 승계", dup.prompt == "나무 상자", dup.prompt)
check("복제본 레인 충돌 없음", len({j.lane for j in props.jobs}) == 4,
      str(sorted(j.lane for j in props.jobs)))

# 4) 순서 이동 — 범위 밖 요청은 조용히 무시해야 한다 (버튼은 항상 눌린다)
first = props.jobs[0].uid
jobs.move_job(props, 0, 1)
check("아래로 이동", props.jobs[1].uid == first)
jobs.move_job(props, 1, -1)
check("위로 이동 원복", props.jobs[0].uid == first)
n = len(props.jobs)
jobs.move_job(props, 0, -1)
jobs.move_job(props, n - 1, 1)
check("범위 밖 이동 무시", len(props.jobs) == n)

# 5) 삭제 — job_index가 범위를 벗어나면 패널 draw가 매 프레임 터진다
jobs.remove_job(bpy.context, len(props.jobs) - 1)
check("삭제 반영", len(props.jobs) == n - 1)
check("job_index 범위 유지", 0 <= props.job_index < len(props.jobs), str(props.job_index))
while len(props.jobs):
    jobs.remove_job(bpy.context, 0)
check("전부 삭제 후 active_job=None", props.active_job() is None)

# 6) 레인 오프셋 멱등성 — 개선하기를 누를 때마다 모델이 +Y로 밀려나던 결함의 회귀 검사
coll = bpy.data.collections.new("LP3D_LaneProbe")
bpy.context.scene.collection.children.link(coll)
obj = bpy.data.objects.new("LP3D_LaneProbeObj", bpy.data.meshes.new("LP3D_LaneProbeMesh"))
coll.objects.link(obj)
jobs.apply_lane_offset("LP3D_LaneProbe", 2)
once = obj.location.y
check("레인 2 적용", abs(once - lanes.lane_dy(2)) < 1e-6, f"y={once}")
jobs.apply_lane_offset("LP3D_LaneProbe", 2)
check("같은 레인 재적용은 이동 없음", abs(obj.location.y - once) < 1e-6,
      f"{once} -> {obj.location.y}")
jobs.apply_lane_offset("LP3D_LaneProbe", 0)
check("레인 0 복귀", abs(obj.location.y) < 1e-6, f"y={obj.location.y}")
bpy.data.objects.remove(obj)
bpy.data.collections.remove(coll)

# 7) 스케줄러·세션 유휴 상태
counts = scheduler.counts()
check("스케줄러 유휴", all(v == 0 for v in counts.values()), str(counts))
check("세션 없음", session.is_active() is False and session.active_count() == 0)

# 8) 시작 거부 — 프롬프트가 비면 세션을 만들지 않고 오류 문구를 돌려줘야 한다
empty = jobs.add_job(props, "")
error = session.start_job(bpy.context.scene.name, empty.uid)
check("빈 프롬프트 거부", bool(error), repr(error))
check("거부 후 세션 잔재 없음", session.active_count() == 0)
jobs.remove_job(bpy.context, len(props.jobs) - 1)

# 9) 교착 해제 — Dev Reload·파일 다시 열기로 세션만 사라지고 RUNNING이 남는 상황
stuck = jobs.add_job(props, "좀비 확인")
stuck.state = 'RUNNING'
check("reset_stale 1건 처리", jobs.reset_stale(props) == 1)
check("RUNNING -> PENDING", stuck.state == 'PENDING', stuck.state)
jobs.remove_job(bpy.context, len(props.jobs) - 1)

# 10) 유휴 상태 shutdown — Dev Reload가 살아있는 세션 없이도 안전해야 한다
session.shutdown()
check("유휴 shutdown 무해", session.active_count() == 0)

print(("실패 " + ", ".join(fails)) if fails else "모두 통과")
if fails:
    raise SystemExit(1)
