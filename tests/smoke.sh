#!/usr/bin/env bash
# 키트 회귀 시험 (임시 폴더에서만 돈다. Orca·네트워크 불필요)
#   사용: tests/smoke.sh [작업 폴더]     종료코드 0 = 전부 통과
set -uo pipefail
KITSRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="${1:-$(mktemp -d)}"; mkdir -p "$W"
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=dev@t.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=dev@t.invalid
export ORCA_CLI_COMMAND="$W/no-orca"; printf '#!/bin/sh\necho "{\\"ok\\":false}"; exit 1\n' > "$ORCA_CLI_COMMAND"; chmod +x "$ORCA_CLI_COMMAND"
# 구독 CLI 대역(모델 목록만 답한다) — 라우팅 시험을 이 머신의 실제 구독과 떼어 놓는다
B="$W/bin"; mkdir -p "$B"
cat > "$B/claude" <<'SH'
#!/bin/sh
exit 0
SH
cat > "$B/codex" <<'SH'
#!/bin/sh
echo '{"models":[{"slug":"gpt-6-astra"},{"slug":"gpt-6.1-sol"},{"slug":"gpt-6.2-sol"},{"slug":"gpt-6.1-terra"},{"slug":"gpt-6-luna"},{"slug":"gpt-9-new"}]}'
SH
cat > "$B/agy" <<'SH'
#!/bin/sh
printf 'gemini-4-neon-low\tL\ngemini-4-neon-medium\tM\ngemini-4-neon-high\tH\ngemini-4-neon-xhigh\tX\ngemini-4-carbon-low\tL\ngemini-4-carbon-medium\tM\ngemini-4-carbon-high\tH\ngemini-3.8-flash-low\tF\nclaude-opus-5-5-medium\tO\n'
SH
cat > "$B/grok" <<'SH'
#!/bin/sh
V="$(dirname "$0")/grok.ver"
case "$1" in
  --version) echo "grok $(cat "$V" 2>/dev/null || echo 1.0.0) (abc) [stable]" ;;
  update) echo "updated"; echo 1.0.1 > "$V" ;;
  *) echo "Available models:"; echo "  * grok-4.7 (default)"; echo "  - grok-4.7-build-fast"; echo "  - grok-4.6" ;;
