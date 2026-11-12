# 오케스트레이션 구조 — {{PROJECT_NAME}}

Orca 오케스트레이션(Run · Task DAG · Worker)으로 개발한다.
원칙은 **요구사항 1개 = 작업 1개 = 세션 1개 = 브랜치 1개**다. 목적은 컨텍스트 관리다. 세션 하나가 요구사항 하나만 알면 짧고, 품질이 일정하고, 실패해도 영향이 그 안에 머문다.
결과는 아래에서 위로 계층적으로 통합한다.

## 1. 세션 계층

```
사용자
  │
마스터 세션 ─────────── 사용자와 소통 · 결정 · 지시 (저장소에 쓰지 않음)      orchestration/sessions/MASTER.md
  │  run:<마스터 Run> ◄── 보고·질문      send ─► run:<작업 Run>
작업관리 매니저 (L0) ── Orca Run 운영, 머지, 문서·현황 갱신, 종료 정리          orchestration/sessions/MANAGER.md
  ├─ L1 모듈 통합 세션 ×모듈 수 (상시, int/<모듈>)                            orchestration/templates/integration-spec.md
  └─ L2 요구사항 세션 ×N (req/<ID>)                                           orchestration/templates/worker-spec.md
```

- **마스터와 매니저를 나눈다.** 한 세션이 사용자 소통과 워커 관리를 겸하면 한쪽이 막힐 때 다른 쪽도 멈춘다.
- **매니저는 상시 세션이다.** 마스터가 매니저를 워커로 띄운 뒤 `worker-abandon`으로 감독에서 빼고, 둘은 Run 메일함(`send --to run:<id>`)으로 소통한다. 작업 단위 워커와 생명주기가 다르기 때문이다(대기 경고가 반복되는 문제를 없앤다).
- **사용자 결정만 위로 올린다.** 요구 해석·범위·비용·외부 선택만 마스터를 거쳐 사용자에게 간다. 기술 판단은 매니저·마스터 선에서 끝낸다.

## 2. 브랜치·워크트리

| 계층 | 브랜치 | 분기 기준 | 머지 대상 | 머지 주체 |
|---|---|---|---|---|
| L0 | `{{MAIN_BRANCH}}` | - | - | 매니저 |
| L0 (W0) | `int/common` | `{{MAIN_BRANCH}}` | `{{MAIN_BRANCH}}` | 매니저 |
| L1 | `int/<모듈>` | `{{MAIN_BRANCH}}` | `{{MAIN_BRANCH}}` | 매니저 |
| L2 | `req/<ID>` | `int/<모듈>` | `int/<모듈>` | L1 통합 세션 |

모듈 통합 브랜치: {{MODULE_BRANCHES}}

- 머지는 **`--no-ff`**다. 요구사항 단위 머지 커밋이 남아 RTM과 git 이력이 1:1로 이어진다.
- **rebase·force push 금지.** 하위 브랜치는 상위 브랜치를 `merge`로 따라간다.
- 커밋 메시지 첫 줄은 **`[작업ID]`** 접두로 시작한다(`[L0]` 운영, `[G]` 머지·동기화). 대시보드가 이 접두로 진행을 추적한다. 동기화 머지도 `-m "[G] Sync ..."`로 커밋한다(기본 메시지 금지).
- Orca가 만드는 워커의 로컬 브랜치 이름은 규칙과 다를 수 있다. 명세에 `git push <원격> HEAD:req/<ID>`(로컬 전용이면 `git push . HEAD:req/<ID>`)를 넣어 이름을 맞춘다.
- **원격:** {{PUSH_POLICY}}
- 공통 기반이 바뀌면 매니저가 `{{MAIN_BRANCH}}`에 반영 → `int/*`가 `{{MAIN_BRANCH}}`를 머지 → 진행 중인 `req/*`가 자기 `int/*`를 머지한다.

투입은 `orchestration/tools/launch.sh <작업ID>`가 한 번에 한다: 선행 작업·status·기준 브랜치 확인 → 명세 렌더링 → `task-create` → `worker-start --worktree new-top-level --base-branch <기준>` → tasks.yaml running.
작업 항목의 `agent`·`model`·`effort`가 있으면 그 모델로 띄운다. 구현 검토는 `--review`, 남은 지적 재투입은 `--retry`.

## 3. 웨이브

```
W0 계약·공통 기반 ──► G-0 기반 머지 ──► W1 요구사항(병렬) ──► W2 모듈 통합 ──► W3 체계 통합
F-01 계약             int/common        req/<ID> ×N            I-<모듈> (상시)    S-01 종단
F-02 공통 라이브러리    → {{MAIN_BRANCH}}   (deps 풀린 것부터)     req 끝날 때마다     S-02 확장·검증
F-03 골격·빌드          int/<모듈> 생성                            점진 머지
```

