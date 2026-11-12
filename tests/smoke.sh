#!/usr/bin/env bash
# 키트 회귀 시험 (임시 폴더에서만 돈다. Orca·네트워크 불필요)
#   사용: tests/smoke.sh [작업 폴더]     종료코드 0 = 전부 통과
set -uo pipefail
KITSRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="${1:-$(mktemp -d)}"; mkdir -p "$W"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=dev@t.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=dev@t.invalid
export ORCA_CLI_COMMAND="$W/no-orca"; printf '#!/bin/sh\necho "{\\"ok\\":false}"; exit 1\n' > "$ORCA_CLI_COMMAND"; chmod +x "$ORCA_CLI_COMMAND"
pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   $1"; }
bad() { fail=$((fail+1)); echo "  FAIL $1"; }
check() { local name=$1; shift; if "$@" >/dev/null 2>&1; then ok "$name"; else bad "$name"; fi; }
has() { grep -q -- "$2" <<<"$1"; }

# 키트 사본(git 이력 1개)으로 시험한다 — upgrade 3방향 병합 시험에 이력이 필요하다
K="$W/kit"; rm -rf "${K:?}"; mkdir -p "$K"
(cd "$KITSRC" && git ls-files -co --exclude-standard -z | xargs -0 -I{} cp --parents {} "$K/") 2>/dev/null
git -C "$K" init -q -b main && git -C "$K" add -A && git -C "$K" commit -qm base
KIT="python3 $K/skills/orca-orchestration-kit/scripts/kit.py"

echo "[1] init (로컬 전용 운영)"
R="$W/proj"; rm -rf "${R:?}"; mkdir -p "$R"; git -C "$R" init -q -b main; git -C "$R" commit -q --allow-empty -m root
out=$($KIT init --target "$R" --answers "$K/examples/demo-project/answers.yaml" --git-remote '' 2>&1); echo "$out" | tail -3 >/dev/null
check "init 종료코드 0" test -f "$R/.orca-kit/installed.json"
left=$(grep -rl '{{[A-Z_]*}}' "$R/orchestration" "$R/AGENTS.md" "$R/docs" | grep -v 'sessions/MANAGER.md' || true)
check "남은 {{자리표시자}} 없음(MANAGER 제외)" test -z "$left"
check "kit.yaml git_remote 빈 값" grep -q 'git_remote: ""' "$R/orchestration/kit.yaml"
check "AGENTS: 원격 push 금지 문구" grep -q '원격에 push하지 않는다' "$R/AGENTS.md"
check "procedures 설치" test -f "$R/orchestration/sessions/procedures/merge-gate.md"
cd "$R" && git add -A && git commit -qm "[L0] init"
T=orchestration/tools

echo "[2] DAG·렌더링"
check "validate_dag 오류 0" python3 $T/validate_dag.py -q
python3 $T/render_spec.py F-01 >/dev/null
check "F-01 명세: 로컬 push 명령" grep -q 'git push . HEAD:int/common' orchestration/specs/F-01.md
check "F-01 명세: 큰 문서 선독 지시 없음" bash -c "! grep -q 'orchestration/README.md\`(구조)를 읽어라' orchestration/specs/F-01.md"
check "--list-ready = F-01(W0 포함)" test "$(python3 $T/render_spec.py --list-ready)" = "F-01"

