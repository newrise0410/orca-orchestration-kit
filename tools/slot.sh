#!/usr/bin/env bash
# 워크트리 사이 동시 실행 제한: 공용 git 디렉터리의 잠금(flock)으로 같은 이름의 명령을 N개까지만 돌린다.
#   사용: orchestration/tools/slot.sh [-n 슬롯수] <이름> -- <명령...>
#   예)   orchestration/tools/slot.sh test -- npm test        # 전체 시험 동시 수 제한(메모리 부족으로 앱이 죽은 사례)
#   슬롯 수: -n → 환경변수 KIT_SLOTS_<이름> → kit.yaml slots.<이름> → 1
#   부분 시험처럼 가벼운 명령에는 쓰지 않는다. 실행 격리 모듈(scripts/run)을 쓰면 RUN_HEAVY가 같은 일을 한다.
# 다 차 있으면 KIT_SLOT_POLL초(기본 5)마다 모든 슬롯을 다시 본다(어느 슬롯이든 먼저 비는 것을 잡는다).
# 기다리는 동안 <공용 git>/orca-kit/slots/<이름>.wait.<pid>, 잡은 뒤 <이름>.<i>.holder에 시각·워크트리·명령을 적는다
# → status.py가 '슬롯 대기'로 보여 준다(그냥 보면 '진행 중'으로만 보인다).
set -uo pipefail
n=""
[ "${1:-}" = "-n" ] && { n=$2; shift 2; }
name=${1:?이름이 필요하다}; shift
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { sed -n '2,9p' "$0"; exit 2; }
command -v flock >/dev/null || { echo "slot: flock이 없어 제한 없이 실행한다" >&2; exec "$@"; }
gd=$(git rev-parse --git-common-dir 2>/dev/null) || { echo "slot: git 저장소 밖이라 제한 없이 실행한다" >&2; exec "$@"; }
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
var="KIT_SLOTS_${name//[^A-Za-z0-9_]/_}"
n=${n:-${!var:-$(python3 "$T/kitcfg.py" "slots.$name" 1 2>/dev/null || echo 1)}}
[[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] || n=1
dir="$(cd "$gd" && pwd)/orca-kit/slots"; mkdir -p "$dir"
wt=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
mark=""
trap '[ -n "$mark" ] && rm -f "$mark"' EXIT
trap 'exit 130' INT; trap 'exit 143' TERM
grab() {
  local i
  for i in $(seq 1 "$n"); do
    exec 9>"$dir/$name.$i.lock"
    if flock -n 9; then got=$i; return 0; fi
    exec 9>&-
  done
  return 1
}
got=""
if ! grab; then
  start=$(date +%s)
  mark="$dir/$name.wait.$$"
  printf '%s\t%s\t%s\t%s\n' "$start" "$$" "$wt" "$*" > "$mark"
  echo "slot: '$name' 슬롯 $n개가 모두 사용 중이다. 하나라도 비면 잡는다 ($dir)" >&2
  until grab; do sleep "${KIT_SLOT_POLL:-5}"; done
  rm -f "$mark"
  echo "slot: $(( $(date +%s) - start ))초 기다려 슬롯 $got/$n" >&2
fi
mark="$dir/$name.$got.holder"
printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$$" "$wt" "$*" > "$mark"
"$@"