- **계약 먼저(W0).** 인터페이스·공통 규약·모듈 골격(진입점까지 미리 등록)을 W1 전에 고정해야 요구사항 세션들이 같은 파일을 고치지 않는다.
- **G-0 앞당김.** 인프라를 기다릴 필요가 없는 작업은 먼저 투입한다. 인프라 의존 부분은 '모의로 먼저, 실제로 다시'를 명세에 적는다(`env` 블록의 `.pending.md`).
- **W2는 점진 통합이다.** 통합 워커를 `--keep-open`으로 띄워 두고 req가 끝날 때마다 머지를 지시한다. DAG의 I-작업은 "모듈 전체 통합 완료" 게이트 역할이다.
- 빠진 인프라는 진행하면서 매니저가 찾아 DAG에 추가한다.

작업 목록과 의존성의 정본은 [`tasks.yaml`](tasks.yaml)이다. 고친 뒤에는 `python3 orchestration/tools/validate_dag.py`를 통과시킨다.

## 4. 쓰기 범위(write_scope)와 소유권

| 경로 | 소유자 | 다른 계층이 바꿔야 할 때 |
|---|---|---|
| 계약(인터페이스·스키마·ICD) | **L0 매니저** | L2는 `ask`로 요청. 매니저가 반영 후 `{{MAIN_BRANCH}}` → `int/*` 순으로 전파 |
| 공통 라이브러리 | **L0 매니저** (W0 작업) | 위와 같음. 버그 수정은 L1이 제안 |
| `docs/01`~`05` 단계 문서(RTM·TBD 포함) | **L0 매니저** | L1·L2는 증적만 쓰고 매니저가 반영 |
| 모듈 기동·통합 시험 | **L1** | |
| 모듈 기능 경로 (tasks.yaml `write_scope`) | **해당 L2** | 다른 L2의 경로는 읽기만 |
| `docs/06_evidence/<작업ID>/` | **해당 작업** | 경로가 겹치지 않으므로 충돌 없음 |
| `orchestration/`, `scripts/`, 빌드·컨테이너 설정 | **L0 매니저** | |

쓰기 범위 밖을 고쳐야 하면 고치지 말고 `ask`로 요청한다. 이 규칙이 병렬 세션 사이의 충돌을 구조적으로 막는다.

## 5. 동시 실행과 자원

- 한도는 **세션 수가 아니라 자원**으로 잡는다. 투입할 작업이 있으면 2개씩 늘리고 5분 동안 지표를 본다. 안전선을 넘으면 증설을 멈추고, 그때 수 − 2를 실측 한도로 기록한다. 돌고 있는 워커는 죽이지 않는다.
- 안전선 예: 가용 RAM, swap 증가 없음, 5분 load, GPU 메모리, 실패 징후(OOM, 비정상 종료, 시험 타임아웃 급증, API 사용량 제한).
- 무거운 작업(다중 인스턴스 시뮬·학습·대규모 시나리오)은 **HEAVY 슬롯**(flock)으로 따로 제한한다. 실행 격리 모듈을 쓰면 `scripts/run/README.md` 참고.
- 지표 수집: `scripts/run/metrics.sh`(실행 격리 모듈 설치 시).

## 6. 완료 기준 (Definition of Done)

L2 작업은 아래를 모두 만족해야 `worker_done`을 보낼 수 있다.
1. 빌드가 통과한다(경고 0 목표).
2. 단위 시험이 통과하고, 요구사항 ID를 시험 이름이나 docstring에 표기한다.
3. acceptance 시나리오를 검증 단계 ①에서 실행했고 결과가 있다.
4. `perf: true` 작업은 지연 p50/p95를 남겼다. **지연은 계기(무엇이 출력을 일으켰나)별로 나눠 잰다.**
5. `docs/06_evidence/<작업ID>/report.md`를 작성했고 `evidence_lint.py`가 0건이다(양식 `templates/evidence.md`).
6. `[<작업ID>]` 접두 커밋을 자기 브랜치에 푸시했다.
7. 새 외부 의존성과 라이선스를 report.md에 적었다.