echo "[3] launch 투입 조건 (dry-run)"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run S-01 2>&1); check "S-01 선행 미완료로 보류" has "$o" "선행 미완료"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run F-01 2>&1); check "int/common 없으면 보류+안내" has "$o" "git branch int/common main"
git branch int/common main
o=$(KIT_RUN=run_x $T/launch.sh --dry-run F-01 2>&1); check "F-01 투입 명령(--base-branch int/common)" has "$o" "--base-branch int/common"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --worktree path:/x --force S-01 2>&1); check "기존 워크트리엔 생성 플래그 없음" bash -c "! grep -q -- '--name' <<<\"\$0\"" "$o"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run G-0 2>&1); check "게이트는 투입 거부" has "$o" "게이트"
for i in F-01 F-02 F-03 G-0; do python3 $T/task_status.py $i merged >/dev/null; done
check "L1(I-API)은 L2 선행을 기다리지 않음" bash -c "python3 $T/render_spec.py --list-ready | grep -qx I-API"
python3 - <<'PY'
import re
p='orchestration/tasks.yaml'; s=open(p,encoding='utf-8').read()
s=re.sub(r"(?m)^(\s*)- id: I-WEB\n", lambda m: f"{m.group(0)}{m.group(1)}  hold: 마스터 승인 대기\n", s, count=1); open(p,'w',encoding='utf-8').write(s)
PY
check "hold 작업은 투입 가능에서 빠짐" bash -c "! python3 $T/render_spec.py --list-ready | grep -qx I-WEB"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run I-API 2>&1); check "int/api 없으면 L1 보류" has "$o" "G-0에서 만든다"
git branch int/api main; git branch int/web main
o=$(KIT_RUN=run_x $T/launch.sh --dry-run I-API 2>&1); check "L1 투입은 --keep-open" has "$o" "--keep-open"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --review F-01 2>&1); check "--review: 기준 main, req/F-01-RV" bash -c "grep -q -- '--base-branch main' <<<\"\$0\" && test -f orchestration/specs/F-01.review.md && grep -q 'req/F-01-RV' orchestration/specs/F-01.review.md" "$o"
printf -- '- 오류 입력 처리 누락\n' > "$W/notes.md"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --retry "$W/notes.md" --suffix r1 F-01 2>&1); check "--retry 명세에 지적 포함" grep -q '오류 입력 처리 누락' orchestration/specs/F-01.retry-r1.md
check "task-yaml effort만 있으면 경고" bash -c "python3 - <<'PY'
import sys; sys.path.insert(0,'orchestration/tools'); import validate_dag
r=validate_dag.validate('tasks:\n  - {id: A, layer: L2, module: api, effort: high, acceptance: x}\n', {})
sys.exit(0 if any('effort' in w for w in r['warnings']) else 1)
PY"

echo "[4] 보관 (archive_tasks)"
before=$(python3 -c "import yaml;print(len(yaml.safe_load(open('orchestration/tasks.yaml'))['tasks']))")
check "archive 실행" python3 $T/archive_tasks.py
after=$(python3 -c "import yaml;print(len(yaml.safe_load(open('orchestration/tasks.yaml'))['tasks']))")
check "tasks.yaml에서 merged 4개가 빠짐" test $((before-after)) -eq 4
check "보관 뒤 validate 오류 0" python3 $T/validate_dag.py -q
check "보관 뒤 I-API 투입 가능 유지" bash -c "python3 $T/render_spec.py --list-ready | grep -qx I-API"
check "status.py --no-orca" bash -c "python3 $T/status.py --no-orca | grep -q '보관 4'"
check "대시보드가 보관 작업까지 읽음" bash -c "python3 $T/dashboard/build.py --out $W/dash.html >/dev/null 2>&1 && grep -q 'F-01' $W/dash.html"
git add -A && git commit -qm "[L0] archive"

echo "[5] sync_main (체크아웃을 바꾸지 않음)"
git worktree add -q "$W/l0" int/common
(cd "$W/l0" && echo x > docs/note.md && git add -A && git commit -qm "[L0] note")
cur=$(git branch --show-current)
o=$(cd "$W/l0" && orchestration/tools/sync_main.sh 2>&1); check "sync_main 성공" bash -c "git merge-base --is-ancestor int/common main"
check "현재 체크아웃 브랜치 그대로" test "$(git branch --show-current)" = "$cur"
check "main 체크아웃 파일도 갱신(ff)" test -f docs/note.md

