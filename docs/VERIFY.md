# v0.1.0 검증 기록 (증적)

| 항목 | 내용 |
|---|---|
| 일자 | 2026-11-06 |
| 환경 | Linux, Python 3 + PyYAML 6.0.3, git, docker |
| 원본 프로젝트 | 실전 프로젝트 저장소를 **읽기만** 했다. 검증 설정·출력은 `/tmp` 아래 임시 폴더에만 썼다. 검증 전후 원본의 HEAD와 작업 트리 상태가 같음을 확인했다 |

## 1. DAG 검증기 (`tools/validate_dag.py`)

| 대상 | 작업 | 의존 간선 | 시작점 | 끝점 | 최장 사슬 | 오류 | 경고 | 종료코드 |
|---|---|---|---|---|---|---|---|---|
| 예제 `examples/demo-project/orchestration/tasks.yaml` | 7 (W0 1 · G-0 1 · W1 2 · W2 2 · W3 1) | 7 | 1 | 1 | 5 | **0** | 0 | 0 |
| 실전 프로젝트 tasks.yaml (읽기만) | 46 (W0 12 · G-0 1 · W1 28 · W2 3 · W3 2) | 87 | 2 | 7 | 11 | **0** | 11 | 0 |
| 키트 템플릿 `templates/orchestration/tasks.yaml` | 9 | 12 | 1 | 1 | 7 | **0** | 0 | 0 |
| init이 만든 W0 초안 (모듈 2) | 7 | 10 | 1 | 1 | 6 | **0** | 0 | 0 |

- 실전 프로젝트 경고 11건의 내역: 웨이브 미지정 작업의 추정 역행 4건(G-0 뒤에 추가된 인프라 작업이 규칙상 W0로 추정됨 → `wave` 명시 권고), 동시 실행 가능한 작업끼리 `write_scope` 접두 겹침 7건(공용 시뮬 패키지 경로). 둘 다 정보성 경고이고 오류가 아니다.
- 실패 사례 검증(임시 파일): 중복 ID, 없는 선행작업, 자기 의존, 2개 작업 순환, 한 작업 안의 중복 키(`status` 두 번)를 모두 오류로 잡고 종료코드 1을 냈다.
- 작업 수가 작업 시작 시점(43)과 다른 것은 실전 프로젝트가 검증 중에도 다른 세션에서 진행 중이었기 때문이다.

## 2. 명세 렌더링 (`tools/render_spec.py`)
`examples/demo-project/make_demo.sh`가 예제 저장소에서 렌더링했다. 결과는 `examples/demo-project/orchestration/specs/`.

| 명세 | 템플릿 | 확인한 것 |
|---|---|---|
| `R-API-001.md` | worker-spec (L2) | `perf: true` → 계기별 p50/p95 문구, 요구사항 2개, 쓰기 범위, 선행 G-0(merged), `git push origin HEAD:req/R-API-001` |
| `R-WEB-001.md` | worker-spec (L2) | `env: [sim]` + `runtime.sim: false` → `sim.pending.md`('모의로 먼저, 실제로 다시') 블록, `{base}` 치환(int/web) |
| `I-WEB.md` | integration-spec (L1) | 머지 절차(`[G]` 동기화 메시지, `--no-ff`, 스모크 실패 시 머지 거부), 증적 경로 |

남은 자리표시자 없음(알려진 이름만 치환, 나머지 중괄호는 보존).

## 3. 대시보드 생성기 (`tools/dashboard/build.py`)

| 대상 | 설정 | 결과 | 종료코드 |
|---|---|---|---|
| 예제 (make_demo 저장소) | init이 만든 `orchestration/kit.yaml` | 작업 7 (merged 2 · running 1 · ready 1 · todo 3), RTM 3행, TBD 2건, 문서 8개 **경고 0**, 커밋 4, 브랜치 5, 증적 1 → `examples/demo-project/dashboard/index.html` | 0 |
| 실전 프로젝트 (읽기만, 출력 `/tmp`) | `/tmp`의 별도 설정 파일(모듈 3, 메인 `master`, 검토 단계 4, 상위 요구사항 패턴) | 작업 46, RTM 44행 + 파생 5행, TBD 14건, 문서 179개 **경고 0**, 커밋 30, 브랜치 52, 증적 30 | 0 |
| 검토 단계 `defense` 프리셋 설치본 | init 생성 kit.yaml | '검토 단계별 산출물' 카드 표시, 문서 경고 0 | 0 |

