# 병렬 AI 생성 + Blender 직렬 큐 설계

> 과거 큐 설계 기록입니다. 2026-09-07부터 Claude 지원과 비평·개선 턴은 제거되었습니다. 큐의 병렬 AI 호출과 Blender 직렬 실행은 유지하며, 각 항목은 Astra 단일 생성으로 완료합니다. 현재 사용법은 [README](../../../README.md)를 따릅니다.

- 날짜: 2026-09-03
- 대상: `lp3d_modelmaker` (Blender 애드온)
- 상태: 승인됨

## 1. 문제

현재 `core/session.py`는 `_current` 싱글턴으로 생성 세션을 **동시에 1개만** 허용한다.
그런데 실제 병목은 성격이 다른 두 가지가 섞여 있다.

- **AI 호출(CLI 서브프로세스)**: `claude -p` / `codex exec`는 각각 독립 프로세스다.
  여러 개를 동시에 띄워도 서로 간섭하지 않는다. 시간의 대부분이 대기다(수십 초~수 분).
- **Blender 작업**: `bpy` 조작은 단일 스레드·전역 상태다. 반드시 하나씩 순차 실행해야 한다.

따라서 세션 전체를 직렬화하는 것은 과도한 제약이다. 사용자는 여러 프롬프트를
한꺼번에 걸어두고 자리를 비우고 싶어 한다.

## 2. 목표

1. AI 호출은 여러 개가 동시에 진행된다(동시 개수는 환경설정으로 제한).
2. Blender 작업은 전역 FIFO 큐로 항상 하나씩만 실행된다.
3. AI 모델러 탭의 프롬프트 입력이 **리스트(큐)** 기반으로 바뀐다.
   항목을 추가해 쌓아두고 한 번에 실행한다.
4. 한 항목이 실패해도 나머지 큐는 계속 진행된다.
5. 개선/변형/익스포트/에셋 등록은 **리스트에서 선택한 항목**을 대상으로 한다.

### 비목표 (YAGNI)

- 잡 우선순위, 스케줄 예약, 잡 간 의존성
- 여러 .blend 파일/씬에 걸친 분산 실행
- 진행 중인 잡의 프롬프트 편집
- 실패 항목 자동 재시도 (수동 `[재시도]` 버튼만 제공)

## 3. 현재 구조에서 확인한 제약

설계 근거가 되는, 코드에서 실제로 확인한 사실들이다.

| 사실 | 위치 | 설계 영향 |
|---|---|---|
| CLI 실행기가 이미 다중 작업 리스트 기반 | `core/runner.py` `_jobs` | AI 병렬은 거의 그대로 동작한다 |
| `runner.cancel()`이 **모든** 프로세스를 종료 | `core/runner.py` | 잡 단위 취소로 바꿔야 한다 |
| `set_keepalive`가 전역 불리언 | `core/runner.py` | 한 세션 종료가 다른 세션의 펌프를 끈다. 참조 카운트로 바꿔야 한다 |
| 캡처가 세션 컬렉션 외 오브젝트를 `hide_render`로 모두 숨김 | `core/capture.py` | 여러 잡의 컬렉션이 씬에 동시에 있어도 렌더가 오염되지 않는다 |
| `lowpoly.set_session()`이 전역 컬렉션 이름 | `lowpoly/__init__.py` | `executor.execute()`가 원자적으로 실행되면 안전하다 |
| `executor.snapshot()/rollback()`이 `bpy.data` 전체 이름 집합 기준 | `core/executor.py` | 직렬 실행이면 자기 잡의 데이터만 롤백된다. 병렬이면 남의 결과를 지운다 |
| 렌더/스냅샷/정리가 모두 동기 블로킹 호출 | `capture.py`, `snapshots.py` | 큐 작업 1개 = 타이머 틱 1개로 원자 실행 가능 |
| 결과 컬렉션이 항상 원점에 생성 | `session.py`, `snapshots.py` | 배치 실행 시 결과들이 겹친다. 잡별 배치 오프셋이 필요하다 |
| 상태(status/log/iteration/phase)가 씬 단일 필드 | `properties.py` | 잡 항목별 상태로 이전해야 한다 |
| 한글 입력이 OS 네이티브 팝업 경유 | `core/native_input.py` | 항목 추가도 이 경로를 유지해야 한다 |

