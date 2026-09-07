# 병렬 AI 생성 + Blender 직렬 큐 Implementation Plan

> 과거 큐 구현 기록입니다. 2026-09-07부터 Claude 지원과 비평·개선 턴은 제거되었습니다. 큐의 병렬 AI 호출과 Blender 직렬 실행은 유지하며, 각 항목은 Astra 단일 생성으로 완료합니다. 현재 사용법은 [README](../../../README.md)를 따릅니다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI(CLI) 호출은 여러 개를 동시에 진행하고 Blender 작업만 전역 FIFO 큐로 하나씩 실행하도록 분리하고, AI 모델러 탭을 프롬프트 리스트(큐) 기반으로 바꾼다.

**Architecture:** 순수 파이썬 스케줄러(`core/scheduler.py`)가 AI 슬롯 N개와 Blender 슬롯 1개를 관리한다. `core/runner.py`의 기존 타이머 펌프가 스케줄러를 구동한다. `core/session.py`의 상태머신은 로직을 유지한 채 AI 호출 지점을 `submit_ai`로, bpy 조작 지점을 `submit_blender`로 감싸고 싱글턴에서 `uid → 세션` 딕셔너리로 바뀐다. 씬 단일 상태 필드는 `LP3DJobItem` 컬렉션으로 이전하고, `core/jobs.py`가 리스트 조작과 세션 기동을 담당한다.

**Tech Stack:** Python 3.11 (Blender 4.2 번들), `bpy` / `bmesh`, `unittest` (bpy 비의존 순수 모듈만 테스트), Blender Extensions manifest.

**Spec:** `docs/superpowers/specs/2026-09-03-parallel-ai-blender-queue-design.md`

## Global Constraints

- 모든 주석·독스트링·UI 문자열은 **한국어**로 작성한다 (`CLAUDE.md` 언어 규칙). 변수명·함수명은 영어.
- `core/scheduler.py`는 **`bpy`를 import하지 않는다**. Blender 없이 `unittest`로 돌아야 한다 (`core/loop.py`와 동일한 규약).
- 테스트는 `tests/test_*.py`에 `unittest` 스타일로 작성하고, 리포 파일을 `importlib.util.spec_from_file_location`으로 직접 로드한다 (`tests/test_loop.py`의 `_load` 헬퍼 패턴). `core` 패키지를 import하면 `bpy` 때문에 실패한다.
- 테스트 실행 명령: `python -m unittest discover -s tests -v` (리포 루트에서).
- 새 모듈은 `__init__.py`의 `_discover_submodules()`가 자동으로 찾으므로 목록 갱신이 필요 없다. 단 `ui/` 하위 모듈은 `_MODULES` 튜플에 명시되어 있다.
- 애드온 버전은 최종 태스크에서 `blender_manifest.toml`의 `version`을 `0.6.1` → `0.7.0`으로 올린다.
- `bpy.types.PropertyGroup`의 `EnumProperty` 항목 식별자는 대문자 스네이크(`PENDING`, `CLAUDE`)를 쓴다 (기존 `agent` 프로퍼티와 동일).
- 커밋 메시지는 한국어. 각 태스크 끝에서 커밋한다.

---

### Task 1: `core/scheduler.py` — AI 슬롯 + Blender FIFO 큐

**Files:**
- Create: `core/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: 없음 (순수 모듈, 최초 태스크)
- Produces:
  - `set_ai_limit(n: int) -> None`
  - `submit_ai(job_key, fn) -> None` — 슬롯이 있으면 즉시 `fn()` 호출, 없으면 대기열에 넣는다
  - `release_ai(job_key) -> None` — 해당 잡의 AI 슬롯을 반환한다
  - `submit_blender(job_key, fn) -> None` — FIFO 대기열에 넣는다 (즉시 실행하지 않는다)
  - `pump() -> None` — 대기 중 작업을 실행 가능한 만큼 꺼내 실행한다. Blender는 호출당 최대 1개
  - `cancel_job(job_key) -> int` — 해당 잡의 대기 항목을 모두 제거하고 제거 개수를 반환. AI 슬롯도 반환한다
  - `counts() -> dict` — `{"ai_running": int, "ai_waiting": int, "blender_waiting": int}`
  - `has_work() -> bool` — 실행 중이거나 대기 중인 작업이 하나라도 있는가
  - `reset() -> None` — 전체 상태 초기화 (Dev Reload·테스트용)

- [ ] **Step 1: 실패하는 테스트를 작성한다**

`tests/test_scheduler.py` 전체 내용:

```python
# 스케줄러 정책 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    """리포 내 파일을 패키지 임포트 없이 모듈로 읽어들인다 (core/__init__.py의 bpy 회피)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scheduler = _load("scheduler", "core/scheduler.py")


class SchedulerTestBase(unittest.TestCase):
    def setUp(self):
        scheduler.reset()
        self.calls = []

    def _rec(self, tag):
        """호출 순서를 기록하는 작업 함수를 만든다."""
        return lambda: self.calls.append(tag)


class TestAiSlots(SchedulerTestBase):
    def test_runs_immediately_within_limit(self):
        # 한도 안이면 제출 즉시 실행된다 — 대기 없이 CLI가 떠야 한다
        scheduler.set_ai_limit(2)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        self.assertEqual(self.calls, ["a", "b"])

    def test_queues_beyond_limit(self):
        # 한도를 넘으면 대기 — 슬롯이 반환될 때까지 실행되지 않는다
        scheduler.set_ai_limit(2)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.submit_ai(3, self._rec("c"))
        self.assertEqual(self.calls, ["a", "b"])
        self.assertEqual(scheduler.counts()["ai_waiting"], 1)

    def test_release_runs_next(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        self.assertEqual(self.calls, ["a"])
        scheduler.release_ai(1)
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])

    def test_release_unknown_job_is_noop(self):
        # 이미 끝난 잡의 중복 반환이 슬롯 수를 음수로 만들면 안 된다
        scheduler.set_ai_limit(1)
        scheduler.release_ai(99)
        scheduler.release_ai(99)
        self.assertEqual(scheduler.counts()["ai_running"], 0)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        self.assertEqual(self.calls, ["a"])

    def test_one_slot_per_job(self):
        # 한 잡은 AI 슬롯을 최대 1개만 점유한다 (상태머신이 한 번에 한 요청)
        scheduler.set_ai_limit(3)
        scheduler.submit_ai(1, self._rec("a1"))
        scheduler.submit_ai(1, self._rec("a2"))
        self.assertEqual(self.calls, ["a1"])
        self.assertEqual(scheduler.counts()["ai_running"], 1)

    def test_fifo_order(self):
        # 기아 방지: 먼저 제출된 작업이 먼저 실행된다
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        for key, tag in ((2, "b"), (3, "c"), (4, "d")):
            scheduler.submit_ai(key, self._rec(tag))
        for key in (1, 2, 3):
            scheduler.release_ai(key)
            scheduler.pump()
        self.assertEqual(self.calls, ["a", "b", "c", "d"])

    def test_raising_limit_starts_waiting(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.set_ai_limit(2)
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])


class TestBlenderQueue(SchedulerTestBase):
    def test_not_run_until_pump(self):
        # Blender 작업은 제출 즉시 실행하지 않는다 — 타이머 틱에서만 돈다
        scheduler.submit_blender(1, self._rec("a"))
        self.assertEqual(self.calls, [])
        scheduler.pump()
        self.assertEqual(self.calls, ["a"])

    def test_one_per_pump(self):
        # 틱당 1개만 — 작업 사이에 UI 리드로우가 들어가야 한다
        scheduler.submit_blender(1, self._rec("a"))
        scheduler.submit_blender(2, self._rec("b"))
        scheduler.pump()
        self.assertEqual(self.calls, ["a"])
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])

    def test_fifo_across_jobs(self):
        for key, tag in ((1, "a"), (2, "b"), (1, "c")):
            scheduler.submit_blender(key, self._rec(tag))
        for _ in range(3):
            scheduler.pump()
        self.assertEqual(self.calls, ["a", "b", "c"])

    def test_task_exception_does_not_stall_queue(self):
        # 한 작업이 터져도 큐가 멈추면 안 된다 (항목 실패해도 나머지는 계속)
        def boom():
            raise RuntimeError("의도된 실패")

        scheduler.submit_blender(1, boom)
        scheduler.submit_blender(2, self._rec("b"))
        scheduler.pump()
        scheduler.pump()
        self.assertEqual(self.calls, ["b"])

    def test_nested_submit_runs_next_pump(self):
        # 작업 중 새 작업을 제출해도 같은 틱에 연달아 실행되지 않는다
        scheduler.submit_blender(1, lambda: (self.calls.append("a"),
                                             scheduler.submit_blender(1, self._rec("b"))))
        scheduler.pump()
        self.assertEqual(self.calls, ["a"])
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])


class TestCancel(SchedulerTestBase):
    def test_cancel_removes_only_that_job(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a1"))
        scheduler.submit_ai(2, self._rec("a2"))
        scheduler.submit_blender(2, self._rec("b2"))
        scheduler.submit_blender(3, self._rec("b3"))
        removed = scheduler.cancel_job(2)
        self.assertEqual(removed, 2)
        scheduler.pump()
        self.assertEqual(self.calls, ["a1", "b3"])

    def test_cancel_releases_running_slot(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.cancel_job(1)
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])


