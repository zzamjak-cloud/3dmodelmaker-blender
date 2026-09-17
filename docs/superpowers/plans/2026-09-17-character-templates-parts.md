# 캐릭터 템플릿·부위 분리 구현 계획

**Goal:** Claude 세션에서 승인된 편집 가능한 베이스 메시와 3분할 생성 기능을 완성한다.

**Architecture:** 템플릿/등록 모듈과 부위 생성 상태 흐름을 분리한다. 기존 세션 스케줄러,
이미지 생성 클라이언트, 리토폴로지 및 텍스처 기능을 재사용한다.

**Tech Stack:** Python, bpy/bmesh, Blender 5.2, unittest.

**Spec:** `docs/superpowers/specs/2026-09-17-character-templates-parts-design.md`

## 공통 제약

- 주석·문서·UI 안내는 한국어, 식별자는 영어.
- 기존 사용자 데이터와 실행 중 Blender를 보존한다. Git push/릴리스는 수행하지 않는다.
- 새 외부 에셋 의존성을 추가하지 않는다. 테스트 프로필은 저장소 내부에 둔다.
- 작성 에이전트와 최종 검증 에이전트는 분리한다.

## 작업 1: 베이스 메시와 사용자 등록

- [x] 얼굴·관절 그룹, 유효 면 인덱스·연결성을 검사하는 실패 테스트를 작성한다.
- [x] `lowpoly/base_templates.py`에서 자체 제작 인간형/네발형 쿼드 데이터를 생성한다.
- [x] `core/templates.py`에서 목록·씬 로드·선택 메시/외부 blend 등록 API를 구현한다.
- [x] `lowpoly/retopo.py`에서 TEMPLATE 경로와 부위 보존 옵션을 구현한다.
- [x] 순수 테스트와 격리 Blender에서 로드/등록/적합을 검증한다.

## 작업 2: 부위별 생성 파이프라인

- [x] 부위 시트 프롬프트와 순차 실행/취소/실패 흐름의 실패 테스트를 작성한다.
- [x] `core/character_parts.py`와 `core/session.py`에 세 부위 시트→셰이프→리토폴로지 흐름을 구현한다.
- [x] 부위별 텍스처, 몸체 은면 보존, 실패 상태와 결과 로그를 연결한다.
- [x] 기존 단일 생성 및 테스트를 유지하고 회귀 테스트를 수행한다.

## 작업 3: UI·설정·통합 검증

- [x] 캐릭터 설정에 분리 모드/템플릿 선택, 템플릿 불러오기·등록 버튼을 연결한다.
- [x] 설정 영속화, README 사용법, 격리 개발 실행기를 갱신한다.
- [x] 전체 unittest 및 실제 Blender 검증을 실행하고 결과 파일을 남긴다.
- [x] 독립 검토자에게 요구사항/변경 사항/검증 근거를 전달하고 발견 사항을 수정한다.

## 검증 결과와 이어갈 작업

- 전체 unittest 402개 통과. Blender 5.2.1에서 UI 등록/편집/외부 가져오기/복제/리로드/해제 통과.
- 베이스 메시 양쪽 264 closed manifold quads, 자동 적합 후 4,224 quads.
- 외부 AI만 fixture로 대체한 Blender 분리 흐름 통과: 세 부품·면 보존, 몸체 1.8m/무기 0.9m,
  독립 텍스처 대상, 작업 종료 후 스케줄러 잔여 없음.
- 독립 리뷰의 서버 파편 제거, 프롬프트 충돌, 취소 재시도, 조용한 폴백 지적을 수정했다.
- 템플릿은 편집용 원형이다. 실제 늑대 적합은 주둥이/귀를 충분히 재현하지 못하므로 QuadriFlow
  기본값을 유지한다. `wolf-template-fit.blend`는 완성 캐릭터가 아닌 적합 시험 결과다.
- [ ] 실제 외부 AI를 사용한 3분할 생성과 텍스처/정렬 시각 품질 검증.
  2026-09-17 12:07 기준 기존 Hunyuan 서버(127.0.0.1:8081, PID 12172)는 preserve_parts 미지원.
  Windows에서 종료 시 Access is denied. 사용자 쪽에서 기존 서버를 종료해야 최신 저장소 서버를
  실행할 수 있다. `scripts/hunyuan3d/start_server.py --hunyuan-root D:\Tools\Hunyuan3D-2 --port 8081`
  실행은 기존 `.venv/Scripts/python.exe`를 사용한다. 의존성 import와 --help는 확인했다.
- 사용자 Blender 설치본 및 기존 서버 파일, Git commit/push는 변경하지 않았다.

### 서버 재개 후 상태

2026-09-17 사용자 쪽에서 기존 서버를 종료했다. 8081 포트와 PID 12172가 없음을 확인하고
저장소 서버를 숨김 프로세스로 실행했다. `/status`가
`{"ok":true,"multiview":true,"capabilities":{"preserve_parts":true}}`를 반환한다.
새 실제 서버 PID는 28224이며 로그는 `Generate/hunyuan-server/20260917-121807.*.log`다.

`scripts/resume_character.py`로 기존 늑대 시트를 재사용한 실제 분리 생성을 시도했지만
첫 몸체 시트 OpenRouter 요청에서 WinError 10013(소켓 접근 권한 거부)으로 실패했다.
결과 보고서는 `Generate/wolf-live-20260917/report.json`이다. 이 폴더의 blend는 실패 상태
기록이며 완성 모델이 아니다. 외부 API 접근이 허용된 실행 환경에서 재실행해야 한다.
