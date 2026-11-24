# orca-orchestration-kit

AI 에이전트 다중 세션으로 소프트웨어를 개발하는 워크플로를 **어떤 프로젝트에도 다시 쓸 수 있게** 묶은 키트다.
템플릿 저장소와 Claude Code 스킬로 되어 있다. 사람 1명과 AI 세션 수십 개가 함께 일하는 실전 프로젝트에서 검증한 운영 방식을 일반화했다.

- 세션 3계층: **마스터**(사용자 소통·결정) → **작업관리 매니저**(Orca Run 운영) → **작업 세션**(요구사항 1개 = 세션 1개)
- 작업 DAG와 웨이브: W0 계약 → G-0 기반 머지 → W1 요구사항(병렬) → W2 모듈 통합 → W3 체계 통합
- 브랜치·머지 규약, 쓰기 범위 소유권, 번호 문서 체계, 요구사항 추적(RTM)·TBD·증적, 과정 기록 3종
- 단일 HTML 대시보드(오프라인), 실행 격리 모듈(선택), 검토 단계 모듈(선택)

버전: **v0.4.1** (2026-11-16)

---

## 1. 개념도

### 1.1 세션 3계층
```
 사용자
   │  방향·제약·결정
   ▼
 마스터 세션 ───────────── 사용자와 소통 · 결정 · 지시. 저장소에 쓰지 않는다. 구현하지 않는다
   │   ▲
   │   └── run:<마스터 Run> 메일함 ◄── 매니저의 보고·질문 (사용자 결정이 필요한 것만 위로)
   ▼ send --to run:<작업 Run>
 작업관리 매니저 (L0) ──── 상시 세션(worker-abandon 후 메일함 운영). 작업 투입 · 질문 응답 · 머지 ·
   │                       문서·현황 갱신 · 종료 정리. 구현하지 않는다
   ├─ L1 모듈 통합 세션 ×모듈 수 ─ 상시. req 머지·모듈 스모크·머지 거부 권한
   └─ L2 요구사항 세션 ×N ──────── 요구사항 1개 = 작업 1개 = 세션 1개 = 브랜치 1개 = 워크트리 1개
```

### 1.2 브랜치
```
 main ◄──────────── int/common ◄── W0 작업(F-xx)          머지: --no-ff, 첫 줄 [작업ID] / [G] / [L0]
  │  ▲                                                    rebase·force push 금지
  │  └──── int/<모듈 A> ◄── req/<ID> ◄── L2 세션           하위는 상위를 merge로 따라간다
  │  └──── int/<모듈 B> ◄── req/<ID>
  └ 머지 주체: int/* → main 매니저, req/* → int/<모듈> L1 통합 세션
```

### 1.3 웨이브
```
 W0 계약·공통 기반 ─► G-0 기반 머지 ─► W1 요구사항(병렬) ─► W2 모듈 통합 ─► W3 체계 통합
 F-01 계약/인터페이스    int/common → main  req/<ID> ×N          I-<모듈>(상시,       S-01 종단
 F-02 공통 라이브러리    int/<모듈> 생성     deps 풀린 것부터     req마다 점진 머지)    S-02 확장·검증
 F-03 골격·빌드
```

---

## 2. 빠른 시작

### 2.1 스킬 설치 (심볼릭 링크)
```bash
./install.sh --dry-run          # 어디에 걸리는지 확인
./install.sh                    # Orca 프로필: $ORCA_CLAUDE_CONFIG_DIR/skills/  · 일반 Claude Code: ~/.claude/skills/
./install.sh --dir ~/.claude/skills   # 경로 직접 지정
./install.sh --uninstall
```
링크라서 이 저장소를 `git pull`하면 스킬도 바로 최신이 된다. 필요: Python 3 + PyYAML, git, (실행 격리) docker·rsync·ssh.

