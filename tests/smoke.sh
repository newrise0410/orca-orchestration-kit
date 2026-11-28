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
cp orchestration/kit.yaml "$W/kit.yaml.pre-rv"; cp orchestration/state/dispatch.tsv "$W/dispatch.pre-rv"
sed -i '/^review:/,/^[a-z]/{s/^  agent: ""/  agent: "claude"/;s/^  model: ""/  model: "claude-opus-5-5"/;s/^  effort: ""/  effort: "high"/}' orchestration/kit.yaml
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --review F-01 2>&1); check "검토 1회차: kit.yaml review.effort 그대로(high)" bash -c '! grep -q "회차: 좁은 확인" <<<"$0" && grep -q -- "--effort high" <<<"$0"' "$o"
printf 'F-01-RV\ttask_r\tctx_r1\tf-01-rv\t2026-01-01 10:00:00\tclaude\tclaude-opus-5-5\thigh\t\t\t\n' >> orchestration/state/dispatch.tsv
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --review F-01 2>&1); check "검토 2회차: 좁은 확인이라 effort 한 단계 낮춤(high → medium)" bash -c 'grep -q "검토 2회차: 좁은 확인이라 effort high → medium" <<<"$0" && grep -q -- "--effort medium" <<<"$0"' "$o"
o=$(KIT_RUN=run_x $T/launch.sh --dry-run --review --effort high F-01 2>&1); check "검토 2회차라도 --effort를 주면 그것" bash -c '! grep -q "좁은 확인" <<<"$0" && grep -q -- "--effort high" <<<"$0"' "$o"
cp "$W/kit.yaml.pre-rv" orchestration/kit.yaml; cp "$W/dispatch.pre-rv" orchestration/state/dispatch.tsv
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
printf 'a@example.com b@sub.example.com\n' > docs/06_evidence/F-01/ok.md
check "이메일 허용 도메인: example.com·하위 도메인은 통과" python3 $T/evidence_lint.py docs/06_evidence/F-01/ok.md
printf 'c@notexample.com\n' > docs/06_evidence/F-01/bad.md
check "이메일 허용 도메인: notexample.com은 걸린다(끝 글자만 맞는 도메인)" bash -c "! python3 $T/evidence_lint.py docs/06_evidence/F-01/bad.md"
rm -f docs/06_evidence/F-01/ok.md docs/06_evidence/F-01/bad.md
check "slot.sh 실행" bash -c "$T/slot.sh test -- true"
check "slot.sh 종료코드 그대로" bash -c "$T/slot.sh test -- sh -c 'exit 7'; test \$? -eq 7"
# 슬롯 2개: 1번은 오래, 2번은 잠깐 잡혀 있으면 기다리는 쪽은 2번이 비자마자 잡는다(예전: 1번 잠금만 기다렸다)
KIT_SLOT_POLL=1 $T/slot.sh -n 2 test -- sleep 8 2>/dev/null & sa=$!
KIT_SLOT_POLL=1 $T/slot.sh -n 2 test -- sleep 1 2>/dev/null & sb=$!
sleep 0.4; t0=$(date +%s)
KIT_SLOT_POLL=1 $T/slot.sh -n 2 test -- true 2>/dev/null; dt=$(( $(date +%s) - t0 ))
check "slot.sh: 여러 슬롯 중 먼저 빈 것을 잡는다(${dt}초 < 6초)" test "$dt" -lt 6
wait $sa $sb
check "kitlib.suggest_slots: 코어 4·메모리 8 GiB당 1, 1~8" python3 -c "
import sys; sys.path.insert(0,'$T'); import kitlib as k
assert (k.suggest_slots(16,64),k.suggest_slots(4,8),k.suggest_slots(2,4),k.suggest_slots(16,16),k.suggest_slots(128,1024))==(4,1,1,2,8)"
check "설치: kit.yaml slots.test에 기계 기준 제안값과 근거" grep -Eq '^  test: [1-8] +# 설치 기계\(코어 [0-9]+·메모리 [0-9]+ GiB\) 기준 제안' orchestration/kit.yaml
# run_logged: 시간 제한·한 번의 호출(요약만)·묶음 전체 정리
printf '#!/bin/bash\necho "FAILED tests/test_a.py::test_x - assert 1"\nexec python3 -X faulthandler -c "\ndef spin():\n    while True: pass\nspin()"\n' > "$W/hang.sh"; chmod +x "$W/hang.sh"
o=$(KIT_RUN_GRACE=2 $T/run_logged.sh 2 "$W/rl/hang.log" -- "$W/hang.sh"); rc=$?
check "run_logged: 시간 제한이면 rc 124 · SIGABRT로 파이썬 스택이 로그에 · 실패 이름 요약" bash -c 'test "$2" = 124 && grep -q "시간 제한 2초로 끝냄 rc=124" <<<"$0" && grep -q "FAILED tests/test_a.py::test_x" <<<"$0" && grep -q "in spin" "$1" && ! grep -q "코어 덤프" <<<"$0"' "$o" "$W/rl/hang.log" "$rc"
check "run_logged: 시간 제한 뒤 남은 프로세스 없음" bash -c '! pgrep -f "[d]ef spin" >/dev/null'
o=$($T/run_logged.sh 20 "$W/rl/ok.log" -- bash -c 'echo hi; (sleep 300; :) & exit 3'); rc=$?
check "run_logged: 명령 종료코드 그대로 · 출력은 로그로만 · 남은 묶음 정리" bash -c 'test "$2" = 3 && grep -q "rc=3" <<<"$0" && grep -q "^hi$" "$1" && grep -Eq "남은 프로세스 [0-9]+개를 정리" "$1" && ! pgrep -f "[s]leep 300" >/dev/null' "$o" "$W/rl/ok.log" "$rc"
check "run_logged: 끝나면 실행 표시 파일 없음" bash -c 'test -z "$(ls "$(git rev-parse --git-common-dir)/orca-kit/runs/" 2>/dev/null)"'

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
o=$($KIT upgrade --target "$U" --apply --summary 2>&1)
check "upgrade 적용: 매니저 커밋용 'git add -- <키트가 바꾼 경로만>'" bash -c 'l=$(grep "^  git add -- " <<<"$0"); grep -q "\.gitignore" <<<"$l" && grep -q "\.orca-kit/installed.json" <<<"$l" && ! grep -q -- " -A\| \.$\| orchestration/ " <<<"$l" && grep -q "orchestration kit v.* upgrade" <<<"$0"' "$o"
check "upgrade 미리보기: 적용 전 매니저에게 알리라는 안내" bash -c "$KIT upgrade --target $U --summary | grep -q '적용 직전에 매니저에게 알린다'"
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
ctx={'objective':'balanced','learned':{('codex-sol','mid'):(3.0,3)},'running':{},'usage':{},'failed':set(),'avoid_agents':[]}
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
$M discover >/dev/null 2>&1   # 위 시험이 snap PATH로 모델 캐시를 다시 썼다 → 원래 PATH로 되돌린다
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
# 한도 전환: 도는 워커의 구독이 한도 100% → 정지 점검 '한도 정지' → quota_switch.py(대기 판정 또는 보존·정리·이어받기)
cat > "$W/fake-orca-sw.py" <<'PY'
import json, os, sys, time
a = " ".join(sys.argv[1:]); st = os.environ["FAKE_SW_DIR"]
open(st + "/calls.log", "a").write(a + "\n")
now, h = time.time() * 1000, 3600 * 1000
reset = now + (10 * 60 * 1000 if os.environ.get("FAKE_SW") == "soon" else 3 * h)
used = 20 if os.environ.get("FAKE_SW") == "free" else 100
tight = os.environ.get("FAKE_SW") == "tight"   # 다른 구독: 세션 60%가 100분 만에 찼다(0.6%/분) → 67분 뒤 다시 찬다
def out(o): print(json.dumps(o)); sys.exit(0)
if a.startswith("account list"):
    ok = lambda u, m, r: {"usedPercent": u, "windowMinutes": m, "resetsAt": r}
    accts = {}
    if os.environ.get("FAKE_SW_ACCTS"):   # Orca 실제 꼴: 계정 목록 + 고른 계정(readingAccount)의 한도만
        accts = {"claude": {"activeAccountId": "bbbbbbbb-2", "accounts": [{"id": "aaaaaaaa-1", "home": "/x/claude-profiles/aaaaaaaa-1/home"},
                                                                       {"id": "bbbbbbbb-2", "home": "/x/claude-profiles/bbbbbbbb-2/home"}]}}
    out({"ok": True, "result": {**accts, "rateLimits": {
        "codex": {"provider": "codex", "status": "ok", "session": ok(used, 300, reset), "weekly": ok(40, 10080, now + 100 * h)},
        **{p: dict({"provider": p, "status": "ok", "weekly": ok(10, 10080, now + 100 * h),
                    **({"usageMetadata": {"readingAccount": {"accountId": "bbbbbbbb-2"}}} if p == "claude" and accts else {})},
                   **({"session": ok(60, 300, now + 200 * 60 * 1000)} if tight else {})) for p in ("claude", "grok", "antigravity")}}}})
if "worker-list" in a:
    ws = [{"dispatchId": os.environ.get("FAKE_SW_DID", "ctx_5a1"), "taskId": "task_5a1", "state": "running",
           "liveness": "alive", "workspace": "ws-5a1::" + os.environ["FAKE_SW_WT"]}]
    if os.environ.get("FAKE_SW_MAIL_DID"):   # escalation을 보낸(이미 멈춘) 워커
        ws.append({"dispatchId": os.environ["FAKE_SW_MAIL_DID"], "taskId": "task_5a1", "state": "failed", "liveness": "exited",
                   "workspace": "ws-5a1::" + os.environ["FAKE_SW_WT"]})
    out({"ok": True, "result": {"workers": ws}})   # Orca 실제 꼴: <워크스페이스 ID>::<경로>
