# orca-orchestration-kit

AI 에이전트 다중 세션으로 소프트웨어를 개발하는 워크플로를 **어떤 프로젝트에도 다시 쓸 수 있게** 묶은 키트다.
템플릿 저장소와 Claude Code 스킬로 되어 있다. 사람 1명과 AI 세션 수십 개가 함께 일하는 실전 프로젝트에서 검증한 운영 방식을 일반화했다.

- 세션 3계층: **마스터**(사용자 소통·결정) → **작업관리 매니저**(Orca Run 운영) → **작업 세션**(요구사항 1개 = 세션 1개)
- 작업 DAG와 웨이브: W0 기획·유사 오픈소스 조사·계약 → G-0 기반 머지 → W1 요구사항(병렬) → W2 모듈 통합 → W3 체계 통합
- 브랜치·머지 규약, 쓰기 범위 소유권, 번호 문서 체계, 요구사항 추적(RTM)·TBD·증적, 과정 기록 3종
- 모델 자동 라우팅: 작업 난이도에 맞춰 구독 CLI(Claude·Codex·Antigravity·Grok)·모델·effort를 고르고 실적으로 학습
- 단일 HTML 대시보드(오프라인, 작업 보드), 실행 격리 모듈(선택), 검토 단계 모듈(선택)
- 배포: Claude Code 플러그인 마켓플레이스(자동 업데이트) 또는 심볼릭 링크

버전: **v0.11.0** (2026-11-26) · 라이선스: [Apache-2.0](LICENSE)

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
 W0 기획·계약·공통 기반 ► G-0 기반 머지 ─► W1 요구사항(병렬) ─► W2 모듈 통합 ─► W3 체계 통합
 F-00a 기획             int/common → main  req/<ID> ×N          I-<모듈>(상시,       S-01 종단
 F-00b 유사 오픈소스     int/<모듈> 생성     deps 풀린 것부터     req마다 점진 머지)    S-02 확장·검증
       조사·반영
 F-01 계약/인터페이스
 F-02 공통 라이브러리
 F-03 골격·빌드