esac
SH
chmod +x "$B"/*; export PATH="$B:$PATH" KIT_ORCA_HOME="$W/orca-home"
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
check ".gitignore: 대시보드 도구는 추적, 루트 출력만 무시" bash -c "cd $R && ! git check-ignore -q orchestration/tools/dashboard/build.py && git check-ignore -q dashboard/index.html"
cd "$R" && git add -A && git commit -qm "[L0] init"
T=orchestration/tools

echo "[2] DAG·렌더링"
check "validate_dag 오류 0" python3 $T/validate_dag.py -q
# W0 초안: 기획(F-00a, hold) → 유사 오픈소스 조사·반영(F-00b) → 계약(F-01)
check "W0 초안: 기획 → 유사 오픈소스 조사 → 계약 순서" python3 -c "
import yaml; t={x['id']:x for x in yaml.safe_load(open('orchestration/tasks.yaml'))['tasks']}
assert t['F-00a']['deps']==[] and t['F-00a'].get('hold') and t['F-00b']['deps']==['F-00a'] and t['F-01']['deps']==['F-00b']
assert t['F-00b']['write_scope']==t['F-00a']['write_scope'] and all(p.startswith('docs/') for p in t['F-00a']['write_scope'])"
check "tasks.yaml에 YAML 앵커(&·*)가 없음" bash -c "! grep -nE '(: |- )[&*]id[0-9]' orchestration/tasks.yaml"
check "기획이 보류면 투입 가능 작업 없음" test -z "$(python3 $T/render_spec.py --list-ready)"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run F-00a 2>&1); check "F-00a는 hold라 투입 보류" has "$o" "보류 중"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run F-01 2>&1); check "F-01은 조사(F-00b) 전에는 보류" has "$o" "선행 미완료"
check "F-00b는 조사 태그로 분류" bash -c "python3 $T/models.py route F-00b 2>&1 | grep -q '태그.*research'"
for i in F-00a F-00b; do python3 $T/task_status.py $i merged >/dev/null; done
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
check "tasks.yaml에서 merged 6개가 빠짐" test $((before-after)) -eq 6
check "보관 뒤 validate 오류 0" python3 $T/validate_dag.py -q
check "보관 뒤 I-API 투입 가능 유지" bash -c "python3 $T/render_spec.py --list-ready | grep -qx I-API"
check "status.py --no-orca" bash -c "python3 $T/status.py --no-orca | grep -q '보관 6'"
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
# .gitignore 키트 블록: 옛 판(dashboard/)을 upgrade가 매니페스트와 맞추고, 블록 밖의 줄은 그대로 둔다
sed -i 's#^/dashboard/$#dashboard/#' "$U/.gitignore"; echo "mine.log" >> "$U/.gitignore"
check "옛 블록이면 대시보드 도구가 무시됨(재현)" bash -c "cd $U && git check-ignore -q orchestration/tools/dashboard/build.py"
o=$($KIT upgrade --target "$U" --summary 2>&1)
check "upgrade 미리보기: .gitignore 키트 블록 갱신" has "$o" ".gitignore: 키트 블록 갱신"
check "미리보기는 .gitignore를 바꾸지 않음" grep -qx 'dashboard/' "$U/.gitignore"
$KIT upgrade --target "$U" --apply --summary >/dev/null 2>&1
check "upgrade 적용: 블록 갱신 + 블록 밖 줄 보존" bash -c "cd $U && grep -qx '/dashboard/' .gitignore && grep -qx 'mine.log' .gitignore && ! git check-ignore -q orchestration/tools/dashboard/build.py && git check-ignore -q dashboard/index.html"
check "다시 적용해도 .gitignore는 그대로" bash -c "! $KIT upgrade --target $U --summary | grep -q '.gitignore'"

echo "[10] 모델 자동 라우팅"
cd "$R"
M="python3 $T/models.py"
first() { head -1 <<<"$1"; }
o=$($M discover 2>&1)
check "discover: CLI 모델 목록 읽음" bash -c 'grep -q "codex: 모델 6개" <<<"$0" && grep -q "✓ codex-sol" <<<"$0"' "$o"
check "discover: 계열의 가장 높은 판(gpt-6.2-sol)·옛 판은 알림에서 뺌" bash -c 'grep -q "codex-sol .*gpt-6.2-sol .*계열 gpt-{v}-sol → 6.2판, 카탈로그 gpt-6.1-sol" <<<"$0" && grep -q "옛 판" <<<"$0" && ! grep -q "grok-4.6" <<<"$0"' "$o"
check "discover: 카탈로그에 없는 모델 알림" has "$o" "gpt-9-new"
check "캐시는 .gitignore" grep -q 'models_cache.json' .gitignore
o=$($M route --json S-01 2>/dev/null)
check "체계 통합(S-01)은 품질 4 이상" python3 -c "import json,sys;d=json.loads(sys.argv[1]);sys.exit(0 if d['quality']>=4 else 1)" "$o"
cat >> orchestration/tasks.yaml <<'YAML'

- id: X-DOC
  title: README 오타 수정
  layer: L2
  module: api
  branch: req/X-DOC
  base: int/api
  deps: []
  write_scope: [README.md]
  acceptance: "README의 오타를 고친다"
  status: todo
- id: X-PIN
  title: 고정 라우팅
  layer: L2
  module: api
  branch: req/X-PIN
  base: int/api
  deps: []
  write_scope: [api/pin/]
  acceptance: "고정한 모델로 띄운다"
  route: codex-sol
  difficulty: hardest
  status: todo
YAML
o=$($M route X-DOC 2>&1)
check "쉬운 작업은 저가 모델(cost 1)" bash -c 'grep -Eq "^\[X-DOC\] (claude-haiku|codex-luna|agy-gemini-carbon) " <<<"$0"' "$o"
check "근거 출력(난이도·선택·다음 후보)" bash -c 'grep -q "난이도 근거" <<<"$0" && grep -q "다음 후보" <<<"$0"' "$o"
check "validate_dag: 잘못된 difficulty 경고" bash -c "python3 $T/validate_dag.py 2>&1 | grep -q 'X-PIN: difficulty'"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run X-PIN 2>&1)
check "route: 고정 → codex 계열 최신 gpt-6.2-sol" bash -c 'grep -q "route: codex-sol" <<<"$0" && grep -q -- "--agent codex --model gpt-6.2-sol" <<<"$0"' "$o"
check "계열: models.yaml이 model을 정하면 따라가지 않음 · {effort} 계열은 판별 ID" python3 -c "
import sys, yaml, tempfile; sys.path.insert(0,'$T'); import routing
f=tempfile.NamedTemporaryFile('w',suffix='.yaml',delete=False); yaml.safe_dump({'models':{'codex-sol':{'model':'gpt-6.1-sol'}}},f); f.close()
cat,_=routing.load('.',policy_path=f.name)
disc={'codex':{'models':['gpt-6.1-sol','gpt-6.2-sol']},'antigravity':{'models':['gemini-4-neon-low','gemini-4-neon-high','gemini-5-neon-low','gemini-5-neon-medium']}}
r=routing.resolve_catalog(cat,disc)['models']
assert r['codex-sol']['model']=='gpt-6.1-sol', r['codex-sol']
n=r['agy-gemini-neon']; assert n['model']=='gemini-5-neon-medium' and n['model_by_effort']=={'low':'gemini-5-neon-low','medium':'gemini-5-neon-medium'}, n
assert routing.vkey('5-5')==(5,5) and routing.family_regex('claude-opus-{v}-{effort}').match('claude-opus-5-5-high')"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --explain X-DOC 2>&1)
check "launch: 라우팅 결과로 --agent/--model" bash -c 'grep -q "라우팅: " <<<"$0" && grep -q "선택 근거" <<<"$0" && grep -Eq -- "--agent (claude|codex|antigravity|grok) --model" <<<"$0"' "$o"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --no-route X-DOC 2>&1)
check "--no-route: orca.agent 기본값" bash -c '! grep -q "라우팅" <<<"$0" && grep -q -- "--agent claude --worktree" <<<"$0"' "$o"
check "agy는 effort 대신 모델 ID 변형" python3 -c "
import sys; sys.path.insert(0,'$T'); import routing
m={'agent':'antigravity','model':'g-m','model_by_effort':{'low':'g-l','medium':'g-m','high':'g-h'}}
e,mid,_=routing.pick_effort(m,4.6,'balanced',3); assert e is None and mid=='g-h', (e,mid)"
# 교차 검토: F-03을 claude가 구현했다고 기록
printf 'F-03\ttask_1\tctx_1\tf-03\t2026-01-01 10:00:00\tclaude\tsonnet\thigh\tclaude-sonnet\t3.5\n' >> orchestration/state/dispatch.tsv
o=$($M route --review F-03 2>&1)
check "교차 검토: 구현(claude)과 다른 구독" bash -c 'l=$(head -1 <<<"$0"); grep -q "→ " <<<"$l" && ! grep -q "→ claude " <<<"$l"' "$o"
# 실패 이력: S-01을 claude-opus로 띄웠는데 todo로 되돌아왔다
printf 'S-01\ttask_2\tctx_2\ts-01\t2026-01-01 11:00:00\tclaude\topus\thigh\tclaude-opus\t4.0\n' >> orchestration/state/dispatch.tsv
o=$($M route S-01 2>&1)
check "실패 이력: 난이도 올림 + 실패 모델 피함" bash -c 'grep -q "앞선 시도 실패 1회" <<<"$0" && ! grep -q "^\[S-01\] claude-opus " <<<"$0"' "$o"
$M cooldown claude --min 30 --reason '사용량 한도' >/dev/null
o=$($M route F-01 2>&1)
check "쿨다운 구독은 제외" bash -c '! grep -q "^\[F-01\] claude-" <<<"$0" && grep -q "쿨다운" <<<"$0"' "$o"
$M cooldown --clear claude >/dev/null
sed -i 's/^    codex: 4$/    codex: 0/' orchestration/models.yaml
o=$($M route --review F-03 2>&1)
check "동시 상한이 찬 구독은 제외" bash -c '! head -1 <<<"$0" | grep -q "→ codex " && grep -q "동시 상한" <<<"$0"' "$o"
sed -i 's/^    codex: 0$/    codex: 4/' orchestration/models.yaml
sed -i 's/^  agents: .*/  agents: [codex]/' orchestration/models.yaml
o=$($M route F-01 2>&1)
check "routing.agents 밖 구독은 안 씀" bash -c 'grep -q "^\[F-01\] codex-" <<<"$0"' "$o"
check "학습: merged·retry·재투입·검토 판정 점수" python3 -c "
import sys; sys.path.insert(0,'$T'); import routing
rows=[dict(kit_id='A',mode='new',ts='1',agent='claude',model='opus',key='claude-opus',difficulty='4',did='d1'),
      dict(kit_id='A',mode='new',ts='2',agent='codex',model='gpt-6.1-sol',key='',difficulty='4',did='d2'),
      dict(kit_id='A',mode='retry',ts='3',agent='codex',model='gpt-6.1-sol',key='codex-sol',difficulty='3',did='d3')]