## 4. 아키텍처

```
UI (jobs UIList)
      |  전체 실행
      v
core/jobs.py        각 항목마다 GenerationSession 생성·추적
      |
      v
core/session.py     상태머신 (기존 재시도·폴백 로직 유지)
      |                     |
   AI 단계               Blender 단계
      |                     |
      v                     v
core/scheduler.py   AI 슬롯 N개        Blender 슬롯 1개 (FIFO)
      |                     |
      v                     v
core/runner.py      subprocess       executor / capture / snapshots
```

### 4.1 `core/scheduler.py` (신규)

두 개의 큐를 관리한다. **bpy에 의존하지 않는 순수 로직**으로 작성해
`core/loop.py`처럼 Blender 없이 단위 테스트한다. 타이머 연동은 얇은 어댑터로 분리한다.

```
submit_ai(job_key, fn)        # AI 슬롯이 나면 fn() 호출
release_ai(job_key)           # CLI 응답 도착 시 슬롯 반환
submit_blender(job_key, fn)   # FIFO. 한 번에 하나, 틱당 하나
cancel_job(job_key)           # 해당 잡의 대기 항목을 큐에서 제거
set_ai_limit(n)               # 환경설정 반영
counts()                      # (ai_running, ai_waiting, blender_waiting) — UI 표시용
pump()                        # 타이머가 주기적으로 호출. 실행 가능한 작업을 꺼내 실행
```

원칙:

- **AI 슬롯**: 잡 하나가 동시에 점유하는 AI 슬롯은 최대 1개다(상태머신이 한 번에
  한 요청만 보낸다). 따라서 슬롯 수 = 동시 진행 잡 수의 상한이 된다.
- **Blender 슬롯**: 항상 1. 작업은 동기 실행되므로 실행 = 완료다. 틱당 1개만 처리해
  작업 사이에 UI 리드로우가 들어가게 한다.
- **FIFO**: 제출 순서를 보장해 기아를 막는다.
- **취소**: 대기 중 항목은 큐에서 제거하고, 실행 중 AI 프로세스는
  `runner.cancel(job_key)`로 종료한다.

### 4.2 `core/runner.py` 변경

- `run_cli_async(...)`에 `job_key` 인자 추가. 각 job dict에 저장한다.
- `cancel(job_key=None)`: 키가 주어지면 해당 잡의 프로세스만 종료, 없으면 전체(기존 동작).
- `set_keepalive(active)` → 잡 단위 참조 카운트(`add_keepalive(key)` /
  `remove_keepalive(key)`)로 변경. 한 세션이 끝났다고 다른 세션의 펌프가 꺼지면 안 된다.
- 펌프에서 `scheduler.pump()`를 함께 호출한다.

### 4.3 `core/session.py` 변경

- `_current` → `_sessions: dict[int, GenerationSession]` (키는 잡 uid)
- `is_active()` — 하나라도 실행 중이면 True (기존 호출부 호환)
- `is_active(uid)` / `active_count()` 추가
- `start_session(context, ...)` → `start_job(scene_name, job_uid, mode, ...)`.
  프롬프트·참조 이미지·턴 수·에이전트를 **잡 항목에서** 읽는다(씬 필드가 아니라).
- `_props()` → `_job()`: `scene.lp3d.jobs`에서 `uid`로 항목을 찾아 반환.
  항목이 사라졌으면(사용자가 삭제) 세션을 조용히 종료한다.
- `_dispatch()`는 `scheduler.submit_ai(uid, ...)`로 감싼다.
  `_on_response()` 진입 시 `scheduler.release_ai(uid)`.
- Blender를 건드리는 지점을 `scheduler.submit_blender(uid, ...)`로 감싼다:
  - `_execute()`의 `executor.execute()`
  - `_critique()` 및 개선 모드 `start()`의 `capture.capture_collection()`
  - `snapshots.capture_turn()`
  - `_finalize()`의 정리 작업
  - `cancel()`의 컬렉션 정리