class TestCounts(SchedulerTestBase):
    def test_counts_and_has_work(self):
        self.assertFalse(scheduler.has_work())
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.submit_blender(3, self._rec("c"))
        self.assertEqual(scheduler.counts(),
                         {"ai_running": 1, "ai_waiting": 1, "blender_waiting": 1})
        self.assertTrue(scheduler.has_work())
        scheduler.reset()
        self.assertFalse(scheduler.has_work())
        self.assertEqual(scheduler.counts(),
                         {"ai_running": 0, "ai_waiting": 0, "blender_waiting": 0})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m unittest discover -s tests -p "test_scheduler.py" -v`
Expected: FAIL — `FileNotFoundError` 또는 `spec_from_file_location` 실패 (`core/scheduler.py` 없음)

- [ ] **Step 3: `core/scheduler.py`를 구현한다**

```python
# AI/Blender 작업 스케줄러: AI는 N개 동시, Blender는 항상 1개씩 순차
#
# 이 모듈은 bpy에 의존하지 않는 순수 로직만 담는다 (Blender 없이 테스트 가능).
# 타이머 연동은 core/runner.py의 펌프가 pump()를 주기적으로 호출하는 방식이다.
#
# 배경: CLI 서브프로세스(claude/codex)는 서로 독립이라 여러 개를 동시에 띄워도
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
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m unittest discover -s tests -p "test_scheduler.py" -v`
Expected: PASS (전체 케이스)

- [ ] **Step 5: 전체 테스트가 깨지지 않았는지 확인한다**

Run: `python -m unittest discover -s tests -v`
Expected: PASS (기존 테스트 포함)

- [ ] **Step 6: 커밋한다**

```bash
git add core/scheduler.py tests/test_scheduler.py
git commit -m "AI 슬롯 N개 + Blender 직렬 FIFO 큐 스케줄러 추가"
```

---

### Task 2: `core/runner.py` — 잡 단위 취소·keepalive, 스케줄러 펌프 연동

**Files:**
- Modify: `core/runner.py`

**Interfaces:**
- Consumes: `core/scheduler.py`의 `pump()`, `has_work()`
- Produces:
  - `run_cli_async(cmd, cwd, timeout, on_done, stdin_text=None, job_key=None)` — `job_key` 인자 추가
  - `cancel(job_key=None)` — 키가 있으면 해당 잡만, 없으면 전체
  - `add_keepalive(job_key)` / `remove_keepalive(job_key)` — 기존 `set_keepalive(bool)` 대체

- [ ] **Step 1: `run_cli_async`에 `job_key`를 추가한다**

`core/runner.py`의 시그니처와 job dict를 수정한다:

```python
def run_cli_async(cmd: list, cwd: str, timeout: int, on_done, stdin_text: str = None,
                  job_key=None):
    """CLI를 논블로킹으로 실행하고 완료 시 메인 스레드에서 on_done(stdout, error)를 호출한다.

    job_key는 잡 단위 취소용 식별자다 (없으면 전체 취소에만 걸린다).

    stdin_text가 주어지면 파일로 저장해 stdin으로 넘긴다. 프롬프트를 명령줄 인자로
    넘기면 Windows의 .cmd 셸림(npm 설치본)이 첫 줄에서 잘라버리기 때문이다."""
    global _job_counter
    _job_counter += 1
    out_path = os.path.join(cwd, f"cli_stdout_{_job_counter}.log")
    err_path = os.path.join(cwd, f"cli_stderr_{_job_counter}.log")
    job = {
        "cmd": cmd, "on_done": on_done, "cancelled": False, "job_key": job_key,
        "out_path": out_path, "err_path": err_path,
        "deadline": time.monotonic() + timeout, "timeout": timeout,
    }
```

이하 본문은 그대로 둔다.

- [ ] **Step 2: `cancel()`을 잡 단위로 바꾼다**

기존 `cancel()`을 통째로 아래로 교체한다:

```python
def cancel(job_key=None):
    """진행 중인 CLI 프로세스를 종료한다.

    job_key가 주어지면 그 잡의 프로세스만, 없으면 전부 종료한다.
    여러 세션이 동시에 도는 구조에서 한 세션의 취소가 남의 프로세스를
    죽이면 안 되므로 기본은 잡 단위 호출이다."""
    for job in _jobs:
        if job_key is not None and job.get("job_key") != job_key:
            continue
        job["cancelled"] = True
        if job["proc"].poll() is None:
            job["proc"].terminate()
```

- [ ] **Step 3: `set_keepalive`를 참조 카운트로 교체한다**

`_state`의 `keepalive` 항목을 집합으로 바꾸고, `set_keepalive`를 두 함수로 교체한다:

```python
_jobs = []  # 진행 중인 CLI 작업 목록
_state = {"pump_on": False, "last_redraw": 0.0}
_keepalive = set()  # 펌프를 살려둬야 하는 job_key 집합
```

```python
def add_keepalive(job_key):
    """세션이 살아있는 동안 펌프를 유지한다 (session.py가 제어).

    전역 불리언이었을 때는 한 세션이 끝나면 다른 세션의 펌프까지 꺼져
    진행 중인 잡이 영영 멈췄다. 그래서 잡 단위 집합으로 관리한다."""
    _keepalive.add(job_key)
    _ensure_pump()


def remove_keepalive(job_key):
    _keepalive.discard(job_key)
```

- [ ] **Step 4: 펌프에서 스케줄러를 구동하고 종료 조건을 갱신한다**

`_pump()` 함수 안, 완료 작업 처리 루프가 끝난 직후(`if _state["keepalive"] or _jobs:` 줄 바로 앞)에 스케줄러 펌프 호출을 넣고, 종료 조건을 교체한다:

```python
    scheduler.pump()

    if _keepalive or _jobs or scheduler.has_work():
        # 세션 진행 중에는 주기적으로 패널을 갱신 (경과 시간 실시간 표시)
        now = time.monotonic()
        if now - _state["last_redraw"] >= _REDRAW_INTERVAL:
            _state["last_redraw"] = now
            _redraw_view3d()
        return _PUMP_INTERVAL
    _state["pump_on"] = False
    return None  # 펌프 종료
```

파일 상단 import에 스케줄러를 추가한다:

```python
from . import errors, scheduler
```

- [ ] **Step 5: 문법과 기존 테스트를 확인한다**

Run: `python -m py_compile core/runner.py && python -m unittest discover -s tests -v`
Expected: 컴파일 성공, 기존 테스트 PASS (`runner.py`는 테스트 대상이 아니지만 문법 오류를 잡는다)

- [ ] **Step 6: 커밋한다**

```bash
git add core/runner.py
git commit -m "runner: 잡 단위 취소·keepalive와 스케줄러 펌프 연동"
```

---

### Task 3: `preferences.py` — 동시 AI 실행 수 설정

**Files:**
- Modify: `preferences.py`
- Modify: `core/persist.py:12-15`

**Interfaces:**
- Consumes: 없음
- Produces: `preferences.get_prefs().ai_concurrency` (int, 기본 3)

- [ ] **Step 1: `ai_concurrency` 프로퍼티를 추가한다**

`preferences.py`의 `timeout` 프로퍼티 정의 **바로 다음**에 삽입한다:

```python
    ai_concurrency: IntProperty(
        name="동시 AI 실행 수",
        description=("동시에 실행할 AI CLI 개수 — 큐에 쌓인 여러 프롬프트의 AI 호출이 "
                     "이만큼 병렬로 진행된다. Blender 작업은 이 값과 무관하게 항상 "
                     "하나씩 순차 실행된다. 1로 두면 예전처럼 완전 순차 동작"),
        default=3, min=1, max=8,
        update=_persist_cb,
    )
```

- [ ] **Step 2: 환경설정 UI에 노출한다**

`LP3DPreferences.draw()`의 `col.prop(self, "timeout")` 다음 줄에 추가한다:

```python
        col.prop(self, "ai_concurrency")
```

- [ ] **Step 3: `_Defaults`에 기본값을 추가한다**

`preferences.py`의 `class _Defaults:` 안, `timeout` 기본값이 정의된 곳 옆에 추가한다 (애드온으로 활성화되지 않은 상태에서 쓰는 값):

```python
    ai_concurrency = 3
```

- [ ] **Step 4: 설정 영속화 키에 추가한다**

`core/persist.py:12-15`의 `_PREF_KEYS` 튜플에 `"ai_concurrency"`를 추가한다:

```python
_PREF_KEYS = ("claude_path", "codex_path", "gen_model", "critique_model",
              "timeout", "ai_concurrency", "capture_count", "capture_resolution",
              "use_multiview", "keep_turn_snapshots", "use_library",
              "asset_library_path")
```

- [ ] **Step 5: 문법을 확인한다**

Run: `python -m py_compile preferences.py core/persist.py`
Expected: 오류 없음

- [ ] **Step 6: 커밋한다**

```bash
git add preferences.py core/persist.py
git commit -m "환경설정: 동시 AI 실행 수(기본 3) 추가"
```

---

### Task 4: `properties.py` — 잡 항목 컬렉션으로 상태 이전

**Files:**
- Modify: `properties.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `LP3DJobItem` PropertyGroup — 필드: `uid, prompt, ref_image_path, agent, auto_turns, state, status, status_hint, phase, iteration, total_turns, started_at, log, collection_name, code, entry_id, multiview_path, lane, improve_feedback`
  - `LP3DSceneProps.jobs: CollectionProperty(type=LP3DJobItem)`
  - `LP3DSceneProps.job_index: IntProperty`
  - `LP3DSceneProps.next_uid: IntProperty(default=1)`
  - 유지되는 씬 필드: `agent`, `auto_turns`, `export_dir`, `multiview_preview_open`
  - `STATE_ICONS: dict` — 상태 → Blender 아이콘 이름 매핑 (패널이 사용)

- [ ] **Step 1: `LP3DJobItem`을 추가한다**

`properties.py`의 `class LP3DSceneProps` **앞**에 삽입한다:

