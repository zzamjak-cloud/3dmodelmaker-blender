# Codex CLI 기반 GPT-6 Astra 통합 구현 계획

> 과거 구현 기록입니다. 2026-09-07부터 생성은 Astra 1턴으로 고정되고 Codex CLI 기본 모델은 접근 불가 시 폴백으로만 사용합니다. 아래의 Claude 지원, 모델 선택, 비평·개선 턴 계획은 폐기되었습니다. 현재 동작과 검증은 [README](../../../README.md)와 [수동 테스트](../../../tests/manual_scenarios.md)를 따릅니다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ModelMaker가 Codex CLI에서 GPT-6 Astra를 기본 사용하고, 실제 실행 모델과 제한적 fallback을 job 및 UI에 정확히 표시하도록 만든다.

**Architecture:** Blender 비의존 모델 정책을 `core/models.py`에 두고 설정, session, UI가 같은 model ID와 표시명을 사용한다. Codex 첫 요청에 `-m gpt-6-astra`를 전달하고 모델 가용성 오류일 때만 동일 요청을 CLI 기본 모델로 한 번 재시도한다. 기존 Blender Python 생성, 실행, 캡처, 비평 loop와 Claude 경로는 유지한다.

**Tech Stack:** Python 3.11, Blender 4.2+, `bpy`, `unittest`, Codex CLI

**Spec:** `docs/superpowers/specs/2026-09-04-codex-astra-integration-design.md`

## Global Constraints

- 모든 Python 주석과 docstring은 한국어로 작성한다.
- Responses API와 API key 관리는 추가하지 않는다.
- Codex model은 AddonPreferences 전역 설정이며 job별 선택 UI는 추가하지 않는다.
- 기존 저장 사용자의 agent 및 Claude model 설정은 보존한다.
- 모델 가용성 오류 외 auth, quota, network, timeout에는 fallback하지 않는다.
- 현재 자동화 환경에서는 외부 Codex 호출이 차단되므로 live A/B는 수동 시나리오로 검증한다.

## 파일 구조

- Create: `core/models.py` - model ID, 표시명, Codex 설정 변환, 모델 가용성 오류 판별
- Create: `tests/test_models.py` - Blender 비의존 모델 정책 unit test
- Create: `tests/test_codex_backend.py` - Codex CLI initial/resume command 계약 test
- Modify: `preferences.py` - Codex model Enum과 기본값
- Modify: `properties.py` - Codex agent 기본값과 job 실행 모델 추적 필드
- Modify: `core/persist.py` - `codex_model` 환경설정 영속화
- Modify: `core/session.py` - provider별 모델 고정, 실제 모델 기록, Astra fallback
- Modify: `ui/panel.py` - 예정/실제 모델과 fallback 표시
- Modify: `tests/verify_queue_in_blender.py` - Blender RNA 기본값과 job 모델 필드 검증
- Modify: `tests/manual_scenarios.md` - Astra 접근, fallback, 3종 A/B 시나리오

---

### Task 1: Blender 비의존 모델 정책

**Files:**
- Create: `core/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `ASTRA_ID: str`, `CODEX_DEFAULT_LABEL: str`, `codex_model_id(selection: str) -> str`, `model_label(agent: str, model_id: str) -> str`, `is_model_unavailable(error: str, model_id: str) -> bool`
- Consumes: 없음

- [ ] **Step 1: 모델 변환과 오류 판별 failing test 작성**

```python
class TestCodexModelSelection(unittest.TestCase):
    def test_astra_maps_to_api_model_id(self):
        self.assertEqual(models.codex_model_id("ASTRA"), "gpt-6-astra")

    def test_default_omits_explicit_model(self):
        self.assertEqual(models.codex_model_id("DEFAULT"), "")

    def test_labels_are_stable(self):
        self.assertEqual(models.model_label("CODEX", "gpt-6-astra"), "GPT-6 Astra")
        self.assertEqual(models.model_label("CODEX", ""), "Codex CLI 기본 모델")


