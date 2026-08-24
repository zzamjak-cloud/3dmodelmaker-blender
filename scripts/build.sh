#!/usr/bin/env bash
#
# AI LowPoly ModelMaker 확장을 배포용 .zip으로 빌드한다.
#
# 패키지에서 제외할 파일(개발 도구, 캐시, __pycache__, .git 등)은
# blender_manifest.toml의 [build].paths_exclude_pattern에서 관리한다.
#
# 사용법:
#   ./scripts/build.sh                          # ./dist에 빌드
#   BLENDER=/path/to/blender ./scripts/build.sh # 특정 Blender 사용
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Blender 실행 파일 — PATH에 없으면 BLENDER 환경변수로 지정 (macOS 기본 경로 폴백)
BLENDER="${BLENDER:-blender}"
if ! command -v "${BLENDER}" >/dev/null 2>&1; then
  if [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
    BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
  else
    echo "error: Blender 실행 파일을 찾을 수 없습니다. BLENDER 환경변수를 지정하세요." >&2
    exit 1
  fi
fi

OUTPUT_DIR="${PROJECT_ROOT}/dist"
mkdir -p "${OUTPUT_DIR}"

echo "Building AI LowPoly ModelMaker extension..."
"${BLENDER}" --command extension build \
  --source-dir "${PROJECT_ROOT}" \
  --output-dir "${OUTPUT_DIR}" \
  "$@"

# 원격 저장소용 index.json 생성 — GitHub Release에 zip과 함께 올리면
# Blender가 releases/latest/download/index.json 을 통해 자동 업데이트를 감지한다.
# dist에는 과거 버전 zip이 섞여 있으므로 현재 버전만 스테이징해서 생성한다.
VERSION="$(sed -n 's/^version *= *"\(.*\)"/\1/p' "${PROJECT_ROOT}/blender_manifest.toml" | head -1)"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGE_DIR}"' EXIT
cp "${OUTPUT_DIR}"/lp3d_modelmaker-"${VERSION}"*.zip "${STAGE_DIR}/"
"${BLENDER}" --command extension server-generate --repo-dir "${STAGE_DIR}"
cp "${STAGE_DIR}/index.json" "${OUTPUT_DIR}/index.json"

echo
echo "완료: ${OUTPUT_DIR}"
ls -1 "${OUTPUT_DIR}" | sed 's/^/  /'