- 잡마다 **레인 오프셋**을 배정한다. 잡의 `lane` 인덱스에 따라 결과 컬렉션 전체를
  Y축으로 이동시켜 배치 결과가 겹치지 않게 한다. 스냅샷은 X축을 쓰므로 축이 충돌하지 않는다.
  오프셋은 `_finalize()` 직전 한 번 적용한다(생성 코드는 항상 원점 기준으로 작성되므로).

### 4.4 `properties.py` 변경

```
class LP3DJobItem(bpy.types.PropertyGroup):
    # 입력
    uid: IntProperty()
    prompt: StringProperty()
    ref_image_path: StringProperty(subtype='FILE_PATH')
    agent: EnumProperty(CLAUDE/CODEX)
    auto_turns: IntProperty(default=3, min=1, max=9)
    # 실행 상태
    state: EnumProperty(PENDING/RUNNING/DONE/FAILED/CANCELLED)
    status, status_hint, phase: StringProperty()
    iteration, total_turns: IntProperty()
    started_at: FloatProperty()
    log: StringProperty()
    # 결과
    collection_name, code, entry_id, multiview_path: StringProperty()
    lane: IntProperty()
    improve_feedback: StringProperty()
```

씬 프로퍼티:

- 추가: `jobs: CollectionProperty(type=LP3DJobItem)`, `job_index: IntProperty()`,
  `next_uid: IntProperty(default=1)`
- 제거(잡 항목으로 이전): `prompt`, `ref_image_path`, `improve_feedback`, `improve_open`,
  `is_running`, `status`, `status_hint`, `phase`, `started_at`, `iteration`,
  `total_turns`, `log`, `last_collection`, `last_code`, `last_prompt`,
  `last_entry_id`, `multiview_path`
- 유지: `agent`, `auto_turns`(새 항목의 **기본값**으로 사용), `export_dir`,
  `multiview_preview_open`

`persist.py`의 `_SCENE_KEYS`는 그대로(`agent`, `auto_turns`, `export_dir`).
잡 리스트는 .blend에 저장되며 별도 JSON 영속화는 하지 않는다.

### 4.5 `core/jobs.py` (신규)

씬 잡 리스트와 세션을 잇는 얇은 레이어. bpy를 쓴다.

```
add_job(props, prompt, **overrides) -> item   # uid 발급, 기본값(agent/turns) 상속
remove_job(props, index)                      # 실행 중이면 취소 후 제거
duplicate_job(props, index)
move_job(props, index, delta)
start_all(context)                            # PENDING 항목을 전부 세션으로 띄운다
stop_all(context)                             # RUNNING 항목 전부 취소
retry_job(context, index)                     # FAILED -> PENDING 후 시작
next_lane(props) -> int                       # 사용 중이 아닌 최소 레인 번호
reset_stale(props)                            # 살아있는 세션이 없는 RUNNING을 PENDING으로
```

`start_all()`은 PENDING 항목마다 즉시 `GenerationSession`을 만든다. 실제 동시성 제어는
스케줄러가 하므로 여기서 개수를 제한하지 않는다(세션 객체는 가볍고, 대기 상태가
UI에 그대로 드러나는 편이 낫다).

### 4.6 UI (`ui/panel.py`, `ui/operators.py`)

**AI 모델 생성 패널**

- `LP3D_UL_jobs` UIList
  - 좌: 상태 아이콘 (`DOT` 대기 / `PLAY` 실행 / `CHECKMARK` 완료 / `ERROR` 실패 / `X` 취소)
  - 중: 프롬프트 (없으면 "(빈 프롬프트)")
  - 우: 실행 중이면 `2/3`, 실패면 `실패`
- 우측 세로 버튼: `＋`(추가) `－`(삭제) 복제 `▲▼`(순서)
- `＋`는 `lp3d.edit_prompt`와 같은 OS 네이티브 팝업으로 프롬프트를 받아 항목을 추가한다
  (한글 IME 문제 회피 경로를 유지)
