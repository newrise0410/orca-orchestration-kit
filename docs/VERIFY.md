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

## 12. v0.5.1: 공개 배포 전 보안 점검 (2026-11-24)
현재 파일과 git 이력 전체(모든 브랜치, 커밋 메시지·작성자 포함)를 검사했다.

| 항목 | 결과 |
|---|---|
| 비밀값(API 키·토큰·개인 키·비밀번호 모양) | 0건. 걸린 것은 시험용 가짜 값과 `evidence_lint.py`의 탐지 정규식뿐 |
| 개인 정보(개인 이메일·홈 경로·사설/Tailscale IP·Orca 계정·Run/dispatch ID) | 0건. `/home/someone`(시험 픽스처), `*.ts.net`(문서 자리표시)은 의도 |
| 원 프로젝트 출처 표기 | 문서 3개(LESSONS·DESIGN-NOTES·VERIFY)와 커밋 메시지 3개, 첫 커밋 작성자에 있었다 → 일반 표현으로 바꾸고 **이력 전체를 다시 썼다**(git filter-repo: 내용·메시지 치환, 작성자 통일). 강제 push 뒤 이력 재검사 0건 |
| 플러그인 자동 실행 요소 | 없음(루트에 hooks·commands·agents·MCP 설정 없음, 스킬만 등록) |
| 대시보드 | 127.0.0.1 바인드, 문서 본문 HTML 이스케이프, 링크는 http(s)만 |
| 코드 | `shell=True`·`os.system`·`eval` 없음 |
| 외부 전송 | JEV(선택)만. 기본 꺼짐, 켜도 작업 제목·완료 조건만 보낸다 |
| 라이선스 | Apache-2.0 (`LICENSE`, `plugin.json`) |

- 이력을 다시 쓰기 전 상태는 로컬 번들로만 보관했다(저장소에 넣지 않는다).
- 이력을 다시 쓰기 전 저장소가 이미 공개 상태였다. GitHub는 강제 push로 참조가 끊긴 옛 커밋을 해시로 한동안 보여 줄 수 있다. 완전히 지우려면 GitHub 지원에 캐시된 뷰 삭제를 요청한다.
- 키트를 고친 사람이 새로 넣는 문서에도 같은 기준(원 프로젝트 이름·내부 번호의 출처 표기 금지)을 적용한다.

## 13. v0.5.2: .gitignore 키트 블록 (2026-11-24)
- 현상: 실사용 설치본에서 `git ls-files orchestration/tools/dashboard`가 0건이었다. 키트 블록의 `dashboard/`는 앞에 `/`가 없어 어느 깊이의 `dashboard` 폴더에나 맞는다.
- 수정: 매니페스트를 `/dashboard/`로 바꿨다. `kit.py`의 `gitignore_plan`이 블록을 매니페스트와 맞추고, init과 upgrade(미리보기는 diff만, `--apply`일 때 쓴다)가 같이 쓴다. 끝 표시가 없는 블록은 사람이 고친 것으로 보고 손대지 않는다.
- 회귀 시험 83/83: 기존 77 + 6(init 뒤 도구는 추적되고 루트 출력만 무시, 옛 블록에서 결함 재현, 미리보기에 갱신 표시, 미리보기는 파일을 안 바꿈, 적용 뒤 블록 갱신과 블록 밖 줄 보존, 다시 돌리면 변화 없음).
- 하지 않은 것: 대시보드 출력 경로를 `--out`으로 루트 밖에 둔 프로젝트는 그 경로를 직접 무시 목록에 넣어야 한다(키트는 기본 경로만 안다).

