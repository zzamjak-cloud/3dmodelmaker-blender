#!/bin/zsh
# 개발용: 격리 프로필로 Blender 실행 (macOS) — 릴리스 설치본과 완전 분리
#
# BLENDER_USER_RESOURCES를 전용 프로필(LP3DModelMakerDev)로 바꿔 실행하므로
# 기본 프로필(~/Library/Application Support/Blender/<버전>)의 설정·확장 목록을
# 전혀 건드리지 않는다. 기본 프로필에 릴리스 설치본(원격 저장소)이 있어도
# 충돌 없이 개발 소스(심링크)만 로드된다.
#
# 사용법: ./scripts/dev_run.sh [블렌더버전, 기본 5.2]
# Windows는 scripts/dev_run.ps1 을 쓴다.
set -eu
VERSION="${BLENDER_VERSION:-5.2}"
if [[ "${1:-}" == <->.<-> ]]; then
    VERSION="$1"
    shift
fi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$SRC/blender_manifest.toml" ]] || { echo "매니페스트를 찾을 수 없습니다" >&2; exit 1; }
ADDON_ID="$(sed -n 's/^id = "\([^"]*\)"/\1/p' "$SRC/blender_manifest.toml")"
[[ "$ADDON_ID" == lp3d_modelmaker ]] || { echo "예상하지 못한 Extension ID입니다" >&2; exit 1; }
PROFILE="$HOME/Library/Application Support/Blender/LP3DModelMakerDev/$VERSION"
EXT_DIR="$PROFILE/extensions/user_default"
LINK="$EXT_DIR/$ADDON_ID"
BINARY="${BLENDER_BINARY:-/Applications/Blender.app/Contents/MacOS/Blender}"
[[ -x "$BINARY" ]] || { echo "Blender 실행 파일을 찾을 수 없습니다: $BINARY" >&2; exit 1; }
mkdir -p "$EXT_DIR"
if [[ -e "$LINK" && ! -L "$LINK" ]]; then
    echo "소스 링크 자리에 실제 파일이나 폴더가 있습니다: $LINK" >&2
    exit 1
fi
TEMP_LINK="$EXT_DIR/.$ADDON_ID.$$"
trap 'rm -f "$TEMP_LINK"' EXIT
ln -s "$SRC" "$TEMP_LINK"
mv -fh "$TEMP_LINK" "$LINK"
echo "격리 프로필: $PROFILE"
echo "소스 링크: $LINK -> $SRC"

export BLENDER_USER_RESOURCES="$PROFILE"
exec "$BINARY" --python-exit-code 1 --python "$SRC/scripts/dev_bootstrap.py" "$@"