if "task-list" in a: out({"ok": True, "result": {"tasks": [{"id": "task_5a1", "title": os.environ.get("FAKE_SW_TITLE", "X-SW")}]}})
if "worker-show" in a:   # 확인 창 대기(Orca observation.agentWait)
    if os.environ.get("FAKE_SW_WAIT") == "rm":
        out({"ok": True, "result": {"waitingOnHuman": True, "observation": {"agentWait": {"evidence": "prompt-text",
             "prompt": 'Bash: rm -f "$EV/$f.raw.log" · Do you want to proceed?'}}}})
    out({"ok": True, "result": {"waitingOnHuman": False}})
if "orchestration deny" in a: out({"ok": True, "result": {"denied": True}})
if "worker-read" in a and "--source terminal" in a:
    scr = ["› 경계 입력을 만든다", "ⓘ This content can’t be shown", "  We take extra care with some cybersecurity requests. If you’re doing authorized", "› gpt-6.2-sol"] \
        if os.environ.get("FAKE_SW_SCREEN") == "filter" else ["› 시험을 돌린다", "ok"]
    out({"ok": True, "result": {"source": "terminal", "terminal": {"handle": "term_x", "status": "running", "tail": scr},
                                "status": {"worker": "running", "terminal": "running", "liveness": "live"}}})   # Orca 실제 꼴
if "worker-read" in a:
    rd = os.environ.get("FAKE_SW_READ", "")
    last = "API Error: Claude Code is unable to respond to this request, which appears to violate our Usage Policy." if rd == "filter" else "경계값 시험을 추가했다. 다음은 시험 실행."
    msgs = [{"role": "assistant", "blocks": [{"type": "tool-call", "name": "Read"}]},
            {"role": "tool", "blocks": [{"type": "tool-result", "output": "README: 이 도구는 usage policy를 따른다" if rd == "toolonly" else "ok"}]},
            {"role": "assistant", "blocks": [{"type": "text", "text": last}]}]
    out({"ok": True, "result": {"transcript": {"messages": msgs}}})
if "inbox" in a:
    mail = os.environ.get("FAKE_SW_MAIL", "")
    lo = {"filter": "› 경계 입력 시험을 이어 간다\n\nⓘ This content can’t be shown\n  We take extra care with some cybersecurity requests. If you’re doing authorized security work, apply for Daybreak to\n  get broader access.\n  Learn more: https://help.openai.com/en/articles/20001326\n\n› gpt-6.2-sol · 41% context left",
          "noise": "문서 3.2절: 차단되면 화면에 'This content can’t be shown'이 뜬다고 적혀 있다\n› 다음 절로 간다",
          "errored": "Error: stream disconnected before completion\n› 다시 시도하려면 Enter",
          "quota": "⚠ Individual quota reached. Please upgrade your subscription to increase your limits. Resets in 2h22m45s.\n› gemini-4-neon"}.get(mail)
    # Orca 실제 꼴(현장 확인): --full이 없으면 본문이 221자에서 잘리고 payload는 taskId·dispatchId뿐.
    # --full이면 본문 전체, payload는 JSON 문자열(stopReason 포함). '…stopped on an error…'는 제목에만 있다.
    body = ("Dispatch " + os.environ.get("FAKE_SW_MAIL_DID", "") + " ended its turn on an error and did not send worker_done. "
            "The coordinator should inspect the worker, decide whether to retry, and release its terminal when done. "
            "Worker state: running; liveness: idle.\nLast output:\n" + (lo or ""))
    did_ = os.environ.get("FAKE_SW_MAIL_DID", "")
    if "--full" in a:
        pl = json.dumps({"taskId": "task_5a1", "dispatchId": did_, "stopReason": "errored"})
    else:
        body, pl = body[:221] + "… (truncated; --full)", {"taskId": "task_5a1", "dispatchId": did_}
    msgs = [{"id": "msg_stop_e5c1", "type": "escalation", "handled": True, "subject": "Worker stopped on an error without reporting",
             "body": body, "payload": pl}] if lo else []
    if os.environ.get("FAKE_SW_WAIT") == "rm" and os.environ.get("FAKE_SW_APPROVAL"):
        msgs.append({"id": "msg_perm_1", "type": "escalation", "handled": False, "subject": "Worker is waiting on a permission prompt",
                     "body": "rm -f \"$EV/$f.raw.log\"", "payload": json.dumps({"dispatchId": os.environ.get("FAKE_SW_DID", "ctx_5a1"),
                                                                                  "approvalId": os.environ["FAKE_SW_APPROVAL"]})})
    if os.path.exists(st + "/done.json"):   # worker_done 메일(advance.py 시험)
        msgs += json.load(open(st + "/done.json"))
    out({"ok": True, "result": {"messages": msgs}})
if "worker-stop" in a or "worker-release" in a or a.startswith("orchestration send"): out({"ok": True, "result": {}})
if a.startswith("automations create"): out({"ok": True, "result": {"automation": {"id": "auto-1", "enabled": True}}})
if a.startswith("automations show"): out({"ok": True, "result": {"automation": {"id": "auto-1", "name": "t", "enabled": True}}})
if a.startswith("automations remove") or a.startswith("automations edit"): out({"ok": True, "result": {}})
if a.startswith("automations runs"):
    st = os.environ.get("FAKE_SW_RUNS", "skipped_precheck")
    out({"ok": True, "result": {"runs": [{"id": "r1", "status": st, "scheduledFor": 9999999999999}]}})
if "task-create" in a: out({"ok": True, "result": {"id": "task_abc777"}})
if "worker-start" in a:
    did = "ctx_def777"
    if os.environ.get("FAKE_SW_UNIQ"):   # 투입마다 다른 dispatch ID
        n = int(open(st + "/uniq").read()) + 1 if os.path.exists(st + "/uniq") else 1
        open(st + "/uniq", "w").write(str(n)); did = f"ctx_u{n:03d}"
    out({"ok": True, "result": {"dispatchId": did, "state": "ready"}})
print('{"ok":false}'); sys.exit(1)
PY
printf '#!/bin/sh\nexec python3 %s "$@"\n' "$W/fake-orca-sw.py" > "$W/fake-orca-sw"; chmod +x "$W/fake-orca-sw"
mkdir -p "$W/sw"; : > "$W/sw/calls.log"
cat >> orchestration/tasks.yaml <<'YAML'
- id: X-SW
  title: 한도 전환 시험 작업
  layer: L2
  module: api
  branch: req/X-SW
  base: int/api
  deps: []
  write_scope: [api/sw/]
  acceptance: "api/sw/ 아래 기능을 만든다"
  status: running