```python
# 잡 상태 → 리스트에 표시할 아이콘. 상태를 한눈에 구분할 수 있어야 한다.
STATE_ICONS = {
    'PENDING': 'DOT',
    'RUNNING': 'PLAY',
    'DONE': 'CHECKMARK',
    'FAILED': 'ERROR',
    'CANCELLED': 'X',
}


class LP3DJobItem(bpy.types.PropertyGroup):
    """생성 큐의 항목 하나 — 프롬프트 입력값과 그 실행 상태·결과를 함께 담는다.

    예전에는 이 값들이 씬에 단 하나씩만 있어서 세션을 동시에 하나만 돌릴 수 있었다.
    항목별로 상태를 갖게 하면서 여러 생성을 병렬로 진행할 수 있게 됐다."""

    # --- 입력 ---
    uid: IntProperty()  # 세션과 항목을 잇는 식별자 (리스트 인덱스는 정렬·삭제로 바뀐다)
    prompt: StringProperty(
        name="프롬프트",
        description="만들고 싶은 로우폴리 모델 설명 (예: 낡은 나무 배럴, 금속 밴드 2개)",
        default="",
    )
    ref_image_path: StringProperty(
        name="참조 이미지",
        description="모델링 시 참고할 이미지 (선택) — 형태·비율·색 구성을 이 이미지에 맞춰 생성·개선",
        subtype='FILE_PATH',
        default="",
    )
    agent: EnumProperty(
        name="에이전트",
        items=[
            ('CLAUDE', "Claude", "claude -p 서브프로세스 사용"),
            ('CODEX', "Codex", "codex exec 서브프로세스 사용"),
        ],
        default='CLAUDE',
    )
    auto_turns: IntProperty(
        name="자동 반복(턴)",
        description="자동 시각 피드백 루프의 총 턴 수 — 값 그대로가 턴 수 (권장 3: 생성 1 + 비평·개선 2)",
        default=3, min=1, max=9,
    )
    improve_feedback: StringProperty(
        name="개선 요청",
        description="어디가 마음에 안 드는지 설명 (선택 — 비워두면 자동 비평만으로 개선)",
        default="",
    )

    # --- 실행 상태 ---
    state: EnumProperty(
        items=[
            ('PENDING', "대기", "아직 실행되지 않음"),
            ('RUNNING', "실행 중", "AI 호출 또는 Blender 작업 진행 중"),
            ('DONE', "완료", "생성 성공"),
            ('FAILED', "실패", "생성 실패 — 원인은 상태·로그 참고"),
            ('CANCELLED', "취소됨", "사용자가 중단함"),
        ],
        default='PENDING',
    )
    status: StringProperty(default="대기 중")
    # 실패 시 사용자가 할 일 (예: "터미널에서 `codex login` 실행 후 다시 시도").
    # 상태줄에 함께 넣으면 사이드바 폭에서 가운데가 잘려 정작 조치가 사라진다.
    status_hint: StringProperty(default="")
    phase: StringProperty(default="")        # GEN/EXEC/CAPTURE/CRITIQUE/FINAL
    started_at: FloatProperty(default=0.0)   # 경과 시간 표시용
    iteration: IntProperty(default=0)
    total_turns: IntProperty(default=0)
    log: StringProperty(default="")

    # --- 결과 ---
    collection_name: StringProperty(default="")
    code: StringProperty(default="")
    entry_id: StringProperty(default="")       # 라이브러리에 축적된 결과의 id
    multiview_path: StringProperty(default="")  # 이 잡의 멀티뷰 시트 경로
    lane: IntProperty(default=0)                # 결과를 Y축으로 밀어둘 레인 번호
```

- [ ] **Step 2: `LP3DSceneProps`를 잡 리스트 기반으로 교체한다**

`class LP3DSceneProps(bpy.types.PropertyGroup):` 본문 전체를 아래로 교체한다:

```python
class LP3DSceneProps(bpy.types.PropertyGroup):
    # 생성 큐 — 프롬프트를 항목으로 쌓아두고 한 번에 실행한다
    jobs: CollectionProperty(type=LP3DJobItem)
    job_index: IntProperty(default=0)
    next_uid: IntProperty(default=1)  # 다음 항목에 발급할 uid

    # --- 새 항목의 기본값이 되는 씬 설정 (설정 JSON으로 영속화) ---
    agent: EnumProperty(
        name="에이전트",
        items=[
            ('CLAUDE', "Claude", "claude -p 서브프로세스 사용"),
            ('CODEX', "Codex", "codex exec 서브프로세스 사용"),
        ],
        default='CLAUDE',
        update=_persist_cb,
    )
    auto_turns: IntProperty(
        name="자동 반복(턴)",
        description="새 항목에 적용할 기본 턴 수 — 값 그대로가 턴 수 (권장 3)",
        default=3, min=1, max=9,
        update=_persist_cb,
    )
    export_dir: StringProperty(
        name="익스포트 폴더",
        subtype='DIR_PATH',
        default="//exports/",
        update=_persist_cb,
    )
    multiview_preview_open: BoolProperty(
        name="멀티뷰 미리보기",
        description="패널에 멀티뷰(3면도) 시트 썸네일을 펼쳐 보여준다",
        default=True,
    )

    def active_job(self):
        """리스트에서 선택된 항목. 없으면 None."""
        if 0 <= self.job_index < len(self.jobs):
            return self.jobs[self.job_index]
        return None

    def job_by_uid(self, uid: int):
        for job in self.jobs:
            if job.uid == uid:
                return job
        return None
```

- [ ] **Step 3: import와 등록 목록을 갱신한다**

파일 상단 import에 `CollectionProperty`를 추가한다:

```python
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, IntProperty, PointerProperty,
                       StringProperty)
```

`register()` / `unregister()`를 교체한다 (`LP3DJobItem`이 `LP3DSceneProps`보다 먼저 등록되어야 한다):

```python
def register():
    bpy.utils.register_class(LP3DJobItem)
    bpy.utils.register_class(LP3DSceneProps)
    bpy.types.Scene.lp3d = PointerProperty(type=LP3DSceneProps)
    bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.timers.register(_restore_deferred, first_interval=0.2)


def unregister():
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    del bpy.types.Scene.lp3d
    bpy.utils.unregister_class(LP3DSceneProps)
    bpy.utils.unregister_class(LP3DJobItem)
```

- [ ] **Step 4: 파일 다시 열기 시 멈춘 항목을 되살린다**

`_apply_saved()` 함수 본문 끝에 잡 상태 복구를 추가한다:

```python
def _apply_saved(scene=None):
    """저장된 씬 설정(agent/반복/익스포트 폴더)을 복원하고, 멈춘 잡 상태를 정리한다."""
    from .core import jobs, persist
    scenes = [scene] if scene else bpy.data.scenes
    for sc in scenes:
        if getattr(sc, "lp3d", None):
            persist.apply_scene(sc.lp3d)
            # 파일을 다시 열거나 Dev Reload를 하면 세션 객체는 사라지는데
            # 항목은 RUNNING으로 남아 실행 버튼이 잠긴다 — 대기로 되돌린다
            jobs.reset_stale(sc.lp3d)
```

- [ ] **Step 5: 문법을 확인한다**

Run: `python -m py_compile properties.py`
Expected: 오류 없음 (`core.jobs`는 Task 5에서 만든다 — import가 함수 안에 있어 컴파일에는 영향 없음)

- [ ] **Step 6: 커밋한다**

```bash
git add properties.py
git commit -m "씬 단일 상태를 잡 항목 컬렉션(LP3DJobItem)으로 이전"
```

---

### Task 5: `core/jobs.py` — 잡 리스트 조작과 세션 기동

**Files:**
- Create: `core/jobs.py`

**Interfaces:**
- Consumes: `core/session.py`의 `start_job(scene_name, uid, ...)`, `is_active(uid)`, `cancel_session(uid)` (Task 6에서 구현 — 이 태스크는 함수 안에서 지연 import 한다)
- Produces:
  - `add_job(props, prompt="") -> LP3DJobItem`
  - `remove_job(context, index) -> None`
  - `duplicate_job(props, index) -> LP3DJobItem | None`
  - `move_job(props, index, delta) -> int` — 새 인덱스 반환
  - `start_all(context) -> tuple[int, str]` — (시작한 개수, 오류 메시지 또는 "")
  - `start_one(context, job) -> str` — 오류 메시지 또는 ""
  - `stop_all(context) -> int` — 중단한 개수
  - `retry_job(context, index) -> str`
  - `next_lane(props) -> int`
  - `reset_stale(props) -> int`
  - `LANE_SPACING: float` — 레인 간 Y축 간격(미터)

- [ ] **Step 1: `core/jobs.py`를 작성한다**

```python
# 생성 큐 관리: 잡 항목 추가·삭제·정렬과 세션 기동
#
# 씬의 잡 리스트(properties.LP3DJobItem)와 세션 상태머신(core/session.py)을
# 잇는 얇은 레이어다. 동시성 제어 자체는 core/scheduler.py가 한다 —
# 여기서 개수를 제한하지 않고, 대기 상태가 UI에 그대로 드러나게 한다.
import logging

import bpy

log = logging.getLogger(__name__)

LANE_SPACING = 4.0  # 레인 간 Y축 간격(미터). 배치 결과가 겹치지 않도록 벌린다


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

    생성 코드는 항상 원점 기준으로 작성되므로, 마무리 직전에 한 번만 적용한다."""
    if not lane:
        return
    coll = bpy.data.collections.get(collection_name)
    if not coll:
        return
    dy = LANE_SPACING * lane
    for obj in coll.objects:
        if obj.parent is None:  # 자식은 부모를 따라 움직인다
            obj.location.y += dy
```

- [ ] **Step 2: 문법을 확인한다**

Run: `python -m py_compile core/jobs.py`
Expected: 오류 없음

- [ ] **Step 3: 커밋한다**

```bash
git add core/jobs.py
git commit -m "생성 큐 관리 모듈 추가 (항목 추가·정렬·일괄 실행·레인 배치)"
```

---

### Task 6: `core/session.py` — 싱글턴 해체와 스케줄러 연동

**Files:**
- Modify: `core/session.py`

**Interfaces:**
- Consumes: `core/scheduler.py` (`submit_ai`, `release_ai`, `submit_blender`, `cancel_job`, `set_ai_limit`), `core/runner.py` (`run_cli_async(..., job_key=)`, `cancel(job_key)`, `add_keepalive`, `remove_keepalive`), `core/jobs.py` (`apply_lane_offset`)
- Produces:
  - `start_job(scene_name, uid, variation_of=None, variation_count=3, improve=False) -> str | None` — 오류 메시지 또는 None
  - `is_active(uid=None) -> bool`
  - `active_count() -> int`
  - `cancel_session(uid=None) -> None`
  - `cancel_all() -> None`

- [ ] **Step 1: 모듈 전역과 진입점을 교체한다**

`core/session.py` 상단의 `_current` 선언부터 `_end_session()`까지를 아래로 교체한다:

