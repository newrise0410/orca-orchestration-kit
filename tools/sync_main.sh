#!/usr/bin/env bash
# int/common → 메인 브랜치 머지 (tasks.yaml·RTM 갱신 커밋 직후). 지금 체크아웃의 브랜치를 바꾸지 않는다(교훈 L-04).
#   사용: orchestration/tools/sync_main.sh [--all] [--push]
#     --all   메인 → int/<모듈>도 전파 (코드·계약 변경 때)
#     --push  갱신한 브랜치를 원격(kit.yaml project.git_remote)에 올린다. 원격 push가 배포를 일으키는 저장소면 승인 뒤에만 쓴다.
#   머지는 임시 워크트리에서 한다. 대상 브랜치가 다른 워크트리에 체크아웃돼 있으면 그 워크트리가 깨끗할 때만 fast-forward한다.
#   커밋 끝 줄은 KIT_TRAILER 환경변수(예: 'Co-Authored-By: ...')로 붙인다. 커밋 안 된 변경은 반영하지 않는다.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
T=orchestration/tools
MAIN=$(python3 $T/kitcfg.py project.main_branch main)
REM=$(python3 $T/kitcfg.py project.git_remote origin)
ALL=0; PUSH=0
for x in "$@"; do case "$x" in --all) ALL=1 ;; --push) PUSH=1 ;; -h|--help) sed -n '2,8p' "$0"; exit 0 ;; *) echo "알 수 없는 옵션 $x" >&2; exit 2 ;; esac; done
CO=""; [ -n "${KIT_TRAILER:-}" ] && CO=$'\n\n'"$KIT_TRAILER"
[ -n "$REM" ] && git fetch -q "$REM"

tip() {  # 브랜치 최신 커밋: 로컬과 원격 중 앞선 것. 갈라졌으면 실패
  local b=$1 l r=""
  l=$(git rev-parse -q --verify "refs/heads/$b" || true)
  [ -n "$REM" ] && r=$(git rev-parse -q --verify "refs/remotes/$REM/$b" || true)
  if [ -z "$l" ]; then echo "$r"; return 0; fi
  if [ -z "$r" ] || git merge-base --is-ancestor "$r" "$l"; then echo "$l"; return 0; fi
  if git merge-base --is-ancestor "$l" "$r"; then echo "$r"; return 0; fi
  echo "중단: 로컬 $b와 $REM/$b가 갈라졌다. 먼저 맞춘다" >&2; return 1
}
wt_of() { git worktree list --porcelain | awk -v b="branch refs/heads/$1" '/^worktree /{p=substr($0,10)} $0==b {print p}' | head -1; }
publish() {  # publish <브랜치> <새 커밋>: 로컬 브랜치를 옮기고(--push면 원격도)
  local b=$1 new=$2 w old
  w=$(wt_of "$b"); old=$(git rev-parse -q --verify "refs/heads/$b" || true)
  if [ -n "$w" ]; then
    [ -z "$(git -C "$w" status --porcelain --untracked-files=no)" ] || { echo "중단: $b가 체크아웃된 $w에 커밋 안 된 변경이 있다(새 커밋 ${new:0:9}는 버려진다)" >&2; return 1; }
    [ "$old" = "$new" ] || git -C "$w" merge -q --ff-only "$new"
  elif [ "$old" != "$new" ]; then
    git update-ref "refs/heads/$b" "$new" ${old:+"$old"}
  fi
  if [ $PUSH = 1 ] && [ -n "$REM" ]; then git push -q "$REM" "$new:refs/heads/$b"; fi
}
merge_into() {  # merge_into <대상> <원천> <메시지>
  local dst=$1 src=$2 msg=$3 d s tmp new
  d=$(tip "$dst") || return 1; s=$(tip "$src") || return 1
  [ -n "$d" ] || { echo "건너뜀: $dst 없음"; return 0; }
  [ -n "$s" ] || { echo "중단: $src 없음" >&2; return 1; }
  if git merge-base --is-ancestor "$s" "$d"; then publish "$dst" "$d"; echo "$dst: 이미 $src를 포함"; return 0; fi
  tmp=$(mktemp -d)
  git worktree add -q --detach "$tmp/m" "$d"
  if git -C "$tmp/m" merge -q --no-ff "$s" -m "$msg$CO"; then
    new=$(git -C "$tmp/m" rev-parse HEAD)
    git worktree remove --force "${tmp:?}/m"; rmdir "${tmp:?}"
    publish "$dst" "$new"
    git log --oneline -1 "$new"
  else
    git -C "$tmp/m" merge --abort || true
    git worktree remove --force "${tmp:?}/m"; rmdir "${tmp:?}"
    echo "충돌: $src → $dst 수동 해결 필요(아무 브랜치도 바꾸지 않았다)" >&2; return 1
  fi
}

[ "$(git branch --show-current)" = int/common ] && [ -n "$(git status --porcelain --untracked-files=no)" ] \
  && echo "참고: int/common의 커밋 안 된 변경은 반영되지 않는다" >&2
if [ $PUSH = 1 ] && [ -n "$REM" ]; then c=$(tip int/common); [ -n "$c" ] && git push -q "$REM" "$c:refs/heads/int/common"; fi
merge_into "$MAIN" int/common "[G] Sync int/common → $MAIN"
if [ $ALL = 1 ]; then
  for p in $(python3 $T/kitcfg.py modules.keys); do
    merge_into "int/$p" "$MAIN" "[G] Merge $MAIN → int/$p"
  done
fi
[ $PUSH = 1 ] || { [ -n "$REM" ] && echo "(원격에는 올리지 않았다. 올리려면 --push)"; } || true