### 2.2 새 프로젝트에 적용
Claude Code(마스터가 될 세션)에서: **"새 프로젝트를 오케스트레이션으로 시작"**, **"orchestration kit init"**, **"3계층 세션으로 개발"**.
스킬이 묻고 → 깔고 → W0 초안을 같이 만들고 → Orca Run·매니저를 띄운다. 직접 돌리려면:
```bash
K=skills/orca-orchestration-kit/scripts/kit.py
python3 $K init --target ~/src/my-app --name "내 앱" --slug myapp \
    --modules "api:API 서버:API,web:웹 UI:WEB" --review-preset none --run --dry-run   # 계획만
    # 원격 push가 배포를 일으키는 저장소: --git-remote ''(로컬 전용) 또는 --protected main(승인 필요 브랜치)
python3 $K init --target ~/src/my-app ...                                      # 설치
python3 ~/src/my-app/orchestration/tools/validate_dag.py --repo ~/src/my-app    # W0 초안 검증
git -C ~/src/my-app branch int/common main                       # W0 브랜치 (커밋 뒤)
python3 $K bootstrap --target ~/src/my-app --step hooks          # pre-push 훅(로컬 전용·보호 브랜치·배포 제한 파일)
python3 $K bootstrap --target ~/src/my-app --step master-run     # 마스터 Run
python3 $K bootstrap --target ~/src/my-app --step manager        # 매니저 워커 → 기동 보고를 기다린다
python3 $K bootstrap --target ~/src/my-app --step abandon --work-run <매니저 Run>   # 메일함 운영으로 전환
python3 $K bootstrap --target ~/src/my-app --step dashboard      # http://127.0.0.1:8765/
python3 $K bootstrap --target ~/src/my-app --step attach         # 새 마스터 세션이 기존 마스터 Run을 이어받을 때
python3 $K status  --target ~/src/my-app [--stall]               # 현황·정지 점검·메일함 미처리(메시지 ID)
python3 $K upgrade --target ~/src/my-app [--apply]               # 키트 새 버전 반영(diff → 확인 → 적용, 3방향 병합)
```
`bootstrap`은 한 번에 한 단계다(`--step` 필수). 매니저 기동 보고를 받기 전에는 abandon하지 않는다.
`bootstrap`에 `--dry-run`을 붙이면 Orca 명령을 실행하지 않고 출력만 한다.

### 2.3 설치 후 일상 운영 (매니저)
```bash
python3 orchestration/tools/status.py [--stall]          # 현황·투입 가능·메일함 미처리·정지 점검 (깨어날 때·대기 전)
python3 orchestration/tools/validate_dag.py              # DAG 수정 후 항상
orchestration/tools/launch.sh R-API-001 R-WEB-001        # 선행·status·기준 브랜치 확인 → 모델 자동 라우팅 → 렌더링 → task-create → worker-start → running
python3 orchestration/tools/models.py plan               # 투입 가능 작업의 구독·모델·effort 배분 미리보기 (route <ID>: 근거, discover: 쓸 수 있는 모델, stats: 실적·사용량)
python3 orchestration/tools/models.py cooldown codex --min 120   # 사용량 한도에 걸린 구독을 잠시 뺀다
orchestration/tools/launch.sh --review R-API-001         # 구현 검토(보고서 브랜치 req/R-API-001-RV, 메인 기준)
orchestration/tools/launch.sh --retry 지적.md --suffix r1 R-API-001   # 남은 지적 재투입(req/R-API-001에서)
python3 orchestration/tools/archive_tasks.py             # merged 작업을 tasks.archive.yaml로 (tasks.yaml을 작게)
orchestration/tools/sync_main.sh [--all] [--push]        # int/common → main (체크아웃을 바꾸지 않음, 원격은 --push일 때만)
orchestration/tools/cleanup_worker.sh r-api-001 req/R-API-001 int/api   # 머지 직후 정리(4조건)
orchestration/tools/slot.sh test -- npm test             # 워크트리 사이 전체 시험 동시 수 제한
python3 orchestration/tools/evidence_lint.py             # 증적 위생(환경 변수 덤프·홈 경로·이메일·비밀값)
orchestration/tools/dashboard/serve.sh --orca            # 대시보드
```
키트 시험: `tests/smoke.sh` (임시 폴더, Orca 불필요).

---

## 3. 모듈 목록

