#!/usr/bin/env bash
# 워크트리 사이 동시 실행 제한: 공용 git 디렉터리의 잠금(flock)으로 같은 이름의 명령을 N개까지만 돌린다.
#   사용: orchestration/tools/slot.sh [-n 슬롯수] <이름> -- <명령...>
#   예)   orchestration/tools/slot.sh test -- npm test        # 전체 시험은 한 번에 하나만(메모리 부족으로 앱이 죽은 사례)
#   슬롯 수: -n → 환경변수 KIT_SLOTS_<이름> → kit.yaml slots.<이름> → 1
#   부분 시험처럼 가벼운 명령에는 쓰지 않는다. 실행 격리 모듈(scripts/run)을 쓰면 RUN_HEAVY가 같은 일을 한다.
set -uo pipefail
n=""
[ "${1:-}" = "-n" ] && { n=$2; shift 2; }
name=${1:?이름이 필요하다}; shift
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || { sed -n '2,6p' "$0"; exit 2; }
command -v flock >/dev/null || { echo "slot: flock이 없어 제한 없이 실행한다" >&2; exec "$@"; }
T="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
var="KIT_SLOTS_${name//[^A-Za-z0-9_]/_}"
n=${n:-${!var:-$(python3 "$T/kitcfg.py" "slots.$name" 1 2>/dev/null || echo 1)}}
[[ "$n" =~ ^[0-9]+$ ]] && [ "$n" -ge 1 ] || n=1
dir="$(git rev-parse --git-common-dir)/orca-kit/slots"; mkdir -p "$dir"
got=""
for i in $(seq 1 "$n"); do
  exec 9>"$dir/$name.$i.lock"
  if flock -n 9; then got=$i; break; fi
  exec 9>&-
done
if [ -z "$got" ]; then
  echo "slot: '$name' 슬롯 $n개가 모두 사용 중이다. 빌 때까지 기다린다 ($dir)" >&2
  start=$(date +%s)
  exec 9>"$dir/$name.1.lock"; flock 9; got=1
  echo "slot: $(( $(date +%s) - start ))초 기다림" >&2
fi
"$@"
