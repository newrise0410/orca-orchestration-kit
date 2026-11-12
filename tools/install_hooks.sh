#!/usr/bin/env bash
# 키트 git 훅(pre-push)을 공용 git 디렉터리에 깐다. 모든 워크트리(워커 포함)에 걸린다.
#   사용: orchestration/tools/install_hooks.sh [--dry-run] [--force]
#   이미 다른 pre-push가 있으면 멈춘다(--force면 <훅>.bak으로 옮기고 깐다). core.hooksPath를 쓰는 저장소면 그 폴더에 깐다.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
DRY=0; FORCE=0
for x in "$@"; do case "$x" in --dry-run) DRY=1 ;; --force) FORCE=1 ;; *) echo "알 수 없는 옵션 $x" >&2; exit 2 ;; esac; done
src=orchestration/tools/hooks/pre-push
hp=$(git config --get core.hooksPath || true)
if [ -n "$hp" ]; then dir=$hp; else dir="$(git rev-parse --git-common-dir)/hooks"; fi
dst="$dir/pre-push"
if [ -e "$dst" ] && ! cmp -s "$src" "$dst"; then
  grep -q "orca-orchestration-kit" "$dst" 2>/dev/null || [ $FORCE = 1 ] || { echo "중단: 다른 pre-push 훅이 있다($dst). 합치거나 --force(백업 후 교체)" >&2; exit 1; }
  [ $DRY = 1 ] || cp -p "$dst" "$dst.bak"
fi
if [ $DRY = 1 ]; then echo "[dry-run] install $src → $dst"; exit 0; fi
mkdir -p "$dir"; cp "$src" "$dst"; chmod 755 "$dst"
echo "설치: $dst (로컬 전용·보호 브랜치·배포 제한 파일 검사, 설정은 kit.yaml project.*)"
