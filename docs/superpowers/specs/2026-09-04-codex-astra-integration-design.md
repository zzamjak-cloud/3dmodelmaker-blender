# Codex CLI 기반 GPT-6 Astra 통합 설계

- 작성일: 2026-09-04
- 대상: `lp3d_modelmaker` Blender 확장
- 목적: GPT-6 Astra를 기존 Codex CLI 생성 루프에 연결하고, 새 설치에서 Codex를 기본 에이전트로 사용하며 실제 사용 모델을 UI에 명확히 표시한다.

## 1. 배경과 범위

ModelMaker는 AI가 3D 파일을 직접 반환하는 구조가 아니다. Claude Code 또는 Codex CLI가 `lowpoly` API 기반 Blender Python 코드를 작성하고, 애드온이 코드를 실행한 뒤 렌더 캡처와 geometry 통계를 다시 모델에 전달하여 반복 개선한다.

GPT-6 Astra는 native mesh 생성 모델이 아니라 text/image 입력, code generation, vision, spatial reasoning을 제공하는 범용 모델이다. 따라서 새 asset import 경로를 만들지 않고 기존 Codex backend에서 모델을 명시하는 방식으로 통합한다.

이번 변경 범위는 다음과 같다.

- Codex 전용 모델 설정 추가
- 새 설치 및 새 scene의 기본 에이전트를 Codex로 변경
- GPT-6 Astra를 새 설치의 기본 Codex 모델로 지정
- 실제 요청 모델과 실행 모델을 job에 기록하고 UI에 표시
- Astra 가용성 오류에 한정한 Codex CLI 기본 모델 fallback
- 관련 unit test, Blender headless 검증, 수동 A/B 시나리오 추가

Responses API 직접 호출, API key 관리, native 3D 파일 출력, job별 모델 선택은 범위에서 제외한다.

## 2. 설정과 호환성

`LP3DPreferences`에 Codex 전용 `codex_model` EnumProperty를 추가한다.

- `ASTRA`: `gpt-6-astra`
- `DEFAULT`: Codex CLI 설정의 기본 모델

신규 기본값은 `ASTRA`다. 기존 `gen_model`과 `critique_model`은 Claude 전용 설정으로 유지하여 저장 형식과 기존 사용자 선택을 보존한다. `codex_model`을 preferences persistence key에 추가하며, 값이 없는 기존 설정은 신규 기본값을 사용한다.

`LP3DSceneProps.agent`와 `LP3DJobItem.agent`의 신규 기본값은 `CODEX`로 바꾼다. 이미 JSON 또는 `.blend`에 저장된 agent 값은 기존 복원 절차를 통해 유지한다. Codex CLI가 설치되지 않은 환경에서는 현재처럼 실행 전에 명확한 CLI 미설치 오류를 표시하며 Claude로 조용히 전환하지 않는다.

모델 선택은 AddonPreferences 전역 설정으로 둔다. job별 모델 선택은 큐 persistence와 UI 복잡도를 크게 늘리므로 이번 범위에 포함하지 않는다.

## 3. Job 모델 추적

`LP3DJobItem`에 다음 runtime/result 문자열 필드를 추가한다.

- `requested_model`: job 시작 시 요청한 표시용 모델명
- `effective_model`: 현재 실제 실행 중이거나 최종 실행된 표시용 모델명
- `model_fallback`: Astra fallback 여부

job 시작 시 preferences 값을 session에 고정한다. 실행 도중 환경설정을 바꿔도 진행 중 job의 명령과 표시가 변하지 않는다. 완료된 job에도 값을 남겨 어떤 모델로 결과를 만들었는지 확인할 수 있게 한다. job 복제 시 결과 추적 필드는 복사하지 않고 새 실행 시 다시 결정한다.

표시명은 다음 원칙을 따른다.

- Astra: `GPT-6 Astra`
- Codex CLI 기본: `Codex CLI 기본 모델`
- Claude alias: 기존 `Opus`, `Sonnet`, `Haiku`, `기본 모델`

## 4. 생성 데이터 흐름

Codex job 시작 시 `codex_model`을 읽어 `CodexBackend.model`에 전달한다. `ASTRA`이면 첫 명령은 `codex exec -m gpt-6-astra ...`가 된다. `DEFAULT`이면 `-m`을 생략한다.

Codex의 resume 명령은 모델 인자를 별도로 받지 않고 최초 session의 모델을 유지한다. 따라서 코드 생성, 실행 오류 자기수정, 렌더 비평과 개선은 모두 같은 Astra session에서 수행한다. Claude의 생성 모델과 비평 모델 분리 동작은 변경하지 않는다.

기존 파이프라인은 그대로 유지한다.

1. 선택적 multiview 참조 생성
2. Blender Python 코드 생성
3. 제한된 executor에서 코드 실행
4. multi-angle render와 geometry 통계 수집
5. 같은 AI session에서 비평 및 전체 코드 재작성
6. cleanup, game-ready 처리, library 저장

## 5. Astra 가용성 Fallback

