#!/usr/bin/env bash
# 세션(워크트리)별 격리 실행 컨테이너 — orca-orchestration-kit 실행 격리 모듈
#
#   사용: scripts/run/run.sh [명령...]      # 명령이 없으면 대화형 bash
#         scripts/run/run.sh --stop        # 이 워크트리의 컨테이너·전용 네트워크 정지 (원격이면 RUN_REMOTE 붙여서)
#         scripts/run/run.sh --info        # 계산된 이름·격리 값만 출력
#
# 격리
#   - 컨테이너 이름: <RUN_PREFIX>-<워크트리 폴더명>. 이미 떠 있으면 exec로 재사용한다.
#   - host network를 쓰지 않는다. 컨테이너마다 **전용 docker 네트워크**(<이름>)를 만든다.
#     같은 포트·멀티캐스트·탐색 프로토콜을 쓰는 동시 실행이 서로 섞이지 않는다.
#     (해시로 정한 ID만으로는 충돌한다. 네트워크 수준 격리가 기본이어야 한다.)
#   - 프로젝트별 격리 변수 훅: profiles/<RUN_PROFILE>/profile.sh 의 profile_env 함수가
#     ISO_ENV 배열에 -e KEY=VAL을 더한다(예: ros2 프로필의 ROS_DOMAIN_ID·GZ_PARTITION).
#   - 격리 전 방식으로 떠 있는 컨테이너가 있으면 경고한다(--stop 후 재실행).
#
# 설정: scripts/run/run.conf (없으면 기본값). 환경변수가 설정 파일보다 우선한다.
#   RUN_REMOTE=user@host   원격 모드
#   RUN_CPUS / RUN_MEM     자원 상한 (컨테이너 생성 때만 적용)
#   RUN_GPU=1, RUN_GPU_DEVICE=<인덱스|UUID|이름 일부|all>  GPU 지정 (이름 일부 예: 3090, a100. 1~2자리 숫자는 인덱스)
#   RUN_IMAGE=<이미지>     실행 이미지 (예: 학습용 이미지로 바꿔 띄우기)
#   RUN_HEAVY=1            무거운 작업 슬롯(flock) 하나를 잡을 때까지 기다렸다 실행. 슬롯 수 RUN_HEAVY_SLOTS
#   RUN_REMOTE_PULL=0      원격 결과 회수 생략
#   호스트 환경변수 중 RUN_PASS_ENV 정규식에 맞는 것은 컨테이너 명령에 전달된다.
#
# 원격 모드 (RUN_REMOTE)
#   1) 워크트리를 rsync로 원격 ~/<RUN_REMOTE_DIR>/<워크트리>/ 에 동기화한다.
#      RUN_EXCLUDES는 보내지 않는다: .git, 빌드 산출물, 캐시, 그리고 **배포 제한 파일 패턴**(예: *.pdf, .orca/)
#   2) 원격에서 같은 이름의 컨테이너를 띄우고 명령을 실행한다.
#   3) 끝나면 RUN_PULL_DIRS(증적·원본 결과)를 로컬로 회수한다(-u: 로컬이 더 새로우면 덮어쓰지 않는다).
#   전제: ssh 키 인증(BatchMode), 원격 이미지, (GPU 시) nvidia-container-toolkit, rsync
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
# 워크트리별 실행이므로 '호출한 위치'의 워크트리를 쓴다
CALLER_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$ROOT")"
ROOT="$CALLER_ROOT"

# ── 설정 기본값 → run.conf → 환경변수
RUN_PREFIX_D=kit; RUN_IMAGE_D=ubuntu:22.04; RUN_REMOTE_DIR_D=kit_ws
RUN_CPUS_LOCAL=4; RUN_MEM_LOCAL=6g; RUN_CPUS_REMOTE=8; RUN_MEM_REMOTE=24g; RUN_SHM=1g
RUN_WORKDIR=/ws; RUN_HEAVY_SLOTS_D=3; RUN_PROFILE_D=""
RUN_PASS_ENV_D='^(APP_|SMOKE_)'
RUN_PULL_DIRS=(docs/06_evidence .runs)
RUN_EXCLUDES=('/.git' '/.cache/' '/.orca/' '/build/' '/dist/' '/node_modules/' '/.runs/' '*.pdf' '*.PDF')
RUN_DOCKER_ARGS=()
CONF="${RUN_CONF:-$HERE/run.conf}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && . "$CONF"
PREFIX="${RUN_PREFIX:-$RUN_PREFIX_D}"
IMAGE="${RUN_IMAGE:-${RUN_IMAGE_DEFAULT:-$RUN_IMAGE_D}}"
REMOTE_DIR="${RUN_REMOTE_DIR:-$RUN_REMOTE_DIR_D}"
HEAVY_SLOTS="${RUN_HEAVY_SLOTS:-$RUN_HEAVY_SLOTS_D}"
PROFILE="${RUN_PROFILE:-$RUN_PROFILE_D}"
PASS_ENV="${RUN_PASS_ENV:-$RUN_PASS_ENV_D}"
REMOTE="${RUN_REMOTE:-${RUN_REMOTE_DEFAULT:-}}"

