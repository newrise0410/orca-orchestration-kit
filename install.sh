#!/usr/bin/env bash
# orca-orchestration-kit 스킬 설치: 스킬 폴더를 사용자 스킬 경로에 심볼릭 링크로 건다.
#   사용: ./install.sh [--dir <스킬 경로>] [--dry-run] [--uninstall]
#   기본 스킬 경로: $ORCA_CLAUDE_CONFIG_DIR/skills (Orca 프로필) → 없으면 ~/.claude/skills (일반 Claude Code)
#   링크라서 키트 저장소를 git pull 하면 스킬도 바로 최신이 된다. kit.py는 링크를 따라 키트 본체를 찾는다.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/skills/orca-orchestration-kit"
DIR=""; DRY=0; UN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --uninstall) UN=1; shift ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$DIR" ]; then
  if [ -n "${ORCA_CLAUDE_CONFIG_DIR:-}" ]; then DIR="$ORCA_CLAUDE_CONFIG_DIR/skills"; else DIR="$HOME/.claude/skills"; fi
fi
DST="$DIR/orca-orchestration-kit"
do_() { if [ $DRY = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }
python3 -c 'import yaml' 2>/dev/null || echo "참고: PyYAML이 없다. 도구를 쓰려면 설치한다 (sudo apt install python3-yaml)" >&2
if [ $UN = 1 ]; then
  if [ -L "$DST" ]; then do_ rm "$DST"; echo "제거: $DST"; else echo "링크가 없다: $DST"; fi
  exit 0
fi
if [ -e "$DST" ] && [ ! -L "$DST" ]; then
  echo "중단: $DST 가 링크가 아닌 실제 폴더다. 직접 확인하고 옮긴 뒤 다시 실행하라." >&2; exit 1
fi
do_ mkdir -p "$DIR"
do_ ln -sfn "$SRC" "$DST"
[ $DRY = 1 ] && echo "(dry-run: 아무것도 바꾸지 않았다)" || echo "설치: $DST -> $SRC"
echo "확인: Claude Code에서 '/orca-orchestration-kit' 또는 \"orchestration kit init\"이라고 말한다."