| 모듈 | 키트 경로 | 설치 위치 | 내용 |
|---|---|---|---|
| 세션 체계 | `templates/sessions/` | `orchestration/sessions/`, `orchestration/templates/` | `MASTER.md`, `MANAGER.md`(상시 핵심), `procedures/merge-gate.md`·`review.md`(필요할 때 읽는 절차), `worker-spec.md`(L2), `integration-spec.md`(L1), `review-spec.md`(검토). 프로젝트 규칙은 `*.local.md` 덧붙임 |
| 작업 DAG | `templates/orchestration/tasks.yaml`, `tools/` | `orchestration/` | 스키마 주석(`hold`·`agent/model/effort` 포함), `validate_dag.py`, `render_spec.py`(`--review`·`--retry`), `launch.sh`(=`launch.py`), `status.py`(정지 점검), `archive_tasks.py`, `task_status.py`, `cleanup_worker.sh`(4조건), `sync_main.sh` |
| 모델 라우팅 | `tools/routing.py`, `models.py`, `model_catalog.yaml`, `templates/orchestration/models.yaml` | `orchestration/tools/`, `orchestration/models.yaml` | 작업마다 구독(Claude·Codex·Antigravity·Grok CLI, Orca `--agent`)·모델·effort 자동 선택. 난이도·태그 분류 → 능력·소모·속도·강점 점수 → 투입 결과 학습·검토 판정 반영, 구독별 동시 상한·쿨다운·교차 검토, Orca 사용량 집계 참고. 근거를 한 줄로 남긴다 |
| 운영 보호 | `tools/` | `orchestration/tools/` | `slot.sh`(동시 실행 슬롯), `evidence_lint.py`(증적 위생), `hooks/pre-push` + `install_hooks.sh`(로컬 전용·보호 브랜치·배포 제한 파일) |
| 구조·규약 | `templates/orchestration/README.md` | `orchestration/README.md` | 계층, 브랜치·머지, 웨이브, 쓰기 범위 소유권 표, 동시 실행, 완료 기준, 소통, 종료 정리 |
| 공통 규칙 | `templates/AGENTS.md`, `CLAUDE.md` | 저장소 루트 | 개인 이메일 금지, 배포 제한 파일 금지, 컨테이너 빌드, 쓰기 범위 밖은 ask |
| 문서 체계 | `templates/docs/` | `docs/` | 번호 폴더, 파일명·개정이력·보관 사본 규칙, RTM, TBD 관리대장, 증적 양식, 과정 기록 3종(프로세스·지표 / 의사결정 / 운영 이슈·교훈) |
| 검토 단계 (선택) | `templates/docs/review-stages/` | `docs/01~04_*`, kit.yaml | 프리셋 `defense`(SRR/SFR/PDR/CDR), `product`(REQ/SPEC/DESIGN/RELEASE). 이름만 바꿔 쓰기 가능 |
| 대시보드 | `tools/dashboard/` | `orchestration/tools/dashboard/` | 설정 기반 단일 HTML 생성기, `serve.sh`, `snapshot.sh`, systemd user 서비스 예시, `tailscale serve` 사설망 공개 절차 |
| 실행 격리 (선택) | `tools/run/` | `scripts/run/` | 워크트리별 컨테이너 + 전용 네트워크, 격리 변수 훅, 원격 실행(rsync → 원격 컨테이너 → 결과 회수, 배포 제한 패턴 제외), GPU 지정, HEAVY 슬롯(flock), 지표 수집 |
| ROS 2 프로필 (선택) | `tools/run/profiles/ros2/` | `scripts/run/profiles/ros2/` | `ROS_DOMAIN_ID`·`GZ_PARTITION` 격리, setup 자동 source, colcon test 순차 |
| 스킬 | `skills/orca-orchestration-kit/` | 사용자 스킬 경로(링크) | `SKILL.md`(흐름 고르기·status·이어받기), `references/init.md`·`upgrade.md`, `scripts/kit.py` |
| 예제 | `examples/demo-project/` | - | 가상 "할 일 관리 웹앱"(모듈 2, 작업 6 + 게이트 1)에 적용한 결과: tasks.yaml, 렌더링된 명세 3개, 대시보드 |

파일 배치의 정본은 [`kit-manifest.yaml`](kit-manifest.yaml)이다. `managed`는 키트 소유(upgrade 대상), `seed`는 한 번 깔고 프로젝트 소유다.
프로젝트별 값은 `orchestration/kit.yaml`(모델 라우팅 정책만 `orchestration/models.yaml`)에 있다(모듈, 웨이브, 경로, git 원격·보호 브랜치·배포 제한 패턴, 검증 단계, 검토 단계·검토 모델, 슬롯, Orca Run ID, 실행 격리, 대시보드).

연계(선택): 요구사항 세션 안에서 `claude-codex-workflow` 플러그인(Claude 기획 → Codex 구현 → Claude 검수)을 쓸 수 있다. 의존성은 아니다(SKILL.md '연계').

---

## 4. 실전에서 얻은 교훈 (일반화)