WT="$(basename "$ROOT" | sed 's/[^a-zA-Z0-9_.-]/-/g')"   # tr 범위 표기는 '.-\n'을 범위로 오해한다 → sed
NAME="$PREFIX-$WT"
NET="$NAME"
ISO_ENV=()
ISO_ARGS=(--network "$NET" --label "kit.isolated=1" --label "kit.prefix=$PREFIX")
WRAP_PREFIX=""        # 프로필이 명령 앞에 붙일 셸 문장(예: setup 스크립트 source)

if [ -n "$PROFILE" ]; then
  PF="$HERE/profiles/$PROFILE/profile.sh"
  [ -f "$PF" ] || { echo "[run.sh] 프로필 없음: $PF" >&2; exit 1; }
  # shellcheck disable=SC1090
  . "$PF"
  declare -F profile_env >/dev/null && profile_env
fi

if [ "${1:-}" = "--info" ]; then
  echo "root=$ROOT name=$NAME net=$NET image=$IMAGE remote=${REMOTE:-local} profile=${PROFILE:-none}"
  printf '  %s\n' "${ISO_ENV[@]}"
  exit 0
fi

warn_unisolated() {
  if [ "$1" != "1" ]; then
    echo "[run.sh] 경고: $NAME 는 격리 전 방식(기본 bridge)으로 떠 있다. 다른 실행과 트래픽이 섞일 수 있다." >&2
    echo "[run.sh]       무거운 시험 전에 'scripts/run/run.sh --stop'(원격이면 RUN_REMOTE 붙여서) 후 다시 실행하라." >&2
  fi
}

# 호스트 → 컨테이너 전달 환경변수 (RUN_* 자체는 제외)
ENV_ARGS=()
while IFS='=' read -r k _; do
  case "$k" in RUN_*) continue ;; esac
  [[ "$k" =~ $PASS_ENV ]] && ENV_ARGS+=(-e "$k=${!k}")
done < <(env)
ENV_ARGS+=("${ISO_ENV[@]}")