## 14. v0.6.0: W0 초안의 기획·유사 오픈소스 조사 (2026-11-24)
- 바뀐 것: `kit.py init`이 만드는 W0 초안이 `F-00a` 기획(hold) → `F-00b` 유사 오픈소스 조사·반영 → `F-01` 계약으로 시작한다. 두 작업의 쓰기 범위는 요구·인터페이스(기능 정의)·설계 문서 폴더 세 곳이고, 검토 단계 프리셋을 쓰면 그 폴더 이름을 따른다(`defense`: `01_SRR`·`02_SFR`·`03_PDR`, `product`: `01_REQ`·`02_SPEC`·`03_DESIGN`으로 확인).
- 회귀 시험 89/89: 기존 83 + 6(초안의 순서와 hold, 기획이 보류면 투입 가능 작업 없음, `F-00a`는 hold라 투입 보류, `F-01`은 조사 전 선행 미완료로 보류, `F-00b`가 조사 태그로 분류됨, tasks.yaml에 YAML 앵커 없음). 보관 시험의 merged 수는 4 → 6으로 고쳤다.
- 시험이 잡은 결함: 두 작업이 같은 목록 객체를 쓰기 범위로 함께 써서 tasks.yaml에 YAML 앵커(`&id001`)가 찍혔고 `archive_tasks.py`가 작업을 하나씩 읽다가 실패했다. 작업마다 목록을 복사해 넣게 고치고 앵커 검사를 시험에 넣었다.
- 하지 않은 것: 예제(`examples/demo-project`)는 자체 tasks.yaml을 덮어쓰는 구성이라 그대로 뒀다(기획 단계가 없는 축약 예제다). 기존 설치본의 tasks.yaml은 seed라 upgrade로 바뀌지 않는다. 실제 Orca에서 `F-00a` → `F-00b` → `F-01`을 차례로 투입해 보지는 않았다.

## 15. v0.7.0: 벤치마크 품질·잔여 한도·구독 CLI 자동 업데이트·카탈로그 계열 (2026-11-25)

| 항목 | 결과 |
|---|---|
| `tests/smoke.sh` | 107/107 통과(KIT-09까지 89 + 이번 18). 동점 규칙·임계 경로 실적 대역 추가. 벤치·한도 추가분: Terminal-Bench 줄 고르기(옛 판·다른 CLI 안 빌림)·models.yaml 품질 고정·꼴이 바뀐 페이지 실패·AA 무료 API(가짜 서버)·키 없으면 건너뜀, 한도 소진 제외(초기화 시각·다른 계정 알림)·Fable 주간은 Fable에만·Antigravity 묶음·세션 벌점·곧 초기화 가점·캐시에 이메일 없음·모두 소진이면 launch 보류·대시보드 칩. 그 밖의 v0.7 추가분: 추가: 계열의 가장 높은 판(gpt-6.2-sol)·옛 판 알림 제외, route: 고정도 계열 판, models.yaml model 지정 시 고정, `{effort}` 계열 재구성·`5-5` 판 읽기, `update` 판 기록·`--check`·snap 건너뜀, launch.sh 하루 한 번 업데이트 |
| 실제 CLI (이 머신) | `models.py update`: claude 2.1.331 최신, codex 0.169.0 → 0.172.0, agy 1.5.16 최신, grok 1.0.92 최신 (14초). 입력 없이 끝났다 |
| 실제 계열 판 | codex-astra → gpt-6.2-astra, codex-sol → gpt-6.2-sol, terra 6.1, luna 6, agy neon·carbon 4, agy claude-opus 5-5, grok 4.7. 새 계열 알림: codex-auto-review·gpt-reserve, Antigravity argon·hydrogen·claude-sonnet 등, grok-4.7-build-fast |
| Antigravity | snap 커뮤니티 포장본(1.4.12) 대신 공식 설치 스크립트를 읽고 실행 → `~/.local/bin/agy` 1.5.16(sha512 검증, 자체 업데이트). PATH에서 snap보다 앞선다 |
| Terminal-Bench 4.1 실데이터 | 리더보드 67줄 중 카탈로그에 맞는 36줄. 품질(5 × 해결률/최고): fable·opus 5.0, sonnet 4.9, carbon 4.9, astra·sol·neon 4.7, terra 4.1, haiku 3.0, grok 2.9, luna 1.2. agy-claude-opus는 벤치 없음(카탈로그 4) |
| 잔여 한도 실데이터 | Orca rateLimits: claude 주간 30%·Fable 주간 26%, codex 세션 100%(초기화 시각까지 제외, 다른 계정 여유 알림)·주간 48%, antigravity Gemini 묶음 세션 90%·주간 38%(벌점 −2.7), Claude and GPT 묶음 0%, grok 주간 0%. 캐시 파일에 이메일 없음 확인 |
| 실제 결정 | 데모 F-01(계약·임계 경로) → claude-sonnet high, S-01(체계 통합) → claude-opus high. codex는 세션 소진으로 제외 |