```python
_sessions = {}  # uid -> GenerationSession. 여러 세션이 동시에 진행될 수 있다


def is_active(uid=None) -> bool:
    """세션 진행 여부. uid를 주면 그 잡만, 없으면 하나라도 돌고 있는지."""
    if uid is None:
        return bool(_sessions)
    return uid in _sessions


def active_count() -> int:
    return len(_sessions)


def start_job(scene_name, uid, variation_of=None, variation_count=3, improve=False):
    """잡 항목 하나의 생성 세션을 시작한다. 오류 메시지 또는 None을 반환한다.

    프롬프트·참조 이미지·에이전트·턴 수는 씬이 아니라 잡 항목에서 읽는다 —
    여러 항목이 서로 다른 설정으로 동시에 돌 수 있어야 하기 때문이다."""
    if uid in _sessions:
        return "이미 진행 중인 항목입니다"
    scene = bpy.data.scenes.get(scene_name)
    if not scene or not getattr(scene, "lp3d", None):
        return "씬을 찾을 수 없습니다"
    props = scene.lp3d
    job = props.job_by_uid(uid)
    if job is None:
        return "항목을 찾을 수 없습니다"

    if improve:
        if not job.code or not job.collection_name:
            return "개선할 결과가 없습니다 — 먼저 모델을 생성하세요"
        coll = bpy.data.collections.get(job.collection_name)
        if not coll or not any(o.type == 'MESH' for o in coll.objects):
            return "개선할 모델 컬렉션을 찾을 수 없습니다"
    request = job.prompt.strip()
    if not request:
        return "프롬프트를 입력하세요"
    exe = preferences.resolve_cli_path(job.agent)
    if not exe:
        return f"{job.agent} CLI를 찾을 수 없습니다. 환경설정에서 경로를 지정하세요"
    ref_image = None
    if job.ref_image_path.strip():
        ref_image = bpy.path.abspath(job.ref_image_path.strip())
        if not os.path.isfile(ref_image):
            return f"참조 이미지를 찾을 수 없습니다: {job.ref_image_path}"
        if os.path.splitext(ref_image)[1].lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            return "참조 이미지는 PNG/JPG/WEBP만 지원합니다"

    # 환경설정 변경이 즉시 반영되도록 세션 시작 때마다 한도를 갱신한다
    scheduler.set_ai_limit(getattr(preferences.get_prefs(), "ai_concurrency", 3))
    session = GenerationSession(
        scene_name=scene_name,
        uid=uid,
        request=request,
        agent=job.agent,
        exe=exe,
        max_iterations=1 if improve else loop.total_turns(job.auto_turns),
        variation_code=variation_of,
        variation_count=variation_count,
        improve_code=job.code if improve else None,
        improve_feedback=job.improve_feedback.strip() if improve else "",
        improve_collection=job.collection_name if improve else None,
        ref_image=ref_image,
        lane=job.lane,
    )
    _sessions[uid] = session
    session.start()
    return None


def cancel_session(uid=None):
    """세션을 취소한다. uid가 없으면 전부 취소한다."""
    targets = [_sessions[uid]] if uid in _sessions else (
        list(_sessions.values()) if uid is None else [])
    for session in targets:
        session.cancel()


def cancel_all():
    cancel_session(None)


def _end_session(uid):
    _sessions.pop(uid, None)
    scheduler.cancel_job(uid)
    runner.remove_keepalive(uid)
```

- [ ] **Step 2: import에 스케줄러를 추가한다**

`from . import (capture, errors, executor, library, loop, multiview, prompts, runner, snapshots)` 를 아래로 교체한다:

```python
from . import (capture, errors, executor, jobs, library, loop, multiview,
               prompts, runner, scheduler, snapshots)
```

- [ ] **Step 3: `GenerationSession.__init__`에 `uid`와 `lane`을 받는다**

시그니처와 첫 줄들을 교체한다:

```python
    def __init__(self, scene_name, uid, request, agent, exe, max_iterations,
                 variation_code=None, variation_count=3,
                 improve_code=None, improve_feedback="", improve_collection=None,
                 ref_image=None, lane=0):
        self.scene_name = scene_name
        self.uid = uid
        self.lane = lane
        self.request = request
```

이하 기존 본문은 그대로 둔다.

- [ ] **Step 4: 상태 기록을 잡 항목 기준으로 바꾼다**

`_props()`와 `_set_status()`를 교체한다:

```python
    def _job(self):
        """이 세션에 대응하는 잡 항목. 사용자가 삭제했으면 None."""
        scene = bpy.data.scenes.get(self.scene_name)
        if not scene or not getattr(scene, "lp3d", None):
            return None
        return scene.lp3d.job_by_uid(self.uid)

    def _set_status(self, status: str, log: str = None, phase: str = None, hint: str = ""):
        job = self._job()
        if job:
            job.status = status
            job.status_hint = hint
            job.iteration = self.iteration
            job.total_turns = self.max_iterations
            if phase is not None:
                job.phase = phase
            if log:
                lines = (job.log + "\n" + log).strip().splitlines()
                job.log = "\n".join(lines[-30:])
        if log:
            _log.info(log)
        self._redraw()
```

`_model_label()`이 쓰는 `self.prefs`는 그대로 둔다. `start()`와 `_finish()`는 뒤 스텝에서
통째로 교체되고, `_on_multiview()`는 Step 6에서 고친다 — 이 스텝 이후 `_props`라는 이름은
파일에 남지 않아야 한다(Step 12에서 grep으로 확인).

- [ ] **Step 5: `start()`를 잡 항목 기준으로 고치고 Blender 캡처를 큐에 넣는다**

`start()` 메서드를 교체한다:

```python
    def start(self):
        job = self._job()
        if job:
            job.state = 'RUNNING'
            job.log = ""
            job.started_at = time.time()
            job.phase = ""
            job.status = "대기 중 (순서 기다리는 중)"
        runner.add_keepalive(self.uid)
        self.backend.prepare_workdir(prompts.build_system_prompt())
        if self.improve_code:
            # 개선 세션: 현재 모델을 캡처해 첫 턴부터 이미지+코드+피드백으로 개선 요청
            self._set_status("현재 모델 캡처 대기중...", phase='CAPTURE')
            scheduler.submit_blender(self.uid, self._blender_improve_capture)
            return
        if self.variation_code:
            first = prompts.build_variation_prompt(self.request, self.variation_code,
                                                   self.variation_count)
            # 변형은 기존 코드 스타일을 따르므로 참조 이미지·멀티뷰 불필요
            self._set_status(f"코드 생성 — {self._model_label()} 호출 대기중...",
                             f"세션 시작: {self.request} ({self.backend.name})", phase='GEN')
            self._dispatch(first)
            return
        # 신규 생성: 멀티뷰 참조 시트를 먼저 생성 (codex image_gen — 없으면 스킵)
        if getattr(self.prefs, "use_multiview", True) and multiview.is_available():
            self._set_status("멀티뷰 참조 생성중 (codex image_gen)...",
                             "멀티뷰 참조 시트 생성 시작", phase='GEN')
            # 멀티뷰도 CLI 호출이므로 AI 슬롯을 점유한다
            scheduler.submit_ai(self.uid, self._run_multiview)
            return
        self._start_generation()

    def _run_multiview(self):
        multiview.generate(self.request, self.workdir, self.prefs.timeout,
                           self._on_multiview, ref_image=self.ref_image)

    def _blender_improve_capture(self):
        """개선 세션의 첫 캡처 — Blender 큐에서 실행된다."""
        self._set_status("현재 모델 캡처중...", phase='CAPTURE')
        images, stats = capture.capture_collection(
            self.collection_name, self.workdir,
            count=self.prefs.capture_count, resolution=self.prefs.capture_resolution,
            silhouettes=1,
        )
        if not images:
            self._finish("실패: 개선할 모델 캡처 불가", ok=False)
            return
        first = prompts.build_improve_prompt(
            self.request, self.improve_code, self.improve_feedback,
            [os.path.basename(p) for p in images], stats,
            ref_image=self._ref_name(),
        )
        if self.ref_image:
            images = images + [self.ref_image]
        self._set_status(f"스크린샷 분석·개선 — {self._model_label(critique=True)} 호출 대기중...",
                         f"개선 세션 시작: {self.request}"
                         + (f" / 피드백: {self.improve_feedback}" if self.improve_feedback else ""),
                         phase='CRITIQUE')
        self._dispatch(first, images=images)
```

- [ ] **Step 6: `_on_multiview`에서 AI 슬롯을 반환한다**

`_on_multiview(self, path, error=None)` 본문 **첫 줄**에 추가한다:

```python
        scheduler.release_ai(self.uid)
```

`props = self._props()` / `props.multiview_path = ...` 부분을 잡 항목 기준으로 바꾼다:

```python
            job = self._job()
            if job:
                job.multiview_path = saved or path
```

- [ ] **Step 7: `cancel()`과 `_finish()`를 잡 단위로 고친다**

`cancel()`을 교체한다:

```python
    def cancel(self):
        runner.cancel(self.uid)
        scheduler.cancel_job(self.uid)
        # 정리도 bpy 조작이므로 큐를 통해 순서대로 실행한다
        scheduler.submit_blender(self.uid, self._blender_cancel_cleanup)

    def _blender_cancel_cleanup(self):
        snapshots.clear_all(self.collection_name)  # 취소된 세션의 중간 단계는 남기지 않는다
        executor.clear_collection(self.collection_name)
        coll = bpy.data.collections.get(self.collection_name)
        if coll and not coll.objects:
            bpy.data.collections.remove(coll)
        self._finish("취소됨", ok=False, state='CANCELLED')
```

`_finish()`를 교체한다:

```python
    def _finish(self, status: str, ok: bool, detail=None, hint: str = "", state=None):
        # 개선 실패로 기존 모델까지 사라졌으면 이전 코드로 복원한다
        if not ok and self.improve_code:
            coll = bpy.data.collections.get(self.collection_name)
            if not coll or not any(o.type == 'MESH' for o in coll.objects):
                restored, _ = executor.execute(self.improve_code, self.collection_name,
                                               seed=1, workdir=self.workdir)
                if restored:
                    status += " — 이전 결과 복원됨"
        job = self._job()
        if job:
            job.state = state or ('DONE' if ok else 'FAILED')
            if ok:
                job.collection_name = self.collection_name
                # 개선 세션이 코드 없이 DONE으로 끝나면 이전 코드를 유지
                job.code = self.last_code or self.improve_code or ""
                if self.improve_code:
                    job.improve_feedback = ""  # 반영된 피드백은 비움
                job.entry_id = self._archive(job.code)
        # 상태줄은 한 줄뿐이라 원인을 다 담을 수 없다 — 상세는 로그 패널에 남긴다
        log_text = f"세션 종료: {status}"
        if detail:
            log_text += "\n" + "\n".join(f"  · {line}" for line in detail)
        self._set_status(status, log_text, phase="", hint=hint)
        _end_session(self.uid)
```

- [ ] **Step 8: `_dispatch`를 AI 슬롯 경유로 바꾼다**

`_dispatch()`를 교체한다:

```python
    def _dispatch(self, prompt: str, images=None):
        if images:
            prompt += self.backend.image_prompt_hint(images)
        use_resume = self.session_id and not self.stateless
        if self.stateless and self.last_code:
            # 폴백: 세션 기억이 없으므로 맥락을 프롬프트에 인라인
            prompt = (
                f"(세션 요약) 원 요청: {self.request}\n"
                f"현재 코드:\n```python\n{self.last_code}\n```\n\n" + prompt
            )
        if use_resume:
            cmd = self.backend.build_resume_command(self.session_id, prompt, images or [])
        else:
            cmd = self.backend.build_initial_command(prompt, images or [])
        self._was_resume = use_resume
        # AI 슬롯이 빌 때까지 대기했다가 실행된다 (동시 실행 수는 환경설정)
        scheduler.submit_ai(self.uid, lambda: self._launch(cmd, prompt))

    def _launch(self, cmd, prompt):
        # 프롬프트는 stdin으로 — 명령줄 인자는 Windows .cmd 셸림에서 첫 줄만 전달된다
        runner.run_cli_async(cmd, self.workdir, self.prefs.timeout, self._on_response,
                             stdin_text=prompt, job_key=self.uid)
```

