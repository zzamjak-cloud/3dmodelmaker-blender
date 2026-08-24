#!/bin/zsh
# 개발용: 소스 디렉토리를 Blender 확장 폴더에 심링크
# 사용법: ./scripts/dev_link.sh [블렌더버전, 기본 5.2]
set -e
VERSION="${1:-5.2}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
EXT_DIR="$HOME/Library/Application Support/Blender/$VERSION/extensions/user_default"
mkdir -p "$EXT_DIR"
ln -sfn "$SRC" "$EXT_DIR/lp3d_modelmaker"
echo "링크 완료: $EXT_DIR/lp3d_modelmaker -> $SRC"
echo "Blender 환경설정 > Get Extensions > 새로고침 후 'AI LowPoly ModelMaker' 활성화"
