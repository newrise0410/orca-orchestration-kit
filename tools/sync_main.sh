#!/usr/bin/env bash
# int/common → 메인 브랜치 머지·푸시 (대시보드가 origin/<메인>을 읽을 때). tasks.yaml·RTM 갱신 커밋 직후 실행한다.
#   사용: orchestration/tools/sync_main.sh [--all]   --all: 메인 → int/<모듈>도 전파(코드·계약 변경 때)
#   커밋 끝 줄은 KIT_TRAILER 환경변수(예: 'Co-Authored-By: ...')로 붙인다.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
T=orchestration/tools
MAIN=$(python3 $T/kitcfg.py project.main_branch main)
CO=""; [ -n "${KIT_TRAILER:-}" ] && CO=$'\n\n'"$KIT_TRAILER"
[ "$(git branch --show-current)" = int/common ] || { echo "int/common에서 실행하라" >&2; exit 1; }
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "커밋 안 된 변경이 있다" >&2; exit 1; }
git pull -q --no-rebase origin int/common && git push -q origin int/common
git checkout -q "$MAIN" && git pull -q --no-rebase origin "$MAIN"
git merge -q --no-ff int/common -m "[G] Sync int/common → $MAIN$CO" || { git merge --abort; git checkout -q int/common; echo "충돌: 수동 해결 필요" >&2; exit 1; }
git push -q origin "$MAIN"
if [ "${1:-}" = "--all" ]; then
  for p in $(python3 $T/kitcfg.py modules.keys); do
    git show-ref -q "refs/heads/int/$p" || git show-ref -q "refs/remotes/origin/int/$p" || continue
    git checkout -q "int/$p" && git pull -q --no-rebase origin "int/$p"
    git merge -q --no-ff "$MAIN" -m "[G] Merge $MAIN → int/$p$CO" && git push -q origin "int/$p"
  done
fi
git checkout -q int/common
git log --oneline -1 "$MAIN"