L1 작업은 소속 req/*를 모두 머지하고, 모듈 스모크를 통과하고, `docs/06_evidence/I-<모듈>/report.md`를 작성해야 완료다.

**`{{MAIN_BRANCH}}` 머지 관문**(매니저, `sessions/procedures/merge-gate.md`): 머지 범위 확인(실패하면 멈춤) → build → 전체 시험 → **체계 스모크**(모듈 사이 실제 값으로 핵심 흐름 하나, 시험 전용 덧씌움 없이 기본 설정, 몇 분). 관문은 전용 체크아웃에서 돌리고 결과에 실행 HEAD와 회차 유효성(시뮬이면 실시간 비율·부하)을 남긴다. 기준 미달 회차는 PASS여도 무효다.

**검토 작업**(`templates/review-spec.md`, `sessions/procedures/review.md`): 보고서 브랜치는 `{{MAIN_BRANCH}}` 기준, 대상은 읽기 전용 체크아웃. 머지 차단은 실행으로 재현되는 반례만. 보고서는 경로만 가져온다.

**부분 완료:** 문맥이 모자란 L2는 끝난 묶음까지 커밋·푸시하고 `결과: 부분` + 남은 목록으로 끝낸다. 남은 것은 매니저가 새 작업으로 낸다.

## 7. 소통

| 방향 | 명령 |
|---|---|
| L2·L1 → 매니저 질문 | `orca orchestration ask` (그다음 턴을 끝낸다) |
| 매니저 → 워커 정보 | `orca orchestration send --to dispatch:<id>` (보내기 전에 `worker-list`로 최신 디스패치 확인). 일하는 워커에는 현재 턴이 끝난 뒤, 끝난 워커에는 후속 Task로 전달된다 — **원 명세 범위에 섞이지 않는다.** 짧은 정보만 보내고 receipt를 확인한다 |
| 매니저 → 워커 새 일 | 범위 추가·끝난 작업 뒤의 일은 **tasks.yaml 항목 + 새 작업**(`launch.sh <새 ID>`, 지적 수정은 `--retry`). 워커 터미널에 직접 입력하지 않는다 |
| 매니저 → 마스터 보고·질문 | `orca orchestration send --to run:<마스터 Run> --type status\|question` (`dispatch:`로 보내지 않는다. 후속 작업이 중첩된다) |
| 마스터 → 매니저 지시 | `orca orchestration send --to run:<작업 Run>` |

### 7.1 종료 정리
머지가 끝난 워커는 **머지 직후 바로 정리**한다: `orchestration/tools/cleanup_worker.sh <워크트리> <원격 브랜치> <상위 브랜치>`.
- 정리 조건(모두 만족해야 한다. 하나라도 못 맞추면 지우지 않고 이유를 출력한다)
  1. `worker_done`(succeeded)으로 보고를 마쳤다. 끝나지 않은 디스패치가 없다(실행 중 send로 생긴 후속 디스패치 포함 — `worker-show`로 확인하고, 남았으면 처리하거나 멈춘 뒤 해제한다).
  2. 작업 브랜치가 원격에 푸시됐다(로컬 HEAD = 원격 브랜치).
  3. 그 브랜치가 상위 브랜치에 머지됐다.
  4. 워크트리에 커밋 안 된 변경이 없다.
- 정리 순서: `worker-release`(안 되면 `worker-stop`) → `orca worktree rm --delete-branch`(원격 브랜치는 추적용으로 남김) → 정리 훅(실행 격리 모듈: 원격 컨테이너·네트워크 정지, 원본 결과 보관).
- 남기는 것: 실행 중인 워커, 상시 L1 통합 세션, 매니저, 보완 지시를 기다리는 워커.
- 정리한 워커에 보완이 필요하면 원격 브랜치에서 새 워커를 띄운다(`worker-start --base-branch req/<ID>`). 작은 보완은 관련 워커나 L1에 쓰기 범위를 좁게 넓혀 맡긴다.

## 8. 상태 파일
- `tasks.yaml`: 진행 중·남은 작업(정본). 일부러 미루는 작업은 `hold: <사유>`.
- `tasks.archive.yaml`: merged 작업 보관(`tools/archive_tasks.py`가 옮긴다). 도구가 함께 읽고, 사람·세션은 평소에 읽지 않는다.
- `state/issues.md`: 매니저 작업 메모(정리본은 `docs/07_process/03_*`)
- `state/dispatch.tsv`: 투입 기록(제목, task, dispatch, 워크트리, 시각) — `launch.sh`가 쓴다
- `specs/<ID>.md`: 렌더링된 명세, `specs/<ID>.extra.md`: 투입 전 인계 사항(렌더러가 끝에 붙인다), `specs/<ID>.review*.md`·`<ID>.retry-*.md`: 검토·재투입 명세

## 9. 프로젝트 덧붙임 (키트 파일을 고치지 않고 규칙을 더한다)
키트 소유(managed) 파일을 직접 고치면 `upgrade` 때 충돌한다. 프로젝트 규칙은 덧붙임 파일에 둔다.
| 대상 | 덧붙임 파일 | 반영 |
|---|---|---|
| 작업 명세 템플릿 `templates/<이름>.md` | `templates/<이름>.local.md` (예: `worker-spec.local.md`) | 렌더러가 명세의 '## 종료' 앞에 넣는다. `{branch}` 같은 자리표시자를 쓸 수 있다 |
| 매니저 명세 | `sessions/MANAGER.local.md` | 매니저 기동 명세 끝에 붙는다 |
| 마스터 명세 | `sessions/MASTER.local.md` | 마스터가 MASTER.md 다음에 읽는다 |
