#!/usr/bin/env bash
# 긴 명령(빌드·전체 시험·스모크)을 시간 제한을 걸고 한 번의 호출로 돌린다.
#   사용: orchestration/tools/run_logged.sh <초> <로그 파일> -- <명령...>
#   예)   orchestration/tools/run_logged.sh 1500 "$EV/test-api.log" -- scripts/dev/test.sh api
#         orchestration/tools/slot.sh test -- orchestration/tools/run_logged.sh 1500 "$EV/test.log" -- scripts/dev/test.sh
#         (슬롯은 바깥에 둔다: 슬롯 대기가 시간 제한에 들어가지 않게)
# - 명령을 새 프로세스 묶음으로 띄우고 이 자리에서 끝까지 기다린다(따로 기다리거나 tail하지 않는다).
# - 시간 제한에 걸리면 묶음 전체에 SIGABRT(pytest·파이썬 faulthandler가 모든 스레드 스택을 로그에 찍는다),
#   KIT_RUN_GRACE초(기본 5) 뒤 SIGKILL. timeout(1)과 달리 손자 프로세스(test.sh 아래 pytest)까지 끝난다.
# - 명령이 끝난 뒤 묶음에 남은 프로세스(시험이 띄운 서버 등)는 SIGTERM으로 정리한다(KIT_RUN_KEEP_GROUP=1이면 둔다).
# - 출력은 로그 파일로만. 표준 출력에는 종료 코드·걸린 시간·실패한 시험 이름·로그 끝 KIT_RUN_TAIL줄(기본 15)만.
# - 종료 코드: 명령의 것. 시간 제한이면 124.
# - 도는 동안 <공용 git>/orca-kit/runs/<pid>.run에 시각·제한·워크트리·명령을 적는다(status.py가 '긴 명령'으로 보인다).
set -uo pipefail
limit=${1:?초가 필요하다}; log=${2:?로그 파일이 필요하다}; shift 2
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { sed -n '2,15p' "$0"; exit 2; }
[[ "$limit" =~ ^[0-9]+$ ]] && [ "$limit" -gt 0 ] || { echo "run_logged: 초는 양의 정수다: $limit" >&2; exit 2; }
grace=${KIT_RUN_GRACE:-5}; tailn=${KIT_RUN_TAIL:-15}
mkdir -p "$(dirname "$log")"; : > "$log"
mark=""
if gd=$(git rev-parse --git-common-dir 2>/dev/null); then
  rd="$(cd "$gd" && pwd)/orca-kit/runs"; mkdir -p "$rd"; mark="$rd/$$.run"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%s)" "$$" "$limit" "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" "$log" "$*" > "$mark"
fi
start=$(date +%s)
exec 3>&2 2>/dev/null                   # 셸이 찍는 작업 종료 알림('코어 덤프됨')을 숨긴다. 명령의 출력은 로그로 간다
set -m                                  # 배경 작업이 자기 프로세스 묶음을 갖는다(pgid = pid). setsid가 없는 macOS에서도 된다
"$@" > "$log" 2>&1 < /dev/null &
pid=$!
set +m
killgrp() { kill "-$1" -- "-$pid" 2>/dev/null; }
cleanup() { [ -n "$mark" ] && rm -f "$mark"; }
trap 'killgrp TERM; sleep 1; killgrp KILL; cleanup; exit 130' INT TERM
trap cleanup EXIT
timed_out=0
while kill -0 "$pid" 2>/dev/null; do
  if [ $(( $(date +%s) - start )) -ge "$limit" ]; then
    timed_out=1
    { echo; echo "[run_logged] 시간 제한 ${limit}초: 프로세스 묶음 $pid에 SIGABRT(스택), ${grace}초 뒤 SIGKILL"; } >> "$log"
    killgrp ABRT
    for _ in $(seq 1 "$grace"); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    killgrp KILL
    break
  fi
  sleep 1
done
wait "$pid" 2>/dev/null; rc=$?
exec 2>&3 3>&-
if [ "$timed_out" = 1 ]; then
  sleep 0.2; killgrp KILL; rc=124
elif [ "${KIT_RUN_KEEP_GROUP:-0}" != 1 ] && pgrep -g "$pid" >/dev/null 2>&1; then
  left=$(pgrep -g "$pid" | wc -l | tr -d ' ')
  killgrp TERM; sleep 1; killgrp KILL
  echo "[run_logged] 명령이 끝난 뒤 남은 프로세스 ${left}개를 정리했다" >> "$log"
fi
el=$(( $(date +%s) - start ))
fails=$(grep -aE '^(FAILED|ERROR) [^ ]|^--- FAIL: |^test .* \.\.\. FAILED$|^ *(FAIL|×|✗|✕) |^not ok |^  FAIL ' "$log" 2>/dev/null \
        | sed -E 's/^ +//' | awk '!s[$0]++' | head -20)
nf=$(printf '%s' "$fails" | grep -c . || true)
if [ "$timed_out" = 1 ]; then
  echo "run_logged: 시간 제한 ${limit}초로 끝냄 rc=124 · ${el}초 · 로그 $log (스택은 로그 끝)"
else
  echo "run_logged: rc=$rc · ${el}초 · 로그 $log"
fi
[ "$nf" -gt 0 ] && { echo "실패 ${nf}개:"; printf '%s\n' "$fails" | sed 's/^/  /'; }
echo "--- 로그 끝 ${tailn}줄 ---"
tail -n "$tailn" "$log"
exit "$rc"