cat,_=routing.load('.')
o={r['did']:r['score'] for r in routing.outcomes(rows,{'A':{'status':'merged'}},[],cat['models'])}
assert o=={'d1':0.0,'d2':0.6,'d3':1.0}, o
o={r['did']:r['score'] for r in routing.outcomes(rows,{'A':{'status':'merged'}},[{'kit_id':'A','ts':'2.5','score':0.3}],cat['models'])}
assert o['d2']==0.3, o"
$M feedback F-03 minor --note '경계값 누락' >/dev/null
o=$($M stats 2>&1)
check "stats: 실적 표 · 검토 판정 기록" bash -c 'grep -q "claude-sonnet" <<<"$0" && grep -q "F-03" orchestration/state/route_feedback.tsv' "$o"
mkdir -p "$KIT_ORCA_HOME"; printf '{"dailyAggregates":[{"day":"%s","model":"claude-opus-5-5","outputTokens":3000000}]}' "$(date +%F)" > "$KIT_ORCA_HOME/orca-claude-usage.json"
o=$($M stats 2>&1)
check "stats: Orca 사용량(오늘 출력 토큰)" has "$o" "claude: 오늘 3000k"
o=$(python3 $T/status.py --no-orca 2>&1); check "status.py --no-orca (라우팅 포함 설치본)" test -n "$o"
sed -i 's/^  agents: .*/  agents: [claude, codex, antigravity, grok]/' orchestration/models.yaml
# snap 격리 설치본(/snap/bin/x → /usr/bin/snap)은 워커로 못 쓴다: worker_done·ask를 보낼 orca CLI를 실행하지 못한다
mkdir -p "$W/fake/usr/bin" "$W/bin2"; cp "$W/bin/agy" "$W/fake/usr/bin/snap"; ln -sf "$W/fake/usr/bin/snap" "$W/bin2/agy"
o=$(PATH="$W/bin2:$PATH" $M discover 2>&1)
check "snap 격리 agy는 제외" bash -c 'grep -q "snap 격리" <<<"$0" && grep -q "✗ agy-gemini-neon" <<<"$0"' "$o"
$M discover >/dev/null 2>&1
# Orca에서 꺼진 에이전트(agent_unconfigured)는 쿨다운하고 다시 라우팅한다
cat > "$W/fake-orca" <<'SH'
#!/bin/sh
case "$*" in
  *task-create*) echo '{"ok":true,"result":{"id":"task_abc123"}}' ;;
  *worker-list*) echo '{"ok":true,"result":{"workers":[]}}' ;;
  *worker-start*"--agent $FAKE_DISABLED "*) echo '{"ok":false,"error":{"code":"agent_unconfigured","message":"Agent launcher is disabled"}}'; exit 1 ;;
  *worker-start*) echo '{"ok":true,"result":{"dispatchId":"ctx_def456","state":"ready"}}' ;;
  *) echo '{"ok":false}'; exit 1 ;;