- **동등성:** 같은 시점에 실전 프로젝트의 원래 생성기와 키트 생성기를 나란히 돌려 요약 수치(작업 상태 분포, RTM·TBD·문서·커밋·브랜치·증적 수)가 모두 같았다.
- 원본 저장소용 설정 파일은 키트에 넣지 않았다(고유 정보 포함).

## 4. 스킬 init (임시 디렉터리의 빈 git 저장소)
```
git init -b main <tmp>/empty
kit.py init --target <tmp>/empty --answers examples/demo-project/answers.yaml --run --profile ros2 --dry-run
kit.py init --target <tmp>/empty --answers examples/demo-project/answers.yaml --run --profile ros2
kit.py bootstrap --target <tmp>/empty --dry-run
```
- `--dry-run`: 배치 계획 54줄(+) 출력, 대상에는 `.git` 외 아무것도 생기지 않음(종료코드 0).
- 실제 설치: 파일 55개(계획 54 + `.gitignore`). 폴더: `docs/01_requirements` `02_interface` `03_design` `04_test` `05_reports` `06_evidence` `07_process/snapshots`, `orchestration/{sessions,specs,state,templates/blocks,tools/dashboard/systemd}`, `scripts/run/profiles/ros2`, `.orca-kit/installed.json`. 예제 배치 목록: `examples/demo-project/installed-tree.txt`.
- bootstrap dry-run 출력(Orca 명령은 실행하지 않음):
  - `[master-run]` `orca orchestration run-create --objective '할 일 관리 웹앱 마스터 Run (사용자 소통·결정)' --json`
  - `[manager]` `orca orchestration worker-start --spec-file <tmp>/empty/.git/orca-kit/MANAGER.launch.md --task-title '작업관리 매니저' --display-name '매니저' --agent claude --worktree path:<tmp>/empty --json`
  - `[abandon]` `orca orchestration worker-abandon --dispatch <manager_dispatch> --json`
  - `[dashboard]` `setsid nohup <tmp>/empty/orchestration/tools/dashboard/serve.sh --orca -p 8765 > .git/orca-kit/dashboard.log 2>&1 &`
- 추가 확인
  - 설치본에서 `validate_dag` 오류 0, `render_spec I-API` 정상, 대시보드 생성 종료코드 0.
  - `defense` 프리셋 + 원격 + ros2: `docs/01_SRR`~`04_CDR` 생성, RTM·TBD가 `01_SRR`에, W0 계약 작업의 쓰기 범위가 `docs/02_SFR/`, `run.conf`에 원격 기본값과 배포 제한 제외 패턴.
  - `upgrade`: 설치 뒤 키트 템플릿을 바꾸고 설치본 파일 하나를 사용자가 고친 상태에서 미리보기 → '키트 갱신' 1건, '충돌' 1건과 unified diff. `--apply` → 갱신 파일은 교체, 충돌 파일은 `<파일>.kit-new`로 기록, 다시 미리보기하면 충돌만 남음.
  - 심볼릭 링크로 연 스킬 경로에서 `kit.py`가 키트 본체를 찾음(설치 방식 검증, 실제 사용자 스킬 경로에는 설치하지 않음. `install.sh --dry-run`만 실행).
  - `launch.sh --dry-run`: task-create → worker-start(L2는 `--base-branch int/<모듈>`, L1은 `--keep-open`) → `task_status.py` 순서 출력. `task_status.py`는 블록형 항목의 status 한 줄만 바꿈.
  - `cleanup_worker.sh --dry-run`: 조건 불충족(워크트리 없음)이면 아무것도 지우지 않고 종료코드 1.
  - `scripts/run/run.sh` (로컬 docker, ros2 프로필, 로컬 이미지): 전용 네트워크(`NetworkMode=<접두>-<워크트리>`, 라벨 `kit.isolated=1`), 컨테이너 안 `ROS_DOMAIN_ID`·`GZ_PARTITION`·전달 변수 확인, `RUN_HEAVY=1` 슬롯 획득, `--stop` 후 네트워크 제거.