YAML
git add -A >/dev/null && git commit -qm "[L0] X-SW" && git branch -f int/api HEAD
git worktree add -q -b req/X-SW "$W/wt-x-sw" int/api && mkdir -p "$W/wt-x-sw/api/sw"
echo a > "$W/wt-x-sw/api/sw/done.txt"; git -C "$W/wt-x-sw" add -A && git -C "$W/wt-x-sw" commit -qm "[X-SW] 절반"
echo b > "$W/wt-x-sw/api/sw/wip.txt"; swhead=$(git -C "$W/wt-x-sw" rev-parse HEAD)
printf 'X-SW\ttask_5a1\tctx_5a1\tx-sw\t2026-01-02 10:00:00\tcodex\tgpt-6.2-sol\tmedium\tcodex-sol\t3.0\t\n' >> orchestration/state/dispatch.tsv
cp orchestration/kit.yaml "$W/kit.yaml.pre-sw"; sed -i 's/^  work_run: .*/  work_run: "run_sw"/' orchestration/kit.yaml
export FAKE_SW_DIR="$W/sw" FAKE_SW_WT="$W/wt-x-sw"
SW="env ORCA_CLI_COMMAND=$W/fake-orca-sw KIT_NO_CLI_UPDATE=1"
rm -f orchestration/state/quota_cache.json
check "workspace 값: <ID>::<경로>·경로만·Windows 경로·dict" python3 -c "
import sys; sys.path.insert(0,'$T'); import kitlib
f=kitlib.workspace_path
assert f('7909a5da-7dd8::/home/u/w') == '/home/u/w' and f('/home/u/w') == '/home/u/w'
b=chr(92); wp='C:'+b+'Users'+b+'PC'+b+'w'; assert f('5fac85ea::C:/Users/PC/orca/w') == 'C:/Users/PC/orca/w' and f(wp) == wp and f('id::'+wp) == wp
assert f({'path': '/x'}) == '/x' and f(None) == ''"
o=$($SW python3 $T/status.py --json 2>/dev/null)
check "status: Orca workspace(<ID>::<경로>)에서 워커 경로·마지막 커밋을 읽는다" python3 -c "
import json,sys; d=json.loads(sys.argv[1]); w=d['orca']['workers'][0]
assert w['workspace']==sys.argv[2] and w['last_commit'], w" "$o" "$W/wt-x-sw"
o=$($SW python3 $T/status.py --stall 2>&1)
check "정지 점검: 한도 정지(구독·창·초기화 시각 → quota_switch.py)" bash -c 'grep -q "한도 정지 X-SW (ctx_5a1): codex 세션 100%(.* 초기화) · codex-sol → python3 orchestration/tools/quota_switch.py" <<<"$0"' "$o"
o=$($SW python3 $T/quota_switch.py --dry-run 2>&1)
check "quota_switch --dry-run: 전환 계획만(Orca 변경 없음)" bash -c 'grep -q "^\[전환\] X-SW" <<<"$0" && grep -q "(dry-run) worker-stop --dispatch ctx_5a1" <<<"$0" && ! grep -q "worker-stop" "$1/calls.log"' "$o" "$W/sw"
o=$(FAKE_SW=soon $SW python3 $T/quota_switch.py 2>&1)
check "곧 풀리면(기준 30분) 대기" bash -c 'grep -q "^\[대기\] X-SW .*풀린다(.*분 뒤, 기준 30분)" <<<"$0" && ! grep -q "worker-stop" "$1/calls.log"' "$o" "$W/sw"
check "대기: 재개 예약(풀리는 시각 기록)" bash -c 'grep -q "재개 예약: .*에 풀리면" <<<"$0" && grep -q "\"ctx_5a1\"" orchestration/state/quota_wait.json' "$o"
rm -f orchestration/state/quota_cache.json
o=$(FAKE_SW=soon $SW python3 $T/status.py --stall 2>&1)
check "정지 점검: 대기 중인 한도 정지는 재개 예정 시각 표시" has "$o" "대기 중("
python3 -c "
import json; p='orchestration/state/quota_wait.json'; d=json.load(open(p)); d['ctx_5a1']['resets']-=3600; json.dump(d,open(p,'w'))"
rm -f orchestration/state/quota_cache.json
o=$(FAKE_SW=free $SW python3 $T/status.py --stall 2>&1)
check "정지 점검: 한도가 풀린 대기 → 한도 풀림" has "$o" "한도 풀림 X-SW (ctx_5a1)"
check "quota_switch --due: 할 일 있으면 0" bash -c "FAKE_SW=free $SW python3 $T/quota_switch.py --due >/dev/null"
o=$(FAKE_SW=free $SW python3 $T/quota_switch.py 2>&1)
check "재개: 워커에 '이어서 하라'를 보내고 대기 기록을 지운다" bash -c 'grep -q "^\[재개\] X-SW (ctx_5a1)" <<<"$0" && grep -q "orchestration send --to dispatch:ctx_5a1 --type status --subject 한도 풀림" "$1/calls.log" && ! grep -q "ctx_5a1" orchestration/state/quota_wait.json && ! grep -q "worker-stop" "$1/calls.log"' "$o" "$W/sw"
check "quota_switch --due: 할 일 없으면 1" bash -c "! FAKE_SW=free $SW python3 $T/quota_switch.py --due >/dev/null"
# 예약 점검(quota_watch): 사전 확인은 할 일이 있으면 매니저 Run에 메일, 언제나 1(에이전트를 띄우지 않는다), 같은 알림은 30분에 한 번
: > "$W/sw/calls.log"; rm -f orchestration/state/quota_cache.json orchestration/state/quota_watch.json
check "watch precheck: 할 일 없으면 1 · 메일 없음" bash -c "! FAKE_SW=free $SW python3 $T/quota_watch.py precheck && ! grep -q 'orchestration send' $W/sw/calls.log"
rm -f orchestration/state/quota_cache.json
check "watch precheck: 한도 정지면 매니저 Run에 알림 · 그래도 1" bash -c "! $SW python3 $T/quota_watch.py precheck && grep -q 'orchestration send --to run:run_sw --type status --subject 예약 점검: 한도 정지·필터 중단 1' $W/sw/calls.log"
check "watch precheck: 같은 내용은 다시 알리지 않음" bash -c "! $SW python3 $T/quota_watch.py precheck && test \$(grep -c 'orchestration send --to run:run_sw' $W/sw/calls.log) -eq 1"
o=$($SW python3 $T/quota_watch.py install --every 10 2>&1)
check "watch install: Orca 예약 실행(10분, 사전 확인 절대 경로) 등록·기록" bash -c 'grep -q "등록: .* (auto-1)" <<<"$0" && grep -q -- "automations create .*--trigger \*/10 \* \* \* \* .*--precheck cd .* && ORCA_CLI_COMMAND=.* orchestration/tools/quota_watch.py precheck" "$1/calls.log" && grep -q auto-1 orchestration/state/quota_watch.json' "$o" "$W/sw"
o=$($SW python3 $T/quota_watch.py install 2>&1); check "watch install: 이미 있으면 다시 만들지 않음" has "$o" "이미 등록됨"
: > "$W/sw/calls.log"
FAKE_SW_RUNS=completed $SW python3 $T/quota_watch.py precheck; rc=$?
check "watch 안전장치: 예약 실행이 에이전트를 띄웠으면 스스로 끄고 알린다(사전 확인은 그래도 1)" bash -c 'test "$2" = 1 && grep -q "automations edit --id auto-1 --disabled" "$1/calls.log" && grep -q "subject 한도 점검 예약을 껐다" "$1/calls.log" && grep -q guard_disabled orchestration/state/quota_watch.json' "" "$W/sw" "$rc"
o=$($SW python3 $T/quota_watch.py remove 2>&1); check "watch remove" bash -c 'grep -q "지움: auto-1" <<<"$0" && ! grep -q auto-1 orchestration/state/quota_watch.json' "$o"
rm -f orchestration/state/quota_cache.json orchestration/state/quota_watch.json
rm -f orchestration/state/quota_cache.json
cp orchestration/models.yaml "$W/models.yaml.pre-sw"
python3 -c "
import yaml; p='orchestration/models.yaml'; d=yaml.safe_load(open(p)); d['routing'].setdefault('quota',{})['on_exhausted']='wait'; yaml.safe_dump(d,open(p,'w'),allow_unicode=True,sort_keys=False)"
o=$($SW python3 $T/quota_switch.py 2>&1)
check "설정 on_exhausted: wait면 대기" has "$o" "on_exhausted: wait"
cp "$W/models.yaml.pre-sw" orchestration/models.yaml
check "넘겨받을 여유: 남은 % · 소모 속도 · 막 열린 창은 속도를 믿지 않음" python3 -c "
import sys,time; sys.path.insert(0,'$T'); import routing
ag={'codex':{}}; pol={'routing':{}}; now=time.time(); m={'agent':'codex'}
q=lambda used,left_min: {'codex':{'status':'ok','windows':{'session':{'used':used,'minutes':300,'resets':now+left_min*60}},'groups':{}}}
ok,why=routing.headroom(m,ag,q(80,100),pol,120); assert not ok and '남은 20%' in why, why
ok,why=routing.headroom(m,ag,q(60,200),pol,120); assert not ok and '다시 찬다' in why, why
ok,why=routing.headroom(m,ag,q(20,200),pol,120); assert ok, why
ok,why=routing.headroom(m,ag,q(50,290),pol,120); assert ok, why
ok,why=routing.headroom(m,ag,q(60,200),pol,30); assert ok, why"
rm -f orchestration/state/quota_cache.json
o=$(FAKE_SW=tight $SW python3 $T/quota_switch.py 2>&1)
check "넘겨받을 구독도 곧 다시 차면 전환하지 않고 대기" bash -c 'grep -q "^\[대기\] X-SW .*넘겨받을 구독도 곧 다시 찬다: .*다시 찬다" <<<"$0" && ! grep -q "worker-stop" "$1/calls.log"' "$o" "$W/sw"
rm -f orchestration/state/quota_cache.json
o=$($SW python3 $T/quota_switch.py 2>&1)
check "전환: 멈춤 → 보존 → 해제 → 같은 워크트리에서 다른 구독으로 이어받기" bash -c 'l=$(cat "$1/calls.log"); grep -q "worker-stop --dispatch ctx_5a1" <<<"$l" && grep -q "worker-release --dispatch ctx_5a1" <<<"$l" && grep -q -- "worker-start .*--worktree path:$2" <<<"$l" && ! grep -q -- "worker-start .*--agent codex" <<<"$l" && grep -q "보존: wip/X-SW-quota-" <<<"$0"' "$o" "$W/sw" "$W/wt-x-sw"
wip=$(git for-each-ref --format='%(refname:short)' 'refs/heads/wip/X-SW-quota-*' | head -1)
check "보존 브랜치에 커밋하지 않은 변경까지 · 작업 브랜치·작업 트리는 그대로" bash -c 'test -n "$0" && git show "$0:api/sw/wip.txt" | grep -qx b && test "$(git -C "$1" rev-parse HEAD)" = "$2" && test -f "$1/api/sw/wip.txt" && git -C "$1" status --porcelain | grep -q "wip.txt"' "$wip" "$W/wt-x-sw" "$swhead"
check "기록: dispatch(handoff-h1)·quota_switch.tsv·이어받기 명세·status는 running 그대로" bash -c 'tail -1 orchestration/state/dispatch.tsv | grep -q "^X-SW handoff-h1	task_abc777	ctx_def777" && grep -q "	X-SW	ctx_5a1	codex	codex-sol	" orchestration/state/quota_switch.tsv && grep -q "## 이어받기" orchestration/specs/X-SW.handoff-h1.md && grep -q "$0" orchestration/specs/X-SW.handoff-h1.md && grep -A9 "id: X-SW" orchestration/tasks.yaml | grep -q "status: running"' "$wip"
check "실적: 한도로 멈춘 투입은 셈에서 뺀다 · handoff는 새 투입처럼" python3 -c "
import sys; sys.path.insert(0,'$T'); import routing, kitlib
cfg=kitlib.load_config('.'); _,tasks=kitlib.all_tasks('.',cfg); cat,_=routing.load('.')
rows=routing.read_dispatch('.',set(tasks)); sk=routing.read_switches('.')
assert 'ctx_5a1' in sk, sk
assert [r['mode'] for r in rows if r['did']=='ctx_def777']==['handoff']
tasks['X-SW']['status']='merged'
o={x['did']:x['score'] for x in routing.outcomes(rows,tasks,[],cat['models'],skip=sk) if x['kit_id']=='X-SW'}
assert 'ctx_5a1' not in o and o.get('ctx_def777')==1.0, o"
# 검토 워커: 남은 후보가 구현한 구독뿐이면 검토 독립성 때문에 대기
printf 'X-SW-RV\ttask_5a2\tctx_5a2\tx-sw-rv\t2026-01-02 12:00:00\tcodex\tgpt-6.2-sol\tmedium\tcodex-sol\t3.0\t\n' >> orchestration/state/dispatch.tsv
impl=$(tail -2 orchestration/state/dispatch.tsv | head -1 | cut -f6)
sed -i "s/^  agents: .*/  agents: [codex, $impl]/" orchestration/models.yaml
: > "$W/sw/calls.log"
o=$(FAKE_SW_DID=ctx_5a2 FAKE_SW_TITLE=X-SW-RV $SW python3 $T/quota_switch.py 2>&1)
check "검토 워커: 남은 후보가 구현한 구독이면 대기(검토 독립성)" bash -c 'grep -q "^\[대기\] X-SW .*검토 독립성" <<<"$0" && ! grep -q "worker-stop" "$1/calls.log"' "$o" "$W/sw"
# 검토 워커 이어받기: kit.yaml review.agent가 한도 찬 구독(codex)으로 고정돼 있어도 고른 구독으로 넘긴다
cp "$W/models.yaml.pre-sw" orchestration/models.yaml
python3 -c "
import yaml; p='orchestration/kit.yaml'; d=yaml.safe_load(open(p)); d['review']={'agent':'codex','model':'gpt-6.2-sol','effort':''}; yaml.safe_dump(d,open(p,'w'),allow_unicode=True,sort_keys=False)"
: > "$W/sw/calls.log"; rm -f orchestration/state/quota_cache.json
o=$(FAKE_SW_DID=ctx_5a2 FAKE_SW_TITLE=X-SW-RV $SW python3 $T/quota_switch.py 2>&1)
check "검토 이어받기: review.agent 고정(codex)보다 고른 구독 · 구현 구독도 피함" bash -c 'grep -q "^\[전환\] X-SW" <<<"$0" && l=$(grep "worker-start" "$1/calls.log"); test -n "$l" && ! grep -q -- "--agent codex" <<<"$l" && ! grep -q -- "--agent $2 " <<<"$l" && tail -1 orchestration/state/dispatch.tsv | grep -q "^X-SW-RV-h2	"' "$o" "$W/sw" "$impl"
# 커밋 없음: 마지막 커밋과 이 투입 시작 중 늦은 쪽으로 잰다(보완 워커가 앞 차수 브랜치를 쓰면 앞 차수 커밋이 마지막 커밋)
check "커밋 없음: 투입 28분째 워커는 앞 차수 커밋(61분 전)으로 오탐하지 않음" python3 -c "
import sys,time; sys.path.insert(0,'$T'); import status
now=time.time(); w=dict(dispatch='d1',kit_id='X',title='X',attention=[],last_commit=now-61*60,started=now-28*60)
s={'orca':{'workers':[w],'mail':{},'error':None},'running':[],'ready':[],'gates_ready':[]}
assert not any('커밋 없음' in l for l in status.stall_lines(s,60)), status.stall_lines(s,60)
w['started']=now-70*60; assert any('커밋 없음' in l and '투입' in l for l in status.stall_lines(s,60))
assert abs(status.started_ts('2026-01-02 10:00:00')-time.mktime((2026,1,2,10,0,0,0,0,-1)))<1 and status.started_ts('x') is None"
# 필터 중단: 워커 마지막 응답이 안전 필터·이용 정책 차단 → 별도 줄, 같은 절차로 이어받기, 그 모델은 이 작업 후보에서 뺀다
printf 'X-SW\ttask_5a3\tctx_5a3\tx-sw\t2026-01-02 13:00:00\tclaude\tsonnet\thigh\tclaude-sonnet\t3.0\t\n' >> orchestration/state/dispatch.tsv
$M cooldown --clear claude >/dev/null
export FAKE_SW_DID=ctx_5a3 FAKE_SW_TITLE=X-SW
rm -f orchestration/state/quota_cache.json
o=$(FAKE_SW=free FAKE_SW_READ=toolonly $SW python3 $T/status.py --stall 2>&1)
check "필터 중단: 도구 결과 속 문구는 오탐하지 않음" bash -c '! grep -q "필터 중단" <<<"$0"' "$o"
o=$(FAKE_SW=free FAKE_SW_READ=filter $SW python3 $T/status.py --stall 2>&1)
check "필터 중단: 정지 점검이 별도 줄로 알림(증거 문구)" bash -c 'grep -q "필터 중단 X-SW (ctx_5a3): \"API Error: .*Usage Policy" <<<"$0" && ! grep -q "한도 정지 X-SW (ctx_5a3)" <<<"$0"' "$o"
: > "$W/sw/calls.log"
o=$(FAKE_SW=free FAKE_SW_READ=filter $SW python3 $T/quota_switch.py 2>&1)
check "필터 중단: 같은 절차로 이어받기 · 그 모델은 이 작업 후보에서 뺌 · 기록 kind=filter" bash -c 'grep -q "^\[전환\] X-SW (ctx_5a3, claude-sonnet): 필터 중단" <<<"$0" && grep -q "worker-stop --dispatch ctx_5a3" "$1/calls.log" && grep -q "worker-start" "$1/calls.log" && tail -1 orchestration/state/quota_switch.tsv | grep -q "	ctx_5a3	claude	claude-sonnet	안전 필터: .*	filter$" && ! grep -q -- "worker-start .*--model sonnet" "$1/calls.log" && grep -q "같은 입력·표현을 그대로 되풀이하지 말고" orchestration/specs/X-SW.handoff-h*.md' "$o" "$W/sw"
check "필터 중단 모델은 그 작업의 재투입 후보에서 빠진다(다른 작업은 그대로)" python3 -c "
import sys; sys.path.insert(0,'$T'); import routing, kitlib
cfg=kitlib.load_config('.'); _,t=kitlib.all_tasks('.',cfg)
assert routing.read_filter_blocks('.').get('X-SW')=={'claude-sonnet'}
d=routing.route('.',cfg,'X-SW',t,'new',quota={}); assert d['excluded'].get('claude-sonnet')=='이 작업에서 안전 필터로 끊긴 모델', d['excluded']
d=routing.route('.',cfg,'X-DOC',t,'new',quota={}); assert 'claude-sonnet' not in d['excluded'] or '필터' not in d['excluded']['claude-sonnet']"
# codex 안전 필터는 응답 블록이 아니라 화면에만 찍힌다(현장): 화면 끝·escalation 메일의 Last output에서, 고정 안내 줄과 줄 전체가 맞을 때만
check "화면 안내 줄: 굽은·곧은 따옴표, 아이콘 떼고 줄 전체 · 문장 속 인용은 무시" python3 -c "
import sys; sys.path.insert(0,'$T'); import status
f=status.screen_match
assert f('ⓘ This content can’t be shown') == 'This content can’t be shown'
assert f(\"  This content can't be shown\") and f('  We take extra care with some cybersecurity requests. If you’re doing authorized security work, apply')
assert f('문서 3.2절: 차단되면 화면에 This content can’t be shown이 뜬다') is None and f('see: This content can’t be shown here') is None
assert f('API Error: Claude Code is unable to respond to this request, which appears to violate our Usage Policy (https://x)')"
printf 'X-SW\ttask_5a5\tctx_5a5\tx-sw\t2026-12-30 10:00:00\tcodex\tgpt-6.2-sol\thigh\tcodex-sol\t3.0\t\n' >> orchestration/state/dispatch.tsv
rm -f orchestration/state/quota_cache.json
o=$(FAKE_SW=free FAKE_SW_DID=ctx_5a5 FAKE_SW_SCREEN=filter $SW python3 $T/status.py --stall 2>&1)
check "필터 중단(화면): 응답 블록에 없어도 터미널 화면 끝에서 잡는다" has "$o" "필터 중단 X-SW (ctx_5a5): \"This content can’t be shown\" (화면)"
printf 'X-SW\ttask_5a4\tctx_5a4\tx-sw\t2026-12-31 10:00:00\tcodex\tgpt-6.2-sol\thigh\tcodex-sol\t3.0\t\n' >> orchestration/state/dispatch.tsv
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_MAIL=noise FAKE_SW_MAIL_DID=ctx_5a4 $SW python3 $T/status.py --stall 2>&1)
check "메일 Last output: 문서 속 인용은 필터 중단이 아니라 오류 중단(확인 필요)" bash -c '! grep -q "필터 중단" <<<"$0" && grep -q "오류 중단 X-SW (ctx_5a4): Orca escalation stopReason=errored" <<<"$0"' "$o"
: > "$W/sw/calls.log"
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_MAIL=filter FAKE_SW_MAIL_DID=ctx_5a4 $SW python3 $T/status.py --stall 2>&1)
check "필터 중단(메일): 멈춘 워커도 escalation의 Last output으로 잡는다" has "$o" "필터 중단 X-SW (ctx_5a4): \"This content can’t be shown\" (메일 Last output)"
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_MAIL=filter FAKE_SW_MAIL_DID=ctx_5a4 $SW python3 $T/quota_switch.py 2>&1)
check "필터 중단(메일): 이미 멈춘 워커는 stop 생략 · 이어받기 · codex-sol은 이 작업 후보에서 뺌" bash -c 'grep -q "^\[전환\] X-SW (ctx_5a4, codex-sol): 필터 중단" <<<"$0" && grep -q "worker-stop 생략" <<<"$0" && ! grep -q "worker-stop --dispatch ctx_5a4" "$1/calls.log" && grep -q "worker-start" "$1/calls.log" && ! grep -q -- "worker-start .*--model gpt-6.2-sol" "$1/calls.log" && tail -1 orchestration/state/quota_switch.tsv | grep -q "	ctx_5a4	codex	codex-sol	.*	filter$"' "$o" "$W/sw"
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_MAIL=filter FAKE_SW_MAIL_DID=ctx_5a4 $SW python3 $T/status.py --stall 2>&1)
check "넘긴 dispatch의 메일은 다시 잡지 않는다" bash -c '! grep -q "ctx_5a4" <<<"$0"' "$o"
check "메일: payload가 JSON 문자열이어도 · errored는 제목·본문(ended its turn on an error)으로도" python3 -c "
import sys; sys.path.insert(0,'$T'); import kitlib, status
assert kitlib.msg_payload({'payload': '{\"dispatchId\": \"d\", \"stopReason\": \"errored\"}'}) == {'dispatchId': 'd', 'stopReason': 'errored'}
assert kitlib.msg_payload({'payload': 'not json'}) == {} and kitlib.msg_payload({}) == {}
assert status.errored({'subject': 'Worker stopped on an error without reporting', 'payload': {'dispatchId': 'd'}})
assert status.errored({'body': 'Dispatch d ended its turn on an error and did not send worker_done.'})
assert not status.errored({'subject': 'done', 'body': 'ok', 'payload': '{}'})"
printf 'X-SW\ttask_5a8\tctx_5a8\tx-sw\t2026-12-31 12:00:00\tantigravity\tgemini-4-neon-medium\t\tagy-gemini-neon\t3.0\t\n' >> orchestration/state/dispatch.tsv
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_MAIL=quota FAKE_SW_MAIL_DID=ctx_5a8 $SW python3 $T/status.py --stall 2>&1)
check "메일: 한도로 멈춘 워커(quota reached·Resets in)는 필터 중단도 오류 중단도 아니다" bash -c '! grep -q "필터 중단\|오류 중단" <<<"$0"' "$o"
printf 'X-SW\ttask_5a7\tctx_5a7\tx-sw\t2027-01-01 10:00:00\tcodex\tgpt-6.2-sol\thigh\tcodex-sol\t3.0\t\n' >> orchestration/state/dispatch.tsv
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_MAIL=errored FAKE_SW_MAIL_DID=ctx_5a7 $SW python3 $T/status.py --stall 2>&1)
check "오류 중단: errored인데 한도도 차단 문구도 아니면 확인하라고 알림" has "$o" "오류 중단 X-SW (ctx_5a7)"
# 슬롯 대기: 다른 워크트리가 전체 시험 슬롯을 잡고 있고 이 워커는 줄에 서 있다 → '커밋 없음'이 아니라 '슬롯 대기'
KIT_SLOT_POLL=1 KIT_SLOTS_test=1 $T/slot.sh test -- sleep 5 2>/dev/null & sh1=$!
sleep 0.5
(cd "$W/wt-x-sw" && KIT_SLOT_POLL=1 KIT_SLOTS_test=1 $T/slot.sh test -- true) 2>/dev/null & sh2=$!
sleep 1
o=$(KIT_SLOTS_test=1 FAKE_SW=free FAKE_SW_DID=ctx_9z9 $SW python3 $T/status.py --stall --stale-min 0 2>&1)
check "정지 점검: 줄에 선 워커는 '슬롯 대기'(커밋 없음 아님)" bash -c 'grep -q "슬롯 대기 X-SW (ctx_9z9): .test. 0분째 · 1번째 · 사용 중 1/1" <<<"$0" && ! grep -q "커밋 없음 X-SW (ctx_9z9)" <<<"$0"' "$o"
check "정지 점검: 대기 ≥ 슬롯 수면 '슬롯 줄'(잡은 쪽·대기 수)" bash -c 'grep -q "슬롯 줄 .test.: 사용 1/1 (.* 0분) · 대기 1 · 최장 0분" <<<"$0"' "$o"
o=$(KIT_SLOTS_test=1 FAKE_SW=free FAKE_SW_DID=ctx_9z9 $SW python3 $T/status.py 2>&1)
check "현황: 워커 줄에 슬롯 대기, 슬롯 요약 줄" bash -c 'grep -q "^- X-SW ctx_9z9 .*· 슬롯 대기 .test. 0분째" <<<"$0" && grep -q "^## 슬롯 .test.: 사용 1/1 · 대기 1" <<<"$0"' "$o"
wait $sh2; wait $sh1
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 $SW python3 $T/status.py --stall 2>&1)
check "슬롯이 비면 표시가 사라진다(대기·잡음 표시 파일 정리)" bash -c '! grep -q "슬롯" <<<"$0" && ! ls "$(git rev-parse --git-common-dir)"/orca-kit/slots/ | grep -q "holder\|wait"' "$o"
# 확인 창 대기(빈 변수 의심 rm): 고쳐 쓸 꼴과 deny 명령 · 예약 점검은 승인 ID를 알면 스스로 거절(한 번만)
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_WAIT=rm FAKE_SW_APPROVAL=apr_1 $SW python3 $T/status.py --stall 2>&1)
check "정지 점검: 확인 창 대기 · 빈 변수 의심 rm → 고쳐 쓸 꼴 · deny 명령(승인 ID)" bash -c 'grep -q "확인 창 대기 X-SW (ctx_9z9): prompt-text" <<<"$0" && grep -q "{EV:?}" <<<"$0" && grep -q "orca orchestration deny --dispatch ctx_9z9 --prompt apr_1" <<<"$0"' "$o"
: > "$W/sw/calls.log"; rm -f orchestration/state/quota_watch.json orchestration/state/quota_cache.json
FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_WAIT=rm FAKE_SW_APPROVAL=apr_1 $SW python3 $T/quota_watch.py precheck; rc=$?
check "예약 점검: 빈 변수 의심 rm 확인 창을 거절하고 알린다(그래도 1)" bash -c 'test "$1" = 1 && grep -q "orchestration deny --dispatch ctx_9z9 --prompt apr_1 --reason 빈 변수 의심 rm" "$0/calls.log" && grep -q "subject 예약 점검: 멈춤 1 · 자동 거절 1" "$0/calls.log"' "$W/sw" "$rc"
FAKE_SW=free FAKE_SW_DID=ctx_9z9 FAKE_SW_WAIT=rm FAKE_SW_APPROVAL=apr_1 $SW python3 $T/quota_watch.py precheck
check "예약 점검: 같은 승인은 다시 거절하지 않음" test "$(grep -c '^orchestration deny' "$W/sw/calls.log")" -eq 1
rm -f orchestration/state/quota_watch.json
if [ -d /proc/self ]; then
  cp orchestration/kit.yaml "$W/kit.yaml.pre-lc"; sed -i 's/^  long_cmd_min: 30\b.*/  long_cmd_min: 0/' orchestration/kit.yaml
  (cd "$W/wt-x-sw" && exec bash -c 'sleep 60; :' fakepytest) & lc=$!
  sleep 0.5
  o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 $SW python3 $T/status.py --stall 2>&1)
  check "정지 점검: 워커 워크트리에서 시간 제한 없이 도는 시험 → 긴 명령(pid·kill -ABRT)" bash -c 'grep -q "긴 명령 X-SW (ctx_9z9): .bash -c sleep 60; : fakepytest. 0분째, 시간 제한 없음 (pid $1)" <<<"$0" && grep -q "kill -ABRT $1" <<<"$0"' "$o" "$lc"
  : > "$W/sw/calls.log"; rm -f orchestration/state/quota_cache.json
  FAKE_SW=free FAKE_SW_DID=ctx_9z9 $SW python3 $T/quota_watch.py precheck
  check "예약 점검: 긴 명령도 매니저를 깨운다" bash -c 'grep -q "subject 예약 점검: 멈춤 1" "$0/calls.log" && grep -q "긴 명령 X-SW" "$0/calls.log"' "$W/sw"
  kill $lc 2>/dev/null; wait $lc 2>/dev/null; rm -f orchestration/state/quota_watch.json
  (cd "$W/wt-x-sw" && exec "$T/run_logged.sh" 60 "$W/rl/lc.log" -- bash -c 'sleep 60; :' fakepytest) >/dev/null & rl=$!
  sleep 1
  o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 $SW python3 $T/status.py 2>&1)
  check "run_logged로 띄운 시험은 '긴 명령' 경보가 아니라 제한과 함께 표시" bash -c '! grep -q "^- 긴 명령" <<<"$0" && grep -q "^- X-SW ctx_9z9 .*· 긴 명령 0분째(제한 1분)" <<<"$0"' "$o"
  kill -TERM $rl; wait $rl 2>/dev/null
  check "run_logged를 끄면 묶음도 끝난다" bash -c '! pgrep -f "[s]leep 60; : fakepytest" >/dev/null'
  cp "$W/kit.yaml.pre-lc" orchestration/kit.yaml