**세션 구조**
- **1 task = 1 session의 목적은 컨텍스트 관리다.** 코디네이터가 "직접 해"를 문구대로 받아 구현하면 컨텍스트가 비대해진다. 지시는 문구가 아니라 운영 원칙의 목적으로 해석한다. 코디네이터 컨텍스트에는 결과 요약만 남긴다.
- **사람과 대화하는 창구와 일을 돌리는 루프는 분리한다.** 한 세션이 겸하면 한쪽이 막힐 때 다른 쪽도 멈춘다 → 마스터/매니저 분리.
- **상시 세션은 작업 단위 워커와 생명주기가 다르다.** 상시 매니저를 워커로 두면 턴마다 '보고 없이 멈춤' 경고가 쌓인다 → `worker-abandon` 후 Run 메일함(`send --to run:`)으로 소통. 기존 Run은 `run-use`로 인수한다. 대기 경고는 무시한다.
- **장기 세션은 대화(터미널) 단위와 작업(디스패치) 단위가 다르다.** 보내기 전에 최신 디스패치를 확인한다. 닫힌 디스패치로 거부된 보고는 원문으로 처리한다.
- **사용자 결정만 위로 올린다.** 요구 해석·범위·비용·외부 선택만 사용자에게. 내부 인터페이스·기술 판단은 하위에서 끝내고 사후 보고한다.

**병렬 개발**
- **계약 우선이 병렬화의 전제다.** 인터페이스·공통 규약·모듈 골격(진입점까지)·쓰기 범위를 먼저 고정해야 세션 수십 개를 동시에 돌린다.
- **관례는 아는 즉시 하류 명세로 흘려보낸다.** 계약의 관례가 구현보다 늦게 정해지면 같은 워커에 보완이 여러 번 간다 → 투입 전 인계 사항(`specs/<ID>.extra.md`).
- **공통 결함은 한 곳에서 고친다.** 여러 워커가 같은 인프라 결함을 겪으면 각자 우회시키지 말고 인프라 작업으로 추가한다. 운영 도구는 결국 문제를 겪은 하위 세션의 보고에서 나온다.
- **통합 스모크가 단위시험의 빈틈을 메운다.** 입력 순서·타이밍 경합, 모듈 사이 약속 불일치, 측정 착시는 통합 단계에서만 드러났다 → L1에 머지 거부 권한.
- **'모의로 먼저, 실제로 다시'를 명시한다.** 인프라가 덜 됐을 때 모의 입력으로 진행하되 보고서·RTM에 '부분(모의)'을 숨기지 않는다(◐).

**자원과 격리**
- **세션 수가 아니라 무거운 작업이 병목이다.** 동시성 단위는 자원 종류별 슬롯(HEAVY, flock)이다. 한도는 2개씩 늘려 실측한다.
- **격리는 처음부터 넣는다.** 동시 시뮬 컨테이너의 토픽 혼선은 간헐적이라 늦게 발견되고 머지된 결과까지 재검증하게 만든다. 해시 기반 ID는 충돌한다 → 전용 네트워크. 같은 컨테이너 안의 병렬 시험도 섞인다 → 시험 기본 순차.
- **무거운 일은 원격으로, 로컬은 세션 호스트로.** 보낼 때 배포 제한 파일 패턴은 제외 목록에 넣는다.

**템플릿과 측정**
- **템플릿에 들어간 값은 복제된다.** 개인 이메일이 든 템플릿은 확인 프롬프트를 띄워 자율 실행을 멈췄다 → 공용 주소. 골격 코드의 결함(종료 경합)도 수십 곳에 복제됐다 → 처음부터 공용 도우미.
- **지연은 계기별로 나눠 잰다.** 서로 다른 입력의 시각을 섞어 재면 수십 배 과대 측정된다.
- **임계값은 실제 쓰일 데이터 분포에서 검증한다.** 합성 데이터로 정한 값이 실제 장면을 전부 '불가'로 판정한 사례가 있다.
- **모의 입력은 실제보다 많이 알 때가 많다.** 시나리오 시험의 데이터 흐름 전제(누가 무엇을 받는가)를 실제 구성과 대조한다.
- **스크립트 함정:** `tr` 문자 범위(`.-\n`이 범위로 해석), GPU 모델 번호(`3090`)를 장치 인덱스로 해석 — 키트 스크립트에 반영했다.

