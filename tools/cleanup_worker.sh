#!/usr/bin/env bash
# 머지가 끝난 워커 정리 (orchestration/README.md 7.1 '종료 정리')
#   사용: orchestration/tools/cleanup_worker.sh [--dry-run] <워크트리이름> <원격 브랜치> <상위 브랜치>
#   예)   orchestration/tools/cleanup_worker.sh r-api-001 req/R-API-001 int/api
#   네 조건(보고 완료·푸시됨·상위에 머지됨·변경 없음)을 하나라도 못 맞추면 아무것도 지우지 않고 이유를 출력한 뒤 종료코드 1.
#   설정: orca.work_run(KIT_RUN), run.cleanup_hook(KIT_CLEANUP_HOOK: '<훅> <워크트리이름>'으로 호출. 예: scripts/run/cleanup_hook.sh)
#         project.git_remote(비면 로컬 전용: 브랜치 비교를 로컬 브랜치로 한다)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
T=orchestration/tools
ORCA="${ORCA_CLI_COMMAND:-orca}"
DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
[ $# -eq 3 ] || { sed -n '2,6p' "$0"; exit 2; }
n=$1; rb=$2; up=$3
RUN="${KIT_RUN:-$(python3 $T/kitcfg.py orca.work_run)}"
HOOK="${KIT_CLEANUP_HOOK:-$(python3 $T/kitcfg.py run.cleanup_hook)}"
REM="$(python3 $T/kitcfg.py project.git_remote origin)"; P=""; [ -n "$REM" ] && P="$REM/"
# 워크트리 경로: git worktree 목록에서 폴더 이름이 n인 것
d=$(git worktree list --porcelain | awk -v n="$n" '/^worktree /{p=substr($0,10); k=split(p,a,"/"); if (a[k]==n) print p}' | head -1)
[ -n "$d" ] || { echo "보류 $n: 워크트리를 찾지 못했다 (git worktree list)"; exit 1; }
[ -n "$REM" ] && git fetch -q "$REM"
head=$(git -C "$d" rev-parse HEAD)
[ "$head" = "$(git rev-parse -q --verify "$P$rb" || true)" ] || { echo "보류 $n: $P$rb와 HEAD가 다르다(미푸시)"; exit 1; }
git merge-base --is-ancestor "$head" "$P$up" || { echo "보류 $n: $P$up에 머지되지 않았다"; exit 1; }
[ -z "$(git -C "$d" status --porcelain)" ] || { echo "보류 $n: 작업 트리에 변경이 있다"; exit 1; }
wl=$("$ORCA" orchestration worker-list ${RUN:+--run "$RUN"} --limit 100 --json)
act=$(printf '%s' "$wl" | python3 -c "
import json,sys
ws=json.load(sys.stdin).get('result',{}).get('workers',[])
done={'succeeded','failed','abandoned','stopped','cancelled','canceled'}
print(' '.join(w['dispatchId'] for w in ws if str(w.get('workspace','')).endswith('/$n') and w.get('state') not in done))")
[ -z "$act" ] || { echo "보류 $n: 끝나지 않은 디스패치 $act"; exit 1; }
dsps=$(printf '%s' "$wl" | python3 -c "
import json,sys
for w in json.load(sys.stdin).get('result',{}).get('workers',[]):
  if str(w.get('workspace','')).endswith('/$n') and w.get('terminalState')!='released': print(w['dispatchId'])")
if [ $DRY = 1 ]; then
  echo "[dry-run] 조건 충족: $n ($d)"
  for dsp in $dsps; do echo "[dry-run] $ORCA orchestration worker-release --dispatch $dsp"; done
  echo "[dry-run] $ORCA worktree rm --worktree path:$d --delete-branch"
  [ -n "$HOOK" ] && echo "[dry-run] $HOOK $n"
  exit 0
fi
for dsp in $dsps; do
  "$ORCA" orchestration worker-release --dispatch "$dsp" >/dev/null 2>&1 || "$ORCA" orchestration worker-stop --dispatch "$dsp" >/dev/null 2>&1 || true
done
"$ORCA" worktree rm --worktree "path:$d" --delete-branch >/dev/null
[ -n "$HOOK" ] && [ -x "$HOOK" ] && "$HOOK" "$n"
echo "정리 $n: 터미널 해제, 워크트리·로컬 브랜치 제거${HOOK:+, 정리 훅 실행}"
