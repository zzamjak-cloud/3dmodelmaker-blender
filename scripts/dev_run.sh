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
set -e
VERSION="${1:-5.2}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="$HOME/Library/Application Support/Blender/LP3DModelMakerDev/$VERSION"
EXT_DIR="$PROFILE/extensions/user_default"

mkdir -p "$EXT_DIR"
ln -sfn "$SRC" "$EXT_DIR/lp3d_modelmaker"
echo "격리 프로필: $PROFILE"
echo "소스 링크:   $EXT_DIR/lp3d_modelmaker -> $SRC"

export BLENDER_USER_RESOURCES="$PROFILE"
exec /Applications/Blender.app/Contents/MacOS/Blender
