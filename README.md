# AI LowPoly ModelMaker

Codex CLI의 GPT-6 Astra로 게임용 3D 모델(프랍·건물·자연물)을 블렌더 안에서 텍스트 프롬프트로 생성하는 Blender 확장.

- GPT-6 Astra가 헬퍼 라이브러리(`lp`) 기반 bpy 코드를 1턴으로 생성하고 실행한 뒤 완료
- 아트 스타일 5종 — **로우폴리 캐주얼**(기본) / **복셀** / **귀여운 둥근** / **스타일리쉬 캐주얼** / **사실적(미드폴리)**
- 제작 모드 3종 — **오브젝트**(프롭·건물, 멀티뷰 3면도 생성, 기본) / **배경 공간**(씬 규모 선택, 배치 3단계) / **캐릭터**(원화 → 6면도 턴어라운드 → 리깅 자세 모델링 → 매핑)
- 모델링 타입 2종 — **컬러 스와치**(공유 팔레트 텍스처, Unity 드로우콜 1개) / **개별 매핑**(박스 투영 언랩 + 6면도 AI 텍스처를 모델 이름의 개별 텍스처로 베이크)
- FBX/glTF 게임엔진 익스포트, Asset Browser 등록, 변형(variation) 생성
- 참조 이미지(멀티뷰 3면도·씬 컨셉 시트·텍스처 6면도)는 OpenRouter Image API로 생성 — 모델 선택 가능 (기본 덕테이프 `openai/gpt-image-2`)
- 모델링은 `codex` CLI 구독 그대로 — OpenRouter 키가 없으면 이미지도 codex `image_gen`으로 폴백해 API 키 없이 동작

## 설치 (자동 업데이트, 권장)

한 번 등록하면 이후 새 릴리즈가 나올 때 블렌더가 자동으로 업데이트를 확인하고 알립니다. 설치는 사용자가 실행합니다.

1. 블렌더(**5.2 LTS 권장, 최소 4.2**) → Edit → Preferences → **Get Extensions** → 우측 상단 **▼ → Repositories** → **+ → Add Remote Repository**
2. URL에 아래 주소 입력 후 **Check for Updates on Startup** 체크:
   ```
   https://github.com/zzamjak-cloud/3dmodelmaker-blender/releases/latest/download/index.json
   ```
3. Get Extensions 목록에서 **AI LowPoly ModelMaker** 검색 → Install
4. 기존에 Install from Disk나 개발용 심링크로 설치한 버전이 있으면 먼저 제거 (중복 설치 방지)

이후 업데이트는 블렌더 시작 시 알림이 뜨며, Get Extensions에서 **Update All** 한 번으로 반영됩니다.

## 설치 (수동)