## 16. v0.7.0 실기동: Antigravity 공식 설치본 워커 (2026-11-25)
v0.4.1(10장)에서 snap 설치본은 작업 폴더 신뢰 확인 창에 막혔고, 시작한 뒤에도 Orca CLI 심을 실행하지 못해 `worker_done`을 보내지 못했다. snap 판을 지우고 공식 설치본으로 바꾼 뒤 같은 시험을 다시 했다.

| 항목 | 결과 |
|---|---|
| 명령 | `worker-start --environment local --worktree current --agent antigravity --model claude-opus-5-5-low` (읽기 전용 확인 작업) |
| 시작 | 확인 창 없이 바로 `ready` (신뢰 목록 손대지 않음) |
| 실행 파일 | 워커가 보고: `readlink -f "$(command -v agy)"` → `~/.local/bin/agy`, `agy --version` → 1.5.16 |
| 모델 | 워커가 보고: Claude Opus 5.5 · low. Orca가 카탈로그 모델 ID를 그대로 넘겼다 |
| `worker_done` | 도착(outcome `succeeded`). 워커는 끝난 뒤 스스로 닫혔다 |
| 저장소 변경 | 없음 |

- 시험한 한도 묶음은 Antigravity의 "Claude and GPT models"(그때 0%)다. Gemini Neon·Carbon은 같은 실행 파일이고 모델 ID만 다르지만 직접 띄우지는 않았다(Gemini 묶음 세션 90%라 아꼈다).
- 결론: 공식 설치본 Antigravity는 Orca 워커로 쓸 수 있고, 라우팅 후보에 들어간다. snap 설치본은 계속 자동으로 뺀다.

## 17. v0.8.0: 한도 전환 (2026-11-25)

| 항목 | 결과 |
|---|---|
| `tests/smoke.sh` | 129/129(이전 107 + 22). 가짜 Orca(워커 목록·한도·stop/release/start/send 기록)와 실제 git 워크트리로 시험 |
| 예약 점검(가짜 Orca) | precheck: 할 일 없으면 1·메일 없음, 한도 정지면 `send --to run:<work_run> --type status`(한도 점검) 뒤에도 1, 같은 내용 재알림 안 함. install: `automations create --trigger */10 … --precheck 'cd <저장소> && ORCA_CLI_COMMAND=<절대 경로> <python3> …/quota_watch.py precheck'`·상태 기록, 이미 있으면 다시 만들지 않음, remove |
| 예약 점검(실제 Orca) | 1분 간격 시험 예약 실행을 만들어 확인하고 지웠다: 사전 확인이 매분 돌았고 세 번 모두 `skipped_precheck`("Precheck exited with code 1") — 에이전트가 뜨지 않았다. 사전 확인 환경: 작업 폴더에서 실행, 셸 명령(`cd /tmp && X=compound …`가 됐다), `ORCA_CLI_COMMAND` 없음, PATH의 `orca`는 `/usr/bin/orca` |
| 대기 뒤 재개 | 대기 판정 때 `quota_wait.json`에 풀리는 시각 기록 → 정지 점검이 '대기 중(HH:MM에 풀리면 재개 메시지)' → 시각이 지나면 '한도 풀림' → `quota_switch.py`가 `send --to dispatch:<id> --type status`('이어서 하라')를 보내고 기록을 지운다(worker-stop 없음). `--due`는 할 일이 있으면 0, 없으면 1 |
| 감지 | 정지 점검: `한도 정지 X-SW (dispatch): codex 세션 100%(HH:MM 초기화) · codex-sol → quota_switch.py` |
| 미리 보기 | `--dry-run`은 계획만 출력, Orca 변경 호출 없음 |
| 대기 | 30분 안에 풀림 · `on_exhausted: wait` · 검토 워커인데 남은 후보가 구현한 구독(검토 독립성) — 셋 다 worker-stop을 부르지 않는다 |
| 전환 | worker-stop → 보존 → worker-release → 같은 워크트리(`--worktree path:`)로 worker-start, 에이전트는 codex가 아님 |
| 보존 | `wip/X-SW-quota-*`에 커밋하지 않은 파일까지 들어 있고, 작업 브랜치 HEAD·작업 트리·미커밋 파일은 그대로 |
| 기록 | dispatch.tsv `X-SW handoff-h1`, quota_switch.tsv, `specs/X-SW.handoff-h1.md`(이어받기 절·보존 브랜치), tasks.yaml status running 유지 |
| 실적 | 멈춘 dispatch는 outcomes에서 빠지고, 머지되면 handoff 투입이 1.0 |
| 정리 | `cleanup_worker.sh --dry-run`이 보존 브랜치 삭제를 포함 |