fi
# 문맥 크기: 진행 중 워커의 세션 기록(마지막 호출의 캐시 읽기+쓰기+입력) · usage.py 작업별 표
sl=$(python3 -c "import re,sys,os;print(re.sub(r'[^A-Za-z0-9]','-',os.path.realpath(sys.argv[1])))" "$W/wt-x-sw")
mkdir -p "$W/cc/projects/$sl"; now_iso=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
for i in 1 2; do printf '{"type":"assistant","timestamp":"%s","message":{"id":"m%s","model":"claude-opus-5-5","usage":{"input_tokens":10,"cache_creation_input_tokens":1000,"cache_read_input_tokens":%s},"content":[{"type":"tool_use","name":"Bash","input":{"command":"%s"}}]}}\n' "$now_iso" $i $((i * 200000)) "$([ $i = 2 ] && echo 'scripts/dev/test.sh api' || echo ls)"; done > "$W/cc/projects/$sl/s1.jsonl"
export KIT_TRANSCRIPT_DIRS="$W/cc" KIT_CODEX_SESSIONS="$W/codex-none"
o=$(FAKE_SW=free FAKE_SW_DID=ctx_9z9 $SW python3 $T/status.py 2>&1)
check "현황: 워커 줄에 지금 문맥·호출 수, 기준(300k) 넘으면 '문맥 큼'" bash -c 'grep -q "^- X-SW ctx_9z9 .*· 문맥 401k·호출 2 (기준 넘음)" <<<"$0" && grep -q "문맥 큼 X-SW (ctx_9z9): 지금 401k · 호출 2 (기준 300k)" <<<"$0"' "$o"
o=$(python3 $T/usage.py --since 1h 2>&1)
check "usage.py: 작업별 호출 수·호출당 문맥·합계·시험 관련 비중" bash -c 'grep -q "| X-SW | claude s1 | claude-opus-5-5 | 2 | 301k | 401k | 602k | 67% |" <<<"$0"' "$o"
unset KIT_TRANSCRIPT_DIRS KIT_CODEX_SESSIONS
# 계정별 한도: Orca는 고른 계정만 준다 → 다른 계정은 마지막으로 읽은 값, 이 세션의 계정 표시
python3 -c '
import json, time
json.dump({"claude": {"active": "aaaaaaaa-1", "accounts": {"aaaaaaaa-1": {"windows": {"weekly": {"used": 77.0, "minutes": 10080, "resets": 0}}, "seen": time.time() - 3600}}}},
          open("orchestration/state/quota_accounts.json", "w"))'
