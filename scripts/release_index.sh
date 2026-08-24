#!/usr/bin/env bash
#
# 발행된 GitHub Release의 zip을 기준으로 index.json을 생성해 릴리스에 업로드한다.
# (해시/크기가 릴리스 zip과 일치해야 하므로 반드시 릴리스에서 내려받아 생성)
#
# 사용법: ./scripts/release_index.sh [태그, 기본: 최신 릴리스]
set -euo pipefail

REPO="zzamjak-cloud/3dmodelmaker-blender"
TAG="${1:-$(gh release view -R "${REPO}" --json tagName -q .tagName)}"

BLENDER="${BLENDER:-blender}"
if ! command -v "${BLENDER}" >/dev/null 2>&1; then
  if [ -x "/Applications/Blender.app/Contents/MacOS/Blender" ]; then
    BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
  else
    echo "error: Blender 실행 파일을 찾을 수 없습니다. BLENDER 환경변수를 지정하세요." >&2
    exit 1
  fi
fi

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGE_DIR}"' EXIT

echo "릴리스 ${TAG} zip 다운로드..."
gh release download "${TAG}" -R "${REPO}" -p "*.zip" -D "${STAGE_DIR}"

echo "index.json 생성..."
"${BLENDER}" --command extension server-generate --repo-dir "${STAGE_DIR}"

echo "릴리스에 업로드..."
gh release upload "${TAG}" -R "${REPO}" "${STAGE_DIR}/index.json" --clobber

echo "완료: https://github.com/${REPO}/releases/latest/download/index.json"