- [ ] **Step 9: `_on_response`에서 슬롯을 반환하고, 삭제된 항목을 처리한다**

`_on_response()`를 교체한다:

```python
    def _on_response(self, stdout, error):
        # 콜백에서 예외가 나면 runner가 로그만 남기고 삼켜서 세션이 영구히 진행 중으로
        # 남는다(취소/생성 모두 잠김). 여기서 반드시 세션을 종료시킨다.
        scheduler.release_ai(self.uid)
        if self._job() is None:
            # 사용자가 리스트에서 항목을 지웠다 — 조용히 정리하고 끝낸다
            _end_session(self.uid)
            return
        try:
            self._handle_response(stdout, error)
        except Exception as e:
            _log.exception("LP3D 세션 처리 오류")
            self._finish(f"실패: 내부 오류 {type(e).__name__}: {e}", ok=False)
```

- [ ] **Step 10: `_execute`와 `_critique`, `_finalize`를 Blender 큐로 옮긴다**

세 메서드를 교체한다:

```python
    # ---------- 실행/비평 ----------
    def _execute(self, code: str, status):
        self._set_status(f"Blender 실행 대기중 (턴 {self.iteration}/{self.max_iterations})...",
                         phase='EXEC')
        scheduler.submit_blender(self.uid, lambda: self._blender_execute(code, status))

    def _blender_execute(self, code: str, status):
        self._set_status(f"Blender 코드 실행중 (턴 {self.iteration}/{self.max_iterations})...",
                         phase='EXEC')
        ok, error = executor.execute(code, self.collection_name,
                                     seed=self.iteration, workdir=self.workdir)
        if not ok:
            if self.exec_retries < 2:
                self.exec_retries += 1
                self._set_status(f"실행 오류 — {self._model_label()} 자기수정 호출중...",
                                 f"실행 오류(재시도 {self.exec_retries}/2): {error.splitlines()[-1]}",
                                 phase='GEN')
                self._dispatch(prompts.build_error_prompt(error))
                return
            self._finish("실패: 코드 실행 오류 반복", ok=False)
            return

        self.exec_retries = 0
        self.last_code = code
        self._set_status(f"턴 {self.iteration} 생성 완료", f"턴 {self.iteration} 실행 성공")
        finalize = loop.should_finalize(status, self.iteration, self.max_iterations)
        # 다음 턴이 컬렉션을 비우기 전에 이번 턴 결과를 옆으로 복제해 남긴다.
        # 마지막 턴은 그 결과가 곧 최종본(원점)이므로 복제하지 않는다.
        if (not finalize and getattr(self.prefs, "keep_turn_snapshots", True)
                and not self.improve_code):
            try:
                if snapshots.capture_turn(self.collection_name, self.iteration,
                                          self.max_iterations):
                    self._set_status(f"턴 {self.iteration} 생성 완료",
                                     f"턴 {self.iteration} 스냅샷 보관")
            except Exception:
                _log.exception("턴 스냅샷 실패")  # 스냅샷 문제로 생성을 막지 않는다
        if finalize:
            self._finalize()
        else:
            self._critique()

    def _critique(self):
        self._set_status("뷰포트 캡처 대기중...", phase='CAPTURE')
        scheduler.submit_blender(self.uid, self._blender_critique)

    def _blender_critique(self):
        self._set_status("뷰포트 캡처중...", phase='CAPTURE')
        images, stats = capture.capture_collection(
            self.collection_name, self.workdir,
            count=self.prefs.capture_count, resolution=self.prefs.capture_resolution,
            silhouettes=1,  # 비평 속도를 위해 실루엣은 1장만 (iso)
        )
        if not images:
            self._finalize()  # 캡처할 게 없으면 그대로 마무리
            return
        self.last_images = list(images)
        self.iteration += 1
        prompt = prompts.build_critique_prompt(
            [os.path.basename(p) for p in images], stats,
            self.iteration, self.max_iterations,
            allow_done=loop.allow_done(self.iteration, self.max_iterations),
            ref_image=self._ref_name(),
            multiview=self._mv_name(),
        )
        # 비평 턴마다 참조·멀티뷰 시트와 비교하도록 함께 전달
        images = images + [p for p in (self.ref_image, self.multiview) if p]
        self._set_status(
            f"스크린샷 분석 — {self._model_label(critique=True)} 호출 대기중 "
            f"({self.iteration}/{self.max_iterations})...", phase='CRITIQUE')
        self._dispatch(prompt, images=images)

    def _finalize(self):
        self._set_status("마무리 대기중...", phase='FINAL')
        scheduler.submit_blender(self.uid, self._blender_finalize)

    def _blender_finalize(self):
        from ..lowpoly.cleanup import collection_tri_count, cull_hidden_faces, game_ready
        self._set_status("마무리 정리중 (은면 제거·게임레디)...", phase='FINAL')
        coll = bpy.data.collections.get(self.collection_name)
        if coll:
            mesh_objs = [o for o in coll.objects if o.type == 'MESH']
            removed = cull_hidden_faces(mesh_objs)  # join(union)을 안 거친 잔여 은면 제거
            for obj in mesh_objs:
                game_ready(obj)
            tris = collection_tri_count(coll)
            # 배치 실행 결과가 원점에 겹치지 않도록 레인만큼 옆으로 민다
            jobs.apply_lane_offset(self.collection_name, self.lane)
            note = f", 은면 {removed}개 제거" if removed else ""
            self._finish(f"완료 — {self.collection_name} ({tris} tris{note})", ok=True)
        else:
            self._finish("실패: 생성된 오브젝트 없음", ok=False)
```

- [ ] **Step 11: `_archive`의 프리퍼런스 접근을 확인한다**

`_archive()`는 `self.variation_code`와 `self.prefs`만 쓰므로 수정할 것이 없다. `_finish()`에서
`self._archive(job.code)`로 호출하도록 이미 Step 7에서 바꿨는지 확인한다.

- [ ] **Step 12: 문법을 확인하고 남은 `_props` 참조가 없는지 확인한다**

Run: `python -m py_compile core/session.py && grep -n "_props()\|_current\|set_keepalive" core/session.py`
Expected: 컴파일 성공, grep 결과 없음

- [ ] **Step 13: 커밋한다**

```bash
git add core/session.py
git commit -m "세션 싱글턴 해체: uid 기반 다중 세션 + AI/Blender 큐 경유"
```

---

### Task 7: `ui/operators.py` — 잡 리스트 오퍼레이터

**Files:**
- Modify: `ui/operators.py`

**Interfaces:**
- Consumes: `core/jobs.py`, `core/session.py`, `properties.LP3DSceneProps.active_job()`
- Produces (새 bl_idname):
  - `lp3d.job_add`, `lp3d.job_remove`, `lp3d.job_duplicate`, `lp3d.job_move(delta)`,
    `lp3d.job_retry`, `lp3d.job_cancel`, `lp3d.queue_start`, `lp3d.queue_stop`
  - 기존 유지(대상만 잡 항목으로 변경): `lp3d.edit_prompt`, `lp3d.paste_ref_image`,
    `lp3d.clear_ref_image`, `lp3d.show_multiview`, `lp3d.use_multiview_as_ref`,
    `lp3d.open_multiview_folder`, `lp3d.load_last_multiview`, `lp3d.clear_snapshots`,
    `lp3d.rate`, `lp3d.library_discard`, `lp3d.variation`, `lp3d.improve`,
    `lp3d.export`, `lp3d.mark_asset`, `lp3d.dev_reload`
  - 제거: `lp3d.generate`, `lp3d.cancel`, `lp3d.improve_done`

- [ ] **Step 1: import에 `jobs`를 추가한다**

```python
from ..core import (clipboard_image, jobs, library, multiview, native_input,
                    session, snapshots)
```

- [ ] **Step 2: 큐 오퍼레이터를 추가한다**

`LP3D_OT_generate` / `LP3D_OT_cancel` 클래스 정의를 아래 클래스들로 통째 교체한다:

```python
class LP3D_OT_job_add(bpy.types.Operator):
    bl_idname = "lp3d.job_add"
    bl_label = "항목 추가"
    bl_description = "생성 큐에 새 프롬프트 항목을 추가한다 (OS 네이티브 입력 창)"

    @classmethod
    def poll(cls, context):
        return not native_input.is_open()

    def execute(self, context):
        scene_name = context.scene.name

        def on_done(text):
            if text is None:
                return  # 취소 — 항목을 만들지 않는다
            scene = bpy.data.scenes.get(scene_name)
            if scene and getattr(scene, "lp3d", None):
                jobs.add_job(scene.lp3d, native_input.to_single_line(text))

        error = native_input.open_dialog("프롬프트 입력", "", on_done)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_job_remove(bpy.types.Operator):
    bl_idname = "lp3d.job_remove"
    bl_label = "항목 삭제"
    bl_description = "선택한 항목을 큐에서 제거한다 (실행 중이면 먼저 중단)"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.lp3d.jobs)

    def execute(self, context):
        jobs.remove_job(context, context.scene.lp3d.job_index)
        return {'FINISHED'}


class LP3D_OT_job_duplicate(bpy.types.Operator):
    bl_idname = "lp3d.job_duplicate"
    bl_label = "항목 복제"
    bl_description = "선택한 항목과 같은 프롬프트·설정으로 새 대기 항목을 만든다"

    @classmethod
    def poll(cls, context):
        return context.scene.lp3d.active_job() is not None

    def execute(self, context):
        props = context.scene.lp3d
        if jobs.duplicate_job(props, props.job_index) is None:
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_job_move(bpy.types.Operator):
    bl_idname = "lp3d.job_move"
    bl_label = "항목 이동"
    bl_description = "실행 순서를 바꾼다"

    delta: IntProperty(default=-1, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return len(context.scene.lp3d.jobs) > 1

    def execute(self, context):
        props = context.scene.lp3d
        jobs.move_job(props, props.job_index, self.delta)
        return {'FINISHED'}


class LP3D_OT_job_retry(bpy.types.Operator):
    bl_idname = "lp3d.job_retry"
    bl_label = "재시도"
    bl_description = "실패하거나 취소된 항목을 대기로 되돌리고 다시 실행한다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and job.state in ('FAILED', 'CANCELLED')

    def execute(self, context):
        error = jobs.retry_job(context, context.scene.lp3d.job_index)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_job_cancel(bpy.types.Operator):
    bl_idname = "lp3d.job_cancel"
    bl_label = "항목 중단"
    bl_description = "선택한 항목의 생성만 중단한다 (다른 항목은 계속 진행)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and session.is_active(job.uid)

    def execute(self, context):
        session.cancel_session(context.scene.lp3d.active_job().uid)
        return {'FINISHED'}


class LP3D_OT_queue_start(bpy.types.Operator):
    bl_idname = "lp3d.queue_start"
    bl_label = "전체 실행"
    bl_description = ("대기 중인 항목을 모두 실행한다 — AI 호출은 환경설정의 동시 실행 수만큼 "
                      "병렬로, Blender 작업은 하나씩 순차로 진행된다")

    @classmethod
    def poll(cls, context):
        return any(job.state == 'PENDING' for job in context.scene.lp3d.jobs)

    def execute(self, context):
        started, error = jobs.start_all(context)
        if not started:
            self.report({'ERROR'}, error or "실행할 대기 항목이 없습니다")
            return {'CANCELLED'}
        if error:
            self.report({'WARNING'}, f"{started}개 시작 — 일부 실패: {error}")
        else:
            self.report({'INFO'}, f"{started}개 항목 실행 시작")
        return {'FINISHED'}


class LP3D_OT_queue_stop(bpy.types.Operator):
    bl_idname = "lp3d.queue_stop"
    bl_label = "전체 중지"
    bl_description = "진행 중인 모든 항목을 중단하고 생성물을 정리한다"

    @classmethod
    def poll(cls, context):
        return session.is_active()

    def execute(self, context):
        stopped = jobs.stop_all(context)
        self.report({'INFO'}, f"{stopped}개 항목 중단됨")
        return {'FINISHED'}
```