rm -f orchestration/state/quota_cache.json
o=$(CLAUDECODE=1 CLAUDE_CONFIG_DIR=/x/claude-profiles/aaaaaaaa-1/home FAKE_SW=free FAKE_SW_ACCTS=1 $SW python3 $T/models.py quota 2>&1)
check "models.py quota: 계정별 — 고른 계정은 지금 값, 다른 계정은 마지막 값과 나이, 이 세션 표시" bash -c 'grep -q "^- claude aaaaaaaa: weekly 77% (60분 전 값) · 이 세션$" <<<"$0" && grep -q "^- claude bbbbbbbb · 선택됨(새 워커가 쓴다): weekly 10%" <<<"$0"' "$o"
rm -f orchestration/state/quota_accounts.json orchestration/state/quota_cache.json
# 정해진 전이(advance.py): 구현 통과 → 검토 투입 → 검토 차단 → 실적·보완 투입 → 보완 통과 → 2회차 검토 → 같은 계열 차단 → hold·범위 결정
cat >> orchestration/tasks.yaml <<'YAML'
- id: X-AD
  title: 전이 시험 작업
  layer: L2
  module: api
  branch: req/X-AD
  base: int/api
  deps: []
  write_scope: [api/ad/]
  acceptance: "api/ad/ 아래 기능을 만든다"
  status: running