esac
SH
chmod +x "$W/fake-orca"
first_ag=$($M route --json X-DOC | python3 -c 'import sys,json; print(json.load(sys.stdin)["agent"])')
rm -f orchestration/state/cli_update.json
o=$(FAKE_DISABLED=$first_ag ORCA_CLI_COMMAND="$W/fake-orca" KIT_RUN=run_x $T/launch.sh X-DOC 2>&1)
check "launch: 하루 한 번 구독 CLI 업데이트 뒤 투입" bash -c 'grep -q "구독 CLI 업데이트" <<<"$0" && python3 -c "import sys;sys.path.insert(0,\"$1\");import routing;c,p=routing.load(\".\");sys.exit(1 if routing.update_due(\".\",p) else 0)"' "$o" "$T"
check "꺼진 에이전트 → 쿨다운 후 다른 구독으로 투입" bash -c 'grep -q "다시 라우팅" <<<"$0" && grep -q "ctx_def456" <<<"$0" && grep -q "\"$1\"" orchestration/state/cooldown.json && ! tail -1 orchestration/state/dispatch.tsv | grep -q "	$1	"' "$o" "$first_ag"

check "동점: 부동소수 오차 무시 · 부하 → 소모 순으로 가림 · 임계 경로도 같은 대역 실적" python3 -c "
import sys; sys.path.insert(0,'$T'); import routing
cat,pol=routing.load('.'); cat=routing.apply_bench(cat)
c={'difficulty':3.0,'tags':['coding'],'critical':True,'reasons':[]}
ctx={'objective':'balanced','learned':{('codex-sol','mid'):(3.0,3)},'running':{},'usage':{},'failed':set(),'avoid_agent':None}
s,why=routing.score('codex-sol',cat['models']['codex-sol'],c,pol,ctx)
assert any(w.startswith('실적 3.0/3') for w in why), why
a=(0.35000000000000001,'b-key',[],False); b=(0.35,'a-key',[],False)
assert round(a[0],6)==round(b[0],6)"
# 구독 CLI 업데이트: 자체 update 명령 → 버전 기록 → 모델 목록 다시 읽기. snap 설치본은 건너뛴다. launch.sh는 하루 한 번
rm -f "$W/bin/grok.ver"
o=$($M update grok 2>&1)
check "update: grok 1.0.0 → 1.0.1 · 기록 · 카탈로그 대조" bash -c 'grep -q "grok: 갱신 1.0.0 → 1.0.1" <<<"$0" && grep -q "\"after\": \"1.0.1\"" orchestration/state/cli_update.json && grep -q "# 카탈로그 모델" <<<"$0"' "$o"
o=$($M update --check grok 2>&1)
check "update --check: 업데이트하지 않음" bash -c 'grep -q "grok: 확인 1.0.1" <<<"$0"' "$o"
o=$(PATH="$W/bin2:$PATH" $M update antigravity 2>&1)
check "update: snap 설치본은 건너뜀" has "$o" "antigravity: 건너뜀"
# 잔여 한도: Orca rateLimits(orca account list --json). 소진은 초기화까지 제외, 빠듯하면 벌점, 묶음·모델별 창, 숫자만 캐시
cat > "$W/fake-orca-quota.py" <<'PY'
import json, os, sys, time
now = time.time() * 1000; h = 3600 * 1000
full = os.environ.get("FAKE_QUOTA") == "full"
def w(used, mins, resets_h): return {"usedPercent": used, "windowMinutes": mins, "resetsAt": now + resets_h * h}
rl = {"claude": {"provider": "claude", "status": "ok", "planType": "max", "weekly": w(100 if full else 20, 10080, 100), "fableWeekly": w(99, 10080, 100)},
      "codex": {"provider": "codex", "status": "ok", "session": w(100, 300, 2), "weekly": w(40, 10080, 120)},
      "antigravity": {"provider": "antigravity", "status": "ok", "session": w(100 if full else 90, 300, 3), "weekly": w(30, 10080, 160),
                      "buckets": [{"group": "Gemini Models", **w(100 if full else 90, 300, 3)}, {"group": "Gemini Models", **w(30, 10080, 160)},
                                  {"group": "Claude and GPT models", **w(100 if full else 0, 300, 3)}]},
      "grok": {"provider": "grok", "status": "ok", "weekly": w(100 if full else 10, 10080, 10)},
      "inactiveCodexAccounts": [{"rateLimits": {"provider": "codex", "status": "ok", "session": w(0, 300, 5)}}]}
