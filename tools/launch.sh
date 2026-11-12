#!/usr/bin/env bash
# 작업 투입 (v0.3부터 launch.py가 본체다. 사용법: launch.sh --help)
#   orchestration/tools/launch.sh [--dry-run] [--review | --retry 지적.md --suffix r1] <작업ID> [작업ID ...]
set -euo pipefail
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/launch.py" "$@"