**관문·검토·대기 (v0.2, 상세는 [`docs/LESSONS.md`](docs/LESSONS.md) L-01 ~ L-13)**
- **main 머지 관문 = 범위 확인 → build → 전체 시험 → 체계 스모크.** 스모크는 기본 설정으로(덧씌움 금지) 돈다. 관문 체크아웃은 끝날 때까지 건드리지 않는다. 시뮬 관문은 실시간 비율·부하 기준 미달이면 PASS라도 무효다. 관문 도구는 전제 상태를 기다리고 실패 로그를 남긴다.
- **범위 확인은 실패하면 멈추게 쓴다.** `;`로 이은 확인은 출력만 하고 머지를 막지 못한다. 비ASCII 경로는 `core.quotePath=false`가 있어야 비교된다.
- **검토 보고서 브랜치는 기준 브랜치에서 만든다.** 대상 위에 커밋한 보고서를 머지하면 검토가 막은 코드까지 들어온다. 보고서는 경로만 가져온다.
- **코드 검토는 실행으로 재현되는 반례만 차단한다.** 설계 검토는 수렴 모양을 보고 마지막 좁은 검토로 닫는다. 같은 계열 지적이 반복되면 구조 수정, 그래도면 범위 축소를 사용자에게 묻는다. 결함 수정과 새 기능은 섞지 않는다.
- **끝난 작업 뒤의 일은 새 작업으로 낸다.** 실행 중 워커에 보낸 메시지는 후속 작업이 된다. '이어서 하라'를 보내면 양쪽이 서로를 기다린다. 대기 전에 실행 중 워커가 있는지 확인하고, 정지 점검으로 멈춘 이유를 한 줄로 분류한다. 마스터 보고는 `--to run:`.
- **큰 구현은 세션당 4묶음 안팎으로 쪼갠다.** 문맥이 모자라면 부분 완료 + 남은 목록으로 끝낸다.
- **무인 셸 명령:** `pgrep -f` 패턴은 자기 명령줄과 맞는다(pid로 기다린다). 변수 경로 rm은 `"${VAR:?}"`로 쓴다(훅 프롬프트에 멈추지 않게).

**효율·보호 (v0.3, L-14 ~ L-19, 두 번째 실사용 프로젝트)**
- **세션이 매번 읽는 문서를 작게 둔다.** 워커는 명세와 AGENTS.md만 읽는다. merged 작업은 보관 파일로 옮긴다(103KB → 8KB 실측). 절차는 필요할 때 여는 파일로.
- **전체 시험은 슬롯으로 제한한다.** 워크트리마다 동시에 돌리면 메모리가 부족해 IDE까지 죽는다.
- **원격 push가 배포를 일으키는 저장소는 로컬 전용으로 운영한다.** 명세가 `git push . HEAD:<브랜치>`를 쓰고 pre-push 훅이 막는다. 메시지 본문은 작은따옴표로(백틱이 실행된다).
- **증적에 환경 변수·홈 경로를 남기지 않는다.** 보고 전 `evidence_lint.py`.

---

## 5. 저장소 구조
```
README.md  install.sh  kit-manifest.yaml  VERSION
templates/
  AGENTS.md  CLAUDE.md
  sessions/       MASTER.md  MANAGER.md  procedures/{merge-gate,review}.md  worker-spec.md  integration-spec.md  review-spec.md
  orchestration/  README.md  tasks.yaml  kit.yaml  models.yaml  state/  templates/{evidence.md, blocks/*.md}
  docs/           README.md  requirements/  evidence/  process/  review-stages/
tools/
  kitlib.py kitcfg.py validate_dag.py render_spec.py task_status.py launch.sh launch.py status.py archive_tasks.py
  routing.py models.py model_catalog.yaml
  cleanup_worker.sh sync_main.sh slot.sh evidence_lint.py install_hooks.sh hooks/pre-push
  dashboard/      build.py serve.sh snapshot.sh dashboard.example.yaml README.md systemd/
  run/            run.sh run.conf.example cleanup_hook.sh metrics.sh README.md profiles/ros2/
skills/orca-orchestration-kit/   SKILL.md  references/{init,upgrade}.md  scripts/kit.py
tests/                           smoke.sh (키트 회귀 시험)
examples/demo-project/           answers.yaml tasks·RTM·TBD·증적, specs/, dashboard/index.html, make_demo.sh
docs/                            VERIFY.md  DESIGN-NOTES.md  LESSONS.md
```

---

## 6. 버전·변경 이력