if sys.argv[1:4] == ["account", "list", "--json"]:
    print(json.dumps({"ok": True, "result": {"claude": {"accounts": [{"email": "someone@example.invalid"}]}, "rateLimits": rl}}))
else:
    print('{"ok":false}'); sys.exit(1)
PY
printf '#!/bin/sh\nexec python3 %s "$@"\n' "$W/fake-orca-quota.py" > "$W/fake-orca-q"; chmod +x "$W/fake-orca-q"
rm -f orchestration/state/quota_cache.json
o=$(ORCA_CLI_COMMAND="$W/fake-orca-q" $M quota 2>&1)
check "quota: 소진 제외(초기화 시각·다른 계정 힌트) · 묶음·Fable 주간 · 세션 벌점 · 곧 초기화 가점" bash -c 'grep -q "codex-sol .*제외: codex 세션 한도 100% 소진 (.* 초기화) · 다른 codex 계정은 여유" <<<"$0" && grep -q "claude-fable .*제외: claude Fable 주간" <<<"$0" && ! grep -q "claude-opus .*제외" <<<"$0" && grep -q "agy-gemini-neon .*-" <<<"$0" && ! grep -q "agy-claude-opus" <<<"$0" && grep -q "grok .*+0.50 (주간 90% 남고" <<<"$0"' "$o"
check "quota 캐시: 숫자만(계정 이메일 없음)" bash -c 'test -f orchestration/state/quota_cache.json && ! grep -q "@" orchestration/state/quota_cache.json'
python3 $T/dashboard/build.py --out $W/dash-q.html >/dev/null 2>&1
check "대시보드: 구독 칩에 잔여 한도(캐시) · 소진 구독은 쿨다운 칩" bash -c 'grep -q "Grok <b>0/2</b> <span class=\"muted small\">주 10%" $0 && grep -q "Codex <span class=\"small\">한도 소진 · " $0' "$W/dash-q.html"
o=$($M route --json X-DOC 2>/dev/null)
check "route: 캐시된 한도로 소진 구독 제외" python3 -c "import json,sys;d=json.loads(sys.argv[1]);sys.exit(0 if d['agent']!='codex' and any('한도' in v for v in d['excluded'].values()) else 1)" "$o"
rm -f orchestration/state/quota_cache.json
o=$(FAKE_QUOTA=full ORCA_CLI_COMMAND="$W/fake-orca-q" KIT_RUN=run_x $T/launch.sh --dry-run --force X-DOC 2>&1)
check "launch: 모든 구독 한도 소진이면 기본 에이전트로 띄우지 않고 보류" bash -c 'grep -q "보류: 후보 없음" <<<"$0" && grep -q "한도가 모두 찼다" <<<"$0" && ! grep -q "worker-start" <<<"$0"' "$o"
rm -f orchestration/state/quota_cache.json
# 공개 벤치마크 → 품질: Terminal-Bench 페이지(이스케이프된 리더보드 JSON)에서 카탈로그에 맞는 줄만, 다른 CLI 점수는 빌리지 않는다
python3 - "$W/tb.html" <<'PY'
import json, sys
rows = [("Claude Code", "Opus 5.5", "max", 66.0), ("Codex", "GPT-6.1 Sol", "max", 60.0), ("Codex", "GPT-6 Luna", "max", 15.0),
        ("Antigravity CLI", "Gemini 4 Neon", "xhigh", 61.0), ("Codex", "GPT-6 Astra", "high", 59.0), ("Codex", "GPT-6 Astra", "low", 50.0),
        ("GitHub Copilot", "Copilot Fusion", "max", 71.0)]