class TestModelUnavailable(unittest.TestCase):
    def test_requires_model_context_and_availability_phrase(self):
        self.assertTrue(models.is_model_unavailable(
            "The model gpt-6-astra does not exist or you do not have access",
            "gpt-6-astra"))
        self.assertFalse(models.is_model_unavailable("HTTP 401 Unauthorized", "gpt-6-astra"))
        self.assertFalse(models.is_model_unavailable("HTTP 429 rate_limit", "gpt-6-astra"))
        self.assertFalse(models.is_model_unavailable("connection refused", "gpt-6-astra"))

    def test_metadata_warning_is_not_a_failure(self):
        warning = "Model metadata for `gpt-6-astra` not found. Defaulting to fallback metadata"
        self.assertFalse(models.is_model_unavailable(warning, "gpt-6-astra"))
```

- [ ] **Step 2: test가 module 부재로 실패하는지 확인**

Run: `python -m unittest tests.test_models -v`
Expected: FAIL because `core/models.py` does not exist.

- [ ] **Step 3: 최소 모델 정책 구현**

```python
ASTRA_ID = "gpt-6-astra"
CODEX_DEFAULT_LABEL = "Codex CLI 기본 모델"


def codex_model_id(selection: str) -> str:
    """저장된 Codex 모델 선택을 CLI model ID로 바꾼다."""
    return ASTRA_ID if selection == "ASTRA" else ""


def model_label(agent: str, model_id: str) -> str:
    """job 상태와 로그에 사용할 안정적인 모델 표시명을 반환한다."""
    if agent == "CODEX":
        return "GPT-6 Astra" if model_id == ASTRA_ID else CODEX_DEFAULT_LABEL
    return model_id.capitalize() if model_id else "기본 모델"


def is_model_unavailable(error: str, model_id: str) -> bool:
    """명시한 모델 자체가 계정에서 사용 불가한 오류인지 좁게 판별한다."""
    text = (error or "").lower()
    model = (model_id or "").lower()
    if not model or (model not in text and "astra" not in text):
        return False
    if "model metadata" in text and "fallback metadata" in text:
        return False
    phrases = (
        "model not found", "does not exist", "unsupported model",
        "do not have access", "doesn't have access", "not available to",
    )
    return any(phrase in text for phrase in phrases)
```

- [ ] **Step 4: 모델 정책 test 통과 확인**

Run: `python -m unittest tests.test_models -v`
Expected: all tests PASS.

- [ ] **Step 5: Task 1 변경 commit**

```powershell
git add core/models.py tests/test_models.py
git commit -m "feat: Codex 모델 정책을 추가"
```

### Task 2: Codex 명령 및 설정 연결

**Files:**
- Create: `tests/test_codex_backend.py`
- Modify: `preferences.py:54-73,125-153`
- Modify: `properties.py:44-56,99-113`
- Modify: `core/persist.py:12-17`
- Modify: `agents/codex_cli.py:27-47` only if a test exposes a command regression

**Interfaces:**
- Consumes: `models.ASTRA_ID`, `models.codex_model_id()`
- Produces: `LP3DPreferences.codex_model`, new job/scene default agent `CODEX`

- [ ] **Step 1: Codex command 계약 failing test 작성**

`tests/test_codex_backend.py`에서 임시 package module을 구성하여 `agents/base.py`와 `agents/codex_cli.py`를 import한다.

```python
def test_initial_command_includes_explicit_astra_model(self):
    backend = CodexBackend("codex", self.workdir)
    backend.model = "gpt-6-astra"
    command = backend.build_initial_command("ignored")
    self.assertEqual(command[command.index("-m") + 1], "gpt-6-astra")

def test_default_model_omits_model_flag(self):
    backend = CodexBackend("codex", self.workdir)
    self.assertNotIn("-m", backend.build_initial_command("ignored"))

def test_resume_does_not_override_session_model(self):
    backend = CodexBackend("codex", self.workdir)
    backend.model = "gpt-6-astra"
    command = backend.build_resume_command("thread-1", "ignored", [])
    self.assertNotIn("-m", command)
    self.assertIn("thread-1", command)