Fallback은 Astra를 명시한 Codex 첫 호출이 모델 가용성 오류로 실패한 경우에만 한 번 실행한다.

대상 오류는 모델명을 포함하면서 다음 의미가 명확한 응답이다.

- model not found 또는 does not exist
- unsupported model
- account 또는 workspace에 해당 모델 access가 없음

인증 만료, quota, rate limit, network, timeout, 일반 server 오류에는 fallback하지 않는다. 이 오류들을 다른 모델로 재시도하면 원인을 숨기고 중복 과금이나 지연을 만들 수 있기 때문이다.

Fallback 시 새 초기 호출에서 `-m`을 제거하고 Codex CLI 기본 모델을 사용한다. `effective_model`을 `Codex CLI 기본 모델`로 갱신하고 `model_fallback`을 참으로 설정한다. 상태와 로그에는 `GPT-6 Astra 사용 불가 - Codex CLI 기본 모델로 재시도`를 표시한다. fallback 호출까지 실패하면 기존 오류 분류와 종료 처리를 그대로 사용한다.

CLI가 출력하는 로컬 metadata 경고만으로는 fallback하지 않는다. 실제 API 요청이 성공할 수 있으므로 최종 호출 오류만 판단한다.

## 6. UI 표시

선택 job 상세 영역의 상태 상단에 실행 전후 모두 모델 행을 표시한다.

- 대기: `예정 모델: GPT-6 Astra`
- 실행/완료: `사용 모델: GPT-6 Astra`
- fallback: `사용 모델: Codex CLI 기본 모델 (Astra 사용 불가)`

실행 중 큐 목록에는 폭을 과도하게 차지하지 않도록 `Astra 2/3`처럼 축약한 모델명과 진행도를 표시한다. 선택 job의 단계 목록에는 코드 생성과 screenshot 비평 단계 모두 `GPT-6 Astra`를 표시한다.

표시는 preferences의 현재 값이 아니라 job에 고정된 `requested_model`과 `effective_model`을 사용한다. 따라서 설정 변경과 fallback 이후에도 실제 실행 내역과 일치한다.

## 7. 오류 처리

모델 가용성 판별은 `core/errors.py`에 좁은 분류로 추가한다. 광범위한 `403` 또는 문자열 `access`만으로 판별하지 않고, 모델명과 모델 접근 문맥이 함께 있는 경우만 인정한다.

모델 fallback 상태는 기존 resume 실패의 stateless fallback과 별도로 관리한다. 두 fallback은 목적과 조건이 다르며 각각 최대 한 번만 허용한다. session 종료, scheduler AI slot 반환, 취소 처리 방식은 변경하지 않는다.

로그에는 최소한 다음 정보를 남긴다.

- 요청 provider와 모델
- 실제 provider와 모델
- fallback 발생 여부와 원인 요약
- 최종 실패 시 기존 상세 오류

## 8. 검증

순수 Python unit test로 다음 계약을 고정한다.

- Astra 선택 시 초기 Codex 명령에 `-m gpt-6-astra` 포함
- CLI 기본 모델 선택 시 `-m` 생략
- resume 명령은 기존 session을 사용하고 임의 모델 변경 없음
- 모델 가용성 오류에만 fallback 허용
- auth, quota, network, timeout 오류에는 fallback 금지
- job 모델 표시명과 fallback 표시
- 신규 agent 기본값과 preferences persistence key
- Claude backend와 기존 모델 선택 회귀 없음

기존 전체 unit test를 실행하고 Blender headless queue 검증으로 PropertyGroup 등록, job 생성/복제, session 상태 전이를 확인한다.

실제 품질 비교는 동일한 설정으로 다음 대표 asset을 각각 Claude 기존 기본과 Codex+Astra에서 생성한다.

1. 프랍: 낡은 나무 배럴, 금속 band 2개와 깨진 판자 1개
2. 건물: 작은 중세 대장간, 돌 기단과 큰 굴뚝
3. 자연물: 굽은 소나무, 드러난 뿌리와 비대칭 수관

공통 조건은 같은 prompt, seed, 3턴, capture 수와 해상도다. silhouette, 비율, signature detail, 색 분리, floating part, nonmanifold, triangle 수, 실행 오류 횟수를 비교한다. 세 항목 중 두 항목 이상에서 Astra가 우세하면 품질 개선 근거로 기록한다.

현재 자동화 환경에서는 Codex 외부 네트워크가 차단되어 실제 A/B 생성은 수행할 수 없다. 구현과 자동 test를 완료한 뒤 사용자의 로컬 Blender 개발 빌드에서 수동 시나리오를 실행한다.

## 9. 완료 조건

- 새 job이 기본적으로 Codex+Astra를 요청한다.
- UI와 로그에서 요청 모델과 실제 모델을 확인할 수 있다.
- Astra가 지원되지 않는 계정은 모델 가용성 오류에 한해 Codex CLI 기본 모델로 한 번 fallback한다.
- 기존 저장 설정과 Claude workflow가 유지된다.
- unit test와 Blender headless 검증이 통과한다.
- 실제 A/B 절차가 재현 가능한 수동 시나리오로 제공된다.