- 실제 Orca에서 한도로 멈춘 워커를 전환해 보지는 않았다(그 순간을 만들 수 없었다). Orca 명령(worker-stop·worker-release·worker-start `--worktree path:`)은 v0.4.1·v0.7.0 실기동에서 쓴 것과 같다.

## 18. v0.8.1: 넘겨받을 구독의 여유 (2026-11-25)

| 항목 | 결과 |
|---|---|
| `tests/smoke.sh` | 132/132(이전 129 + 3) |
| 여유 판정 | 남은 20% < 30% → 안 넘김 · 세션 60%가 100분 만에 참(0.6%/분) → 67분 뒤 다시 참 < 120분 → 안 넘김 · 20%면 넘김 · 막 열린 창(10분)은 속도를 안 믿음 · 필요 시간이 30분이면 같은 창도 넘김 |
| 대기 | 다른 구독이 모두 '곧 다시 찬다'면 `[대기] … 넘겨받을 구독도 곧 다시 찬다: …`, worker-stop 없음 |
| 검토 이어받기 | `kit.yaml review.agent: codex`(한도 찬 구독)로 고정해도 고른 구독으로 넘기고, 구현한 구독(codex·grok)은 후보에서 빠진다 |
| 시험이 잡은 시험 결함 | `update` 시험이 snap PATH로 모델 캐시를 다시 써서 뒤 시험에서 agy가 제외돼 있었다(도구는 맞게 대기했다). 그 시험 뒤에 캐시를 원래 PATH로 되돌린다 |

## 19. v0.8.2: Orca workspace 값 (2026-11-25)
- 현상(현장 보고): switch로 미리 보기를 돌리면 판정은 "전환"인데 "워커 워크트리를 찾지 못했다"로 보류됐다. 정지 점검도 워커의 마지막 커밋을 읽지 못했다.
- 원인: Orca `worker-list`의 `workspace`는 `<워크스페이스 ID>::<경로>`다(이 머신에서 실제 값 `7909a5da-…::/home/…/orca-orchestration-kit` 확인). 키트는 그 값을 경로로 썼다. 시험의 가짜 Orca가 경로만 줘서 시험이 잡지 못했다.
- 수정: `kitlib.workspace_path`(`::` 뒤를 경로로, Windows `C:`는 그대로)를 status·quota_switch·cleanup_worker가 쓴다. 가짜 Orca도 실제 꼴로 준다.
- 시험 134/134: 값 풀기(ID 붙은 꼴·경로만·Windows 경로·dict), status가 그 꼴에서 경로와 마지막 커밋을 읽음, 한도 전환 전체 흐름이 실제 꼴로 통과.

## 20. v0.8.3: 커밋 없음 기준·필터 중단 (2026-11-25)
- 시험 139/139(이전 134 + 5): 투입 28분째 워커는 앞 차수 커밋(61분 전)으로 '커밋 없음'이 아니고, 투입이 70분 전이면 '커밋 없음 · 투입 …'. 필터 중단: 도구 결과 속 'usage policy'는 오탐하지 않음, 마지막 응답의 차단 문구는 `필터 중단 X (dispatch): "API Error: … Usage Policy"`, quota_switch가 같은 절차로 넘기고 기록 kind=filter, 그 모델(claude-sonnet)은 그 작업의 후보에서 빠지고 다른 작업은 그대로.
- `worker-read` 실제 꼴: 작은 워커(claude haiku, 읽기 전용)를 띄워 확인하고 해제했다. `result.transcript.messages[]`, 메시지마다 `role`(user/assistant/tool)·`blocks[]`(text·tool-call·tool-result)·`timestamp`. 판정은 이 꼴을 쓴다.
- 시험이 잡은 결함: 같은 작업을 같은 초에 두 번 넘기면 보존 브랜치 이름이 겹쳐 보존이 실패했다(시험이 가끔 실패). 이름에 dispatch 끝자리를 붙였다.
- 실제 필터 중단 화면으로는 시험하지 못했다(일부러 만들 수 없었다). 문구 목록이 현장 CLI의 실제 차단 문구를 못 맞추면 정지 점검에 안 나온다 — 그때는 그 문구를 알려 주면 목록에 넣는다.