```

- [ ] **Step 2: 기존 command 구현에서 test 통과 여부 확인**

Run: `python -m unittest tests.test_codex_backend -v`
Expected: PASS. 실패하면 `agents/codex_cli.py`를 command 계약에 맞게 최소 수정한다.

- [ ] **Step 3: 설정 source guard failing test를 `tests/test_models.py`에 추가**

```python
def test_preferences_and_scene_defaults_select_codex_astra(self):
    prefs = read_source("preferences.py")
    props = read_source("properties.py")
    persist = read_source("core/persist.py")
    self.assertIn("codex_model: EnumProperty", prefs)
    self.assertIn("default='ASTRA'", prefs)
    self.assertGreaterEqual(props.count("default='CODEX'"), 2)
    self.assertIn('"codex_model"', persist)
```

- [ ] **Step 4: 설정 test 실패 확인**

Run: `python -m unittest tests.test_models -v`
Expected: FAIL because Codex model preference and defaults are absent.

- [ ] **Step 5: Codex model 설정과 persistence 구현**

`preferences.py`에 다음 Enum을 추가하고 `draw()`에서 Codex CLI 경로 다음에 표시한다.

```python
_CODEX_MODEL_ITEMS = [
    ('ASTRA', "GPT-6 Astra", "공간 추론과 vision이 강화된 고품질 모델"),
    ('DEFAULT', "CLI 기본 모델", "Codex CLI에 설정된 기본 모델 사용"),
]
codex_model: EnumProperty(
    name="Codex 모델",
    description="Codex가 Blender 코드를 생성하고 렌더를 비평할 때 사용할 모델",
    items=_CODEX_MODEL_ITEMS,
    default='ASTRA',
    update=_persist_cb,
)
```

`_Defaults.codex_model = 'ASTRA'`를 추가하고 `core/persist.py`의 `_PREF_KEYS`에 `codex_model`을 추가한다. `properties.py`의 job과 scene agent 기본값을 모두 `CODEX`로 변경한다.

- [ ] **Step 6: 설정과 command test 통과 확인**

Run: `python -m unittest tests.test_models tests.test_codex_backend -v`
Expected: all tests PASS.

- [ ] **Step 7: Task 2 변경 commit**

```powershell
git add preferences.py properties.py core/persist.py agents/codex_cli.py tests/test_models.py tests/test_codex_backend.py
git commit -m "feat: Codex Astra를 기본 생성 설정으로 연결"
```

### Task 3: Session 모델 고정과 Astra fallback

**Files:**
- Modify: `properties.py:84-90`
- Modify: `core/session.py:136-166,219-222,478-520`
- Create: `tests/test_session_models.py`

**Interfaces:**
- Consumes: `models.codex_model_id()`, `models.model_label()`, `models.is_model_unavailable()`
- Produces: `requested_model`, `effective_model`, `model_fallback` job fields; `GenerationSession._try_model_fallback(error: str) -> bool`

- [ ] **Step 1: Session model routing과 fallback failing test 작성**

`tests/test_session_models.py`는 `core/session.py` source guard와 `core/models.py`의 실제 판별을 함께 사용한다.

```python
def test_session_has_single_use_model_fallback(self):
    source = read_source("core/session.py")
    self.assertIn("def _try_model_fallback", source)
    self.assertIn("self.model_fallback_used", source)
    self.assertIn("job.requested_model", source)
    self.assertIn("job.effective_model", source)

def test_job_declares_model_result_fields(self):
    source = read_source("properties.py")
    self.assertIn("requested_model: StringProperty", source)
    self.assertIn("effective_model: StringProperty", source)
    self.assertIn("model_fallback: BoolProperty", source)
