#!/usr/bin/env bash
# 자원 지표 1회 수집 (동시 실행 증설 판단용, orchestration/README.md 5장 안전선)
#   사용: scripts/run/metrics.sh [user@host ...]   인자가 없으면 run.conf의 RUN_REMOTE_DEFAULT
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_REMOTE_DEFAULT=""; RUN_PREFIX=kit
# shellcheck disable=SC1091
[ -f "$HERE/run.conf" ] && . "$HERE/run.conf"
echo "== $(date '+%F %T') local"
free -g | awk 'NR==2{print "  mem_avail_g="$7" mem_used_g="$3} NR==3{print "  swap_used_g="$3}'
awk '{print "  load1="$1" load5="$2" load15="$3}' /proc/loadavg
echo "  agent_procs=$(pgrep -fc 'claude|codex' || true) containers=$(docker ps -q 2>/dev/null | wc -l)"
hosts=("$@"); [ ${#hosts[@]} -eq 0 ] && [ -n "$RUN_REMOTE_DEFAULT" ] && hosts=("$RUN_REMOTE_DEFAULT")
for h in "${hosts[@]}"; do
  echo "== remote $h"
  ssh -o BatchMode=yes -o ConnectTimeout=5 "$h" '
free -g | awk "NR==2{print \"  mem_avail_g=\"\$7}";
top -bn2 -d1 | grep "^%Cpu" | tail -1 | awk "{print \"  cpu_busy_pct=\" 100-\$8}";
awk "{print \"  load5=\"\$2}" /proc/loadavg;
echo "  containers=$(docker ps -q | wc -l)";
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total --format=csv,noheader | sed "s/^/  gpu: /"' 2>&1
done
