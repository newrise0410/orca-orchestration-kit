#!/usr/bin/env bash
# 가상 예제 "할 일 관리 웹앱"에 키트를 적용하고 결과물을 다시 만든다.
#   1) 임시 폴더에 빈 git 저장소 → kit.py init --answers answers.yaml
#   2) 이 폴더의 프로젝트 내용(tasks.yaml, RTM·TBD, F-01 증적)을 덮어쓴다
#   3) 진행 상황을 흉내 낸 git 이력: F-01 머지 → G-0(int/api·int/web) → R-API-001 진행 중
#   4) validate_dag · render_spec(R-API-001, R-WEB-001, I-WEB) · 대시보드 생성 → 결과를 이 폴더로 복사
#   사용: examples/demo-project/make_demo.sh [작업 폴더(기본: 임시)]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$HERE/../.." && pwd)"
W="${1:-$(mktemp -d)}/todo-app"
rm -rf "$W"; mkdir -p "$W"
export GIT_AUTHOR_NAME="todo team" GIT_AUTHOR_EMAIL="dev@todo.invalid" GIT_COMMITTER_NAME="todo team" GIT_COMMITTER_EMAIL="dev@todo.invalid"
export GIT_AUTHOR_DATE="2026-11-06T10:00:00+09:00" GIT_COMMITTER_DATE="2026-11-06T10:00:00+09:00"
g() { git -C "$W" "$@"; }
g init -q -b main
python3 "$KIT/skills/orca-orchestration-kit/scripts/kit.py" init --target "$W" --answers "$HERE/answers.yaml" --date 2026-11-06 >/dev/null
cp "$HERE/orchestration/tasks.yaml" "$W/orchestration/tasks.yaml"
cp "$HERE"/docs/01_requirements/*.md "$W/docs/01_requirements/"
(cd "$W" && find . -path ./.git -prune -o -type f -print | sed 's#^\./##' | sort) > "$HERE/installed-tree.txt"
g add -A && g commit -qm "[L0] orchestration kit v$(python3 -c "import yaml;print(yaml.safe_load(open('$KIT/kit-manifest.yaml'))['version'])") init"
# W0: F-01 (int/common) → main
g checkout -q -b int/common
mkdir -p "$W/contracts" "$W/api/src" "$W/web/src" "$W/docs/06_evidence/F-01"
printf 'openapi: 3.1.0\ninfo: {title: todo, version: 0.1.0}\npaths: {}\n' > "$W/contracts/openapi.yaml"
touch "$W/api/src/.gitkeep" "$W/web/src/.gitkeep"
cp "$HERE/docs/06_evidence/F-01/report.md" "$W/docs/06_evidence/F-01/report.md"
g add -A && GIT_COMMITTER_DATE="2026-11-06T11:00:00+09:00" g commit -qm "[F-01] API 계약(OpenAPI)·모듈 골격"
g checkout -q main && GIT_COMMITTER_DATE="2026-11-06T11:30:00+09:00" g merge -q --no-ff int/common -m "[G] Merge int/common → main"
# G-0: 모듈 통합 브랜치
g branch int/api main && g branch int/web main
# W1: R-API-001 진행 중
g checkout -q -b req/R-API-001 int/api
mkdir -p "$W/api/src/todos" && printf '// 할 일 CRUD 라우터 (초안)\n' > "$W/api/src/todos/router.js"
g add -A && GIT_COMMITTER_DATE="2026-11-06T13:00:00+09:00" g commit -qm "[R-API-001] 할 일 생성·조회 라우터 초안"
g checkout -q main
# 모델 라우팅 상태(가상): 구독 4개 중 Antigravity는 snap 격리, Grok은 Orca에서 꺼져 쿨다운. R-API-001은 Codex가 맡았다.
# 사용량은 이 머신의 Orca 기록 대신 빈 폴더를 읽게 한다(KIT_ORCA_HOME).
mkdir -p "$W/orchestration/state" "$W/.orca-empty"
export KIT_ORCA_HOME="$W/.orca-empty"
python3 - "$W/orchestration/state" <<'PY'
import json, sys, time, datetime as dt
d = sys.argv[1]
agents = {"claude": {"bin": "/usr/local/bin/claude", "models": None, "error": None, "confined": False},
          "codex": {"bin": "/usr/local/bin/codex", "models": ["gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"], "error": None, "confined": False},
          "antigravity": {"bin": "/snap/bin/agy", "models": ["gemini-3.8-flash-low", "gemini-3.8-flash-medium", "gemini-3.8-flash-high"], "error": None, "confined": True},
          "grok": {"bin": "/usr/local/bin/grok", "models": ["grok-4.7", "grok-4.7-build-fast"], "error": None, "confined": False}}
json.dump({"v": 2, "ts": time.time(), "agents": agents}, open(f"{d}/models_cache.json", "w"), indent=1)
json.dump({"grok": {"until": time.time() + 23 * 3600, "reason": "Orca에서 꺼진 에이전트"}}, open(f"{d}/cooldown.json", "w"), indent=1)
ts = (dt.datetime.now() - dt.timedelta(minutes=42)).strftime("%F %T")
open(f"{d}/dispatch.tsv", "w").write("\t".join(["R-API-001 할 일 CRUD API", "task_demo01", "ctx_demo01", "R-API-001", ts,
                                               "codex", "gpt-6.1-sol", "medium", "codex-sol", "3.0"]) + "\n")
PY
cd "$W"
python3 orchestration/tools/validate_dag.py | tee "$HERE/validate_dag.txt"
mkdir -p "$HERE/orchestration/specs"
python3 orchestration/tools/render_spec.py R-API-001 R-WEB-001 I-WEB
cp orchestration/specs/R-API-001.md orchestration/specs/R-WEB-001.md orchestration/specs/I-WEB.md "$HERE/orchestration/specs/"
mkdir -p "$HERE/dashboard"
python3 orchestration/tools/dashboard/build.py --out "$HERE/dashboard/index.html"
# 결과물에 작업 폴더 경로(사용자 이름이 들 수 있다)를 남기지 않는다
for f in "$HERE/dashboard/index.html" "$HERE/validate_dag.txt" "$HERE"/orchestration/specs/*.md; do sed -i "s#$(dirname "$W")#/tmp/kit-verify/demo#g" "$f"; done
echo "작업 저장소: $W"