YAML
git add -A >/dev/null && git commit -qm "[L0] X-AD" && git branch -f req/X-AD int/api
printf 'X-AD\ttask_ad\tctx_ad1\tx-ad\t%s\tclaude\tclaude-opus-5-5\thigh\t\t\t\n' "$(date -d '-1 hour' '+%F %T')" >> orchestration/state/dispatch.tsv
done_msg() {   # done_msg <메일 ID> <dispatch> <본문 첫 줄> [보고서 경로] — worker_done 한 통을 메일함에 더한다(Orca 실제 꼴: payload는 JSON 문자열)
  python3 - "$W/sw/done.json" "$@" <<'PY'
import json, os, sys, time
p, mid, did, first = sys.argv[1:5]; rep = sys.argv[5] if len(sys.argv) > 5 else ""
ms = json.load(open(p)) if os.path.exists(p) else []
pl = {"taskId": "task_x", "dispatchId": did, "outcome": "succeeded"}
if rep: pl["reportPath"] = rep
ms.append({"id": mid, "type": "worker_done", "handled": True, "createdAt": int(time.time() * 1000), "subject": "done",
           "body": first + "\n증적: docs/06_evidence/X-AD/report.md · 남은 이슈 없음", "payload": json.dumps(pl)})
json.dump(ms, open(p, "w"), ensure_ascii=False)
PY
}
AD="env FAKE_SW=free FAKE_SW_UNIQ=1"
done_msg msg_d1 ctx_ad1 "결과: 통과"
o=$($AD $SW python3 $T/advance.py --dry-run 2>&1)
check "advance --dry-run: 할 일만(상태·투입 그대로)" bash -c 'grep -q "^\[자동\] X-AD: 구현 통과 → status review · 검토 투입" <<<"$0" && grep -A12 "id: X-AD" orchestration/tasks.yaml | grep -q "status: running" && test ! -f orchestration/state/advance.json' "$o"
o=$($AD $SW python3 $T/advance.py 2>&1)
check "advance: 구현 '결과: 통과' → status review · 검토 투입(dispatch X-AD-RV) · 이력" bash -c 'grep -q "^\[자동\] X-AD: 구현 통과" <<<"$0" && grep -A12 "id: X-AD" orchestration/tasks.yaml | grep -q "status: review" && grep -q "^X-AD-RV	" orchestration/state/dispatch.tsv && grep -q "\"kind\": \"advance:자동\", \"task\": \"X-AD\"" orchestration/state/events.jsonl && grep -q "\"kind\": \"dispatch\", \"task\": \"X-AD\", \"mode\": \"review\"" orchestration/state/events.jsonl' "$o"
o=$($AD $SW python3 $T/advance.py 2>&1); check "advance: 처리한 메일은 다시 처리하지 않음" has "$o" "새 worker_done 없음"
rv1=$(awk -F'\t' '$1=="X-AD-RV"{d=$3} END{print d}' orchestration/state/dispatch.tsv)
mkdir -p "$W/wt-x-sw/docs/06_evidence/X-AD-RV"; printf '# 검토 X-AD\n- H1 영역분리: api/ad가 다른 모듈 상태를 바꾼다(재현: pytest tests/ad)\n' > "$W/wt-x-sw/docs/06_evidence/X-AD-RV/report.md"
done_msg msg_d2 "$rv1" "판정: 차단 · H 1 · M 2 · 계열: 영역분리" docs/06_evidence/X-AD-RV/report.md
o=$($AD FAKE_SW_DID=$rv1 $SW python3 $T/advance.py 2>&1)
check "advance: 검토 '판정: 차단 · 계열' → 실적 major·계열 기록 · 보고서를 지적으로 보완 투입 r1 · status running" bash -c 'grep -q "^\[자동\] X-AD: 검토 차단(계열 영역분리, H 1) → 실적 major 기록 · 보완 투입 --suffix r1" <<<"$0" && grep -q "	X-AD	.*영역분리$" orchestration/state/route_feedback.tsv && grep -q "H1 영역분리" orchestration/specs/X-AD.retry-r1.md && grep -A12 "id: X-AD" orchestration/tasks.yaml | grep -q "status: running"' "$o"
rt1=$(awk -F'\t' '$1=="X-AD retry-r1"{d=$3} END{print d}' orchestration/state/dispatch.tsv)
done_msg msg_d3 "$rt1" "결과: 통과"
o=$($AD $SW python3 $T/advance.py 2>&1)
check "advance: 보완 통과 → 2회차 검토(--suffix r2)" bash -c 'grep -q "^\[자동\] X-AD: 구현 통과 → status review · 검토 투입 2회차" <<<"$0" && grep -q "^X-AD-RV-r2	" orchestration/state/dispatch.tsv' "$o"
rv2=$(awk -F'\t' '$1=="X-AD-RV-r2"{d=$3} END{print d}' orchestration/state/dispatch.tsv)
done_msg msg_d4 "$rv2" "판정: 차단 · H 2 · M 0 · 계열: 영역분리"
o=$($AD $SW python3 $T/advance.py 2>&1)
check "advance: 같은 계열 차단 2회 연속 → 보완을 내지 않고 hold · [판단] 범위 결정" bash -c 'grep -q "^\[판단\] X-AD: 범위 결정 필요: 계열 영역분리 2회 연속" <<<"$0" && grep -A14 "id: X-AD" orchestration/tasks.yaml | grep -q "hold: \"범위 결정 필요" && ! grep -q "^X-AD retry-r2" orchestration/state/dispatch.tsv && python3 -c "import yaml;yaml.safe_load(open(\"orchestration/tasks.yaml\"))"' "$o"
done_msg msg_d5 ctx_nowhere "결과: 통과"
done_msg msg_d6 "$rv2" "검토를 마쳤다. 자세한 것은 보고서"
o=$($AD $SW python3 $T/advance.py 2>&1)
check "advance: 모르는 투입·형식 없는 보고는 [판단]으로(아무것도 바꾸지 않음)" bash -c 'grep -q "^\[판단\] ctx_nowhere: dispatch.tsv에 없는" <<<"$0" && grep -q "^\[건너뜀\] X-AD\|^\[판단\] X-AD" <<<"$0"' "$o"
rm -f "$W/sw/done.json"
unset FAKE_SW_DID FAKE_SW_TITLE
$M cooldown claude --min 1440 --reason 'Orca에서 꺼진 에이전트' >/dev/null   # 위에서 푼 쿨다운을 되돌린다(뒤 대시보드 시험이 쓴다)
cp "$W/models.yaml.pre-sw" orchestration/models.yaml; cp "$W/kit.yaml.pre-sw" orchestration/kit.yaml
rm -f orchestration/state/quota_cache.json; unset FAKE_SW_DIR FAKE_SW_WT
git branch req/X-CL int/api && git worktree add -q "$W/x-cl" req/X-CL && git branch wip/X-CL-quota-20260101-000000 req/X-CL
o=$(ORCA_CLI_COMMAND="$W/fake-orca-sw" FAKE_SW_DIR="$W/sw" FAKE_SW_WT="$W/wt-x-sw" $T/cleanup_worker.sh --dry-run x-cl req/X-CL int/api 2>&1)
check "cleanup: 머지된 작업의 한도 전환 보존 브랜치도 지운다" has "$o" "git branch -D wip/X-CL-quota-20260101-000000"
# 범위 관리(교훈 L-22): 보장하는 것/범위 밖 닫힌 목록 · 핵심 약속 점검표 · 같은 계열 차단 2회면 범위 결정 · 검토 대체(같은 구독 다른 모델)
cat >> orchestration/tasks.yaml <<'YAML'
- id: X-GU
  title: 가림 처리
  layer: L2
  module: api
  branch: req/X-GU
  base: int/api
  deps: []
  write_scope: [api/mask/]
  acceptance: "로그의 주민번호 꼴을 가린다"
  guarantees: ["로그·응답 본문 두 곳에서 주민번호 꼴(6-7자리) 문자열을 *로 가린다", "언제나 원문이 새지 않는다"]
  out_of_scope: ["첨부 파일 속 원문", "외부 시스템 로그"]
  status: todo