q() { [ $# -eq 0 ] || printf '%q ' "$@"; }    # 원격 셸로 넘길 인자 인용
inner_cmd() { if [ -n "$WRAP_PREFIX" ]; then printf '%s; %s' "$WRAP_PREFIX" "$*"; else printf '%s' "$*"; fi; }

# GPU 지정값 → nvidia 장치(인덱스/UUID, 쉼표 구분) 또는 all.  resolve_gpu <값> [nvidia-smi 앞에 붙일 명령]
resolve_gpu() {
  local spec=$1; shift
  case "$spec" in all|ALL|'') echo all; return 0 ;; esac
  # 1~2자리 숫자만 인덱스로 본다. '3090' 같은 모델 번호는 이름으로 해석한다
  if [[ "$spec" =~ ^[0-9]{1,2}(,[0-9]{1,2})*$ || "$spec" == GPU-* ]]; then echo "$spec"; return 0; fi
  local list key hits
  list=$("$@" nvidia-smi --query-gpu=index,name --format=csv,noheader) \
    || { echo "[run.sh] nvidia-smi 실패: GPU 이름 '$spec'을 해석할 수 없다" >&2; return 1; }
  key=$(printf '%s' "$spec" | tr -d ' _-' | tr '[:upper:]' '[:lower:]')
  hits=$(printf '%s\n' "$list" | awk -F', *' -v k="$key" \
    '{ n = tolower($2); gsub(/[ _-]/, "", n); if (index(n, k)) printf "%s%s", (c++ ? "," : ""), $1 }')
  if [ -z "$hits" ] || [[ "$hits" == *,* ]]; then
    echo "[run.sh] GPU '$spec'이 하나로 정해지지 않는다 (일치: ${hits:-없음}). 목록:" >&2
    printf '%s\n' "$list" | sed 's/^/  /' >&2
    return 1
  fi
  echo "$hits"
}
gpu_args() {
  if [ "$1" = all ]; then GPU=(--gpus all); else GPU=(--gpus "\"device=$1\""); fi
  GPU+=(-e NVIDIA_DRIVER_CAPABILITIES=all --label "kit.gpu=$1")
}
check_label() {   # check_label <실행 중 값> <요청 값> <설명> <요청이 명시됐는가>
  if [ -n "$4" ] && [ "$1" != "$2" ]; then
    echo "[run.sh] 컨테이너 $NAME 가 $3 '${1:-없음}'으로 이미 떠 있다(요청 '$2'). --stop 후 다시 실행하라." >&2
    exit 1
  fi
}

# 무거운 작업 슬롯: SLOTDIR 아래 slot1..N 중 하나를 flock으로 잡고 실행한다
heavy_wrap() {   # heavy_wrap <슬롯 폴더> <실행할 셸 문장>
  printf 'mkdir -p %s; w=0; while :; do for i in $(seq 1 %s); do exec 9>%s/slot$i; if flock -n 9; then echo "[run.sh] heavy slot $i/%s (%s)" >&2; %s; exit $?; fi; done; [ $w -eq 0 ] && echo "[run.sh] heavy 슬롯 대기 중" >&2; w=1; sleep 10; done' \
    "$1" "$HEAVY_SLOTS" "$1" "$HEAVY_SLOTS" "$NAME" "$2"
}

# ───────────────────────── 원격 모드 ─────────────────────────
if [ -n "$REMOTE" ]; then
  SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE")
  RREL="$REMOTE_DIR/$WT"
  RHOME=$("${SSH[@]}" 'echo $HOME')
  RDIR="$RHOME/$RREL"
  if [ "${1:-}" = "--stop" ]; then
    "${SSH[@]}" "docker stop $NAME >/dev/null 2>&1 || true; sleep 1; docker network rm $NET >/dev/null 2>&1 || true"
    echo "[run.sh] remote $NAME stopped" >&2; exit 0
  fi
  "${SSH[@]}" "mkdir -p $(q "$RDIR")"
  EXC=(); for e in "${RUN_EXCLUDES[@]}"; do EXC+=(--exclude "$e"); done
  rsync -az --delete "${EXC[@]}" -e "ssh -o BatchMode=yes" "$ROOT/" "$REMOTE:$RREL/"
  "${SSH[@]}" "docker image inspect $(q "$IMAGE") >/dev/null 2>&1" \
    || { echo "[run.sh] 원격에 이미지 $IMAGE 가 없다 ($REMOTE)" >&2; exit 1; }
  GPU=(); DEV=none
  if [ "${RUN_GPU:-0}" = "1" ]; then
    DEV=$(resolve_gpu "${RUN_GPU_DEVICE:-0}" "${SSH[@]}") || exit 1
    gpu_args "$DEV"
  fi
  if [ -n "$("${SSH[@]}" "docker ps -q -f name=^${NAME}\$")" ]; then
    check_label "$("${SSH[@]}" "docker inspect -f '{{index .Config.Labels \"kit.gpu\"}}' $NAME")" "$DEV" GPU "${RUN_GPU_DEVICE:-}"
    check_label "$("${SSH[@]}" "docker inspect -f '{{.Config.Image}}' $NAME")" "$IMAGE" 이미지 "${RUN_IMAGE:-}"
    warn_unisolated "$("${SSH[@]}" "docker inspect -f '{{index .Config.Labels \"kit.isolated\"}}' $NAME")"
  else
    "${SSH[@]}" "docker network inspect $NET >/dev/null 2>&1 || docker network create --label kit.prefix=$PREFIX $NET >/dev/null 2>&1 || { docker network prune -f --filter label=kit.prefix=$PREFIX >/dev/null; docker network create --label kit.prefix=$PREFIX $NET >/dev/null; }" \
      || { echo "[run.sh] 원격 네트워크 $NET 생성 실패(docker 주소 풀 부족 가능). 쓰지 않는 컨테이너를 --stop 하라" >&2; exit 1; }
    "${SSH[@]}" "$(q docker run -d --rm --init --name "$NAME" \
      --cpus "${RUN_CPUS:-$RUN_CPUS_REMOTE}" --memory "${RUN_MEM:-$RUN_MEM_REMOTE}" --shm-size "$RUN_SHM" \
      "${ISO_ARGS[@]}" "${ISO_ENV[@]}" "${GPU[@]}" "${RUN_DOCKER_ARGS[@]}" \
      -v "$RDIR":"$RUN_WORKDIR" -w "$RUN_WORKDIR" "$IMAGE" sleep infinity) >/dev/null"
    echo "[run.sh] remote=$REMOTE container=$NAME net=$NET gpu=$DEV dir=$RDIR" >&2
  fi
  rc=0
  if [ $# -eq 0 ]; then
    ssh -t "$REMOTE" "docker exec -it $(q "${ENV_ARGS[@]}") $NAME bash" || rc=$?
  else
    INNER="docker exec -i $(q "${ENV_ARGS[@]}" "$NAME" bash -lc "$(inner_cmd "$*")")"
    if [ "${RUN_HEAVY:-0}" = "1" ]; then
      "${SSH[@]}" "$(heavy_wrap '~/.kit_heavy' "$INNER")" || rc=$?
    else
      "${SSH[@]}" "$INNER" || rc=$?
    fi
  fi
  if [ "${RUN_REMOTE_PULL:-1}" = "1" ]; then
    for p in "${RUN_PULL_DIRS[@]}"; do
      mkdir -p "$ROOT/$p"
      rsync -azu -e "ssh -o BatchMode=yes" "$REMOTE:$RREL/$p/" "$ROOT/$p/" 2>/dev/null || true
    done
  fi
  exit $rc
fi

# ───────────────────────── 로컬 모드 ─────────────────────────
if [ "${1:-}" = "--stop" ]; then
  docker stop "$NAME" >/dev/null 2>&1 || true; sleep 1; docker network rm "$NET" >/dev/null 2>&1 || true
  echo "[run.sh] $NAME stopped" >&2; exit 0
fi
GPU=(); DEV=none
if [ "${RUN_GPU:-0}" = "1" ]; then
  DEV=$(resolve_gpu "${RUN_GPU_DEVICE:-all}") || exit 1
  gpu_args "$DEV"
fi
exec_cmd() {
  if [ $# -eq 0 ]; then exec docker exec -it "${ENV_ARGS[@]}" "$NAME" bash; fi
  if [ "${RUN_HEAVY:-0}" = "1" ]; then
    exec bash -c "$(heavy_wrap "$HOME/.cache/kit_heavy" "$(q docker exec -i "${ENV_ARGS[@]}" "$NAME" bash -lc "$(inner_cmd "$*")")")"
  fi
  exec docker exec -i "${ENV_ARGS[@]}" "$NAME" bash -lc "$(inner_cmd "$*")"
}
if [ -n "$(docker ps -q -f "name=^${NAME}$")" ]; then
  check_label "$(docker inspect -f '{{index .Config.Labels "kit.gpu"}}' "$NAME")" "$DEV" GPU "${RUN_GPU_DEVICE:-}"
  check_label "$(docker inspect -f '{{.Config.Image}}' "$NAME")" "$IMAGE" 이미지 "${RUN_IMAGE:-}"
  warn_unisolated "$(docker inspect -f '{{index .Config.Labels "kit.isolated"}}' "$NAME")"
  exec_cmd "$@"
fi
docker network inspect "$NET" >/dev/null 2>&1 || docker network create --label "kit.prefix=$PREFIX" "$NET" >/dev/null 2>&1 \
  || { docker network prune -f --filter "label=kit.prefix=$PREFIX" >/dev/null; docker network create --label "kit.prefix=$PREFIX" "$NET" >/dev/null; } \
  || { echo "[run.sh] 네트워크 $NET 생성 실패(docker 주소 풀 부족 가능)" >&2; exit 1; }
docker run -d --rm --init --name "$NAME" \
  --cpus "${RUN_CPUS:-$RUN_CPUS_LOCAL}" --memory "${RUN_MEM:-$RUN_MEM_LOCAL}" --shm-size "$RUN_SHM" \
  "${ISO_ARGS[@]}" "${ISO_ENV[@]}" "${GPU[@]}" "${RUN_DOCKER_ARGS[@]}" \
  -v "$ROOT":"$RUN_WORKDIR" -w "$RUN_WORKDIR" "$IMAGE" sleep infinity >/dev/null
echo "[run.sh] container=$NAME net=$NET gpu=$DEV (중지: scripts/run/run.sh --stop)" >&2
exec_cmd "$@"