## 21. v0.8.4: 필터 중단을 화면·메일에서 (2026-11-25)
- 현장 보고: codex 안전 필터 안내는 transcript에 없다(`worker-read --limit 8`·`--limit 40` 모두 마지막 블록이 tool-call·tool-result). 화면과 escalation 메일(stopReason=errored)의 Last output에만 있다. 원문(메일 3통 동일): `ⓘ This content can’t be shown` / `We take extra care with some cybersecurity requests. If you’re doing authorized security work, apply for Daybreak to` …
- `worker-read --source terminal` 실제 꼴: 작은 워커(claude haiku, keep-open)를 띄워 읽고 해제했다. `result.terminal = {handle, status, tail: [화면 줄…]}`, `result.status = {worker, terminal, liveness}`.
- 시험 146/146(이전 139 + 7): 화면 안내 줄 판정(굽은·곧은 따옴표, 아이콘 떼기, 문장 속 인용 무시, Claude Usage Policy), 화면 끝에서 필터 중단, 메일 Last output에서 멈춘 워커의 필터 중단, 문서 인용만 있는 메일은 오류 중단, 멈춘 워커는 stop 생략하고 이어받기(codex-sol은 그 작업 후보에서 제외), 넘긴 dispatch의 메일은 다시 안 잡음, errored인데 문구 없음 → 오류 중단.

## 22. v0.8.5: 실제 escalation 메일 꼴·키트 적용 커밋 (2026-11-25)
- 현장 확인(실제 메일 3통에 키트 함수를 돌림): `inbox`를 `--full` 없이 읽으면 본문이 221자에서 `… (truncated; --full)`로 잘리고 payload는 `{taskId, dispatchId}`뿐. `--full`이면 본문 전체(1,274자)와 `"stopReason":"errored"`가 오지만 payload가 JSON 문자열. '…stopped on an error…'는 제목에만 있고 본문은 '… ended its turn on an error'. 필터 메일 2통의 Last output에서는 `This content can’t be shown`이 잡혔고, 한도 메일(agy `⚠ Individual quota reached … Resets in 2h22m45s.`)은 안 잡혔다.
- 수정: `inbox --full`, `kitlib.msg_payload`(문자열이면 JSON으로), errored = payload stopReason · 제목 · 본문 문구, 한도 문구가 있는 Last output은 건너뜀.
- 시험 150/150(이전 146 + 4): 가짜 Orca가 실제 꼴로 준다(`--full` 없으면 잘린 본문·stopReason 없는 dict, `--full`이면 문자열 payload). payload 풀기·errored 판정, 한도 메일은 필터·오류 중단 아님, `upgrade --apply`가 `git add -- <키트가 바꾼 경로>`(.gitignore·installed.json 포함, `-A`·`.`·`orchestration/` 없음)를 출력, 미리보기 끝에 '적용 직전에 매니저에게 알린다'.

## 23. v0.9.0: 범위 관리 (2026-11-25)
- 시험 157/157(이전 150 + 7): guarantees 없음·열린 약속('언제나') 경고, 구현 명세에 보장/범위 밖 절과 점검표('## 항목' 절만, `- [예]` 제외, 종료 절 앞), 검토 명세는 '보장 항목을 깨는 실행 반례만 차단'과 '점검표를 먼저 본다', 같은 계열 차단 2회 → 범위 결정 필요, 계열이 달라도 3회 → 검토 반복, 통과 판정이 오면 연속이 끊김(`--no-orca`), 검토에서 구현한 모델 −3 추가·이번 결정에서 뺀 모델은 '검토 독립성' 이유로 제외.
- 실제 운영 효과(첫 검토 차단 수, 보완 횟수)는 현장 적용 뒤 이정표 지표로 본다.