```

- [ ] **Step 2: test 실패 확인**

Run: `python -m unittest tests.test_session_models -v`
Expected: FAIL because job fields and session fallback are absent.

- [ ] **Step 3: job 모델 추적 필드와 session 초기화 구현**

`properties.py` 결과 필드에 다음을 추가한다.

```python
requested_model: StringProperty(default="")
effective_model: StringProperty(default="")
model_fallback: BoolProperty(default=False)
```

`GenerationSession.__init__()`에서 Codex면 `prefs.codex_model`을 model ID로 변환하여 backend에 주입하고, Claude면 기존 생성/비평 설정을 그대로 주입한다. 요청 ID와 표시명을 session에 고정한 뒤 job 필드에 복사한다. fallback 재전송에 사용할 최근 prompt와 image 목록, `model_fallback_used = False`도 초기화한다.

- [ ] **Step 4: 마지막 요청 보존과 단일 fallback 구현**

`_dispatch()`가 backend용 prompt 조립을 마친 뒤 다음 값을 보존한다.

```python
self._pending_prompt = prompt
self._pending_images = list(images or [])
```

`_handle_response()`의 일반 오류 분류보다 앞에서 `_try_model_fallback(error)`를 호출한다.

```python
def _try_model_fallback(self, error: str) -> bool:
    """Astra가 계정에서 사용 불가할 때 같은 요청을 CLI 기본 모델로 한 번 재시도한다."""
    if (self.backend.name != "codex" or self.model_fallback_used
            or self.backend.model != models.ASTRA_ID
            or not models.is_model_unavailable(error, models.ASTRA_ID)):
        return False
    self.model_fallback_used = True
    self.backend.model = ""
    job = self._job()
    if job:
        job.effective_model = models.CODEX_DEFAULT_LABEL
        job.model_fallback = True
    self._set_status(
        "GPT-6 Astra 사용 불가 - Codex CLI 기본 모델로 재시도중...",
        "모델 fallback: GPT-6 Astra -> Codex CLI 기본 모델",
        phase='GEN')
    self._dispatch(self._pending_prompt, images=self._pending_images)
    return True
```

Fallback 재호출 전에 첫 시도에서 얻은 불완전한 session ID는 사용하지 않도록 `self.session_id = None`으로 초기화한다. 두 번째 실패는 기존 오류 처리로 보낸다.

- [ ] **Step 5: session source test와 모델 정책 test 통과 확인**

Run: `python -m unittest tests.test_session_models tests.test_models tests.test_errors -v`
Expected: all tests PASS.

- [ ] **Step 6: Task 3 변경 commit**

```powershell
git add properties.py core/session.py tests/test_session_models.py
git commit -m "feat: Astra 실행 모델 추적과 fallback을 추가"
```

### Task 4: 실행 모델 UI와 Blender 검증

**Files:**
- Modify: `ui/panel.py:15-22,25-40,84-101,131-164`
- Modify: `tests/verify_queue_in_blender.py:18-45`
- Create: `tests/test_model_ui.py`

**Interfaces:**
- Consumes: job `requested_model`, `effective_model`, `model_fallback`
- Produces: `_job_model_label(job) -> str`, `_queue_model_label(job) -> str`

- [ ] **Step 1: UI label helper failing test 작성**

`tests/test_model_ui.py`에서는 Blender import 없이 UI 표시 규칙을 검사할 수 있도록 표시 문자열 계산을 `core/models.py`의 다음 함수로 확장한다.

```python
def job_model_label(requested_model: str, effective_model: str,
                    fallback: bool, running: bool) -> str:
    if effective_model:
        suffix = " (Astra 사용 불가)" if fallback else ""
        return f"사용 모델: {effective_model}{suffix}"
    return f"예정 모델: {requested_model or '기본 모델'}"
```

Test cases:

```python
self.assertEqual(models.job_model_label("GPT-6 Astra", "", False, False),
                 "예정 모델: GPT-6 Astra")
self.assertEqual(models.job_model_label("GPT-6 Astra", "GPT-6 Astra", False, True),
                 "사용 모델: GPT-6 Astra")
self.assertEqual(models.job_model_label("GPT-6 Astra", "Codex CLI 기본 모델", True, True),
                 "사용 모델: Codex CLI 기본 모델 (Astra 사용 불가)")
