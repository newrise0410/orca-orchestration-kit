#!/usr/bin/env bash
# 이정표 대시보드 스냅샷: docs/07_process/snapshots/<YYYYMMDD_HHMM>_<이정표>.html.gz
#   사용: orchestration/tools/dashboard/snapshot.sh <이정표 이름>   예) W1완료
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
[ $# -ge 1 ] || { echo "사용: $0 <이정표>" >&2; exit 2; }
label=$(printf '%s' "$1" | sed 's/[[:space:]/]/-/g')
out="$REPO/docs/07_process/snapshots/$(date +%Y%m%d_%H%M)_${label}.html"
mkdir -p "$(dirname "$out")"
(cd "$REPO" && python3 "$HERE/build.py" --repo "$REPO" --orca --out "$out" -q)
gzip -f "$out" && echo "${out#$REPO/}.gz"