items = [{"id": f"r{i}", "rank": i + 1, "metadata": {"date": "2026-11-01", "agent_display": {"url": "u", "label": a},
          "model_display": {"url": "u", "label": m}, "reasoning_effort": e},
          "metrics": {"accuracy": acc, "n_trials": 10, "output_tokens": 1000000, "avg_trial_duration_sec": 600, "accuracy_ci95_half_width": 3}}
         for i, (a, m, e, acc) in enumerate(rows)]
blob = json.dumps({"lb": {"name": "4-1-0", "title": "Terminal-Bench 4.1"}, "entries": items}, separators=(",", ":"))
open(sys.argv[1], "w").write('<html><script>self.__next_f.push([1,"' + blob.replace('"', '\\"') + '"])</script></html>')
PY
o=$($M bench --tb-file "$W/tb.html" 2>&1)
check "bench: 카탈로그에 맞는 줄만 · 옛 판 · 다른 CLI 안 빌림" bash -c 'grep -q "전체 7줄 중 카탈로그에 맞는 6줄" <<<"$0" && grep -Eq "^codex-sol .*GPT-6.1 Sol max 60.0% \(옛 판\)" <<<"$0" && grep -Eq "^agy-claude-opus .*카탈로그 추정" <<<"$0" && grep -Eq "^claude-opus .* 5.0 " <<<"$0" && test -f orchestration/state/bench_scores.yaml' "$o"
o=$($M discover 2>&1)
check "discover: 품질 근거·벤치 출처 표시" bash -c 'grep -q "품질 근거: Terminal-Bench 4.1" <<<"$0" && grep -q "벤치 점수: Terminal-Bench 4.1 .*state/bench_scores.yaml" <<<"$0"' "$o"
check "models.yaml quality는 벤치로 바꾸지 않음" python3 -c "
import sys, yaml, tempfile; sys.path.insert(0,'$T'); import routing
f=tempfile.NamedTemporaryFile('w',suffix='.yaml',delete=False); yaml.safe_dump({'models':{'codex-luna':{'quality':3}}},f); f.close()
cat,_=routing.load('.',policy_path=f.name); r=routing.apply_bench(cat)['models']
assert r['codex-luna']['quality']==3 and r['codex-luna']['quality_src']=='models.yaml', r['codex-luna']
assert r['codex-astra']['quality']==round(5*59/66,1), r['codex-astra']"
printf '<html>nothing</html>' > "$W/tb-bad.html"
check "bench: 꼴이 바뀐 페이지는 실패로 알림" bash -c "! $M bench --tb-file $W/tb-bad.html --dry-run 2>&1 | grep -q '→'"
cat > "$W/fake-aa.py" <<'PY'
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.headers.get("x-api-key") == "aa-key"
        out = {"tier": "free", "data": [{"name": "Gemini 4 Neon (Xhigh)", "slug": "gemini-4-neon", "evaluations": {"artificial_analysis_intelligence_index": 55.0},
               "performance_v3": {"mean_output_tokens_per_second": 120.0}}, {"name": "Other Model", "slug": "x", "evaluations": {}}], "pagination": {}} if ok else {"error": "auth"}
        self.send_response(200 if ok else 401); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(json.dumps(out).encode())
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
AP=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 "$W/fake-aa.py" "$AP" & apid=$!; sleep 0.5
o=$(KIT_AA_URL="http://127.0.0.1:$AP/free" AA_API_KEY=aa-key $M bench --tb-file "$W/tb.html" --aa 2>&1)
check "bench --aa: 무료 API(키)로 지능 지수·속도 기록, 카탈로그 밖 모델은 뺌" bash -c 'grep -q "AA 1개" <<<"$0" && grep -q "intelligence: 55" orchestration/state/bench_scores.yaml && grep -q "artificialanalysis.ai" orchestration/state/bench_scores.yaml' "$o"
o=$($M bench --tb-file "$W/tb.html" --aa 2>&1)
check "bench --aa: 키 없으면 건너뜀" has "$o" "AA_API_KEY가 없어"
kill $apid 2>/dev/null; wait $apid 2>/dev/null
rm -f orchestration/state/bench_scores.yaml
# JEV(선택, 기본 꺼짐): 꺼져 있으면 부르지 않는다. 켜면 shadow는 기록만, live는 신뢰도 기준 이상일 때 난이도를 바꾼다.
cat > "$W/fake-jev.py" <<'PY'
import http.server, json, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n))
        ok = self.headers.get("Authorization") == "Bearer test-key" and body["questions"]["difficulty"]["type"] == "score"
        out = {"model": "jev-test", "answers": {"difficulty": {"type": "score", "score": 3.6, "confidence": 0.9}}} if ok else {"error": "auth"}
        self.send_response(200 if ok else 401); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(out).encode()); open(sys.argv[2], "a").write("call\n")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
