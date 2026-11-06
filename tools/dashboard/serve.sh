#!/usr/bin/env bash
# 대시보드를 빌드하고 로컬 주소에서만 띄운다. 주기적으로 다시 빌드한다.
#
#   orchestration/tools/dashboard/serve.sh [-p PORT] [-i 초] [-b 주소] [--orca] [-- build.py 추가 인자...]
#     -p, --port      포트 (기본: kit.yaml dashboard.port 또는 8765)
#     -i, --interval  재빌드 주기(초). 0이면 재빌드하지 않음 (기본 30)
#     -b, --bind      바인드 주소 (기본 127.0.0.1. 사설망 공개는 tailscale serve로 한다 — README)
#     --orca          build.py --orca 전달 (Orca 작업 상태 반영. Run은 kit.yaml orca.work_run)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
PORT="$(cd "$REPO" && python3 "$HERE/../kitcfg.py" dashboard.port 8765 2>/dev/null || echo 8765)"
INTERVAL=30; BIND=127.0.0.1; BUILD_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port) PORT="$2"; shift 2 ;;
    -i|--interval) INTERVAL="$2"; shift 2 ;;
    -b|--bind) BIND="$2"; shift 2 ;;
    --orca) BUILD_ARGS+=(--orca); shift ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; BUILD_ARGS+=("$@"); break ;;
    *) echo "알 수 없는 옵션: $1 (-h 참고)" >&2; exit 2 ;;
  esac
done
[[ "$PORT" =~ ^[0-9]+$ ]] || { echo "포트는 숫자여야 한다: $PORT" >&2; exit 2; }
[[ "$INTERVAL" =~ ^[0-9]+$ ]] || { echo "주기는 0 이상의 정수(초): $INTERVAL" >&2; exit 2; }
OUT_DIR="$REPO/dashboard"
build() { (cd "$REPO" && python3 "$HERE/build.py" --repo "$REPO" --out "$OUT_DIR/index.html" "${BUILD_ARGS[@]}" "$@"); }
build
LOOP_PID=""
cleanup() { [[ -n "$LOOP_PID" ]] && kill "$LOOP_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
if (( INTERVAL > 0 )); then
  ( while sleep "$INTERVAL"; do build -q || echo "[$(date +%T)] 재빌드 실패 (이전 index.html 유지)" >&2; done ) &
  LOOP_PID=$!
  echo "재빌드: ${INTERVAL}초마다"
fi
echo "대시보드: http://${BIND}:${PORT}/  (Ctrl+C로 종료)"
python3 -m http.server "$PORT" --bind "$BIND" --directory "$OUT_DIR"