## 24. v0.9.1: 코드 검토 지적 (2026-11-25)
- 지적 6건을 현재 코드에서 모두 확인했다(이전 판 지적이 아니었다). 5건은 고쳤고 `auto_update` 기본값은 사용자 결정(자동 업데이트 요청)대로 daily를 두고 off 사용처를 적었다.
- 시험 165/165(이전 157 + 8): 이메일 도메인(example.com·하위 도메인 통과, notexample.com 걸림), pre-push(설정 못 읽으면 막고 이유 출력, `KIT_HOOK_NO_CONFIG_OK=1`이면 통과), cleanup(둘째 쪽 워커 목록의 진행 중 디스패치로 보류, `--cursor` 호출), launch(JSON `result.task.id`로 Task ID — 출력 속 다른 `task_…` 문자열에 속지 않음, worker-start 실패면 `task-update --status failed --result cancelled`), 쓰기 잠금(두 스레드가 차례로, 잠금 파일은 .gitignore), quota_watch 안전장치(실행 기록에 completed가 있으면 `automations edit --disabled`와 매니저 알림, 사전 확인은 그래도 1).
- 시험이 잡은 시험 결함: pre-push 시험이 상대 경로로 훅을 불러 `cd` 뒤 파일을 못 찾았고, '막는다' 시험은 그 오류(종료코드 2)로 잘못 통과하고 있었다. 절대 경로로 부르고 막은 이유 문구까지 확인한다.

## 28. v0.12.0: 정해진 전이 (2026-11-28)
- 시험 204/204(이전 197 + 7): 가짜 Orca 메일함의 worker_done(payload는 JSON 문자열, Orca 실제 꼴)으로 한 작업의 사슬을 끝까지 돌렸다 — `--dry-run`은 상태·투입 그대로, 구현 `결과: 통과` → status review·검토 투입(dispatch `X-AD-RV`)·이력(advance·dispatch 이벤트), 같은 메일 다시 처리 안 함, 검토 `판정: 차단 · H 1 · M 2 · 계열: 영역분리` → route_feedback에 major·계열, 검토 워커 워크트리의 보고서가 보완 명세(`X-AD.retry-r1.md`)에 들어감, status running, 보완 통과 → 2회차 검토(`X-AD-RV-r2`), 같은 계열 두 번째 차단 → 보완을 내지 않고 hold(YAML 유효) + `[판단] 범위 결정 필요`, 모르는 dispatch·형식 없는 보고는 `[판단]`. 한도 전환 기록에 이벤트를 더하다 낸 인자 충돌(kind 두 번)은 기존 한도 전환 시험 6개가 잡았다.
- 확인하지 못한 것: 실제 Orca worker_done 메일의 payload 키(`outcome`·`reportPath`)와 `createdAt` 이름. 없으면 본문 첫 줄과 기본 증적 경로로 동작하고, 시각을 모르면 나이 검사를 건너뛴다(기대 상태·뒤따른 투입 검사는 그대로).

