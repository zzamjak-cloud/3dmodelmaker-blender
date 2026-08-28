# AI LowPoly ModelMaker

Claude Code / Codex CLI 에이전트로 캐주얼 게임용 로우폴리 3D 모델(프랍·건물·자연물)을 블렌더 안에서 텍스트 프롬프트로 생성하는 Blender 확장.

- 에이전트가 로우폴리 헬퍼 라이브러리(`lp`) 기반 bpy 코드를 생성 → 실행 → 멀티앵글 캡처를 보고 스스로 비평·개선하는 시각 피드백 루프
- 단일 팔레트 텍스처 머티리얼(Unity 드로우콜 1개), FBX/glTF 게임엔진 익스포트, Asset Browser 등록, 변형(variation) 생성
- API 키 불필요 — 설치된 `claude` / `codex` CLI 구독을 그대로 사용

## 설치 (자동 업데이트, 권장)

한 번 등록하면 이후 새 릴리즈가 나올 때 블렌더가 자동으로 업데이트를 감지·설치합니다.

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

1. `claude` 또는 `codex` CLI가 설치·로그인되어 있어야 합니다. (경로 자동 탐지 실패 시 애드온 환경설정에서 절대경로 지정)
2. 3D 뷰포트 사이드바(N) → **AI 모델러** 탭
3. 프롬프트 입력 (예: `낡은 나무 배럴, 금속 밴드 2개`) → 에이전트 선택 → 개선 반복 횟수 설정 → **모델 생성**
4. 완료 후 **결과물** 패널에서 FBX/glTF 익스포트, 에셋 등록, 변형 생성

## 개발

```bash
./scripts/dev_link.sh        # 확장 폴더에 소스 심링크 (개발 설치)
./scripts/build.sh           # dist/에 배포 zip + index.json 빌드
python3 scripts/spike_cli.py claude   # CLI 계약 검증
```

- 코드 수정 후 패널의 **Dev Reload** 버튼으로 재시작 없이 리로드
- 수동 테스트 시나리오: `tests/manual_scenarios.md`

## 릴리즈 절차

1. `blender_manifest.toml`의 `version` 올리기 → main에 push (Validate 워크플로우가 빌드 검증)
2. GitHub → Actions → **Release** 워크플로우 → Run workflow
3. 생성된 **드래프트 릴리스**를 확인 후 **Publish**
4. `./scripts/release_index.sh` 실행 — 릴리스 zip 기준으로 `index.json`을 생성해 릴리스에 업로드 (이 순간부터 사용자 블렌더가 자동 업데이트를 감지)

## 팔레트 텍스처

모든 생성 모델은 고정된 256x256 팔레트 텍스처 하나를 공유한다. 색은 Oklab
좌표에서 공식으로 결정되므로 생성 순서와 무관하게 항상 동일하다 — 덕분에
에셋 라이브러리에서 머티리얼만 교체해도 색이 그대로 유지된다.

- 텍스처: `lowpoly/LP3D_Palette.png` (자동 생성, 커밋됨)
- 색 데이터: `lowpoly/palette_data.py` (자동 생성, 커밋됨)
- 재생성: `python scripts/gen_palette.py`
- 검증: `python -m unittest tests.test_palette -v`

두 아티팩트는 직접 수정하지 않는다. 팔레트 공식을 바꾸면 기존에 만든 모든
에셋의 색이 바뀌므로 파괴적 변경으로 취급한다.
