#!/usr/bin/env bash
# L2(또는 L0) 작업 투입: 명세 렌더링 → Orca task 생성 → 워커 시작 → tasks.yaml running · state/dispatch.tsv 기록
#   사용: orchestration/tools/launch.sh [--dry-run] <작업ID> [작업ID ...]
#   설정(orchestration/kit.yaml): orca.work_run(=--run), orca.repo(=--repo), orca.agent
#   환경변수로 덮어쓰기: KIT_RUN, KIT_REPO, KIT_AGENT, KIT_WORKTREE(기본 new-top-level)
#   L1 통합 작업은 --keep-open으로 띄운다(상시 세션).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
T=orchestration/tools
ORCA="${ORCA_CLI_COMMAND:-orca}"
DRY=0; [ "${1:-}" = "--dry-run" ] && { DRY=1; shift; }
[ $# -gt 0 ] || { sed -n '2,6p' "$0"; exit 2; }
RUN="${KIT_RUN:-$(python3 $T/kitcfg.py orca.work_run)}"
REPO="${KIT_REPO:-$(python3 $T/kitcfg.py orca.repo)}"
AGENT="${KIT_AGENT:-$(python3 $T/kitcfg.py orca.agent claude)}"
WT="${KIT_WORKTREE:-new-top-level}"
[ -n "$RUN" ] || { echo "orca.work_run이 비어 있다 (kit.yaml 또는 KIT_RUN)" >&2; exit 1; }
python3 $T/validate_dag.py -q >/dev/null || { echo "validate_dag 오류: tasks.yaml을 먼저 고쳐라" >&2; python3 $T/validate_dag.py -q; exit 1; }
run() { if [ $DRY = 1 ]; then printf '[dry-run]'; printf ' %q' "$@"; echo; else "$@"; fi; }

for id in "$@"; do
  read -r layer base <<<"$(python3 - "$id" <<'PY'
import sys; sys.path.insert(0, "orchestration/tools"); import kitlib
r = kitlib.repo_root(); c = kitlib.load_config(r)
d, _ = kitlib.load_tasks_text(kitlib.tasks_path(r, c).read_text())
t = next((t for t in d["tasks"] if str(t["id"]) == sys.argv[1]), None)
if not t: sys.exit(f"알 수 없는 작업 {sys.argv[1]}")
b = str(t.get("branch") or ""); ip = str(c.get("integration_prefix") or "int/")
# 통합 브랜치에서 직접 일하는 작업(L1, int/common의 W0)은 그 브랜치에서, 나머지는 base에서 워크트리를 만든다
print(t.get("layer", "L2"), b if b.startswith(ip) else t.get("base") or kitlib.cfg_get(c, "project.main_branch", "main"))
PY
)"
  spec=$(python3 $T/render_spec.py "$id")            # specs/<ID>.md (+ .extra.md)
  name=$(echo "$id" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g')
  keep=(); [ "$layer" = L1 ] && keep=(--keep-open)
  repo_arg=(); [ -n "$REPO" ] && repo_arg=(--repo "$REPO")
  if [ $DRY = 1 ]; then
    run "$ORCA" orchestration task-create --run "$RUN" --spec-file "$spec" --task-title "$id" --display-name "$id" --json
    run "$ORCA" orchestration worker-start --run "$RUN" --task "<task_id>" --agent "$AGENT" --worktree "$WT" "${repo_arg[@]}" \
        --base-branch "$base" --name "$name" --display-name "$id" "${keep[@]}" --json
    echo "[dry-run] python3 $T/task_status.py $id running"; continue
  fi
  tid=$("$ORCA" orchestration task-create --run "$RUN" --spec-file "$spec" --task-title "$id" --display-name "$id" --json | grep -o 'task_[0-9a-f]*' | head -1)
  [ -n "$tid" ] || { echo "task-create 실패: $id" >&2; exit 1; }
  did=$("$ORCA" orchestration worker-start --run "$RUN" --task "$tid" --agent "$AGENT" --worktree "$WT" "${repo_arg[@]}" \
        --base-branch "$base" --name "$name" --display-name "$id" "${keep[@]}" --json | grep -o 'ctx_[0-9a-f]*' | head -1)
  [ -n "$did" ] || { echo "worker-start 실패: $id ($tid). 다시 실행하지 말고 failedStage를 확인하라" >&2; exit 1; }
  printf "%s\t%s\t%s\t%s\t%s\n" "$id" "$tid" "$did" "$name" "$(date '+%F %T')" >> orchestration/state/dispatch.tsv
  python3 $T/task_status.py "$id" running >/dev/null
  echo "$id $tid $did"
done