```

- [ ] **Step 2: UI helper test 실패 확인**

Run: `python -m unittest tests.test_model_ui -v`
Expected: FAIL because `job_model_label()` is absent.

- [ ] **Step 3: 상세 상태와 큐 표시 구현**

`ui/panel.py`는 preferences를 다시 읽지 않고 job 필드를 우선 사용한다. 아직 시작하지 않은 기존 job은 agent와 현재 preferences로 예정 모델을 계산한다.

- 상세 box의 상태 문구 바로 아래에 `job_model_label()` 결과를 `INFO` icon으로 표시한다.
- 실행 중 queue row는 `Astra {iteration}/{total_turns}`를 표시한다.
- 완료된 선택 job에도 사용 모델 행을 유지한다.
- 단계 목록의 생성 및 비평 label은 `effective_model or requested_model`을 사용한다.

- [ ] **Step 4: Blender headless queue 검증 확장**

`tests/verify_queue_in_blender.py`에서 신규 기본과 필드를 확인한다.

```python
check("새 scene 기본 에이전트 Codex", props.agent == 'CODEX', props.agent)
a = jobs.add_job(props, "나무 상자")
check("새 job 기본 에이전트 Codex", a.agent == 'CODEX', a.agent)
check("새 job 모델 추적값 초기 상태", not a.requested_model and not a.effective_model)
check("새 job fallback 초기값 false", a.model_fallback is False)
```

- [ ] **Step 5: UI helper와 전체 unit test 실행**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: all tests PASS.

- [ ] **Step 6: Blender headless 검증 실행**

Run: `.\scripts\dev_run.ps1 -Background -PythonFile tests\verify_queue_in_blender.py`
Expected: exit code 0 and final output `모두 통과`.

- [ ] **Step 7: Task 4 변경 commit**

```powershell
git add core/models.py ui/panel.py tests/test_model_ui.py tests/verify_queue_in_blender.py
git commit -m "feat: 생성 중 실제 Codex 모델을 표시"
```

### Task 5: A/B 수동 검증과 최종 회귀

**Files:**
- Modify: `tests/manual_scenarios.md:1-25`
- Modify: `README.md:31-40`

**Interfaces:**
- Consumes: 완성된 Codex model 설정, fallback, UI 표시
- Produces: 사용자가 개발 Blender에서 재현 가능한 Astra 검증 절차

- [ ] **Step 1: 기존 모순된 턴 설명 수정**

수동 시나리오의 `자동 반복 1(=3턴)` 문구를 현재 1:1 정책에 맞게 `자동 반복 3`으로 고친다.

- [ ] **Step 2: Astra 접근 및 fallback 시나리오 추가**

다음을 수동 시나리오에 추가한다.

```markdown
24. **Astra 모델 표시**: Codex 모델을 GPT-6 Astra로 두고 job 실행 -> 대기 중 `예정 모델`, 실행/완료 후 `사용 모델: GPT-6 Astra`, 단계 목록의 생성/비평 모델명이 모두 일치하는지 확인.
25. **Astra 가용성 fallback**: Astra 접근이 없는 계정에서 실행 -> 모델 가용성 오류에 한해 `Codex CLI 기본 모델 (Astra 사용 불가)`로 한 번 재시도하고 로그에 요청/실제 모델이 남는지 확인. 네트워크 차단이나 로그인 만료에서는 fallback하지 않아야 한다.
```

- [ ] **Step 3: 대표 3종 A/B 절차 추가**

프랍, 건물, 자연물 prompt와 동일 seed/3턴/capture 조건, silhouette/비율/detail/색/floating/nonmanifold/triangle/error 평가표를 문서화한다. 세 항목 중 두 항목 이상 Astra 우세를 채택 기준으로 명시한다.

- [ ] **Step 4: README 사용법 갱신**

Codex가 신규 기본 agent이며 환경설정에서 `GPT-6 Astra` 또는 `CLI 기본 모델`을 선택할 수 있고, 실제 사용 모델이 job 상태에 표시된다는 내용을 사용법에 추가한다. 단계적 rollout 때문에 Astra 미지원 시 명시적 fallback이 있다는 점도 한 문장으로 설명한다.

- [ ] **Step 5: 최종 정적·unit 검증**

Run: `git diff --check`
Expected: no output, exit code 0.

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: all tests PASS.

- [ ] **Step 6: 최종 Blender headless 검증**

Run: `.\scripts\dev_run.ps1 -Background -PythonFile tests\verify_queue_in_blender.py`
Expected: exit code 0 and final output `모두 통과`.

- [ ] **Step 7: 최종 문서 변경 commit**

```powershell
git add README.md tests/manual_scenarios.md
git commit -m "docs: Astra 개발 빌드 검증 절차를 추가"
```

- [ ] **Step 8: 실제 A/B 제한 기록**

외부 Codex 연결이 가능한 사용자 로컬 Blender에서 3종 A/B를 수행하기 전에는 Astra가 Claude보다 우세하다고 주장하지 않는다. 자동 검증 완료와 live 품질 검증 미수행을 최종 보고에서 분리한다.