- 선택 항목 상세 박스: 프롬프트 편집 버튼, 참조 이미지(붙여넣기·해제), 에이전트,
  자동 반복, 멀티뷰 미리보기(기존 UI 이식), 상태·조치 문구
- 실행 컨트롤: `[전체 실행]` / `[전체 중지]`, 실패 항목 선택 시 `[재시도]`
- 요약 줄: `대기 3 · AI 실행 2 · Blender 대기 1`

**로그 패널**: 선택 항목의 `log`를 보여준다.

**결과물 패널**: 선택 항목이 `DONE`일 때만 개선/변형/익스포트/에셋 등록/평가를 활성화한다.
변형은 새 잡 항목을 큐에 추가하고 바로 시작한다.

기존 오퍼레이터 조정:

- `lp3d.generate` → `lp3d.queue_start` (전체 실행)
- `lp3d.cancel` → `lp3d.queue_stop` (전체 중지) + `lp3d.job_cancel` (선택 항목)
- `lp3d.edit_prompt`의 `target`을 잡 항목 기준(`prompt` / `improve_feedback`)으로 재배선
- `lp3d.improve` / `lp3d.variation` / `lp3d.export` / `lp3d.mark_asset` /
  `lp3d.rate` / `lp3d.library_discard` / `lp3d.clear_snapshots` /
  `lp3d.paste_ref_image` / `lp3d.clear_ref_image` / 멀티뷰 오퍼레이터들의
  `poll`과 대상을 **선택 잡 항목** 기준으로 변경
- 신규: `lp3d.job_add`, `lp3d.job_remove`, `lp3d.job_duplicate`, `lp3d.job_move`,
  `lp3d.job_retry`, `lp3d.job_cancel`

### 4.7 `preferences.py` 변경

```
ai_concurrency: IntProperty(
    name="동시 AI 실행 수",
    description="동시에 실행할 AI CLI 개수. Blender 작업은 항상 하나씩 순차 실행된다",
    default=3, min=1, max=8, update=_persist_cb,
)
```

`persist.py`의 `_PREF_KEYS`에 `ai_concurrency`를 추가한다. `1`로 두면 기존과 동일한
순차 동작이 되므로 문제 발생 시 안전한 폴백이다.

## 5. 오류 처리

- **항목 실패**: 해당 항목만 `FAILED`, `status`/`status_hint`에 원인·조치 저장. 큐는 계속.
- **잡 항목 삭제 중 실행**: `_job()`이 `None`을 반환하면 세션을 즉시 종료하고 정리한다.
- **Dev Reload / 파일 다시 열기**: 세션 객체가 사라져도 항목이 `RUNNING`으로 남을 수 있다.
  등록 시점에 `jobs.reset_stale()`로 살아있는 세션이 없는 `RUNNING` 항목을 `PENDING`으로
  되돌린다 (기존 `is_running` 교착 처리의 확장).
- **취소**: `stop_all()`은 실행 중 세션을 모두 취소하고 대기 큐를 비운다.

## 6. 테스트

- `tests/test_scheduler.py` (신규, bpy 비의존)
  - AI 슬롯 한도 준수, 슬롯 반환 후 대기 작업 실행
  - Blender 큐 FIFO 순서, 동시 실행 없음
  - `cancel_job`이 대기 항목만 제거하고 다른 잡에 영향 없음
  - 기아 없음: 계속 제출해도 먼저 들어온 작업이 먼저 실행됨
  - `counts()` 값 정확성
- 기존 순수 모듈 테스트(`test_loop.py` 등)는 영향 없음
- `tests/manual_scenarios.md`에 배치 시나리오 추가:
  프롬프트 3개 큐잉 → 전체 실행 → AI 3개 동시 진행, Blender 단계는 하나씩 →
  결과 3개가 Y축으로 나란히 배치 → 하나 실패해도 나머지 완료

## 7. 마이그레이션

기존 .blend 파일은 제거된 씬 프로퍼티 값을 잃는다(잡 리스트는 비어 있음). 사용자에게는
"프롬프트를 다시 추가" 수준의 영향이므로 자동 마이그레이션은 하지 않는다.
버전은 `0.7.0`으로 올린다(호환성이 깨지는 변경).
