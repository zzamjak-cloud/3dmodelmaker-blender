# AI LowPoly ModelMaker

Codex CLI의 GPT-6 Astra로 캐주얼 게임용 로우폴리 3D 모델(프랍·건물·자연물)을 블렌더 안에서 텍스트 프롬프트로 생성하는 Blender 확장.

- GPT-6 Astra가 로우폴리 헬퍼 라이브러리(`lp`) 기반 bpy 코드를 1턴으로 생성하고 실행한 뒤 완료
- 단일 팔레트 텍스처 머티리얼(Unity 드로우콜 1개), FBX/glTF 게임엔진 익스포트, Asset Browser 등록, 변형(variation) 생성
- API 키 불필요 — 설치된 `codex` CLI 구독을 그대로 사용

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
2. 3D 뷰포트 사이드바(N) → **AI 모델러** 탭
3. 프롬프트 입력 (예: `낡은 나무 배럴, 금속 밴드 2개`) → **[＋]**로 큐에 추가 → **전체 실행**
   (AI 호출은 환경설정의 동시 실행 수만큼 병렬로, Blender 작업은 하나씩 순차로 진행된다)
4. 완료 후 **결과물** 패널에서 FBX/glTF 익스포트, 에셋 등록, 변형 생성

모든 생성은 `GPT-6 Astra`를 요청하고 1턴으로 완료한다. 대기 중에는 `예정 모델`이, 실행·완료 후에는 실제 사용한 모델이 상태에 표시된다. Astra가 지원되지 않는 경우에는 초기 모델 가용성 오류에 한해서만 `Codex CLI 기본 모델 (Astra 사용 불가)`로 한 번 재시도한다. 네트워크 차단이나 로그인 만료 같은 오류에는 폴백하지 않는다. 코드 실행 실패나 응답 형식 오류의 복구 재시도는 유지하며, 성공한 결과에는 후속 AI 호출을 하지 않는다. 이전 버전의 에이전트·턴 수·모델 선택 저장값은 새 생성에 적용하지 않는다.

## 변경 이력

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

macOS 백그라운드 검증:

```bash
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
```