## 5. 반입 금지어 검사
작업 지시서의 금지어 패턴(원 프로젝트 이름·모듈 약어·도메인 단어·내부 호스트·사설망 주소·개인 이메일·계정명, 10개 항목)으로
`grep -rniE '<패턴>' --exclude-dir=.git .` 와 `git grep --cached -niE '<패턴>'`(커밋 대상 전체)를 돌렸다. 패턴 원문은 이 기록에 적지 않는다(적으면 이 문서가 걸린다).

결과 **0건** (GNU grep, 커밋 직전). `.git/` 안의 4건은 원격 저장소 주소(git 메타데이터)이고 키트 파일이 아니다. 출처 표기 한 줄 예외도 쓰지 않았다(README는 '실전 프로젝트에서 검증'으로 표기).
- 대소문자 무시 검사라 흔한 영단어 안의 철자도 걸린다. 대시보드 CSS는 해당 속성 이름을 CSS 이스케이프로 적었다(`docs/DESIGN-NOTES.md`).

## 6. 하지 않은 것
- 스킬 실제 설치(사용자가 정한다). 설치 명령: `./install.sh` (Orca 프로필 경로 `$ORCA_CLAUDE_CONFIG_DIR/skills/`), 일반 Claude Code는 `./install.sh --dir ~/.claude/skills`.
- 실제 Orca Run 생성·워커 기동(dry-run으로 명령만 확인), 원격 머신 실행(ssh 대상 없음), `tailscale serve` 실제 공개, systemd 서비스 등록.

## 7. v0.2.0 검증 (2026-11-09)
키트 저장소에는 별도 시험 폴더가 없다(v0.3 후보 13). 아래 검사를 돌렸다. 출력은 세션 임시 폴더에만 썼다.

| 항목 | 명령·방법 | 결과 |
|---|---|---|
| 파이썬 문법 | `python3 -m py_compile tools/*.py tools/dashboard/build.py skills/orca-orchestration-kit/scripts/kit.py` | 통과 |
| init 설치 | 빈 git 저장소에 `kit.py init --answers examples/demo-project/answers.yaml --run --profile ros2` | 종료코드 0, `orchestration/templates/review-spec.md` 설치, 남은 자리표시자는 bootstrap이 채우는 `MASTER_RUN`·`WORK_RUN`뿐 |
| DAG 검증 | 설치본 `validate_dag.py` / 키트 템플릿 tasks.yaml | 오류 0 · 경고 0 / 오류 0 (경고 4: 템플릿 예시 모듈 alpha·beta가 예제 kit.yaml에 없음, 정보성) |
| 검토 명세 렌더링 | 설치본 tasks.yaml에 `REV-01`(layer L0, base main, `target: origin/int/api`, `spec_template: review-spec.md`) 추가 → `validate_dag.py`, `render_spec.py --stdout REV-01` | 오류 0. review-spec으로 렌더링, `{target}`·`{base}`·`{evidence_dir}` 치환, 남은 `{이름}` 0 |
| L2 명세 | `render_spec.py --stdout F-01`, 예제 R-API-001·R-WEB-001 | '범위는 이 명세가 전부'·'문맥이 모자랄 때(부분 완료)' 절 포함 |
| 범위 확인 명령(L-08) | 임시 저장소, 한글 경로 보고서만 든 브랜치와 코드가 섞인 브랜치 | `core.quotePath=false`: 보고서만 → 통과, 코드 포함 → 멈춤. quotePath 기본값: 보고서만인데도 멈춤(교훈에 적은 오탐 재현) |
| upgrade | v0.1.0(274b01f)로 설치한 저장소에 v0.2.0 `upgrade` 미리보기 | 키트 갱신: MASTER·MANAGER·worker-spec·integration-spec·README(orchestration)·blocks/sim, 새 파일: review-spec, seed 변경 알림: AGENTS.md·kit.yaml |
| 예제 재생성 | `examples/demo-project/make_demo.sh <임시 폴더>` | validate_dag 오류 0, 대시보드 경고 0. 바뀐 결과물: installed-tree(+review-spec), 렌더링 명세 3개(새 절), 대시보드(생성 시각·커밋 해시. 바닥글의 임시 경로는 v0.1 표기와 같은 꼴로 바꿈) |
| 반입 검사 | 추가·변경 줄에서 원 프로젝트 도메인 단어·모듈 약어·내부 호스트·사설 주소·이메일·디스패치 ID 패턴 검사 | 0건. 출처 표기는 '실전 프로젝트 I-nn/D-nnn' 꼴만 쓴다(작업 지시에 따른 예외, v0.1의 '출처 표기 없음'에서 바뀜) |