| 버전 | 일자 | 내용 |
|---|---|---|
| v0.4.1 | 2026-11-16 | Orca 실기동 반영(`docs/VERIFY.md` 10장): Orca에서 꺼진 에이전트(`agent_unconfigured`)는 쿨다운 후 다시 라우팅, snap 격리 설치본(워커가 orca CLI를 못 부름)은 라우팅에서 뺀다 |
| v0.4.0 | 2026-11-16 | **모델 자동 라우팅**(`docs/DESIGN-NOTES.md` 6장). `launch.sh`가 agent/model 없는 작업을 분류(난이도 1~5·태그·임계 경로)해 구독 CLI(claude·codex·antigravity·grok)·모델·effort를 점수로 고른다. 카탈로그 `tools/model_catalog.yaml`(키트 소유) + 정책 `orchestration/models.yaml`(seed: 쓸 구독·목표·동시 상한·교차 검토·고정·덮어쓰기). 가용성은 각 CLI의 모델 목록으로 확인(`models.py discover`), 실적은 dispatch.tsv × tasks.yaml 결과 × 검토 판정(`models.py feedback`)으로 학습, 한도는 `models.py cooldown`, 사용량은 Orca 집계(`models.py stats`). tasks.yaml `difficulty`·`tags`·`objective`·`route` 추가, dispatch.tsv 칸 추가(에이전트·모델·effort·키·난이도), status.py에 구독별 진행 수·쿨다운. **기존 설치본:** upgrade하면 models.yaml이 새로 생기고 라우팅이 켜진다(끄려면 `routing.enabled: false`). 검증: `docs/VERIFY.md` 9장 |
| v0.3.0 | 2026-11-12 | 효율 검토 반영(`docs/DESIGN-NOTES.md` 4장, 교훈 L-14 ~ L-19). **결함:** upgrade가 키트 밖 파일을 덮어쓰던 것(→ `.kit-new`), status·cleanup의 진행 워커 오판·100개 잘림, launch가 선행·status를 안 보던 것, bootstrap `all`이 기동 보고 전에 abandon하던 것, 매니저 기동에 기존 워크트리 생성 플래그(`--display-name`), sync_main의 체크아웃 전환·자동 push. **효율:** 워커 고정 읽기 축소(AGENTS '읽을 것'), merged 보관(`archive_tasks.py`), MANAGER 분할(`procedures/`), SKILL 분할(`references/`), 정지 점검 도구(`status.py`, `hold`), 작업별 `agent/model/effort`, `launch.sh --review/--retry`, `slot.sh`, `evidence_lint.py`, pre-push 훅, 로컬 전용 운영(`project.git_remote`), 덧붙임 파일(`*.local.md`), upgrade 3방향 자동 병합·새 버전 알림, 회귀 시험 `tests/smoke.sh`. **기존 설치본:** `upgrade` 미리보기 → 로컬 전용이었으면 kit.yaml에 `git_remote: ""`부터 넣는다(`references/upgrade.md`). 검증: `docs/VERIFY.md` 8장 |
| v0.2.0 | 2026-11-09 | 실전 프로젝트 후반 교훈 13건 반영(`docs/LESSONS.md` L-01 ~ L-13, 근거 `docs/DESIGN-NOTES.md` 3장). MANAGER.md에 main 머지 관문(범위 확인·체크아웃 분리·유효 회차·미추적 증적)·검토 작업·대기와 정지 점검 장 추가, 후속 작업·실행 중 send·수정/기능 분리·작업 크기 규칙. 검토 명세 `review-spec.md` 신설(기준 브랜치·읽기 전용 대상·실행 재현 H), `render_spec.py`가 작업별 `spec_template`·`{target}`을 읽음. worker-spec에 범위·부분 완료, AGENTS에 '셸 명령'(pid 대기, `${VAR:?}`), integration-spec·sim 블록에 관문 유효성, MASTER에 정지 점검·범위 축소 선택지, 구조 문서 완료 기준·소통 표 갱신, 스킬에 '운영 점검'. **기존 설치본:** `upgrade`로 managed 파일 갱신, AGENTS.md(seed)는 `--include-seed` diff를 보고 '셸 명령' 절을 손으로 옮긴다. 검증: `docs/VERIFY.md` 7장 |
| v0.1.0 | 2026-11-06 | 최초 추출: 세션 3계층 명세, tasks.yaml 스키마·DAG 검증기·렌더러·투입·정리 도구, 브랜치·머지 규약, 문서 체계(RTM·TBD·증적·과정 기록 3종, 검토 단계 선택 모듈), 설정 기반 대시보드, 실행 격리 모듈(+ROS 2 프로필), Claude Code 스킬(init·bootstrap·status·upgrade), 예제 프로젝트. 검증: `docs/VERIFY.md` |

다음 버전 후보와 일반화 판단 기록: [`docs/DESIGN-NOTES.md`](docs/DESIGN-NOTES.md).
