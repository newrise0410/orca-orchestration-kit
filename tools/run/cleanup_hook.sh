#!/usr/bin/env bash
# cleanup_worker.sh 정리 훅: 워커의 원격 컨테이너·전용 네트워크를 정지하고, 원본 결과를 보관한 뒤 동기화 폴더를 지운다.
#   사용: scripts/run/cleanup_hook.sh <워크트리이름>
#   설정: run.conf (RUN_PREFIX, RUN_REMOTE_DEFAULT, RUN_REMOTE_DIR), RUN_ARCHIVE_SRC(기본 .runs), RUN_ARCHIVE_DIR(기본 ~/<접두>_archive)
#   원본 결과는 증적이 커밋된 뒤 보관한다(재분석 대비). 보관본 삭제는 사용자 판단이다.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_PREFIX=kit; RUN_REMOTE_DIR=kit_ws; RUN_REMOTE_DEFAULT=""
# shellcheck disable=SC1091
[ -f "$HERE/run.conf" ] && . "$HERE/run.conf"
n=$1; NAME="$RUN_PREFIX-$n"
SRC="${RUN_ARCHIVE_SRC:-.runs}"; ARCH="${RUN_ARCHIVE_DIR:-\$HOME/${RUN_PREFIX}_archive}"
REMOTE="${RUN_REMOTE:-$RUN_REMOTE_DEFAULT}"
script="docker stop $NAME >/dev/null 2>&1; docker network rm $NAME >/dev/null 2>&1; mkdir -p $ARCH;
d=\$HOME/$RUN_REMOTE_DIR/$n; if [ -d \$d/$SRC ] && [ -n \"\$(ls -A \$d/$SRC)\" ]; then mv \$d/$SRC $ARCH/$n.$(basename "$SRC"); fi; rm -rf \$d"
if [ -n "$REMOTE" ]; then ssh -o BatchMode=yes "$REMOTE" "$script"; else docker stop "$NAME" >/dev/null 2>&1 || true; docker network rm "$NAME" >/dev/null 2>&1 || true; fi
echo "정리 훅 $n: 컨테이너·네트워크 정지${REMOTE:+, 원격 동기화 폴더 제거(원본 결과는 $ARCH/$n.*)}"