## 8. v0.3.0 검증 (2026-11-12)
출력은 세션 임시 폴더에만 썼다. 실사용 프로젝트는 **읽기만** 했고(복제본으로 시험), 검증 뒤 그 저장소의 `git status`가 비어 있음을 확인했다.

| 항목 | 명령·방법 | 결과 |
|---|---|---|
| 문법 | `python3 -m py_compile tools/*.py tools/dashboard/build.py tools/hooks/pre-push skills/.../kit.py`, `bash -n` 셸 스크립트 전부 | 통과 |
| 회귀 시험 | `tests/smoke.sh` — init(로컬 전용), 자리표시자 잔여, 렌더링(`{push}`), launch 투입 조건(선행·게이트·hold·기준 브랜치·기존 워크트리 생성 플래그·L1 `--keep-open`), `--review`·`--retry`, effort 경고, 보관·보관 뒤 검증·status·대시보드, sync_main(체크아웃 유지·ff), pre-push(로컬 전용·`git push .` 통과·보호 브랜치·`KIT_PUSH_OK`·배포 제한 파일), evidence_lint, slot, bootstrap(`--step` 필수·manager 플래그·abandon `--work-run`), cleanup 보류, upgrade(3방향 자동 병합·키트 밖 파일 보존) | **48/48 통과** |
| 정지 점검(실사용, 읽기 전용) | `kit.py status --target <실사용> --stall` | 진행 중 0(이전 판은 끝난 워커 9개를 '진행'으로 셈) · 전체 367개 페이지 순회(이전 판은 100에서 잘림) · '할당 누락' 3건(마스터 승인·이월로 미룬 작업 → `hold` 필드로 구분) · 키트 새 버전 알림 |
| 보관(실사용 복제본) | `archive_tasks.py` (작업 75개, 주석·흐름형 혼합) | 71개 이동, tasks.yaml 103,276B → 8,017B, 검산 통과, `validate_dag` 오류 0(경고 183 → 1: 끝난 작업끼리의 쓰기 범위 겹침이 빠짐), 보관 작업을 선행으로 둔 명세 렌더링 정상 |
| upgrade(실사용 복제본, v0.1.0 → v0.3.0) | `kit.py upgrade --summary` → `git_remote: ""` 추가 후 `--apply` | 자동 병합 1(orchestration/README.md), 키트 갱신 8, 새 파일 9, 충돌 5(로컬 push 문구와 겹친 명세·도구 → 병합 표시 든 `.kit-new`), 키트 밖 파일 2(프로젝트 자체 review-spec·evidence_lint 보존 + `.kit-new`), seed 3 알림 |
| 문맥 크기 | v0.2 → v0.3 바이트 | SKILL.md 9,725 → 4,476 · MANAGER.md 17,423 → 12,551(관문·검토 절차는 필요할 때 여는 파일로) · AGENTS.md 4,152 → 4,735(읽을 것·위생 규칙 추가) · 렌더된 L2 명세 4,381 → 4,421. 워커가 처음에 읽던 tasks.yaml·구조 문서(실사용 103KB + 10KB)를 읽지 않게 했다 |
| 예제 재생성 | `examples/demo-project/make_demo.sh <임시 폴더>` | validate_dag 오류 0, 대시보드 경고 0. 바뀐 결과물: installed-tree(+procedures·새 도구), 렌더링 명세 3개, 대시보드. 작업 폴더 경로는 스크립트가 `/tmp/kit-verify/demo`로 바꾼다 |
| 반입 검사 | 추가·변경 파일 전체에서 실사용 프로젝트 이름·호스트·계정·이메일·Run/dispatch/terminal ID·사설 주소 패턴 | 0건(시험 픽스처의 가짜 경로 `/home/someone/` 1건은 의도) |