- [ ] **Step 3: `lp3d.edit_prompt`를 잡 항목 기준으로 고친다**

`LP3D_OT_edit_prompt.execute()`와 `poll()`을 교체한다 (`target`은 잡 항목의 필드명):

```python
    @classmethod
    def poll(cls, context):
        return not native_input.is_open() and context.scene.lp3d.active_job() is not None

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        target = self.target
        title = "프롬프트 입력" if target == 'prompt' else "개선 프롬프트 입력"
        scene_name = context.scene.name
        uid = job.uid

        def on_done(text):
            if text is None:
                return  # 취소 — 기존 값 유지
            # 다이얼로그가 떠 있는 동안 씬·리스트가 바뀌었을 수 있으므로 uid로 다시 찾는다
            scene = bpy.data.scenes.get(scene_name)
            if not scene or not getattr(scene, "lp3d", None):
                return
            target_job = scene.lp3d.job_by_uid(uid)
            if target_job:
                setattr(target_job, target, native_input.to_single_line(text))

        error = native_input.open_dialog(title, getattr(job, target, ""), on_done)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}
```

- [ ] **Step 4: 참조 이미지·멀티뷰 오퍼레이터를 잡 항목 기준으로 고친다**

각 클래스에서 `context.scene.lp3d.<field>`를 `context.scene.lp3d.active_job().<field>`로 바꾸고,
`poll`에 항목 존재 확인을 넣는다. 아래 6개 클래스를 교체한다:

```python
class LP3D_OT_paste_ref_image(bpy.types.Operator):
    bl_idname = "lp3d.paste_ref_image"
    bl_label = "클립보드에서 붙여넣기"
    bl_description = "브라우저 등에서 복사한 이미지를 선택 항목의 참조 이미지로 붙여넣는다 (.blend 옆에 PNG로 저장)"

    @classmethod
    def poll(cls, context):
        return clipboard_image.is_supported() and context.scene.lp3d.active_job() is not None

    def execute(self, context):
        # .blend 옆(저장 전이면 다운로드 폴더)에 남겨 다음에도 참조로 재사용할 수 있게 한다
        path, error = clipboard_image.paste_to(multiview.archive_dir())
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        context.scene.lp3d.active_job().ref_image_path = path
        self.report({'INFO'}, f"참조 이미지로 붙여넣음: {os.path.basename(path)}")
        return {'FINISHED'}


class LP3D_OT_clear_ref_image(bpy.types.Operator):
    bl_idname = "lp3d.clear_ref_image"
    bl_label = "참조 이미지 해제"
    bl_description = "참조 이미지 지정을 해제한다 (파일은 지우지 않는다)"

    @classmethod
    def poll(cls, context):
        return context.scene.lp3d.active_job() is not None

    def execute(self, context):
        context.scene.lp3d.active_job().ref_image_path = ""
        return {'FINISHED'}


class LP3D_OT_show_multiview(bpy.types.Operator):
    bl_idname = "lp3d.show_multiview"
    bl_label = "멀티뷰 보기"
    bl_description = "AI가 만든 정면/측면/상면/쿼터 참조 시트를 이미지 에디터 창으로 연다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.multiview_path)

    def execute(self, context):
        path = context.scene.lp3d.active_job().multiview_path
        if not os.path.isfile(path):
            self.report({'ERROR'}, f"파일을 찾을 수 없습니다: {path}")
            return {'CANCELLED'}
        img = next((i for i in bpy.data.images if i.filepath == path), None)
        if img is None:
            img = bpy.data.images.load(path)
        # 기존 이미지 에디터가 있으면 재사용하고, 없으면 새 창을 띄운다
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.image = img
                    return {'FINISHED'}
        bpy.ops.wm.window_new()
        area = context.window_manager.windows[-1].screen.areas[0]
        area.type = 'IMAGE_EDITOR'
        area.spaces.active.image = img
        return {'FINISHED'}


class LP3D_OT_open_multiview_folder(bpy.types.Operator):
    bl_idname = "lp3d.open_multiview_folder"
    bl_label = "저장 폴더 열기"
    bl_description = "멀티뷰 시트가 저장된 폴더를 파일 탐색기로 연다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.multiview_path)

    def execute(self, context):
        folder = os.path.dirname(context.scene.lp3d.active_job().multiview_path)
        if not os.path.isdir(folder):
            self.report({'ERROR'}, f"폴더를 찾을 수 없습니다: {folder}")
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=folder)
        return {'FINISHED'}


class LP3D_OT_use_multiview_as_ref(bpy.types.Operator):
    bl_idname = "lp3d.use_multiview_as_ref"
    bl_label = "참조로 사용"
    bl_description = "이 멀티뷰 시트를 선택 항목의 참조 이미지로 지정한다"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.multiview_path)

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        if not os.path.isfile(job.multiview_path):
            self.report({'ERROR'}, "멀티뷰 파일을 찾을 수 없습니다 (.blend 저장 후 다시 생성하세요)")
            return {'CANCELLED'}
        job.ref_image_path = job.multiview_path
        self.report({'INFO'}, "참조 이미지로 지정됨")
        return {'FINISHED'}


class LP3D_OT_load_last_multiview(bpy.types.Operator):
    bl_idname = "lp3d.load_last_multiview"
    bl_label = "저장된 멀티뷰 불러오기"
    bl_description = ("보관 폴더(.blend 옆 또는 다운로드/blender)에 저장된 "
                      "가장 최근 멀티뷰 시트를 미리보기로 불러온다")

    @classmethod
    def poll(cls, context):
        return context.scene.lp3d.active_job() is not None

    def execute(self, context):
        path = multiview.latest_archived()
        if not path:
            self.report({'WARNING'},
                        f"저장된 멀티뷰 시트가 없습니다: {multiview.archive_dir()}")
            return {'CANCELLED'}
        context.scene.lp3d.active_job().multiview_path = path
        self.report({'INFO'}, f"불러옴: {os.path.basename(path)}")
        return {'FINISHED'}
```

- [ ] **Step 5: 결과물 오퍼레이터를 선택 항목 기준으로 고친다**

`LP3D_OT_clear_snapshots`, `LP3D_OT_rate`, `LP3D_OT_library_discard`,
`LP3D_OT_variation`, `LP3D_OT_improve`, `LP3D_OT_export`, `LP3D_OT_mark_asset`을
아래로 교체하고, `LP3D_OT_improve_done`은 삭제한다:

```python
class LP3D_OT_clear_snapshots(bpy.types.Operator):
    bl_idname = "lp3d.clear_snapshots"
    bl_label = "단계 스냅샷 정리"
    bl_description = "옆에 남겨둔 턴별 중간 결과를 모두 삭제한다 (최종 모델은 유지)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.collection_name)

    def execute(self, context):
        removed = snapshots.clear_all(context.scene.lp3d.active_job().collection_name)
        self.report({'INFO'}, f"스냅샷 {removed}개 정리됨" if removed else "정리할 스냅샷이 없습니다")
        return {'FINISHED'}


class LP3D_OT_rate(bpy.types.Operator):
    bl_idname = "lp3d.rate"
    bl_label = "평가"
    bl_description = "이 결과를 라이브러리에서 평가 — 우수로 표시하면 다음 생성의 예시로 우선 사용된다"

    # 0=평가 취소, 1=합격, 2=우수
    rating: IntProperty(default=2, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.entry_id)

    def execute(self, context):
        entry_id = context.scene.lp3d.active_job().entry_id
        if not library.set_rating(entry_id, self.rating):
            self.report({'WARNING'}, "라이브러리에서 항목을 찾을 수 없습니다")
            return {'CANCELLED'}
        labels = {0: "평가 해제", 1: "합격", 2: "우수"}
        self.report({'INFO'}, f"평가: {labels.get(self.rating, self.rating)}")
        return {'FINISHED'}


class LP3D_OT_library_discard(bpy.types.Operator):
    bl_idname = "lp3d.library_discard"
    bl_label = "라이브러리에서 제외"
    bl_description = "이 결과를 라이브러리에서 삭제 — 품질이 낮아 예시로 쓰고 싶지 않을 때"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.entry_id)

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        if library.delete_entry(job.entry_id):
            job.entry_id = ""
            self.report({'INFO'}, "라이브러리에서 제외됨")
            return {'FINISHED'}
        self.report({'WARNING'}, "라이브러리에서 항목을 찾을 수 없습니다")
        return {'CANCELLED'}


class LP3D_OT_variation(bpy.types.Operator):
    bl_idname = "lp3d.variation"
    bl_label = "변형 생성"
    bl_description = "선택 항목과 같은 스타일의 변형(variation)을 새 큐 항목으로 만들어 실행"

    count: IntProperty(name="변형 수", default=3, min=1, max=8)

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.code)

    def execute(self, context):
        props = context.scene.lp3d
        src = props.active_job()
        # 원본 값을 먼저 복사한다 — jobs.add_job()이 컬렉션을 재할당하면 src 참조가
        # 무효가 되어 접근 시 크래시할 수 있다
        code, prompt = src.code, src.prompt
        agent, turns = src.agent, src.auto_turns
        # 변형은 원본을 덮지 않고 새 항목·새 레인에 만든다
        new_job = jobs.add_job(props, prompt)
        new_job.agent = agent
        new_job.auto_turns = turns
        error = session.start_job(context.scene.name, new_job.uid,
                                  variation_of=code, variation_count=self.count)
        if error:
            new_job.state = 'FAILED'
            new_job.status = f"실패: {error}"
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_improve(bpy.types.Operator):
    bl_idname = "lp3d.improve"
    bl_label = "개선하기"
    bl_description = "선택 항목의 결과를 캡처해 한 단계 개선 (개선 요청 텍스트가 있으면 최우선 반영)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.code) and not session.is_active(job.uid)

    def execute(self, context):
        job = context.scene.lp3d.active_job()
        error = session.start_job(context.scene.name, job.uid, improve=True)
        if error:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
        return {'FINISHED'}


class LP3D_OT_export(bpy.types.Operator):
    bl_idname = "lp3d.export"
    bl_label = "익스포트"
    bl_description = "선택 항목의 결과를 게임엔진용으로 내보내기"

    format: EnumProperty(
        name="포맷",
        items=[('FBX', "FBX (Unity)", ""), ('GLTF', "glTF", "")],
        default='FBX',
    )

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.collection_name)

    def execute(self, context):
        from ..pipeline import export
        props = context.scene.lp3d
        job = props.active_job()
        coll = bpy.data.collections.get(job.collection_name)
        if not coll:
            self.report({'ERROR'}, "생성 컬렉션을 찾을 수 없습니다")
            return {'CANCELLED'}
        out_dir = bpy.path.abspath(props.export_dir)
        os.makedirs(out_dir, exist_ok=True)
        try:
            path = export.export_collection(coll, out_dir, self.format)
        except Exception as e:
            self.report({'ERROR'}, f"익스포트 실패: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"내보냄: {path}")
        return {'FINISHED'}


class LP3D_OT_mark_asset(bpy.types.Operator):
    bl_idname = "lp3d.mark_asset"
    bl_label = "에셋 등록"
    bl_description = "선택 항목의 결과를 Asset Browser에 등록 (프리뷰 + 카탈로그)"

    @classmethod
    def poll(cls, context):
        job = context.scene.lp3d.active_job()
        return job is not None and bool(job.collection_name)

    def execute(self, context):
        from ..pipeline import assets
        job = context.scene.lp3d.active_job()
        coll = bpy.data.collections.get(job.collection_name)
        if not coll:
            self.report({'ERROR'}, "생성 컬렉션을 찾을 수 없습니다")
            return {'CANCELLED'}
        try:
            catalog = assets.register_asset(context, coll, job.prompt)
        except Exception as e:
            self.report({'ERROR'}, f"에셋 등록 실패: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"에셋 등록됨: {coll.name} → {catalog}")
        return {'FINISHED'}
```

- [ ] **Step 6: `dev_reload`가 모든 세션을 정리하게 하고 등록 목록을 갱신한다**

`LP3D_OT_dev_reload.execute()`의 `session.cancel_session()` 호출을 `session.cancel_all()`로 바꾼다.

`_CLASSES` 튜플을 교체한다:

```python
_CLASSES = (
    LP3D_OT_edit_prompt, LP3D_OT_rate, LP3D_OT_library_discard,
    LP3D_OT_show_multiview, LP3D_OT_use_multiview_as_ref, LP3D_OT_open_multiview_folder,
    LP3D_OT_load_last_multiview,
    LP3D_OT_clear_snapshots,
    LP3D_OT_paste_ref_image, LP3D_OT_clear_ref_image,
    LP3D_OT_job_add, LP3D_OT_job_remove, LP3D_OT_job_duplicate, LP3D_OT_job_move,
    LP3D_OT_job_retry, LP3D_OT_job_cancel,
    LP3D_OT_queue_start, LP3D_OT_queue_stop,
    LP3D_OT_variation, LP3D_OT_improve,
    LP3D_OT_export, LP3D_OT_mark_asset, LP3D_OT_dev_reload,
)
```

- [ ] **Step 7: 문법을 확인한다**

Run: `python -m py_compile ui/operators.py && grep -n "improve_open\|last_collection\|last_code\|last_prompt\|last_entry_id\|lp3d.generate\|lp3d.cancel\b" ui/operators.py`
Expected: 컴파일 성공, grep 결과 없음

- [ ] **Step 8: 커밋한다**

```bash
git add ui/operators.py
git commit -m "오퍼레이터: 생성 큐 항목 조작과 선택 항목 기준 결과물 처리"
```

---

### Task 8: `ui/panel.py` — 잡 리스트 UI

**Files:**
- Modify: `ui/panel.py`

**Interfaces:**
- Consumes: `properties.STATE_ICONS`, `core/scheduler.counts()`, `core/session.is_active()`, Task 7의 오퍼레이터들
- Produces: `LP3D_UL_jobs` UIList, 재구성된 `LP3D_PT_main` / `LP3D_PT_log` / `LP3D_PT_output`

- [ ] **Step 1: `ui/panel.py`를 통째로 교체한다**