```

---

## 2. 빠른 시작

### 2.1 설치

**A. 플러그인 마켓플레이스 (사용자용, 권장)**: 이 저장소에 push된 새 커밋을 자동으로 받는다. `<owner>/orca-orchestration-kit`은 이 저장소의 GitHub 주소(소유자/이름)로 바꿔 쓴다.
```
/plugin install orca-orchestration-kit --marketplace <owner>/orca-orchestration-kit
```
Claude Code 2.1.275 이전이면 두 단계로 한다:
```
/plugin marketplace add <owner>/orca-orchestration-kit
/plugin install orca-orchestration-kit@orca-kit
```
- **자동 업데이트는 한 번 켜야 한다.** `/plugin` → Marketplaces → `orca-kit` → auto-update 켜기. 켜면 세션을 시작할 때 새 커밋을 받는다. 켜지 않았으면 `/plugin marketplace update orca-kit`으로 받는다. 팀 플랜 관리자는 managed settings의 `extraKnownMarketplaces`에 `"autoUpdate": true`로 모두에게 켤 수 있다.
- **버전:** `plugin.json`에 `version`을 두지 않았다. 그래서 커밋마다 새 판으로 본다(버전을 올리지 않아도 갱신된다).
- **Orca 프로필:** Orca는 계정 프로필마다 Claude 설정 폴더가 따로다. 마스터로 쓸 프로필마다 한 번씩 설치한다.
- **스킬 이름:** 플러그인 스킬은 `/orca-orchestration-kit:orca-orchestration-kit`이다. "orchestration kit init"처럼 말로 불러도 된다.
- **프로젝트 파일은 따로 올린다:** 자동으로 바뀌는 것은 스킬과 키트 본체다. 각 프로젝트에 깔린 `orchestration/`은 `kit.py upgrade`(아래)로 반영한다.
- 비공개 저장소로 바꾸면 사용자 머신에 git 자격 증명이 저장돼 있어야 한다(`gh auth login` → `gh auth setup-git`).

**B. 심볼릭 링크 (키트를 고치는 사람용)**: 이 저장소를 직접 받아 링크를 건다. A와 함께 쓰면 스킬이 두 번 잡히므로 하나만 쓴다.
```bash
./install.sh --dry-run          # 어디에 걸리는지 확인
./install.sh                    # Orca 프로필: $ORCA_CLAUDE_CONFIG_DIR/skills/  · 일반 Claude Code: ~/.claude/skills/
./install.sh --dir ~/.claude/skills   # 경로 직접 지정
./install.sh --uninstall
```
링크라서 이 저장소를 `git pull`하면 스킬도 바로 최신이 된다.

필요: Python 3 + PyYAML, git, (실행 격리) docker·rsync·ssh. 모델 자동 라우팅은 쓸 구독 CLI(claude·codex·agy·grok), JEV 난이도 판정(선택)은 `TYPESAFE_API_KEY`.

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
python3 orchestration/tools/worktree_sweep.py [--apply]  # 끝난 워커 워크트리 정리(잃을 것이 없는 것만. launch.sh가 한 시간에 한 번 저절로)
python3 orchestration/tools/quota_watch.py install       # (운영 시작 때 한 번) Orca 예약 실행: 10분마다 사전 확인만, 한도 정지·풀림이 있으면 매니저에 메일 (remove로 해제)
python3 orchestration/tools/quota_switch.py [--dry-run]  # 정지 점검의 '한도 정지' 워커: 진행분 보존 → 정리 → 다른 구독으로 이어받기 (곧 풀리면·검토 독립성을 못 지키면 대기)
python3 orchestration/tools/models.py quota              # 구독별 잔여 한도(Orca 집계)와 라우팅 영향: 소진이면 초기화까지 제외, 빠듯하면 벌점
python3 orchestration/tools/models.py bench [--aa]       # 품질 근거(Terminal-Bench, 선택 Artificial Analysis) 다시 받기·카탈로그 추정과 비교
python3 orchestration/tools/models.py update [--check]   # 구독 CLI 업데이트 → 모델 목록 다시 읽기 → 카탈로그 계열 대조 (launch.sh가 하루 한 번 자동으로 한다)
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
| 모델 라우팅 | `tools/routing.py`, `models.py`, `model_catalog.yaml`, `templates/orchestration/models.yaml` | `orchestration/tools/`, `orchestration/models.yaml` | 작업마다 구독(Claude·Codex·Antigravity·Grok CLI, Orca `--agent`)·모델·effort 자동 선택. 난이도·태그 분류 → 능력·소모·속도·강점 점수 → 투입 결과 학습·검토 판정 반영, 구독별 동시 상한·쿨다운·교차 검토, Orca 사용량 집계 참고. 근거를 한 줄로 남긴다. 선택: JEV 난이도 판정(`jev:`, 기본 꺼짐, shadow → live). 구독 CLI 자동 업데이트(`routing.auto_update`, 하루 한 번)와 모델 계열 따라가기(`family:`, 가장 높은 판). 품질 = 공개 벤치마크(Terminal-Bench 4.1, CLI × 모델), 잔여 한도(Orca rateLimits: 소진 제외·빠듯 벌점). 한도 전환(`quota_switch.py`: 한도로 멈춘 워커를 다른 구독으로 이어받기) |
| 운영 보호 | `tools/` | `orchestration/tools/` | `slot.sh`(동시 실행 슬롯), `evidence_lint.py`(증적 위생), `hooks/pre-push` + `install_hooks.sh`(로컬 전용·보호 브랜치·배포 제한 파일) |
| 구조·규약 | `templates/orchestration/README.md` | `orchestration/README.md` | 계층, 브랜치·머지, 웨이브, 쓰기 범위 소유권 표, 동시 실행, 완료 기준, 소통, 종료 정리 |
| 공통 규칙 | `templates/AGENTS.md`, `CLAUDE.md` | 저장소 루트 | 개인 이메일 금지, 배포 제한 파일 금지, 컨테이너 빌드, 쓰기 범위 밖은 ask |
| 문서 체계 | `templates/docs/` | `docs/` | 번호 폴더, 파일명·개정이력·보관 사본 규칙, RTM, TBD 관리대장, 증적 양식, 과정 기록 3종(프로세스·지표 / 의사결정 / 운영 이슈·교훈) |
| 검토 단계 (선택) | `templates/docs/review-stages/` | `docs/01~04_*`, kit.yaml | 프리셋 `defense`(SRR/SFR/PDR/CDR), `product`(REQ/SPEC/DESIGN/RELEASE). 이름만 바꿔 쓰기 가능 |
| 대시보드 | `tools/dashboard/` | `orchestration/tools/dashboard/` | 설정 기반 단일 HTML 생성기. 개요는 작업 보드(현재 웨이브, 구독 칩, 접는 웨이브 × 모듈 격자, 모듈 거르기, 상태 열 카드에 배정 모델·투입 명령 복사), `serve.sh`, `snapshot.sh`, systemd user 서비스 예시, `tailscale serve` 사설망 공개 절차 |
| 실행 격리 (선택) | `tools/run/` | `scripts/run/` | 워크트리별 컨테이너 + 전용 네트워크, 격리 변수 훅, 원격 실행(rsync → 원격 컨테이너 → 결과 회수, 배포 제한 패턴 제외), GPU 지정, HEAVY 슬롯(flock), 지표 수집 |
| ROS 2 프로필 (선택) | `tools/run/profiles/ros2/` | `scripts/run/profiles/ros2/` | `ROS_DOMAIN_ID`·`GZ_PARTITION` 격리, setup 자동 source, colcon test 순차 |
| 스킬 | `skills/orca-orchestration-kit/`, `.claude-plugin/` | 플러그인 캐시(마켓플레이스) 또는 사용자 스킬 경로(링크) | `SKILL.md`(흐름 고르기·status·이어받기), `references/init.md`·`upgrade.md`, `scripts/kit.py` |
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
- **계약보다 먼저 기획과 유사 오픈소스 조사를 한다.** 비슷한 공개 프로젝트를 보지 않고 계약을 고정하면, 가져다 쓸 수 있는 것을 새로 만들거나 이미 알려진 구조 문제를 되풀이한다 → W0 초안의 `F-00a`·`F-00b`.
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
README.md  LICENSE  install.sh  kit-manifest.yaml  VERSION
.claude-plugin/   plugin.json  marketplace.json   (플러그인 마켓플레이스 배포: 저장소 루트 = 플러그인)
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
| v0.11.0 | 2026-11-26 | **시험 토큰과 긴 명령의 멈춤(현장 개선 요청, L-23).** (1) `run_logged.sh <초> <로그> -- <명령>`: 새 프로세스 묶음으로 띄워 그 자리에서 기다리고, 제한에 걸리면 묶음 전체에 SIGABRT(pytest faulthandler 스택이 로그에) → SIGKILL, 끝난 뒤 남은 묶음도 정리. 표준 출력은 종료 코드·걸린 시간·실패한 시험 이름·로그 끝 줄만(시험 한 번 = 호출 한 번). (2) AGENTS.md 셸 명령: 긴 명령은 시간 제한·한 번의 호출, timeout을 쓰면 timeout의 pid를 기다림, 시간 제한 없는 배경 명령을 둔 채 턴을 끝내지 않음, 삭제는 다른 명령과 한 줄에 묶지 않음. (3) 구현 명세: 통과 로그는 마지막 커밋에서 한 번씩, 작업 중엔 자기 폴더 시험만. (4) 검토 명세: 전체 시험은 매니저가 병합 때 한 번, 검토자는 반례·지정 확인만, 증적 삭제 문장 고정. `launch.sh --review` 2회차부터 effort 한 단계 낮춤(`kit.yaml review.recheck_effort_step`). (5) 정지 점검 '긴 명령'(워커 워크트리에서 시간 제한 없이 30분 넘게 도는 시험·빌드, Linux /proc, `kill -ABRT` 대상 pid)·'긴 명령 제한 넘김'·'확인 창 대기'(Orca worker-show, 빈 변수 의심 rm이면 고쳐 쓸 꼴과 deny 명령) → 예약 점검(`quota_watch.py`, 별칭 `stall_watch.py`)이 이것들로도 매니저를 깨우고, 승인 ID를 알면 빈 변수 의심 rm 확인 창을 스스로 거절한다(`kit.yaml watch.*`). (6) `usage.py`: 세션 기록(Claude Code·Codex)에서 작업별 호출 수·호출당 문맥·합계·시험 관련 비중. 현황 워커 줄에 지금 문맥·호출 수, 30만 토큰(`status.context_warn_k`) 넘으면 '문맥 큼'. (7) `models.py quota` 계정별: Orca는 고른 계정의 한도만 주므로 계정마다 마지막 값을 남기고(codex는 세션 기록의 rate_limits로 채움), 이 세션·최근 워커가 어느 계정인지 적는다 |
| v0.10.1 | 2026-11-25 | **전체 시험 슬롯(현장 피드백).** `slot.sh`가 슬롯이 여러 개일 때 1번 잠금만 기다리던 결함: 이제 `KIT_SLOT_POLL`초(기본 5)마다 모든 슬롯을 다시 보고 먼저 빈 것을 잡는다. 기다리는 동안·잡은 뒤 `<공용 git>/orca-kit/slots/`에 표시 파일(시각·워크트리·명령)을 남기고, `status.py`가 그것으로 워커 줄에 '슬롯 대기 N분째'를 붙이고 정지 점검에 '슬롯 대기'(그 워커의 '커밋 없음' 대신)와 '슬롯 줄'(대기 ≥ 슬롯 수이거나 10분 넘게: 잡은 쪽·대기 수·이 기계 기준 제안 슬롯 수)을 보인다. 설치 때 `slots.test`를 기계의 코어·메모리로 제안한다(코어 4개·메모리 8 GiB당 1, 1~8, `--test-slots`로 지정). 종료코드는 명령 그대로 |
| v0.10.0 | 2026-11-25 | **워크트리 정리(`worktree_sweep.py`)**: Orca 워크트리가 쌓이는 문제(실사용 저장소 하나에 워커 워크트리 59개). 이 저장소의 Orca 워크트리마다 어느 작업 것인지(Orca 워커 → dispatch.tsv 워크트리 이름 → 브랜치 `req/<ID>`) 찾고 '정리 / 확인 필요 / 남겨 둠'으로 나눈다. **정리**는 잃을 것이 없을 때만: 메인·고정(pin)·자식 있는 워크트리가 아니고, 진행 중 디스패치 없음, 커밋하지 않은 변경 없음, 1시간 넘게 쉼, 그리고 구현은 작업 merged + HEAD가 기준·메인에 들어 있음, 검토는 보고서 폴더가 메인에 들어왔거나 대상 merged, 통합(L1)은 merged + HEAD가 메인에. 지울 때 끝난 워커 터미널 해제 → `orca worktree rm --delete-branch`(워크트리 로컬 브랜치만, `req/*`·`int/*`는 이력이라 둔다) → 정리 훅 → 그 작업의 한도 전환 보존 브랜치. 주인 없는 워크트리는 `--orphans`로만(HEAD가 메인에 다 있을 때). `launch.sh`가 한 시간에 한 번 '정리'만 저절로 한다(`kit.yaml cleanup.auto_sweep`, 기본 true). 정지 점검에 '워크트리 정리 / 확인 필요' 줄. 설정이 없는 저장소는 메인 워크트리의 현재 브랜치(예: master)를 메인으로 본다 |
| v0.9.1 | 2026-11-25 | **코드 검토 지적 반영.** `cleanup_worker.sh`가 워커 목록을 페이지를 넘겨 전부 읽는다(100개 넘는 Run에서 끝나지 않은 디스패치를 놓치던 것). `hooks/pre-push`가 kit.yaml·PyYAML을 못 읽으면 **막는다**(전에는 빈 설정으로 통과해 보호가 조용히 꺼졌다. 설정 없이 쓰는 저장소는 `KIT_HOOK_NO_CONFIG_OK=1`). `launch.py`가 Orca ID를 JSON으로 읽고(정규식은 대비책), dispatch.tsv·tasks.yaml 쓰기를 파일 잠금(`state/.state.lock`)으로 감싸며(task_status·quota_switch도), task-create 뒤 worker-start가 실패하면 Orca Task를 `failed/cancelled`로 닫는다(고아 Task). `evidence_lint`의 이메일 허용 도메인을 정확히 비교한다(`notexample.com`이 `example.com`으로 통과하던 것). `quota_watch` 안전장치: 예약 실행 기록에 에이전트 실행이 생기면(사전 확인 의미가 바뀜) 스스로 끄고 매니저에게 알린다. `auto_update`는 기본 daily를 유지하고 재현성이 필요한 구간에서 off를 쓰라고 적었다 |
| v0.9.0 | 2026-11-25 | **범위 관리(교훈 L-22, 현장: 투입의 약 80%가 보완·검토)**. (1) tasks.yaml `guarantees`(보장하는 것)·`out_of_scope`(범위 밖) 닫힌 목록: 구현 명세에 절로 붙고 검토 명세는 그 항목을 깨는 실행 반례만 차단으로 삼는다. `validate_dag`가 L1·L2 투입 전 작업에 목록이 없거나 '언제나·모든·완전히' 같은 열린 약속이 있으면 경고. (2) 핵심 약속 점검표 `orchestration/checklist.md`(seed, '## 항목' 절): 구현·보완·이어받기 명세에 붙어 워커가 항목마다 시험 이름을 보고하고, 검토 명세에도 붙어 먼저 본다. (3) `models.py feedback --category`로 차단 계열을 남기면 같은 작업에서 같은 계열이 2회 연속(`kit.yaml review.scope_escalate_after`)일 때 정지 점검이 '범위 결정 필요', 계열이 달라도 3회 연속이면 '검토 반복'(--no-orca에서도). (4) 검토 대체 `routing.cross_review: model`: 독립 구독이 모자라면 같은 구독의 다른 모델 허용(한도 전환 포함). 구현한 모델 자신은 어느 설정에서도 −3 더 깎인다. 현장 적용은 이정표에 묶는다(`docs/UPGRADE-NOTES.md`) |
| v0.8.5 | 2026-11-25 | **결함(현장):** 메일에서 필터 중단을 찾지 못했다. 메일함을 `--full` 없이 읽어 본문이 221자에서 잘렸고(Last output 없음, payload에 stopReason 없음), `--full`이면 payload가 JSON 문자열이라 `.get`에서 오류가 났다. 이제 `inbox --full`로 읽고 `kitlib.msg_payload`가 문자열 payload를 풀며, errored 판정에 제목('Worker stopped on an error')과 본문('ended its turn on an error')을 넣었다. 한도로 멈춘 워커의 errored 메일('quota reached … Resets in …')은 필터·오류 중단으로 잡지 않는다(한도 정지가 맡는다). 시험의 가짜 Orca를 실제 꼴(잘린 본문·문자열 payload)로 바꿨다. **운영 절차(L-21):** 키트 적용 중 매니저의 기록 커밋에 키트 변경이 섞였다 → `upgrade.md`에 적용 직전·끝 알림 단계, `kit.py upgrade --apply`가 끝에 `git add -- <키트가 바꾼 경로>`를 출력, MANAGER.md에 '기록 커밋은 경로를 지정해 담는다' |
| v0.8.4 | 2026-11-25 | **필터 중단을 화면·메일에서도 찾는다**(현장: codex 안전 필터 안내는 transcript에 남지 않고 터미널 화면에만 찍힌다. Orca escalation 메일 `stopReason=errored`의 "Last output:"에 남는다). 감지 입력: (1) transcript 마지막 응답(전과 같음) (2) 진행 중 워커의 터미널 화면 끝(`worker-read --source terminal`의 `terminal.tail`) (3) 작업 Run 메일함의 errored escalation 본문 Last output — 이미 멈춘 워커도 잡는다. 화면·메일은 검토 대상 문서 글이 섞이므로 안내 블록의 고정 줄(`ⓘ This content can’t be shown`, `We take extra care with some … requests`, Claude `API Error: … Usage Policy`)과 **줄 전체가** 맞을 때만, 곧은·굽은 따옴표 모두. errored인데 한도도 차단 문구도 아니면 `오류 중단`으로 알려 사람이 본다. 이미 멈춘 워커는 worker-stop을 건너뛰고 이어받기, 넘긴 dispatch와 같은 작업의 옛 dispatch 메일은 다시 잡지 않는다 |
| v0.8.3 | 2026-11-25 | 현장 피드백 둘. **'커밋 없음' 오탐:** 보완 워커가 앞 차수와 같은 브랜치를 쓰면 앞 차수 커밋 시각으로 재서, 투입 28분째 워커가 '마지막 커밋 61분 전'으로 잡혔다. 이제 마지막 커밋과 이 투입의 시작(dispatch.tsv) 중 늦은 쪽으로 잰다. **필터 중단:** 한도가 아닌 이유(안전 필터·이용 정책 차단)로 턴이 끊기면 워커는 살아 보여도 일하지 않는다. 정지 점검이 워커의 마지막 응답(`worker-read`, assistant 글·오류 블록만, 도구 결과는 보지 않는다)에서 차단 문구를 찾아 `필터 중단`으로 알리고, `quota_switch.py`가 한도 전환과 같은 절차(보존 → 정리 → 다른 구독으로 이어받기)로 넘긴다. 기다려도 풀리지 않아 대기 판정은 없고, 그 모델은 그 작업의 재투입 후보에서 뺀다(`quota_switch.tsv` 9번째 칸 filter). 이어받기 메모에 같은 입력을 되풀이하지 말라는 지시를 넣는다. 설정 `routing.on_filter: switch|wait`. 예약 점검(`quota_watch`)도 필터 중단을 알린다. 보존 브랜치 이름에 dispatch 끝자리를 붙여 같은 초에 두 번 넘겨도 겹치지 않게 했다 |
| v0.8.2 | 2026-11-25 | **결함(현장 보고):** Orca가 워커의 workspace를 `<워크스페이스 ID>::<경로>`로 주는데 키트가 그 값을 그대로 경로로 써서, 한도 전환이 "워커 워크트리를 찾지 못했다"로 보류되고(진행분 보존·이어받기가 안 일어남) 정지 점검이 워커의 마지막 커밋을 못 읽었다('커밋 없음' 판정 누락). `kitlib.workspace_path`가 `::` 뒤를 경로로 풀고 status·quota_switch·cleanup_worker가 같이 쓴다. 시험의 가짜 Orca도 실제 꼴로 바꿨다 |
| v0.8.1 | 2026-11-25 | **한도 전환이 곧 다시 찰 구독으로 넘기지 않는다**(현장 피드백: 독립 검토 구독이 둘뿐인 구성에서 전환이 대부분 대기로 끝나 매니저가 기본값을 wait로 바꿨다). 넘겨받을 후보는 창마다 `switch_min_headroom`(30%) 이상 남고, 5시간 창은 지금까지의 소모 속도로 `switch_need_minutes`(120분)·앞 워커가 풀릴 때까지 중 짧은 쪽 안에 다시 차지 않아야 한다. 검토 이어받기는 구현한 구독을 점수 감점이 아니라 후보에서 아예 빼서, 다른 독립 후보가 있으면 그쪽으로 넘긴다(전에는 감점된 구현 구독이 1순위로 남아 대기가 됐다). 고른 구독을 `launch.sh`에 그대로 넘겨 `kit.yaml review.agent` 고정이 한도 찬 구독을 다시 고르던 결함을 고쳤다. 대기 이유에 나머지 후보가 빠진 까닭을 같이 적는다. 기본값은 switch 그대로 |
| v0.8.0 | 2026-11-25 | **한도 전환**: 정지 점검(`status.py --stall`)이 도는 워커의 구독(그 모델의 창)이 한도 100%면 `한도 정지`로 알린다. `quota_switch.py`가 매니저가 손으로 하던 절차를 한 번에 한다: worker-stop → 진행분(커밋 + 커밋하지 않은 변경)을 임시 브랜치 `wip/<ID>-quota-<시각>`에 스냅샷(작업 브랜치·작업 트리는 그대로) → worker-release → 같은 워크트리에서 `launch.sh --handoff`(새 모드: 이어받기 명세, 작업 항목의 모델 고정은 쓰지 않고 라우팅이 한도 찬 모델을 뺀다). 대기 판정: 설정 `routing.quota.on_exhausted: wait`(기본 switch), 막은 창이 `wait_minutes`(기본 30분) 안에 풀림, 다른 구독도 한도, 검토 워커인데 남은 후보가 구현한 구독뿐(검토 독립성). 멈춘 투입은 실적에서 빼고 handoff는 새 투입처럼 센다. 교차 검토는 이어받기 앞뒤 구독을 모두 피한다. `cleanup_worker.sh`가 머지 뒤 보존 브랜치도 지운다. **대기 뒤 재개**: 대기 판정이면 풀리는 시각을 기록하고, 그 뒤 `quota_switch.py`가 워커에 '이어서 하라'를 보낸다(정지 점검 '한도 풀림'). **예약 점검** `quota_watch.py install`: Orca 예약 실행(10분)의 사전 확인만 써서 할 일이 있으면 매니저 Run에 메일을 보내 깨우고, 사전 확인이 언제나 1로 끝나 에이전트는 띄우지 않는다(구독 소모 없음) |
| v0.7.0 | 2026-11-25 | **잔여 한도 라우팅**: Orca가 모으는 구독별 사용률(`orca account list --json`의 rateLimits: 5시간 세션·주간·Claude Fable 주간·Antigravity 모델 묶음)을 5분 캐시해 본다. 창 하나라도 98% 이상이면 초기화까지 제외(다른 계정에 여유가 있으면 알림), 세션 70% 넘으면 벌점, 주간이 기간보다 앞서 쓰이면 벌점, 곧 초기화되는데 많이 남으면 가점. 쓸 수 있는 구독이 모두 소진이면 launch.sh가 보류. 대시보드 구독 칩에 사용률. `models.py quota`. **품질 = 공개 벤치마크**: `models.py bench`가 Terminal-Bench 4.1(CLI 에이전트 × 모델 × effort 공식 리더보드)에서 카탈로그에 맞는 줄만 `bench_scores.yaml`에 출처와 함께 남기고, 품질 = 5 × 해결률/최고 해결률. 다른 CLI의 점수는 빌리지 않고, 새 판에 점수가 없으면 바로 아래 판 점수를 쓴다. Artificial Analysis 무료 API는 키(`AA_API_KEY`)가 있을 때만 참고값으로. **동점 규칙**: 점수가 같으면(부동소수 오차 포함) 부하 → 이 대역 실적 → 소모 → 품질 → 키 이름 순으로 가리고 근거에 남긴다(전에는 카탈로그 순서·오차가 갈랐다). 임계 경로 작업의 실적 조회 대역을 기록 대역과 맞췄다. **구독 CLI 자동 업데이트**: `launch.sh`가 하루 한 번(`routing.auto_update: daily`, weekly·off) 워커를 띄우기 전에 각 CLI의 자체 업데이트(`claude/codex/agy/grok update`)를 돌리고 모델 목록을 다시 읽는다. 수동은 `models.py update [--check]`. snap 설치본은 건너뛴다. **카탈로그 계열 따라가기**: `family: gpt-{v}-sol`처럼 적은 항목은 CLI 목록에서 가장 높은 판을 쓴다(effort가 ID에 든 계열은 `{effort}`). `discover`는 계열 옛 판을 알림에서 빼고 새 계열만 알린다. 프로젝트가 `models.yaml`에서 model을 정하면 그 항목은 고정. **카탈로그 정리**: Antigravity는 Gemini 4 Neon·Carbon과 claude-opus-5-5(3.8 Flash 뺌), Grok은 grok-4.7 하나(effort low~high, build-fast 뺌), Codex는 계열로 6.2판을 쓴다. Antigravity는 공식 설치본(`curl -fsSL https://antigravity.google/cli/install.sh \| bash`)을 권한다 |
| v0.6.0 | 2026-11-24 | **W0 초안에 기획 두 단계를 기본으로 넣었다**(교훈 L-20): `F-00a` 기획(요구 정리·기능 정의·구조 설계 초안, 마스터가 사용자 요구를 넘길 때까지 hold) → `F-00b` 유사 오픈소스 조사·반영(라이선스·최근 활동·겹치는 요구, 직접 구현 대 가져다 쓰기 대 토대로 삼기 비교, 사용자 결정이 필요한 변경은 제안으로) → `F-01` 계약. init 흐름(`references/init.md`)과 매니저 명세가 '사용자 결정 확정 뒤 F-01'을 따른다. **기존 설치본:** tasks.yaml은 프로젝트 소유라 바뀌지 않는다. 아직 계약 전이면 두 작업을 손으로 넣는다. 검증: `docs/VERIFY.md` 14장 |
| v0.5.2 | 2026-11-24 | **결함:** 키트가 `.gitignore`에 넣던 `dashboard/`가 저장소 루트의 대시보드 출력뿐 아니라 `orchestration/tools/dashboard/`(대시보드 도구)까지 무시해서, 도구가 프로젝트 git에 올라가지 않았다(새로 받은 클론에 도구가 없고 upgrade로 바뀐 도구가 이력에 남지 않는다) → `/dashboard/`로 고쳤다. `upgrade`가 `.gitignore`의 키트 블록을 매니페스트 목록과 맞춘다(블록 밖의 줄은 그대로, v0.5.0에서 늘어난 `jev_cache.json` 줄도 이때 들어간다). **기존 설치본:** `upgrade --apply` 뒤 `git add orchestration/tools/dashboard`로 도구를 올린다. 검증: `docs/VERIFY.md` 13장 |
| v0.5.1 | 2026-11-24 | 공개 배포 준비: Apache-2.0 라이선스, 공개 전 보안 점검(`docs/VERIFY.md` 12장). 문서의 원 프로젝트 출처 표기를 일반 표현으로 바꾸고 git 이력도 같은 기준으로 다시 썼다(커밋 해시가 바뀌었다) |
| v0.5.0 | 2026-11-24 | **플러그인 마켓플레이스 배포**(`.claude-plugin/`, 2.1절): `/plugin install`로 설치하고 auto-update를 켜면 push마다 자동 갱신(버전 미지정 = 커밋 추적). **대시보드 개요를 작업 보드로**(`docs/VERIFY.md` 11장): 현재 웨이브·머지 진행 막대, 구독 칩(동시 실행/상한·쿨다운·snap 제외·오늘 토큰), 접을 수 있는 웨이브 × 모듈 격자(노드를 누르면 보드 카드 표시, 접은 상태 기억), 모듈 거르기, 대기·착수 가능·진행·검토·머지 열 보드(카드에 배정 모델·경과 시간, 착수 가능 카드에 라우팅 예정 모델과 투입 명령 복사). 라우팅 정보는 models.yaml이 있을 때만, CLI를 새로 돌리지 않고 캐시만 읽는다. **JEV 난이도 판정(선택, 기본 꺼짐)**: `models.yaml jev.enabled`와 `TYPESAFE_API_KEY`가 있으면 작업 제목·완료 조건만 TypeSafe JEV에 보내 난이도를 한 번 더 본다. `mode: shadow`(기본)는 기록만 하고 `models.py stats`가 규칙 분류와 결과를 비교, `live`는 신뢰도 0.6 이상일 때 적용. 키·네트워크가 없거나 실패하면 규칙 분류만 쓴다 |
| v0.4.1 | 2026-11-16 | Orca 실기동 반영(`docs/VERIFY.md` 10장): Orca에서 꺼진 에이전트(`agent_unconfigured`)는 쿨다운 후 다시 라우팅, snap 격리 설치본(워커가 orca CLI를 못 부름)은 라우팅에서 뺀다 |
| v0.4.0 | 2026-11-16 | **모델 자동 라우팅**(`docs/DESIGN-NOTES.md` 6장). `launch.sh`가 agent/model 없는 작업을 분류(난이도 1~5·태그·임계 경로)해 구독 CLI(claude·codex·antigravity·grok)·모델·effort를 점수로 고른다. 카탈로그 `tools/model_catalog.yaml`(키트 소유) + 정책 `orchestration/models.yaml`(seed: 쓸 구독·목표·동시 상한·교차 검토·고정·덮어쓰기). 가용성은 각 CLI의 모델 목록으로 확인(`models.py discover`), 실적은 dispatch.tsv × tasks.yaml 결과 × 검토 판정(`models.py feedback`)으로 학습, 한도는 `models.py cooldown`, 사용량은 Orca 집계(`models.py stats`). tasks.yaml `difficulty`·`tags`·`objective`·`route` 추가, dispatch.tsv 칸 추가(에이전트·모델·effort·키·난이도), status.py에 구독별 진행 수·쿨다운. **기존 설치본:** upgrade하면 models.yaml이 새로 생기고 라우팅이 켜진다(끄려면 `routing.enabled: false`). 검증: `docs/VERIFY.md` 9장 |
| v0.3.0 | 2026-11-12 | 효율 검토 반영(`docs/DESIGN-NOTES.md` 4장, 교훈 L-14 ~ L-19). **결함:** upgrade가 키트 밖 파일을 덮어쓰던 것(→ `.kit-new`), status·cleanup의 진행 워커 오판·100개 잘림, launch가 선행·status를 안 보던 것, bootstrap `all`이 기동 보고 전에 abandon하던 것, 매니저 기동에 기존 워크트리 생성 플래그(`--display-name`), sync_main의 체크아웃 전환·자동 push. **효율:** 워커 고정 읽기 축소(AGENTS '읽을 것'), merged 보관(`archive_tasks.py`), MANAGER 분할(`procedures/`), SKILL 분할(`references/`), 정지 점검 도구(`status.py`, `hold`), 작업별 `agent/model/effort`, `launch.sh --review/--retry`, `slot.sh`, `evidence_lint.py`, pre-push 훅, 로컬 전용 운영(`project.git_remote`), 덧붙임 파일(`*.local.md`), upgrade 3방향 자동 병합·새 버전 알림, 회귀 시험 `tests/smoke.sh`. **기존 설치본:** `upgrade` 미리보기 → 로컬 전용이었으면 kit.yaml에 `git_remote: ""`부터 넣는다(`references/upgrade.md`). 검증: `docs/VERIFY.md` 8장 |
| v0.2.0 | 2026-11-09 | 실전 프로젝트 후반 교훈 13건 반영(`docs/LESSONS.md` L-01 ~ L-13, 근거 `docs/DESIGN-NOTES.md` 3장). MANAGER.md에 main 머지 관문(범위 확인·체크아웃 분리·유효 회차·미추적 증적)·검토 작업·대기와 정지 점검 장 추가, 후속 작업·실행 중 send·수정/기능 분리·작업 크기 규칙. 검토 명세 `review-spec.md` 신설(기준 브랜치·읽기 전용 대상·실행 재현 H), `render_spec.py`가 작업별 `spec_template`·`{target}`을 읽음. worker-spec에 범위·부분 완료, AGENTS에 '셸 명령'(pid 대기, `${VAR:?}`), integration-spec·sim 블록에 관문 유효성, MASTER에 정지 점검·범위 축소 선택지, 구조 문서 완료 기준·소통 표 갱신, 스킬에 '운영 점검'. **기존 설치본:** `upgrade`로 managed 파일 갱신, AGENTS.md(seed)는 `--include-seed` diff를 보고 '셸 명령' 절을 손으로 옮긴다. 검증: `docs/VERIFY.md` 7장 |
| v0.1.0 | 2026-11-06 | 최초 추출: 세션 3계층 명세, tasks.yaml 스키마·DAG 검증기·렌더러·투입·정리 도구, 브랜치·머지 규약, 문서 체계(RTM·TBD·증적·과정 기록 3종, 검토 단계 선택 모듈), 설정 기반 대시보드, 실행 격리 모듈(+ROS 2 프로필), Claude Code 스킬(init·bootstrap·status·upgrade), 예제 프로젝트. 검증: `docs/VERIFY.md` |

다음 버전 후보와 일반화 판단 기록: [`docs/DESIGN-NOTES.md`](docs/DESIGN-NOTES.md).