**하지 않은 것:** Orca 실기동(워커 생성, 실행 중·끝난 워커에 send 했을 때의 의미 재확인). dry-run과 Orca 1.4.255 CLI 도움말·오케스트레이션 가이드 대조만 했다. 실사용 프로젝트에 upgrade를 실제로 적용하지 않았다(사용자 결정).

## 9. v0.4.0 검증: 모델 자동 라우팅 (2026-11-16)
출력은 세션 임시 폴더에만 썼다. 실사용 프로젝트는 `git clone`한 복제본으로만 돌렸고, 원본 저장소의 `git status`가 비어 있음을 확인했다.

| 항목 | 명령·방법 | 결과 |
|---|---|---|
| Orca 연동 근거 | Orca 1.4.259 `worker-start --help`, `agent hooks status`, `agent-context --json` | `--agent`가 claude·codex·antigravity·grok 등을 받고, `--model`은 Claude·Codex·Antigravity·Grok 모델 ID를 받는다(`--effort`는 `--model`이 있어야 한다). OpenCode는 쓰지 않는다 |
| 구독 CLI 모델 목록 | `codex debug models`(JSON), `agy models`(탭 구분), `grok models`, Claude는 별칭(`--model fable/opus/sonnet`) | 이 머신에서 `models.py discover`: codex 10, antigravity 6, grok 4, 카탈로그 12개 모두 ✓, 카탈로그에 없는 모델 알림(구 세대 등). 4.1초(이후 하루 캐시) |
| 회귀 시험 | `tests/smoke.sh` — 기존 48 + 라우팅 20(구독 CLI 대역으로 이 머신과 분리): discover·새 모델 알림·캐시 gitignore, 체계 통합 품질 4 이상, 쉬운 작업 저가 모델, 근거 출력, `difficulty` 검증 경고, `route:` 고정, launch 연결·`--no-route`·`--explain`, agy 모델 ID 변형, 교차 검토, 실패 이력(난이도 올림·실패 모델 회피), 쿨다운, 동시 상한, `routing.agents`, 학습 점수(merged·retry·재투입·검토 판정), feedback·stats, Orca 사용량 집계, status.py | **68/68 통과** |
| 분류 보정(실사용 복제본, 작업 74개) | 모든 작업을 `routing.route`로 돌려 분포를 봤다 | 첫 판: 난이도 4~5가 80%, Opus 39건(분야 일상어 '계약'·'권한'·'성능'과 긴 완료 조건이 부풀림). 빈도 거르기(작업 30% 초과 단어 제외)·문턱 조정·balanced 가중 조정 뒤: 난이도 2:11 · 3:36 · 4:17 · 5:10, balanced 배분 codex 32 · claude 21 · antigravity 19 · grok 2(정적 평가라 동시 상한에 따른 분산은 빠져 있다). economy는 antigravity 44, quality는 claude 39 |
| 예제 재생성 | `examples/demo-project/make_demo.sh <임시 폴더>` | 바뀐 결과물: installed-tree(+models.yaml·routing.py·models.py·model_catalog.yaml), 대시보드(생성 시각·커밋 해시) |
| 반입 검사 | 추가·변경 파일 전체에서 실사용 프로젝트 이름·호스트·계정·이메일·Orca 계정 ID·홈 경로 | 0건 |

**하지 않은 것:** 라우팅으로 Orca 워커를 실제로 띄우지 않았다(dry-run 명령까지만). 특히 Antigravity·Grok 워커를 Orca로 띄웠을 때 `--model` 값이 그대로 먹는지, Grok에 `--effort`가 먹는지는 실기동으로 확인해야 한다. 카탈로그 점수는 추정이고 실적이 쌓이기 전에는 보정되지 않는다.