## 27. v0.11.0: 시험 토큰과 긴 명령의 멈춤 (2026-11-26)
- 시험 197/197(이전 180 + 17). run_logged: 2초 제한에 걸린 `python3 -X faulthandler` 무한 반복 → rc 124, 로그에 `File "<string>", line 3 in spin` 스택, 요약에 `FAILED tests/test_a.py::test_x`, 셸의 '코어 덤프됨' 알림 없음, 남은 프로세스 없음. 정상 종료는 종료코드 그대로(3), 출력은 로그로만, 배경으로 남긴 자식 프로세스 정리, 실행 표시 파일 정리.
- 정지 점검: 워커 워크트리에서 도는 `bash -c 'sleep 60; :' fakepytest` → '긴 명령 … 시간 제한 없음 (pid N)'·`kill -ABRT N`, 예약 점검이 '멈춤 1'로 매니저 Run에 알림. 같은 명령을 run_logged로 띄우면 경보 없이 워커 줄에 '긴 명령 0분째(제한 1분)', run_logged를 TERM으로 끄면 묶음도 끝남. 확인 창 대기(가짜 Orca worker-show agentWait, 프롬프트 `rm -f "$EV/$f.raw.log"`) → 고쳐 쓸 꼴과 deny 명령(승인 ID), 예약 점검이 `orchestration deny --dispatch … --prompt apr_1 --reason …`을 한 번만 보낸다. 문맥: 가짜 Claude 세션 기록(호출 2, 마지막 401k) → 워커 줄 '문맥 401k·호출 2 (기준 넘음)', '문맥 큼', usage.py 표 행(호출당 301k, 시험 관련 67%). 검토 2회차 effort high → medium, `--effort`를 주면 그대로. 계정별 한도: 고른 계정은 지금 값, 다른 계정은 마지막 값과 나이, 이 세션 표시.
- 실데이터: usage.py — 이 키트 세션(호출 521, 호출당 339k, 시험 관련 26%), 실사용 저장소(LIG) 30일 — 매니저 세션 하나가 호출 4480·호출당 379k·1.7G, 구현 세션들 호출당 31만~56만·시험 관련 27~42%(현장 표와 같은 꼴). models.py quota 계정별 — claude 계정 2개 중 고른 계정만 값, codex는 고르지 않은 계정의 값을 세션 기록에서 채움(이 세션의 codex 계정이 Orca가 고른 계정과 달랐다: 현장 7번 제안과 같은 상황).
- 확인하지 못한 것: 실제 확인 창 escalation 메일의 승인 ID 키 이름(키 이름에 approval이 든 값을 찾는다)과 worker-show `observation.agentWait`의 실제 꼴. 현장에서 처음 걸릴 때 `status.py --json`의 human_wait를 보고 맞춘다. 승인 ID를 못 찾으면 자동 거절은 하지 않고 deny 명령만 보인다. '긴 명령' 프로세스 감시는 Linux만(/proc).

## 26. v0.10.1: 전체 시험 슬롯 (2026-11-25)
- 시험 180/180(이전 172 + 8): 슬롯 2개 중 1번 8초·2번 1초 점유 → 기다리는 쪽이 약 1초에 2번을 잡는다(예전 코드는 1번을 기다려 7초 넘게). 종료코드 보존. 다른 워크트리가 슬롯을 잡은 동안 가짜 Orca 워커의 워크트리에서 기다리면 정지 점검이 '슬롯 대기 … 1번째 · 사용 중 1/1'(`--stale-min 0`에서도 '커밋 없음' 아님)과 '슬롯 줄'을, 현황이 워커 줄 '슬롯 대기'와 '## 슬롯' 요약을 보이고, 슬롯이 비면 표시 파일이 지워져 사라진다. 제안값 표(16코어·64 GiB → 4, 4·8 → 1, 16·16 → 2, 상한 8), 설치한 kit.yaml에 제안값과 근거 주석.
- 실제 기계(24코어·30 GiB)의 제안값은 3. 시험 하나의 메모리를 모르므로 보수적으로 잡았고, 줄이 길 때 정지 점검이 이 값을 같이 보인다.
- 실행 격리 모듈(`run.sh` RUN_HEAVY)은 원래 모든 슬롯을 돌며 기다린다. 원격일 수 있어 표시 파일은 넣지 않았다.

## 25. v0.10.0: 워크트리 정리 (2026-11-25)
- 시험 172/172(이전 165 + 7): 실제 git 워크트리 8개와 가짜 Orca로 판정 — merged·깨끗함 → 정리, 검토 보고서가 main에 → 정리, 진행 중 디스패치·고정·최근 활동 → 남겨 둠, merged인데 커밋하지 않은 변경·머지 안 된 커밋 → 확인 필요, 주인 없음(main에 있음) → 확인 필요(`--orphans`). 미리 보기는 지우지 않고, `--apply`는 정리만(끝난 워커 터미널 해제 → `worktree rm --delete-branch`, sweep.log 기록), `--orphans`는 주인 없는 것까지. 정지 점검 줄, launch 자동 정리 주기·끄기.
- 실데이터 미리 보기(지우지 않음): kit.yaml이 없는 실사용 저장소(메인 master)의 워커 워크트리 59개 → 정리 13, 확인 필요 36(주인 없음이지만 master에 다 있음 20 — `--orphans`로 정리 가능, 주인 없음 + master에 없는 커밋 15, merged인데 변경 남음 1), 남겨 둠 10. 처음엔 메인을 'main'으로만 찾아 거의 전부 '확인 필요'였다 → 설정이 없거나 그 브랜치가 없으면 메인 워크트리의 현재 브랜치를 쓴다.