echo "[6] pre-push 훅"
B="$W/remote.git"; git init -q --bare "$B"; git remote add origin "$B"
check "훅 설치" $T/install_hooks.sh
check "로컬 전용이면 원격 push 거부" bash -c "! git push -q origin main 2>/dev/null"
check "로컬 브랜치 갱신(git push .)은 통과" git push -q . main:refs/heads/tmp-local
sed -i 's/git_remote: ""/git_remote: "origin"/; s/protected_branches: \[\]/protected_branches: [main]/' orchestration/kit.yaml
check "보호 브랜치는 승인 없이 거부" bash -c "! git push -q origin main 2>/dev/null"
check "KIT_PUSH_OK=1이면 통과" env KIT_PUSH_OK=1 git push -q origin main
git checkout -q -b req/x && mkdir -p customer && echo s > customer/a.txt && git add -A && git commit -qm "[x] restricted"
check "배포 제한 파일 push 거부" bash -c "! git push -q origin req/x 2>/dev/null"
git checkout -q main; git checkout -q -- . 2>/dev/null

echo "[7] evidence_lint · slot"
mkdir -p docs/06_evidence/F-01; printf 'ok\n' > docs/06_evidence/F-01/report.md
check "깨끗한 증적 0건" python3 $T/evidence_lint.py
printf 'log /home/someone/src/x\nPATH=/usr/bin\n' > docs/06_evidence/F-01/run.log
check "홈 경로·PATH 덤프 잡음" bash -c "! python3 $T/evidence_lint.py"
rm -f docs/06_evidence/F-01/run.log
check "slot.sh 실행" bash -c "$T/slot.sh test -- true"

echo "[8] bootstrap · cleanup"
check "bootstrap --step 필수" bash -c "! $KIT bootstrap --target $R --dry-run 2>/dev/null"
o=$($KIT bootstrap --target "$R" --step manager --dry-run 2>&1); check "manager: --display-name 없음" bash -c "! grep -q -- '--display-name' <<<\"\$0\"" "$o"
check "abandon은 --work-run 필요" bash -c "! $KIT bootstrap --target $R --step abandon 2>/dev/null"
check "cleanup: 없는 워크트리면 보류(1)" bash -c "! $T/cleanup_worker.sh --dry-run nope req/x int/api"

echo "[9] upgrade"
U="$W/up"; rm -rf "${U:?}"; mkdir -p "$U"; git -C "$U" init -q -b main; git -C "$U" commit -q --allow-empty -m root
$KIT init --target "$U" --answers "$K/examples/demo-project/answers.yaml" >/dev/null
printf '\n<!-- 프로젝트 끝줄 -->\n' >> "$U/orchestration/templates/worker-spec.md"      # 프로젝트 수정(끝)
sed -i '1s/$/ (키트 새 판)/' "$K/templates/sessions/worker-spec.md"; git -C "$K" commit -qam "kit change"   # 키트 변경(첫 줄)
mkdir -p "$U/x"; echo "프로젝트 파일" > "$U/orchestration/templates/review-spec.md"
python3 - "$U" <<'PY'
import json,sys; p=sys.argv[1]+'/.orca-kit/installed.json'; d=json.load(open(p)); d['files'].pop('orchestration/templates/review-spec.md'); json.dump(d,open(p,'w'))
PY
o=$($KIT upgrade --target "$U" --summary 2>&1)
check "자동 병합 판정" has "$o" "worker-spec.md: 자동 병합"
check "키트 밖 파일 판정" has "$o" "review-spec.md: 키트 밖 파일과 겹침"
$KIT upgrade --target "$U" --apply --summary >/dev/null 2>&1
check "병합 결과에 양쪽 변경" bash -c "head -1 $U/orchestration/templates/worker-spec.md | grep -q '키트 새 판' && grep -q '프로젝트 끝줄' $U/orchestration/templates/worker-spec.md"
check "키트 밖 파일은 보존 + .kit-new" bash -c "grep -q '프로젝트 파일' $U/orchestration/templates/review-spec.md && test -f $U/orchestration/templates/review-spec.md.kit-new"
check "다시 미리보기: 남은 건 키트 밖 파일뿐" bash -c "$KIT upgrade --target $U --summary | grep -c '^===' | grep -qx 1"

echo
echo "결과: 통과 $pass · 실패 $fail  (작업 폴더 $W)"
[ $fail -eq 0 ]