JP=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 "$W/fake-jev.py" "$JP" "$W/jev-calls.log" & jpid=$!
sleep 0.5
o=$(TYPESAFE_API_KEY=test-key $M route --json X-DOC 2>/dev/null)
check "JEV 기본 꺼짐: 부르지 않음" bash -c '! test -s "$1" && python3 -c "import json,sys;d=json.loads(sys.argv[1]);sys.exit(0 if d.get(\"jev\") is None else 1)" "$0"' "$o" "$W/jev-calls.log"
cp orchestration/models.yaml "$W/models.yaml.pre-jev"
printf 'jev:\n  enabled: true\n  mode: shadow\n  url: http://127.0.0.1:%s/v1/systemone\n' "$JP" > "$W/jev.yaml"
python3 - "$W/jev.yaml" <<'PY'
import sys, yaml
p = "orchestration/models.yaml"; d = yaml.safe_load(open(p)); d["jev"] = yaml.safe_load(open(sys.argv[1]))["jev"]
yaml.safe_dump(d, open(p, "w"), allow_unicode=True, sort_keys=False)
PY
base=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['difficulty'])" "$o")
o=$(TYPESAFE_API_KEY=test-key $M route --json X-DOC 2>/dev/null)
check "JEV shadow: 기록만(난이도 그대로)" python3 -c "import json,sys;d=json.loads(sys.argv[1]);j=d['jev'];sys.exit(0 if j['difficulty']==4.6 and not j['applied'] and d['difficulty']==float(sys.argv[2]) else 1)" "$o" "$base"
o=$($M route --json X-DOC 2>/dev/null)
check "JEV 캐시: 키 없이도 같은 판정, 다시 부르지 않음" bash -c 'python3 -c "import json,sys;sys.exit(0 if json.loads(sys.argv[1])[\"jev\"][\"cached\"] else 1)" "$0" && test "$(wc -l < "$1")" -eq 1' "$o" "$W/jev-calls.log"
sed -i 's/^  mode: shadow$/  mode: live/' orchestration/models.yaml
o=$($M route --json X-DOC 2>/dev/null)
check "JEV live: 신뢰도 0.9 ≥ 0.6이면 난이도 적용" python3 -c "import json,sys;d=json.loads(sys.argv[1]);sys.exit(0 if d['jev']['applied'] and d['difficulty']==4.6 and d['rule_difficulty']==float(sys.argv[2]) else 1)" "$o" "$base"
rm -f orchestration/state/jev_cache.json
o=$(TYPESAFE_API_KEY=wrong $M route --json X-DOC 2>/dev/null)
check "JEV 실패하면 규칙 분류로" python3 -c "import json,sys;d=json.loads(sys.argv[1]);sys.exit(0 if 'error' in d['jev'] and d['difficulty']==float(sys.argv[2]) else 1)" "$o" "$base"
kill $jpid 2>/dev/null; wait $jpid 2>/dev/null
cp "$W/models.yaml.pre-jev" orchestration/models.yaml

# 대시보드 개요: 상태 열 보드 · 웨이브 × 모듈 격자 · 구독 칩(쿨다운) · 투입한 작업의 모델 칩
python3 $T/dashboard/build.py --out $W/dash2.html >/dev/null 2>&1
check "대시보드 개요: 보드·격자·구독 칩·모델 칩" bash -c 'f=$0; grep -q "id=\"ov-board\"" $f && grep -q "id=\"ov-dag\"" $f && grep -q "class=\"sub cool\"" $f && grep -q "class=\"kc\" data-id=\"X-DOC\"" $f && grep -q "class=\"km mono\"" $f' "$W/dash2.html"
mv orchestration/models.yaml "$W/models.yaml.bak"
python3 $T/dashboard/build.py --out $W/dash3.html >/dev/null 2>&1
check "대시보드: models.yaml 없어도 보드는 나오고 구독 칩은 없음" bash -c 'grep -q "id=\"ov-board\"" $0 && ! grep -q "class=\"subs\"" $0' "$W/dash3.html"
mv "$W/models.yaml.bak" orchestration/models.yaml

echo
echo "결과: 통과 $pass · 실패 $fail  (작업 폴더 $W)"
[ $fail -eq 0 ]