1. [Releases](https://github.com/zzamjak-cloud/3dmodelmaker-blender/releases)에서 zip 다운로드 (압축 풀지 않음)
2. 블렌더 → Edit → Preferences → Get Extensions → 우측 상단 **▼ → Install from Disk** → zip 선택

## 사용법

1. `codex` CLI가 설치·로그인되어 있어야 합니다. (경로 자동 탐지 실패 시 애드온 환경설정에서 절대경로 지정)
2. (선택) 환경설정 → **참조 이미지 생성**에 OpenRouter API 키를 넣으면 참조 시트를 OpenRouter로 생성합니다 → [이미지 생성 백엔드](#이미지-생성-백엔드)
3. 3D 뷰포트 사이드바(N) → **AI 모델러** 탭
4. 프롬프트 입력 (예: `낡은 나무 배럴, 금속 밴드 2개`) → **스타일** 선택 → **제작 모드** 선택 (오브젝트 또는 배경 공간) → **모델링 타입** 선택 → **[＋]**로 큐에 추가 → **전체 실행**
   (AI 호출은 환경설정의 동시 실행 수만큼 병렬로, Blender 작업은 하나씩 순차로 진행된다)
5. 완료 후 **결과물** 패널에서 FBX/glTF 익스포트, 에셋 등록, 변형 생성

모든 생성은 `GPT-6 Astra`를 요청하고 1턴으로 완료한다. 대기 중에는 `예정 모델`이, 실행·완료 후에는 실제 사용한 모델이 상태에 표시된다. Astra가 지원되지 않거나 서버가 혼잡할 때(`at capacity`)는 초기 모델 가용성 오류에 한해서만 `Codex CLI 기본 모델 (Astra 사용 불가)`로 한 번 재시도한다. 네트워크 차단이나 로그인 만료 같은 오류에는 폴백하지 않는다. 코드 실행 실패나 응답 형식 오류의 복구 재시도는 유지하며, 성공한 결과에는 후속 AI 호출을 하지 않는다. 이전 버전의 에이전트·턴 수·모델 선택 저장값은 새 생성에 적용하지 않는다.

## 모델링 타입

큐 항목마다 드롭다운으로 고른다.

- **컬러 스와치** (기본): 모든 면을 공유 팔레트 텍스처의 색 셀에 매핑한다. 씬의 모든 결과가 머티리얼 하나를 공유해 드로우콜이 1개다.
- **개별 매핑**: 생성·정리가 끝난 모델을 6방향 박스 투영으로 언랩해 한 장의 0~1 아틀라스에 패킹하고, 팔레트 색이 칠해진 모델을 FRONT/RIGHT/BACK/LEFT/TOP/BOTTOM 6시점 직교 렌더(3x2 시트)로 캡처해 codex `image_gen`에 paint-over 가이드로 보낸다. 돌아온 6면도를 시점별로 잘라 모델 표면에 깊이 인식 투영해 UV 아틀라스로 베이크(CPU)하고, **모델 이름**과 같은 이름의 이미지·머티리얼을 만들어 적용한다. 팔레트 UV와 머티리얼은 제거되어 게임엔진에는 텍스처 UV 하나만 나간다. PNG는 `~/Downloads/blender/`에 `{모델 이름}.png`로 보관되고 익스포트 시 함께 복사된다. 텍스처 크기는 환경설정 **개별 매핑 텍스처 크기**(512/1024/2048, 기본 1024)로 정한다.
  - 텍스처 단계가 실패하면(codex 없음·시트 미저장·베이크 오류) 기하는 그대로 두고 팔레트 재질로 마감하며 원인을 로그에 남긴다.
  - 투영 베이크 엔진은 [UVmapping-Blender](https://github.com/zzamjak-cloud/UVmapping-Blender)의 `texture_bake.py`를 그대로 가져온 것이다(`texturing/bake.py`, 수정 없음).

## 스타일

큐 항목마다 드롭다운으로 고른다. 스타일은 **형태 규칙·폴리 버짓·배색**과 **참조 시트 이미지의 화풍**을 함께 정한다 — 둘이 따로 놀면 시트를 보고 만든 모델이 시트와 다른 스타일로 나온다.

| 스타일 | 성격 | 모델 1개 트라이 상한 |
|---|---|---|
| **로우폴리 캐주얼** (기본) | 각진 면, 플랫 셰이딩, 채도 높은 색. 베벨 금지 | 10,000 |
| **복셀 (로블록스형)** | 같은 크기의 정육면체 격자만. 곡선·경사·변형기 없음 | 12,000 |
| **귀여운 둥근** | 전면 베벨, 부푼 덩어리, 상단이 큰 역삼각 비율, 파스텔 | 20,000 |
| **스타일리쉬 캐주얼** | 기울고 휜 실루엣, 극단적 상하 대비, 표면 디테일 다수 | 25,000 |
| **사실적 (미드폴리)** | 실제 비율·실제 치수, 과장 없음, 낮은 채도 | 60,000 |

트라이 상한은 **모델 1개 기준**이다. 배경 공간은 그 안에 에셋이 여러 종 들어가므로 씬 전체를 이 값으로 묶지 않는다.

배경 공간의 자식 에셋 잡은 부모 배경의 스타일을 그대로 물려받는다 (한 공간에서 스타일이 섞이지 않도록).

프롬프트는 공통 골격 `prompts/system_base.md`(출력 형식·실행 환경·스타일 무관 완성도 규칙)와 스타일 조각 `prompts/styles/*.md`(형태 규칙·폴리 버짓·배색·예시 코드)로 나뉘어 있고, 조립은 `core/prompts.py`가 한다. 스타일을 추가하려면 `prompts/styles/`에 md를 넣고 `core/styles.py`의 카탈로그에 한 줄 추가하면 드롭다운·프롬프트·시트 화풍이 전부 따라온다.

복셀 스타일은 프롬프트만으로 성립하지 않아 격자 헬퍼(`lp.voxel` / `lp.voxel_box` / `lp.voxel_column`)를 별도로 제공하며, 이 어휘는 복셀 스타일의 프롬프트에만 노출된다. 맞닿은 안쪽 면은 생성하지 않으므로 블록을 붙여 쌓아도 트라이가 늘지 않는다.

## 제작 모드

큐 항목마다 드롭다운으로 고른다.

- **오브젝트** (기본): 프롭·건물·자연물 단일 모델. 멀티뷰 3면도(정면/측면/상면)로 생성하고 컬러 스와치 또는 개별 매핑으로 텍스처. 원점 바닥 중앙, 단일 메시 join.
- **캐릭터**: 인간형·동물형·크리처형 게임 캐릭터. **원화(참조 이미지)** 를 넣으면 그 캐릭터를 3x2 **턴어라운드 시트**(정면 | 뒷면 | 좌측면 / 우측면 | 상면 | 3/4뷰, 리깅용 중립 자세)로 먼저 전개한다. 이후 경로는 두 가지다:
  - **이미지→3D 셰이프 (기본, 로컬 Hunyuan3D 서버 필요)**: 시트의 정면·뒷면·좌·우측면을 잘라 로컬 Hunyuan3D-2mv 서버에 보내 하이폴리 셰이프(약 45만 면)를 받고, 떠다니는 파편을 제거한 뒤 **데시메이트**(기본, 디테일 보존) 또는 **복셀 리메시 + QuadriFlow**(쿼드 흐름)로 게임용 면수로 줄인다. 키(기본 1.8m)·발바닥 z=0·중심 정규화 후 기존 6면도 텍스처 베이크로 이어진다. Astra 모델링 턴은 건너뛴다. 프리미티브 코드 조립보다 원화 재현도가 압도적으로 높다.
  - **코드 모델링 (폴백)**: 서버가 없거나 셰이프 생성이 실패하면 시트를 보고 Astra가 리깅 자세로 코드 모델링하고, 실행 결과를 시트와 같은 6시점으로 렌더해 **시트와 대조하는 턴**을 돌려 빠진 요소·비율·떨어진 파트를 고친다(환경설정 `캐릭터 6면도 대조 횟수`, 기본 1).
  - 원화가 없으면 프롬프트만으로 시트를 만든다.
  - **캐릭터 유형** 드롭다운(자동 / 인간형 / 동물형 / 크리처형)이 비율·골격 규칙을 정한다 — 자동이면 요청문·원화에서 판단한다. 인간형은 두신 비율(스타일이 정함)과 A-포즈, 동물형은 실제 골격 비율과 관절 방향(앞다리 팔꿈치 뒤·뒷다리 무릎 앞), 크리처형은 동물 부위 조합이되 하나의 골격 논리.
  - 시스템 프롬프트(`prompts/system_character.md`)는 리깅을 전제로 한다: A-포즈/네 발 중립 자세, 정면 -Y, 발바닥 z=0, 중심선 X=0, 반쪽 모델링 + `lp.mirror_x`, **관절 단위 파트 분할**(머리/목/가슴/골반/상완/하완/손/대퇴/하퇴/발), 파트 사이 0.05m 틈, 머리·얼굴에 트라이 25~35%, 장비는 별개 오브젝트로 join 허용. 시트와 요청문이 다르면 시트가 이긴다.
  - 얼굴·의상 디테일은 **개별 매핑**이 유리하다(패널에 힌트 표시). 스타일은 다른 모드와 같은 드롭다운을 공유한다 — 사실적 스타일이면 7~8두신, 귀여운 둥근 스타일이면 2~3두신.
- **배경 공간**: 게임 씬. 씬 규모를 고르면 3단계로 진행된다:
  1. **플랜 턴**: Astra가 지형(또는 실내 껍데기)·구역·에셋 목록·씬 팔레트를 JSON으로 설계
  2. **에셋 키트**: 목록의 고유 에셋을 기존 오브젝트 파이프라인으로 병렬 생성 (랜드마크만 멀티뷰 사용)
  3. **배치 턴**: Astra가 키트를 받아 구조물·인스턴스 배치 코드 생성 후 실행

  배경 모드는 머티리얼을 **컬러 스와치**로 고정한다. 개별 매핑 선택지는 노출되지 않는다. 인스턴스는 공유 메시를 유지해 드로우콜을 최소화하고, 실패 에셋은 제외하고 계속 진행한다 (절반 초과 실패 또는 랜드마크 전멸 시만 전체 실패).

### 씬 규모

규모는 바닥 크기만이 아니라 **무엇을 만드는가**를 정한다. 예전에는 규모가 한 변 길이만 바꿔서, 면적이 16배인 대형에 중형과 같은 밀도를 뿌리면 텅 비어 보였다.

| 규모 | 용도 | 한 변 | 에셋 종류(자동) | 배치 총량 기준 | 랜드마크 |
|---|---|---|---|---|---|
| **실내** | 건물 내부 (방·홀·상점) | 약 12m | 10종 | 약 37개 | 1개 |
| **구역** | 건물 여러 채 + 주변 요소 | 약 40m | 16종 | 약 112개 | 2개 |
| **대규모** | 대도시·대형 성채 | 약 100m | 24종 | 약 500개 | 3개 |

**배치 총량 기준**은 면적 × 규모별 밀도로 산출해 플랜 턴과 배치 턴에 수치로 전달한다 — "몇 개를 깔아야 안 허전한가"를 AI가 짐작하지 않도록 한 것이다.

**실내는 지형을 만들지 않는다.** 기복 있는 땅 위에 가구가 놓이면 실내로 읽히지 않으므로, `lp.room`으로 바닥·벽을 세우고 천장은 위에서 안이 보이도록 열어 둔다. `ground_snap`도 호출하지 않는다. 컨셉 시트도 조감도 대신 **천장을 걷어낸 컷어웨이 + 평면도**로 생성된다.

**트라이 상한은 씬 전체에 걸지 않는다** (환경설정 기본값 0 = 상한 없음). 스타일이 정하는 트라이 상한은 모델 1개 기준이라 에셋이 여러 종 들어가는 배경에 그대로 씌우면 밀도를 만들 수 없기 때문이다. 특정 기기 한도에 맞춰야 할 때만 값을 넣으면 기존처럼 초과 시 밀도 축소 재요청이 돈다. 에셋 1개의 상한에는 스타일 배수가 곱해진다 (로우폴리 L 6,000 → 사실적 L 36,000).

**부지는 정사각형이 아니다.** 플랜 JSON의 `scene.extent`([폭, 깊이], 비율 자유)와 `scene.outline`(6~12점 비정형 다각형)이 부지 형태를 정하고, 배치 턴은 그 윤곽을 `lp.terrain(outline=...)`으로 그대로 쓴다. 구역에는 `rotation`(도)이 있어 배치 축이 XY축에 나란하지 않다. 예전에는 규모에서 정사각형 한 변만 나와 결과가 늘 정사각형이었다.

**규칙적인 배치는 결함으로 취급한다** (계획도시를 명시적으로 요청한 경우 제외). 길·성벽·울타리 폴리라인은 `lp.meander`로 굽히고, 집·노점·덤불처럼 모여 있는 것은 `lp.place_cluster`로 광장·길목 주변에 뭉치게 놓고, `lp.place_along`에는 `spacing_jitter`/`offset_jitter`/`rotate_jitter`를 준다. `lp.place_grid`는 막사·묘지·밭처럼 실제로 격자인 것에만 쓴다. 시스템 프롬프트의 배치 예시도 정사각 성벽 + 격자 주택에서 비정형 요새 + 군집 마을로 바꿨다 — 예시가 곧 결과의 성격을 정한다.

**울타리는 `lp.fence_run`으로 만든다.** `lp.wall_run`은 속이 꽉 찬 벽면이라 울타리에 쓰면 판때기가 된다 — 기둥·가로대·세로 살대를 실제로 세우는 별도 헬퍼를 두고, 프롬프트에서 둘의 용도를 갈랐다 (`wall_run`은 성벽·건물 외벽·막힌 담장 전용).

## 캐릭터 셰이프 서버 (Hunyuan3D 로컬)

캐릭터 모드의 이미지→3D 단계는 로컬 GPU에서 돌아가는 Hunyuan3D-2 서버를 쓴다 (RTX 3060 12GB에서 셰이프 1개 30~60초). 설치:

```powershell
.\scripts\hunyuan3d\setup.ps1            # D:\Tools\Hunyuan3D-2 에 클론 + Python 3.11 venv + PyTorch CUDA 12.6
D:\Tools\Hunyuan3D-2\run_server.bat       # 포트 8081, 첫 실행 시 tencent/Hunyuan3D-2mv 모델 다운로드
```

`scripts/hunyuan3d/lp3d_h3d_server.py`는 원본 `api_server.py`를 대체하는 서버다 — 멀티뷰 입력(`front`/`back`/`left`/`right` base64)을 받고, 원본이 텍스처 모드에서만 돌리던 파편 제거(FloaterRemover)를 항상 적용하며, 생성 중에도 `/status`가 응답한다. 요청 형식은 원본과 호환이라 Blender MCP의 Hunyuan3D `LOCAL_API` 모드도 같은 서버를 쓸 수 있다. 텍스처 파이프라인(C++/CUDA 확장)은 빌드하지 않는다 — 텍스처는 애드온의 6면도 베이크가 담당한다.

환경설정 → **캐릭터**: 셰이프 생성 켜기/끄기, 서버 주소(기본 `http://127.0.0.1:8081`), 리토폴로지 목표 면수(기본 12,000 쿼드 ≈ 24,000 tris), 리토폴로지 방식, 캐릭터 기본 키. 서버가 응답하지 않으면 패널 로그에 남기고 코드 모델링으로 폴백한다.

## 이미지 생성 백엔드

참조 시트 3종(멀티뷰 3면도 · 씬 컨셉 시트 · 텍스처 6면도)을 무엇으로 만들지 환경설정에서 고른다. 모델링 턴은 어느 쪽이든 `codex` CLI의 Astra가 담당한다.

- **OpenRouter API** (기본): [openrouter.ai/settings/keys](https://openrouter.ai/settings/keys)에서 발급한 키를 환경설정에 넣는다. 키 대신 `OPENROUTER_API_KEY` 환경변수도 읽는다. 이미지 1장에 HTTP 요청 1회만 쓰므로 codex 세션을 띄우는 것보다 토큰 소모가 훨씬 적다.
- **Codex CLI**: codex의 `image_gen` 도구로 생성한다. API 키가 필요 없다. **OpenRouter를 골라도 키가 없으면 자동으로 이 경로로 폴백한다.**

실제로 어느 경로가 쓰이는지는 사이드바 멀티뷰 상자 첫 줄 **참조 이미지:** 에 표시된다 — `OpenRouter · 덕테이프 (GPT Image 2)` / `Codex image_gen` / `Codex image_gen (OpenRouter 키 없음 → 폴백)` / `미사용`. 대기 중인 항목은 현재 설정 기준, 실행된 항목은 그때 실제로 쓴 값이다. 로그의 시트 생성 시작 줄에도 같은 문구가 `[...]`로 붙는다.

선택 가능한 모델 (OpenRouter Image API `/api/v1/images` 기준):

| 모델 | ID | 비고 |
|---|---|---|
| 덕테이프 (기본) | `openai/gpt-image-2` | 격자 레이아웃·지시 준수도가 가장 높다. 3면도/6면도 칸 배치가 어긋나면 베이크 좌표가 틀어지므로 기본값으로 둔다 |
| 덕테이프 2.5 선버스트 | `openai/gpt-image-2.5-sunburst` | 품질 `xhigh`/`max` 사용 가능. 느리고 비싸다 |
| 덕테이프 2.5 플레어 | `openai/gpt-image-2.5-flare` | 2.5 경량 티어 |
| 나노바나나 프로 | `google/gemini-3-pro-image-preview` | 묘사력은 높지만 격자 지시를 종종 무시한다 |
| 나노바나나 2 | `google/gemini-3.1-flash-image-preview` | 빠르고 저렴 |
| 나노바나나 2 라이트 | `google/gemini-3.1-flash-lite-image` | 가장 저렴, 1K 고정 |

모델마다 지원 파라미터가 다르다(gpt-image 계열은 `quality`, Gemini 계열은 `resolution`). 지원하지 않는 값은 요청에서 빠지거나 지원 범위로 맞춰진다 — 모델을 추가할 때는 `https://openrouter.ai/api/v1/images/models`의 `supported_parameters`를 실측해 `core/imagegen.py`의 카탈로그에 옮긴다. 추측해서 넣으면 400이 난다.

API 키는 Blender 설정 폴더의 `lp3d_settings.json`에 평문으로 저장된다(다른 설정과 같은 파일). 공용 PC에서는 환경변수 쪽을 쓰는 편이 안전하다.

## 변경 이력

- **0.18.0**: 캐릭터를 **이미지→3D 셰이프 + 리토폴로지**로 생성 — 턴어라운드 시트의 4면을 로컬 Hunyuan3D-2mv 서버(`scripts/hunyuan3d/`)에 보내 하이폴리 셰이프를 받고 파편 제거 → 데시메이트/QuadriFlow → 정규화 → 기존 6면도 텍스처 베이크. 서버가 없으면 코드 모델링으로 폴백. 코드 모델링 경로에는 **6면도 대조 턴**(렌더 vs 시트 비교 후 수정)을 추가하고, 파트 사이 틈 규칙을 없애 관통·관절 구로 잇도록, 폴리 상한 80k·실제 키 규칙·시트 체크리스트를 지침에 넣었다. 참조 이미지 백엔드 표시, 배경 비정형 배치와 함께 실제 생성 테스트로 검증.
- **0.17.0**: 제작 모드에 **캐릭터** 추가 — 원화(참조 이미지)로 3x2 턴어라운드 시트를 만들고 리깅 자세(A-포즈/네 발 중립, 정면 -Y, 관절 단위 파트 분할, 반쪽+mirror_x)로 모델링한 뒤 모델링 타입에 따라 매핑. 캐릭터 유형(자동/인간형/동물형/크리처형) 드롭다운으로 비율·골격 규칙 선택. `prompts/system_character.md` 신설, 멀티뷰 시트에 TURNAROUND 레이아웃(3:2) 추가.
- **0.16.0**: 배경 배치의 규칙성 제거 — 플랜에 부지 `extent`/`outline`(비정형 윤곽)과 구역 `rotation` 추가, `lp.terrain(outline=...)`·`lp.meander`(굽은 폴리라인)·`lp.place_cluster`(군집 배치)·`place_along` 지터 헬퍼 추가. 정사각 부지·격자·직각 동선을 결함으로 규정하고 배치 예시를 비정형 요새로 교체.
- **0.15.1**: 참조 이미지 백엔드 표시 — 패널·상태줄·로그에 실제 사용 경로(OpenRouter 모델 / Codex / 폴백 / 미사용)를 보여준다.
- **0.15.0**: 배경 규모를 용도 기준으로 재정의 — 실내(약 12m, 건물 내부) / 구역(약 40m) / 대규모(약 100m). 규모마다 에셋 종류·배치 총량·랜드마크 수를 함께 정하고 그 수치를 프롬프트에 전달한다. 씬 전체 트라이 상한은 기본 해제(0 = 상한 없음) — 스타일 상한은 모델 1개 기준이다. 실내는 `lp.room`으로 바닥·벽을 만들고 지형·`ground_snap`을 쓰지 않는다. 울타리 전용 `lp.fence_run` 추가 — `wall_run`으로 울타리를 만들면 판때기가 되던 문제.
- **0.14.0**: 아트 스타일 드롭다운 추가 — 로우폴리 캐주얼/복셀/귀여운 둥근/스타일리쉬 캐주얼/사실적. 하드코딩돼 있던 로우폴리 지침을 공통 골격(`prompts/system_base.md`)과 스타일 조각(`prompts/styles/*.md`)으로 분리했고, 스타일이 모델링 규칙·폴리 버짓과 참조 시트 화풍을 함께 정한다. 복셀용 격자 헬퍼 `lp.voxel`/`voxel_box`/`voxel_column` 추가.
- **0.13.0**: 참조 시트 생성을 OpenRouter Image API로 전환 — 이미지 모델 드롭다운(덕테이프 기본, 나노바나나 계열 선택 가능), 품질 티어, API 키 설정 추가. 키가 없으면 기존 codex `image_gen` 경로로 폴백한다.
- **0.12.0**: 제작 모드 추가 — 오브젝트(멀티뷰 3면도, 기존) / 배경 공간(배치 3단계). 배경 모드는 씬 규모 선택 후 플랜 턴(Astra 설계) → 에셋 키트(병렬 생성) → 배치 턴(배치 코드) 진행. 머티리얼은 컬러 스와치 고정, 인스턴스는 공유 메시 유지.
- **0.11.1**: Astra 서버 혼잡("Selected model is at capacity") 오류를 모델 가용성 오류로 인식해 Codex CLI 기본 모델 폴백이 걸리도록 수정 — 이전에는 폴백 없이 생성이 그대로 실패했다. 시스템 프롬프트에 도구 사용 지침 추가(모델링 전 외부 스킬 파일·셸 명령 사용 금지).
- **0.11.0**: 모델링 타입 드롭다운 추가 — 컬러 스와치 / 개별 매핑. 개별 매핑은 박스 투영 언랩 + 6면도 AI paint-over + CPU 투영 베이크로 모델 이름 텍스처를 만든다.
- **0.10.0**: 은면 컬링 정밀도 개선 — 다른 닫힌 파트 안에 완전히 파묻힌 면만 삭제(노멀 반전 파트·오목 공간·테두리 노출 면 보존). 팔레트 텍스처를 열=색상 군집 / 행=명도 레이아웃(64x32 셀)으로 전환 — 기존 에셋 UV의 색이 바뀌는 파괴적 변경.
- **0.9.0**: GPT-6 Astra 기본 생성 및 1턴 완료 고정. Claude 생성, 추가 비평·개선과 반복 설정 제거. Astra 사용 불가 시 Codex 기본 모델 폴백 유지.

## 개발

```bash
./scripts/dev_run.sh         # macOS 격리 프로필로 개발 소스 실행
./scripts/build.sh           # dist/에 배포 zip + index.json 빌드
python3 scripts/spike_cli.py  # Astra 단일 호출 계약 검증 (실제 CLI 호출)
```

- 코드 수정 후 패널의 **Dev Reload** 버튼으로 재시작 없이 리로드
- 수동 테스트 시나리오: `tests/manual_scenarios.md`

Blender 백그라운드 검증 (macOS/Windows):

```bash
blender --background --factory-startup --python tests/verify_scene_in_blender.py
./scripts/dev_run.sh --background --python tests/verify_single_turn_in_blender.py
```

## 릴리즈 절차

1. `blender_manifest.toml`의 `version` 올리기 → main에 push (Validate 워크플로우가 빌드 검증)
2. GitHub → Actions → **Release** 워크플로우 → Run workflow
3. 생성된 **드래프트 릴리스**를 확인 후 **Publish**
4. `./scripts/release_index.sh` 실행 — 릴리스 zip 기준으로 `index.json`을 생성해 릴리스에 업로드 (이 순간부터 사용자 블렌더가 자동 업데이트를 감지)

## 팔레트 텍스처

모든 생성 모델은 고정된 256x256 팔레트 텍스처 하나를 공유한다. 색은 Oklab
좌표에서 공식으로 결정되므로 생성 순서와 무관하게 항상 동일하다 — 덕분에
에셋 라이브러리에서 머티리얼만 교체해도 색이 그대로 유지된다.

레이아웃은 셀 4x8px, 64열 x 32행(2048색)이다. **열(좌→우) = 색상**: 무채색 4열
(그레이·웜 그레이·세피아·쿨 그레이) 다음에 색상 15개가 빨강→주황→노랑→…→자홍
순으로 놓이고, 한 색상은 채도 4단(선명→탁함)이 이웃한 4열 한 군집을 이룬다.
**행(아래→위) = 명도**: 텍스처 위쪽이 밝고 아래쪽이 어둡다. 손으로 UV를 옮겨
색을 바꿀 때 옆으로 가면 색상/채도, 위아래로 가면 밝기만 바뀐다.

- 텍스처: `lowpoly/LP3D_Palette.png` (자동 생성, 커밋됨)
- 색 데이터: `lowpoly/palette_data.py` (자동 생성, 커밋됨)
- 재생성: `python scripts/gen_palette.py`
- 검증: `python -m unittest tests.test_palette -v`

두 아티팩트는 직접 수정하지 않는다. 팔레트 공식을 바꾸면 기존에 만든 모든
에셋의 색이 바뀌므로 파괴적 변경으로 취급한다.

## 개발 환경 (Windows)

`scripts/dev_link.sh`는 macOS 전용이다. Windows에서는 `scripts/dev_run.ps1`을 쓴다.

포터블 Blender를 받아 압축을 푼 뒤(기본 경로 `D:\Tools\Blender-5.2`), 소스를
디렉터리 정션으로 연결하고 개발 전용 프로필로 실행한다. 설치된 릴리스 버전과
완전히 분리되므로 매번 확장을 켜고 끌 필요가 없다.

PowerShell 창에서:

```powershell
.\scripts\dev_run.ps1                 # 개발용 Blender 실행 (GUI)
.\scripts\dev_run.ps1 -LinkOnly       # 연결만
.\scripts\dev_run.ps1 -BlenderDir "E:\Blender-5.2"
```

cmd 창에서는 `.ps1`을 직접 실행할 수 없으므로 `.bat` 래퍼를 쓴다(인자는 동일):

```
scripts\dev_run.bat
scripts\dev_run.bat -LinkOnly
```

포터블 Blender는 <https://download.blender.org/release/> 에서 받는다.
심링크가 아니라 정션을 쓰므로 관리자 권한이 필요 없다.

### 실제 Blender에서 팔레트 검증

`bpy`가 필요해 일반 유닛 테스트로는 닿지 않는 부분(이미지 pack, 구버전 `.blend`
업그레이드, 텍셀 색 일치)을 헤들리스로 검증한다.

```powershell
.\scripts\dev_run.ps1 -Background -PythonFile tests\verify_palette_in_blender.py
```

`RESULT: ALL PASS`가 나와야 한다. macOS는 `./scripts/dev_run.sh --background --python tests/verify_palette_in_blender.py`.

### 실제 Blender에서 은면 컬링·기하 검증

애드온 설치 없이 factory Blender로 바로 돈다 (`lowpoly` 패키지를 직접 임포트).

```bash
blender --background --factory-startup --python tests/verify_cull_in_blender.py      # 은면 판정 (보이는 면 보존·파묻힌 면 삭제)
blender --background --factory-startup --python tests/verify_geometry_in_blender.py  # 대칭도 등 QA 지표
blender --background --factory-startup --python tests/verify_texturing_in_blender.py # 개별 매핑: 언랩·6면도 렌더·베이크·재질 교체
blender --background --factory-startup --python tests/verify_voxel_in_blender.py     # 복셀 격자 헬퍼 (좌표·은면 제거·닫힌 메시)
blender --background --factory-startup --python tests/verify_sceneprops_in_blender.py # 실내 방(room)·울타리(fence_run) 구조
blender --background --factory-startup --python tests/verify_organic_in_blender.py    # 비정형 지형(outline)·meander·place_cluster·place_along 지터
```

## 라이선스

GPL-3.0-or-later. 자세한 내용은 [LICENSE](LICENSE)를 확인하세요.