```python
# 3D 뷰포트 사이드바 패널
import os
import time

import bpy

from .. import preferences
from ..core import scheduler, session, snapshots
from . import previews

# 진행 단계 정의 (session.py의 phase 식별자와 일치)
_PHASES = ('GEN', 'EXEC', 'CAPTURE', 'CRITIQUE', 'FINAL')


def _model_labels(job):
    """단계 표시용 생성/비평 모델 이름."""
    if job.agent == 'CODEX':
        return "Codex", "Codex"
    prefs = preferences.get_prefs()
    gen = "기본 모델" if prefs.gen_model == 'DEFAULT' else prefs.gen_model.capitalize()
    crit = gen if prefs.critique_model == 'DEFAULT' else prefs.critique_model.capitalize()
    return gen, crit


class LP3D_UL_jobs(bpy.types.UIList):
    """생성 큐 리스트 — 상태 아이콘 + 프롬프트 + 진행도."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        from ..properties import STATE_ICONS

        row = layout.row(align=True)
        row.alert = item.state == 'FAILED'  # 실패는 눈에 띄어야 한다
        row.label(text="", icon=STATE_ICONS.get(item.state, 'DOT'))
        row.label(text=item.prompt or "(빈 프롬프트)")
        if item.state == 'RUNNING':
            row.label(text=f"{item.iteration}/{item.total_turns}")
        elif item.state == 'FAILED':
            row.label(text="실패")
        elif item.state == 'DONE':
            row.label(text="완료")


class LP3D_PT_main(bpy.types.Panel):
    bl_label = "AI 모델 생성"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI 모델러"

    def draw(self, context):
        layout = self.layout
        props = context.scene.lp3d

        # --- 생성 큐 리스트 ---
        layout.label(text="생성 큐:")
        row = layout.row()
        row.template_list("LP3D_UL_jobs", "", props, "jobs", props, "job_index", rows=4)
        side = row.column(align=True)
        side.operator("lp3d.job_add", text="", icon='ADD')
        side.operator("lp3d.job_remove", text="", icon='REMOVE')
        side.separator()
        side.operator("lp3d.job_duplicate", text="", icon='DUPLICATE')
        side.separator()
        side.operator("lp3d.job_move", text="", icon='TRIA_UP').delta = -1
        side.operator("lp3d.job_move", text="", icon='TRIA_DOWN').delta = 1

        # --- 실행 컨트롤 ---
        run_row = layout.row(align=True)
        run_row.scale_y = 1.2
        run_row.operator("lp3d.queue_start", icon='PLAY')
        if session.is_active():
            run_row.operator("lp3d.queue_stop", text="", icon='CANCEL')

        counts = scheduler.counts()
        layout.label(
            text=(f"대기 {counts['ai_waiting']} · AI 실행 {counts['ai_running']} · "
                  f"Blender 대기 {counts['blender_waiting']}"),
            icon='SORTTIME')

        job = props.active_job()
        if job is None:
            layout.label(text="[＋]로 프롬프트 항목을 추가하세요", icon='INFO')
            return

        # --- 선택 항목 상세 ---
        box = layout.box()
        box.label(text=f"항목 {props.job_index + 1} / {len(props.jobs)}", icon='TEXT')
        box.prop(job, "prompt", text="")
        # 주의: 입력 필드에 scale을 주면 macOS IME(한글 조합)가 더 불안정해짐.
        # 한글은 필드 직접 입력 대신 [프롬프트 입력] 버튼의 OS 네이티브 팝업을 쓴다.
        box.operator("lp3d.edit_prompt", text="프롬프트 입력", icon='TEXT').target = 'prompt'
        box.prop(job, "ref_image_path", text="참조 이미지")
        ref_row = box.row(align=True)
        ref_row.operator("lp3d.paste_ref_image", text="클립보드에서 붙여넣기", icon='PASTEDOWN')
        if job.ref_image_path:
            ref_row.operator("lp3d.clear_ref_image", text="", icon='X')
        agent_row = box.row(align=True)
        agent_row.prop(job, "agent", expand=True)
        box.prop(job, "auto_turns")

        self._draw_multiview(box, props, job)
        self._draw_status(layout, job)

    def _draw_multiview(self, layout, props, job):
        """AI가 만든 멀티뷰(3면도) 시트 — 패널에서 바로 확인하고 참조로 재사용할 수 있게 한다.

        경로가 비어 있어도 상자를 그린다: 지난 세션 시트를 파일에서 되찾는 버튼이 필요하다."""
        mv = layout.box()
        mv_path = job.multiview_path
        if not mv_path:
            mv.operator("lp3d.load_last_multiview",
                        text="저장된 멀티뷰 미리보기", icon='IMAGE_DATA')
            return
        header = mv.row(align=True)
        header.prop(props, "multiview_preview_open", text="", emboss=False,
                    icon='DISCLOSURE_TRI_DOWN' if props.multiview_preview_open
                    else 'DISCLOSURE_TRI_RIGHT')
        header.label(text=f"멀티뷰: {os.path.basename(mv_path)}", icon='IMAGE_DATA')
        if props.multiview_preview_open:
            icon = previews.icon_id(mv_path)
            if icon:
                mv.template_icon(icon_value=icon, scale=7.5)
            elif not os.path.isfile(mv_path):
                mv.label(text="시트 파일이 사라졌습니다", icon='ERROR')
            else:
                mv.label(text="미리보기를 만들 수 없습니다", icon='ERROR')
        row = mv.row(align=True)
        row.operator("lp3d.show_multiview", text="크게 보기", icon='ZOOM_IN')
        row.operator("lp3d.use_multiview_as_ref", text="참조로 사용", icon='FILE_REFRESH')
        row.operator("lp3d.open_multiview_folder", text="", icon='FILEBROWSER')

    def _draw_status(self, layout, job):
        """선택 항목의 진행 상태: 현재 작업 + 경과 시간 + 단계 목록."""
        box = layout.box()
        # 실패는 눈에 띄어야 한다 — 조용히 지나가면 원인을 놓친다 (로그인 만료 사고)
        failed = job.state == 'FAILED' or "실패" in job.status
        head = box.row()
        head.alert = failed
        head.label(text=f"상태: {job.status}", icon='ERROR' if failed else 'INFO')
        if failed:
            if job.status_hint:
                box.label(text=job.status_hint, icon='CONSOLE')
            box.label(text="자세한 원인은 [로그] 패널 참고", icon='TEXT')
        if job.state in ('FAILED', 'CANCELLED'):
            box.operator("lp3d.job_retry", icon='FILE_REFRESH')
        if not session.is_active(job.uid):
            return
        box.operator("lp3d.job_cancel", icon='CANCEL')
        elapsed = int(time.time() - job.started_at) if job.started_at else 0
        box.label(text=f"경과 {elapsed // 60}:{elapsed % 60:02d} · 턴 {job.iteration}/{job.total_turns}",
                  icon='TIME')
        gen_label, crit_label = _model_labels(job)
        steps = (
            ('GEN', f"코드 생성 — {gen_label}"),
            ('EXEC', "Blender 실행"),
            ('CAPTURE', "뷰포트 캡처"),
            ('CRITIQUE', f"스크린샷 비평 — {crit_label}"),
            ('FINAL', "마무리 정리"),
        )
        cur_idx = _PHASES.index(job.phase) if job.phase in _PHASES else -1
        sub = box.column(align=True)
        sub.scale_y = 0.85
        for i, (_pid, label) in enumerate(steps):
            icon = 'PLAY' if i == cur_idx else ('CHECKMARK' if i < cur_idx else 'DOT')
            sub.label(text=f"{i + 1}. {label}", icon=icon)


class LP3D_PT_log(bpy.types.Panel):
    bl_label = "로그"
    bl_parent_id = "LP3D_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        job = context.scene.lp3d.active_job()
        col = self.layout.column(align=True)
        col.scale_y = 0.7
        if job is None:
            col.label(text="선택된 항목이 없습니다")
            return
        for line in job.log.splitlines()[-15:]:
            col.label(text=line)


class LP3D_PT_output(bpy.types.Panel):
    bl_label = "결과물"
    bl_parent_id = "LP3D_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        props = context.scene.lp3d
        job = props.active_job()
        col = layout.column()
        if job is None or not job.collection_name:
            col.label(text="완료된 항목을 선택하세요", icon='INFO')
            col.separator()
            col.operator("lp3d.dev_reload", icon='FILE_REFRESH')
            return

        col.label(text=f"결과: {job.collection_name}", icon='OUTLINER_COLLECTION')

        # 개선 사이클: 결과가 마음에 안 들면 [개선하기]를 필요한 만큼 반복
        if job.code and not session.is_active(job.uid):
            box = layout.box()
            box.label(text="개선", icon='MODIFIER')
            box.prop(job, "improve_feedback", text="")
            box.operator("lp3d.edit_prompt", text="개선 프롬프트 입력",
                         icon='TEXT').target = 'improve_feedback'
            box.operator("lp3d.improve", icon='FILE_REFRESH')
            # 턴별 중간 결과를 옆에 남겨뒀다면 비교가 끝난 뒤 정리할 수 있게 한다
            snaps = snapshots.count(job.collection_name)
            if snaps:
                box.operator("lp3d.clear_snapshots",
                             text=f"단계 스냅샷 {snaps}개 정리", icon='TRASH')
            # 라이브러리 축적: 잘 나온 결과를 우수로 표시하면 다음 생성의 예시로 우선 쓰인다
            if job.entry_id:
                rate = box.row(align=True)
                rate.operator("lp3d.rate", text="우수", icon='SOLO_ON').rating = 2
                rate.operator("lp3d.library_discard", text="제외", icon='TRASH')

        col = layout.column()
        col.prop(props, "export_dir")
        row = col.row(align=True)
        row.operator("lp3d.export", text="FBX").format = 'FBX'
        row.operator("lp3d.export", text="glTF").format = 'GLTF'
        col.operator("lp3d.mark_asset", icon='ASSET_MANAGER')
        col.operator("lp3d.variation", icon='DUPLICATE')
        col.separator()
        col.operator("lp3d.dev_reload", icon='FILE_REFRESH')


_CLASSES = (LP3D_UL_jobs, LP3D_PT_main, LP3D_PT_log, LP3D_PT_output)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
```

- [ ] **Step 2: 문법과 남은 옛 필드 참조를 확인한다**

Run: `python -m py_compile ui/panel.py && grep -rn "props.prompt\|props.status\|props.log\|last_collection\|last_code\|last_prompt\|last_entry_id\|is_running\|improve_open\|props.multiview_path" ui/ core/ properties.py`
Expected: 컴파일 성공, grep 결과 없음

- [ ] **Step 3: 전체 테스트를 돌린다**

Run: `python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 4: 커밋한다**

```bash
git add ui/panel.py
git commit -m "패널: 프롬프트 큐 리스트 UI와 선택 항목 기준 상태·결과물 표시"
```

---

### Task 9: 버전 올리기와 수동 검증 시나리오

**Files:**
- Modify: `blender_manifest.toml:4`
- Modify: `tests/manual_scenarios.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 앞의 모든 태스크
- Produces: 없음 (릴리스 준비)

- [ ] **Step 1: 애드온 버전을 올린다**

`blender_manifest.toml`의 `version = "0.6.1"`을 `version = "0.7.0"`으로 바꾼다.
호환성이 깨지는 변경(씬 프로퍼티 제거)이므로 마이너를 올린다.

- [ ] **Step 2: 수동 검증 시나리오를 추가한다**

`tests/manual_scenarios.md` 끝에 추가한다:

```markdown
16. **배치 실행 병렬성**: 프롬프트 3개("나무 상자", "돌 항아리", "철제 랜턴")를 [＋]로 큐에 추가 → [전체 실행] → 요약 줄에 `AI 실행 3`이 뜨는지, 작업 관리자/`ps`에서 CLI 프로세스가 3개인지 확인. 환경설정 "동시 AI 실행 수"를 1로 낮추면 `AI 실행 1 · 대기 2`가 되는지도 확인.
17. **Blender 직렬화**: 위 상태에서 여러 항목이 동시에 EXEC/CAPTURE 단계에 들어가지 않는지 확인 — 요약 줄의 `Blender 대기`가 늘었다 줄고, 렌더가 겹쳐 깨지거나 컬렉션이 뒤섞이지 않아야 한다. 결과 3개가 Y축으로 4m씩 벌어져 나란히 놓이는지 확인.
18. **부분 실패 격리**: 항목 하나의 에이전트를 CLI가 설치되지 않은 쪽으로 바꿔 [전체 실행] → 그 항목만 빨간 `실패`로 남고 나머지 2개는 정상 완료되는지, [재시도] 버튼으로 되살아나는지 확인.
19. **항목 단위 중단**: 3개 실행 중 하나를 선택해 [항목 중단] → 그 항목만 `취소됨`이 되고 씬에 잔여 오브젝트가 없으며, 나머지 2개는 계속 진행되는지 확인.
20. **실행 중 항목 삭제**: 실행 중인 항목을 [－]로 삭제 → 크래시 없이 해당 CLI 프로세스가 종료되고 나머지 큐가 계속 도는지 확인.
21. **Dev Reload 교착 해제**: 실행 중에 [Dev Reload] → 모든 세션이 정리되고, 리로드 후 `RUNNING`으로 남은 항목이 `대기 중 (세션이 끊겨 초기화됨)`으로 바뀌어 [전체 실행]이 다시 눌리는지 확인.
22. **선택 항목 기준 결과물**: 완료된 항목 3개를 번갈아 선택하며 [FBX 익스포트]/[에셋 등록]/[개선하기]가 각각 선택한 항목의 컬렉션을 대상으로 동작하는지 확인. [변형 생성]은 새 큐 항목으로 추가되어 원본을 덮지 않아야 한다.
```

- [ ] **Step 3: README의 사용 흐름을 갱신한다**

`README.md`에서 단일 프롬프트 입력을 설명하는 문단을 찾아, 큐 기반 흐름으로 고친다.
확인 명령: `grep -n "프롬프트" README.md`
바꿀 내용: "프롬프트를 입력하고 [모델 생성]" → "[＋]로 프롬프트 항목을 큐에 추가하고 [전체 실행] — AI 호출은 환경설정의 동시 실행 수만큼 병렬로, Blender 작업은 하나씩 순차로 진행된다".
해당 문단이 없으면 "사용법" 섹션에 위 한 줄을 추가한다.

- [ ] **Step 4: 전체 테스트와 문법을 최종 확인한다**

Run: `python -m unittest discover -s tests -v && python -m py_compile core/*.py ui/*.py properties.py preferences.py __init__.py`
Expected: 전부 PASS, 컴파일 오류 없음

- [ ] **Step 5: 커밋한다**

```bash
git add blender_manifest.toml tests/manual_scenarios.md README.md
git commit -m "v0.7.0: 병렬 AI 생성 + Blender 직렬 큐, 배치 검증 시나리오 추가"
```

---

## 검증 한계

이 계획의 자동화 테스트는 `core/scheduler.py`(순수 로직)만 덮는다. `bpy`에 의존하는
부분(세션 상태머신, 잡 리스트, UI)은 Blender 안에서만 실행되므로 `tests/manual_scenarios.md`의
16~22번 시나리오로 검증해야 한다. 계획 완료 = 코드 작성 완료이며, **Blender에서 시나리오
16~22를 실행하기 전까지 "동작 확인됨"이라고 말할 수 없다.**