## 10. v0.4.1: Orca 실기동 (2026-11-16)
시험 전용 Run(`run-create`)을 만들고, 키트 저장소의 현재 워크트리에서 읽기 전용 확인 작업("쓰는 모델을 한 줄로 보고")을 띄웠다. 끝난 뒤 워커를 멈추고 해제했으며 저장소 변경은 없었다.

| 에이전트 | 명령 | 결과 |
|---|---|---|
| grok | `worker-start --agent grok --model grok-4.7 --effort low` | **거부:** `agent_unconfigured`(Orca 설정에서 Grok 에이전트가 꺼져 있음). 워커를 만들기 전에 거부되어 남는 자원 없음 |
| antigravity | `worker-start --agent antigravity --model gemini-3.8-flash-low` | Orca가 모델 ID를 그대로 넘겼다(프로세스 `agy --dangerously-skip-permissions --model gemini-3.8-flash-low`, 화면 "Gemini 3.8 Flash · low"). 처음엔 **작업 폴더 신뢰 확인 창**에서 `agent_readiness` 실패. snap 설치본은 신뢰 목록을 `~/snap/antigravity-cli/common/.gemini/antigravity-cli/settings.json`에서 읽는다. 시험 동안만 그 목록에 키트 저장소를 넣자 시작했고, 모델을 "Gemini 3.8 Flash (Reasoning Effort: Low)"로 답했다. **그러나 snap 격리 때문에 Orca CLI 심(`~/.config/orca/…`)을 실행하지 못해 `worker_done`을 보내지 못했다**(워커 스스로 Permission denied를 보고). 시험 뒤 신뢰 목록은 원래대로 되돌렸다 |

**키트 반영(v0.4.1):**
- `launch.sh`: `agent_unconfigured`를 받으면 그 구독을 24시간 쿨다운하고, 자동 라우팅이었으면 한 번 다시 골라 같은 Orca 작업에 띄운다.
- `models.py discover`/라우팅: snap 격리 설치본(`/snap/…`, `/usr/bin/snap` 링크)을 '워커로 못 씀'으로 표시하고 뺀다. 모델 목록 캐시 형식에 판(`v: 2`)을 넣어 옛 캐시를 버린다.
- 회귀 시험 70/70(+ snap 격리 제외, 꺼진 에이전트 재라우팅).

**사용자 할 일(구독을 라우팅에 넣으려면):** Grok은 Orca 설정에서 에이전트를 켠다. Antigravity는 snap이 아닌 설치본으로 바꾼다(그 전까지 라우팅이 자동으로 뺀다). Codex·Claude는 이번에 띄워 보지 않았다(기존 운영에서 쓰던 경로).

## 11. v0.5.0: 대시보드 개요 = 작업 보드 (2026-11-24)
- 회귀 시험 77/77: 기존 70 + 개요 보드·격자·구독 칩(쿨다운)·투입 작업의 모델 칩, `models.yaml`이 없을 때 보드만 나오고 구독 칩이 빠지는지, JEV 5건(기본 꺼짐이면 호출 없음, shadow는 기록만, 캐시 재사용, live는 신뢰도 기준 이상일 때 적용, 호출 실패 시 규칙 분류). JEV는 로컬 가짜 서버로 시험했고 실제 TypeSafe API는 키가 없어 부르지 않았다(요청·응답 형식은 TypeSafe 문서 기준).
- 예제(`examples/demo-project/dashboard/index.html`)를 다시 만들고 헤드리스 Chrome으로 1440px·390px 화면을 확인했다. 휴대폰 폭에서는 보드 열이 세로로 쌓이고 격자는 상자 안에서 가로로 스크롤된다.
- 같은 파일에 시험 스크립트를 붙여 동작을 확인했다: 모듈 거르기(API → 카드 2장, 열 개수 갱신, 빈 열 문구, 다른 모듈 격자 칸 흐리게), 격자 노드 선택(거르기를 풀고 카드 표시, 다시 누르면 해제), 격자 접기 상태 저장.
- 하지 않은 것: 실제 Orca 진행 중 워커가 있는 저장소에서 `--orca`로 띄워 보지 않았다(모델 칩은 dispatch.tsv 기준이라 `--orca` 유무와 무관하다).