- id: X-NG
  title: 보장 목록 없는 작업
  layer: L2
  module: api
  branch: req/X-NG
  base: int/api
  deps: []
  write_scope: [api/ng/]
  acceptance: "무언가 한다"
  status: todo
YAML
o=$(python3 $T/validate_dag.py 2>&1)
check "DAG 검증: guarantees 없음 경고 · 열린 약속('언제나') 경고" bash -c 'grep -q "X-NG: guarantees(보장하는 것) 없음" <<<"$0" && grep -q "X-GU: 열린 약속 .언제나 원문이 새지 않는다" <<<"$0" && ! grep -q "X-GU: guarantees" <<<"$0"' "$o"
printf '# 점검표\n설명 줄\n- 이 줄은 항목 절 밖이라 안 붙는다\n\n## 항목\n- [예] 예시는 안 붙는다\n- 영역 분리: 다른 영역 응답·로그에 안 나온다(교차 조회 시험)\n- CLI 차단: 비대화 실행에서 확인 창을 안 기다린다\n' > orchestration/checklist.md
python3 $T/render_spec.py X-GU >/dev/null; python3 $T/render_spec.py --review X-GU >/dev/null
check "명세: 보장/범위 밖 절과 점검표(항목 절만, 예시 제외)" bash -c 'f=orchestration/specs/X-GU.md; grep -q "## 보장하는 것 / 범위 밖 (닫힌 목록)" $f && grep -q "주민번호 꼴(6-7자리)" $f && grep -q "### 범위 밖" $f && grep -q "## 핵심 약속 점검표 (완료 보고 전에 스스로 확인)" $f && grep -q "영역 분리: 다른 영역" $f && ! grep -q "예시는 안 붙는다\|항목 절 밖" $f && grep -n "## 보장하는 것" $f | cut -d: -f1 | xargs -I{} test {} -lt $(grep -n "^## 종료" $f | cut -d: -f1)'
check "검토 명세: 보장 목록 안의 반례만 차단 · 점검표를 먼저 본다" bash -c 'f=orchestration/specs/X-GU.review.md; grep -q "머지를 막는 H는 \*\*아래 .보장하는 것. 항목을 깨는 실행 반례\*\*만" $f && grep -q "## 핵심 약속 점검표 (먼저 본다)" $f'
$M feedback X-GU major --category 영역분리 --note 'a' >/dev/null; $M feedback X-GU fail --category 영역분리 --note 'b' >/dev/null
o=$(python3 $T/status.py --stall --no-orca 2>&1)
check "같은 계열 차단 2회 연속 → 범위 결정 필요" has "$o" "범위 결정 필요 X-GU: '영역분리' 차단 2회 연속"
$M feedback X-GU major --category 승인무결성 >/dev/null
o=$(python3 $T/status.py --stall --no-orca 2>&1)
check "계열이 달라도 차단 3회 연속 → 검토 반복" bash -c 'grep -q "검토 반복 X-GU: 차단 3회 연속(계열 승인무결성, 영역분리)" <<<"$0" && ! grep -q "범위 결정 필요 X-GU" <<<"$0"' "$o"
$M feedback X-GU pass >/dev/null
o=$(python3 $T/status.py --stall --no-orca 2>&1)
check "통과 판정이 오면 연속이 끊긴다" bash -c '! grep -q "X-GU" <<<"$0"' "$o"
check "검토: 구현한 모델은 같은 구독 감점과 별도로 더 깎는다 · cross_review: model이면 구현 모델만 뺀다" python3 -c "
import sys; sys.path.insert(0,'$T'); import routing, kitlib
cat,pol=routing.load('.'); cat=routing.apply_bench(cat); m=cat['models']['claude-sonnet']
c={'difficulty':3.0,'tags':['review'],'critical':False}
base={'objective':'balanced','learned':{},'running':{},'usage':{},'failed':set(),'avoid_agents':['claude'],'avoid_keys':[]}
s1,_=routing.score('claude-sonnet',m,c,pol,base); s2,w=routing.score('claude-sonnet',m,c,pol,dict(base,avoid_keys=['claude-sonnet']))
assert s2==s1-3 and '구현한 모델과 같음' in w, (s1,s2,w)
cfg=kitlib.load_config('.'); _,t=kitlib.all_tasks('.',cfg)
d=routing.route('.',cfg,'X-GU',t,'review',quota={},exclude_keys={'codex-sol'})
assert d['excluded'].get('codex-sol','').startswith('검토 독립성'), d['excluded']"
rm -f orchestration/checklist.md
# 코드 검토 지적: pre-push가 설정을 못 읽으면 막는다 · cleanup은 워커 목록 페이지를 넘긴다 · launch는 ID를 JSON으로·쓰기 잠금·고아 Task 닫기 · 예약 점검 안전장치
HG="$W/hookrepo"; rm -rf "$HG"; git init -q -b main "$HG" && git -C "$HG" commit -q --allow-empty -m r
sha=$(git -C "$HG" rev-parse HEAD); HOOK="$PWD/$T/hooks/pre-push"
o=$(cd "$HG" && printf 'refs/heads/main %s refs/heads/main 0000000000000000000000000000000000000000\n' "$sha" | python3 "$HOOK" origin https://x.invalid/r.git 2>&1; echo "rc=$?")
check "pre-push: kit.yaml을 못 읽으면 막는다(이유 출력)" bash -c 'grep -q "설정을 읽지 못해 push를 막았다" <<<"$0" && grep -q "rc=1" <<<"$0"' "$o"
check "pre-push: KIT_HOOK_NO_CONFIG_OK=1이면 통과" bash -c "cd $HG && printf 'refs/heads/main $sha refs/heads/main 0000000000000000000000000000000000000000\n' | KIT_HOOK_NO_CONFIG_OK=1 python3 $HOOK origin https://x.invalid/r.git"
cat > "$W/fake-orca-pg.py" <<'PY'
import json, os, sys
a = " ".join(sys.argv[1:]); open(os.environ["FAKE_PG_LOG"], "a").write(a + "\n")
def out(o): print(json.dumps(o, indent=2)); sys.exit(0)
if "worker-list" in a:
    if "--cursor c2" in a:
        out({"ok": True, "result": {"workers": [{"dispatchId": "ctx_live99", "state": "running", "workspace": "ws::" + os.environ["FAKE_PG_WS"]}]}})
    out({"ok": True, "result": {"workers": [{"dispatchId": f"ctx_d{i}", "state": "succeeded", "workspace": f"ws::/tmp/other{i}"} for i in range(100)],
                                "nextCursor": "c2"}})
if "task-create" in a: out({"ok": True, "result": {"task": {"id": "task_Zx91"}, "note": "ctx_wrong1 task_wrong2"}})
if "worker-start" in a: print(json.dumps({"ok": False, "error": {"code": "agent_readiness", "message": "timeout"}, "failedStage": "agent_readiness"})); sys.exit(1)
if "task-update" in a: out({"ok": True, "result": {}})
print('{"ok":false}'); sys.exit(1)
PY
printf '#!/bin/sh\nexec python3 %s "$@"\n' "$W/fake-orca-pg.py" > "$W/fake-orca-pg"; chmod +x "$W/fake-orca-pg"
export FAKE_PG_LOG="$W/pg.log" FAKE_PG_WS="$W/x-cl"; : > "$W/pg.log"
o=$(ORCA_CLI_COMMAND="$W/fake-orca-pg" $T/cleanup_worker.sh --dry-run x-cl req/X-CL int/api 2>&1)
check "cleanup: 워커 목록 둘째 쪽의 끝나지 않은 디스패치도 본다(100개 넘는 Run)" bash -c 'grep -q "끝나지 않은 디스패치 ctx_live99" <<<"$0" && grep -q -- "--cursor c2" "$1"' "$o" "$W/pg.log"
: > "$W/pg.log"
o=$(ORCA_CLI_COMMAND="$W/fake-orca-pg" KIT_RUN=run_x KIT_NO_CLI_UPDATE=1 $T/launch.sh --force --agent claude X-DOC 2>&1)
check "launch: Task ID는 JSON(result.task.id) · worker-start 실패면 고아 Task를 취소로 닫는다" bash -c 'grep -q "worker-start 실패(task_Zx91)" <<<"$0" && grep -q "orchestration task-update --run run_x --id task_Zx91 --status failed --result cancelled" "$1" && grep -q "취소로 닫았다" <<<"$0"' "$o" "$W/pg.log"
check "쓰기 잠금: 같은 이름 잠금은 차례로 · 잠금 파일은 무시 목록" bash -c "python3 -c \"
import sys,threading,time; sys.path.insert(0,'$T'); import kitlib
order=[]
def w(n):
    with kitlib.locked('.'):
        order.append(n+'+'); time.sleep(0.2); order.append(n+'-')
ts=[threading.Thread(target=w,args=(x,)) for x in 'ab']; [t.start() for t in ts]; [t.join() for t in ts]
assert order[1].endswith('-') and order[3].endswith('-'), order\" && grep -q 'orchestration/state/.\*.lock' .gitignore"
unset FAKE_PG_LOG FAKE_PG_WS
# 워크트리 정리(worktree_sweep): 잃을 것이 없을 때만 지운다 — merged·변경 없음·진행 중 디스패치 없음·오래 쉼·고정 안 됨
cat >> orchestration/tasks.yaml <<'YAML'
- {id: X-WA, title: 정리 A, layer: L2, module: api, branch: req/X-WA, base: int/api, deps: [], write_scope: [api/wa/], acceptance: "a", guarantees: ["a"], status: merged}
- {id: X-WB, title: 정리 B, layer: L2, module: api, branch: req/X-WB, base: int/api, deps: [], write_scope: [api/wb/], acceptance: "b", guarantees: ["b"], status: merged}
- {id: X-WC, title: 정리 C, layer: L2, module: api, branch: req/X-WC, base: int/api, deps: [], write_scope: [api/wc/], acceptance: "c", guarantees: ["c"], status: running}
- {id: X-WF, title: 정리 F, layer: L2, module: api, branch: req/X-WF, base: int/api, deps: [], write_scope: [api/wf/], acceptance: "f", guarantees: ["f"], status: merged}
- {id: X-WM, title: 정리 M, layer: L2, module: api, branch: req/X-WM, base: int/api, deps: [], write_scope: [api/wm/], acceptance: "m", guarantees: ["m"], status: merged}
YAML
mkdir -p docs/06_evidence/X-WA-RV && echo r > docs/06_evidence/X-WA-RV/report.md
git add -A >/dev/null && git commit -qm "[L0] 정리 시험" && git branch -f int/api HEAD
SWP="$W/swp"; mkdir -p "$SWP"
for x in a b c d f m; do git worktree add -q -b "req/X-W${x^^}" "$SWP/x-w$x" int/api; done
git worktree add -q -b req/X-WA-RV "$SWP/x-wa-rv" main
git worktree add -q -b scratch-e "$SWP/scratch-e" main
echo dirty > "$SWP/x-wb/new.txt"
echo extra > "$SWP/x-wm/m.txt"; git -C "$SWP/x-wm" add -A; git -C "$SWP/x-wm" commit -qm "[X-WM] 머지 안 된 커밋"
cat > "$W/fake-orca-swp.py" <<'PY'
import json, os, sys, time
a = " ".join(sys.argv[1:]); open(os.environ["FAKE_SWP_LOG"], "a").write(a + "\n")
def out(o): print(json.dumps(o, indent=2)); sys.exit(0)
P, now = os.environ["FAKE_SWP"], time.time() * 1000
old = now - 5 * 3600 * 1000
def wt(n, **k): return dict({"path": f"{P}/{n}", "branch": "", "isMainWorktree": False, "isPinned": False, "childWorktreeIds": [], "lastActivityAt": old}, **k)
if a.startswith("worktree list"):
    out({"ok": True, "result": {"worktrees": [wt("x-wa"), wt("x-wb"), wt("x-wc"), wt("x-wd", isPinned=True), wt("x-wf", lastActivityAt=now - 60000),
                                              wt("x-wm"), wt("x-wa-rv", branch="refs/heads/req/X-WA-RV"), wt("scratch-e", branch="refs/heads/scratch-e"),
                                              {"path": os.environ["FAKE_SWP_MAIN"], "isMainWorktree": True}]}})
if "worker-list" in a:
    out({"ok": True, "result": {"workers": [
        {"dispatchId": "ctx_wa1", "state": "succeeded", "terminalState": "open", "workspace": "ws::" + P + "/x-wa"},
        {"dispatchId": "ctx_wc1", "state": "running", "terminalState": "open", "workspace": "ws::" + P + "/x-wc"}]}})
if a.startswith("worktree rm") or "worker-release" in a: out({"ok": True, "result": {}})
if "task-list" in a or "inbox" in a: out({"ok": True, "result": {"tasks": [], "messages": []}})
print('{"ok":false}'); sys.exit(1)
PY
printf '#!/bin/sh\nexec python3 %s "$@"\n' "$W/fake-orca-swp.py" > "$W/fake-orca-swp"; chmod +x "$W/fake-orca-swp"
cp orchestration/kit.yaml "$W/kit.yaml.pre-swp"; sed -i 's/^  work_run: .*/  work_run: "run_swp"/' orchestration/kit.yaml
export FAKE_SWP="$SWP" FAKE_SWP_LOG="$W/swp.log" FAKE_SWP_MAIN="$PWD"; : > "$W/swp.log"
WS="env ORCA_CLI_COMMAND=$W/fake-orca-swp"
o=$($WS python3 $T/worktree_sweep.py 2>&1)
check "정리 판정: merged·변경 없음 → 정리 · 검토 보고서가 main에 → 정리" bash -c 'grep -q "^- x-wa \[X-WA·new\]: 작업 X-WA merged" <<<"$0" && grep -q "^- x-wa-rv \[X-WA·review\]: 검토 보고서가 main에 들어왔다" <<<"$0"' "$o"
check "남겨 둠: 진행 중 디스패치 · 고정 · 최근 활동 · 확인 필요: merged인데 변경/머지 안 된 커밋 · 주인 없음(main에 있음)" bash -c 'grep -q "^- x-wc.*진행 중인 디스패치" <<<"$0" && grep -q "^- x-wd.*사용자가 고정함" <<<"$0" && grep -q "^- x-wf.*최근 활동" <<<"$0" && grep -q "^- x-wb.*커밋하지 않은 변경 1개" <<<"$0" && grep -q "^- x-wm.*merged인데 이 워크트리에 기준에 없는 커밋 1개" <<<"$0" && grep -q "^- scratch-e: 어느 작업 것인지 모른다 — HEAD가 main에 들어 있어 --orphans" <<<"$0"' "$o"
check "미리 보기는 아무것도 지우지 않는다" bash -c '! grep -q "worktree rm" "$0"' "$W/swp.log"
o=$($WS python3 $T/worktree_sweep.py --apply 2>&1)
check "--apply: '정리'만 지운다(끝난 워커 터미널 해제 → worktree rm --delete-branch), 주인 없는 것은 안 지움" bash -c 'l=$(cat "$1"); grep -q "worker-release --dispatch ctx_wa1" <<<"$l" && grep -q "worktree rm --worktree path:$2/x-wa --delete-branch" <<<"$l" && grep -q "worktree rm --worktree path:$2/x-wa-rv --delete-branch" <<<"$l" && ! grep -q "path:$2/\(x-wb\|x-wc\|x-wd\|x-wf\|x-wm\|scratch-e\) " <<<"$l" && grep -q "정리함 2" <<<"$0" && grep -q "x-wa	X-WA" orchestration/state/sweep.log' "$o" "$W/swp.log" "$SWP"
: > "$W/swp.log"
o=$($WS python3 $T/worktree_sweep.py --apply --orphans 2>&1)
check "--orphans: 주인 없지만 main에 다 들어 있는 것도" bash -c 'grep -q "worktree rm --worktree path:$1/scratch-e" "$0"' "$W/swp.log" "$SWP"
o=$($WS python3 $T/status.py --stall 2>&1)
check "정지 점검: 워크트리 정리·확인 필요 줄" bash -c 'grep -q "워크트리 정리 .*→ python3 orchestration/tools/worktree_sweep.py --apply" <<<"$0" && grep -q "워크트리 확인 필요 .*개" <<<"$0"' "$o"
check "launch 자동 정리는 한 시간에 한 번(sweep_due) · cleanup.auto_sweep: false면 안 함" python3 -c "
import sys,json,time; sys.path.insert(0,'$T'); import worktree_sweep as w, kitlib
cfg=kitlib.load_config('.'); assert not w.sweep_due('.',cfg)
cfg['cleanup']={'auto_sweep': False}; assert not w.sweep_due('.',cfg)"
cp "$W/kit.yaml.pre-swp" orchestration/kit.yaml; unset FAKE_SWP FAKE_SWP_LOG FAKE_SWP_MAIN
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
