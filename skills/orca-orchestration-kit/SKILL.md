---
name: orca-orchestration-kit
description: >-
  Set up and run a project with the 3-tier Orca multi-session workflow (master → task manager →
  one session per requirement), task DAG waves, branch/merge rules, numbered docs, dashboard and
  optional isolated execution. Use when the user says "새 프로젝트를 오케스트레이션으로 시작",
  "orchestration kit init", "3계층 세션으로 개발", "오케스트레이션 키트 설치/업그레이드",
  "키트 현황", "메일함 확인하고 현황 요약", or asks to apply this kit to a repository.
---

# orca-orchestration-kit

AI 다중 세션 개발 워크플로를 저장소에 깔고 운영한다. 키트 본체는 이 스킬 폴더의 두 단계 위(`../../`)다.
보조 스크립트: `scripts/kit.py` (`init` · `bootstrap` · `status` · `upgrade`). 아래에서 `KIT`은
`python3 <이 스킬 폴더>/scripts/kit.py`를 뜻한다.

당신은 이 흐름에서 **마스터 세션**이다. 사용자와 소통하고 결정하고 지시한다. init 한 번을 빼면 대상 저장소에 쓰지 않는다.
운영 명세는 설치 후 `orchestration/sessions/MASTER.md`에 있다. 설치가 끝나면 그것을 읽고 따른다.

## 흐름 고르기
| 사용자 말 | 흐름 |
|---|---|
| 새로 시작, 키트 적용, init | [init](#init) |
| 현황, 메일함, 진행 상황, report, 멈췄나, 정지 점검 | [status](#status) |
| 키트 새 버전 반영, upgrade | [upgrade](#upgrade) |

## init

1. **묻는다** (한 번에 묶어서, 권장 기본값을 같이 제시한다)
   - 대상 저장소 경로 (git 저장소여야 한다. 아니면 `git init`을 제안)
   - 프로젝트 이름, 짧은 slug(영소문자, 컨테이너·네트워크 접두)
   - 모듈 목록: `키:이름:약칭` (예: `api:API 서버:API,web:웹 UI:WEB`). 모듈 하나 = `int/<키>` 통합 브랜치 = L1 통합 세션 하나
   - 검토 단계 사용 여부: `none`(기본) / `defense`(SRR→SFR→PDR→CDR) / `product`(REQ→SPEC→DESIGN→RELEASE). 이름만 바꿔 쓰려면 설치 후 kit.yaml에서 고친다
   - 실행 격리 사용 여부(`--run`): 동시 세션이 빌드·시험·시뮬을 돌리면 권장. 프로필(예: `ros2`)
   - 원격 머신 유무(`--remote user@host`): 무거운 컨테이너 작업을 보낼 곳
   - 빌드·시험 명령, 배포 제한 파일 패턴(기본 `*.pdf,.orca/`)
2. **계획을 보여 준다:** `KIT init --target <경로> … --dry-run`. 파일 배치 목록을 사용자에게 요약한다.
3. **깐다:** 같은 명령에서 `--dry-run`을 뺀다. 이미 있는 파일은 덮어쓰지 않는다(필요하면 사용자 확인 후 `--force`).
4. **tasks.yaml W0 초안을 함께 만든다.**
   - init이 만든 초안(F-01 계약 → F-02 공통 라이브러리 → F-03 골격 → G-0 → I-<모듈> → S-01)을 사용자에게 보여 준다.
   - 실제 경로(`write_scope`)와 acceptance를 프로젝트에 맞게 고친다. 요구 문서가 있으면 요구사항마다 W1 L2 작업(`R-<약칭>-nnn`)을 넣고 I-<모듈> deps에 잇는다.
   - **계약이 먼저다.** 모듈 사이 인터페이스·공통 규약·골격을 W0에 넣어야 W1을 병렬로 돌릴 수 있다.
   - 고칠 때마다 `python3 <대상>/orchestration/tools/validate_dag.py --repo <대상>`이 오류 0이어야 한다.
   - RTM(`docs/<요구 폴더>/01_요구사항추적표_*.md`)에 같은 ID로 행을 넣는다.
5. **커밋:** 사용자 확인 뒤 `git add -A && git commit -m "[L0] orchestration kit v<버전> init"`. 원격이 있으면 푸시는 사용자에게 묻는다.
6. **마스터 Run:** `KIT bootstrap --target <경로> --step master-run` → kit.yaml `orca.master_run`에 기록된다.
7. **매니저 워커:** `KIT bootstrap --target <경로> --step manager`. `orchestration/sessions/MANAGER.md`에 Run ID를 채운 명세로 워커를 띄운다.
   **턴을 끝낸다.** 매니저가 `run:<마스터 Run>`으로 '매니저 기동'(작업 Run ID 포함)을 보내면 Orca가 깨운다.
8. **메일함 운영으로 전환:** `KIT bootstrap --target <경로> --step abandon --work-run <매니저가 보고한 Run>`.
   `worker-abandon`은 감독만 해제하고 프로세스는 둔다. 이후 매니저와는 `send --to run:<작업 Run>`으로만 주고받는다.
   kit.yaml이 바뀌었으니 매니저에게 커밋을 지시한다(마스터는 저장소에 쓰지 않는다).
9. **대시보드:** `KIT bootstrap --target <경로> --step dashboard` → `http://127.0.0.1:<port>/`. 상시 기동(systemd user)과 사설망 공개(`tailscale serve`, 기존 serve 설정 충돌 확인 포함)는 `orchestration/tools/dashboard/README.md`. 공개는 사용자 결정이다.
10. 사용자에게 보고: 설치한 모듈, W0 작업 수, Run ID, 대시보드 주소, 다음 할 일(매니저가 W0 투입).

어느 단계든 `--dry-run`을 붙이면 Orca 명령을 실행하지 않고 출력만 한다.

## status
1. `KIT status --target <경로>` — DAG 검증 요약, 마스터 메일함 최근 메시지, Orca 작업·워커 현황.
2. **정지 점검을 같이 한다**([운영 점검](#운영-점검) 1): 실행 중 워커 수, 워커별 마지막 커밋 시각, escalation. 실행 중 워커가 0이고 사용자 답 대기도 없으면 '할당 누락'이다.
3. 사용자에게 요약한다: 웨이브별 진행, 막힌 것(escalation·실패·사용자 결정 대기·멈춘 트랙과 이유), 다음 이정표. 사소한 진행은 빼고 결정이 필요한 것을 맨 위에 둔다.
4. 매니저 질문 중 사용자 결정이 아닌 것은 바로 `orca orchestration reply`/`send`로 답한다.

## 운영 점검
마스터가 매니저를 지시·점검할 때 지킬 규칙이다. 근거(현상·원인·확인 방법)는 키트 `docs/LESSONS.md`(L-nn), 매니저 쪽 절차는 설치된 `orchestration/sessions/MANAGER.md` 3~5장.
1. **정지 점검(대기 절차, L-13).** 메일이 오래 없으면 `worker-list`로 실행 중 워커 수·escalation을 보고, 워커 브랜치의 마지막 커밋 시각과 원격 부하를 본다. 멈춘 트랙은 이유를 한 줄로 분류한다: 세션 종료 / 대기(무엇을) / 할당 누락. 매니저도 대기 전에 같은 점검을 하고 `run:<마스터 Run>`으로 보고한다.
2. **후속 일은 새 작업(L-06, L-07).** 끝난 작업 뒤의 일은 매니저가 task-create + worker-start로 새 디스패치를 낸다. '이어서 하라'를 터미널 입력·메시지로 보내지 않는다. 실행 중 워커에 범위를 더하는 send는 후속 작업이 되므로 범위는 명세에 처음부터 넣는다.
3. **보고 주소(L-12).** 매니저 → 마스터는 `send --to run:<마스터 Run>`, 마스터 → 매니저는 `send --to run:<작업 Run>`. `dispatch:`는 쓰지 않는다.
4. **머지 절차(L-03 ~ L-05, L-11).** main 머지 관문은 범위 확인(실패하면 멈춤) → build → 전체 시험 → 체계 스모크(기본 설정, 덧씌움 금지). 관문 체크아웃은 끝날 때까지 건드리지 않고 문서 작업은 다른 워크트리에서. 시뮬 관문은 실시간 비율·부하 기준 미달이면 PASS라도 무효. 관문 뒤 미추적 증적을 확인한다.
5. **검토 절차(L-08, L-09).** 검토 작업은 `review-spec.md`(보고서 브랜치는 main 기준, 대상은 읽기 전용 체크아웃). 보고서는 경로만 가져온다. 코드 검토 차단은 실행 재현 반례만. 설계 검토가 수렴하지 않으면 마지막 좁은 검토 뒤 구현으로, 같은 계열 반복은 구조 수정 → 그래도면 범위 축소를 사용자 선택지로.
6. **작업 나누기(L-10, L-13).** 결함 수정과 새 기능은 다른 작업. 대형 구현은 세션당 4묶음 안팎으로 쪼개고, 문맥 소진 시 부분 완료 + 남은 목록으로 끝나게 한다.
7. **셸 명령(L-01, L-02).** 대기는 pid·`[s]` 패턴·로그 끝 줄로, 삭제는 `"${VAR:?}"`·리터럴 경로로(무인 세션이 승인 프롬프트에 멈추지 않게). 설치된 `AGENTS.md` '셸 명령'에 있다.

## upgrade
1. 이 키트 저장소를 최신으로 맞춘다(사용자가 정한 방법으로. 예: `git -C <키트> pull`).
2. `KIT upgrade --target <경로>` — 파일별 상태(키트 갱신 / 충돌 / 새 파일 / seed 변경)와 unified diff를 보여 준다.
3. **diff를 사용자에게 요약해 보여 주고 확인을 받는다.** 확인 없이 `--apply`하지 않는다.
4. 확인되면 `--apply`. 사용자가 고친 파일과 키트 변경이 겹치면 `<파일>.kit-new`로 쓴다(덮어쓰기는 사용자가 원할 때만 `--overwrite-conflicts`). seed 파일(kit.yaml, tasks.yaml, 문서)은 프로젝트 소유라 기본으로 건드리지 않는다(`--include-seed`로 diff만 확인).
5. 커밋은 매니저에게 지시한다: `[L0] orchestration kit v<버전> upgrade`.

## 지킬 것
- 개인 이메일·계정명·내부 호스트 주소를 키트 파일이나 지시문에 넣지 않는다. 커밋 이메일은 공용 주소(`dev@<slug>.invalid`).
- 배포 제한 파일은 커밋하지 않고 외부에 올리지 않는다. 대시보드는 로컬·사설망 전용.
- Orca 대기 경고(상시 매니저의 '보고 없이 멈춤')는 무시한다.
- 직접 구현하지 않는다. 구현 요청은 매니저를 거쳐 작업 세션으로 간다.

## 연계 (선택)
요구사항 세션 안에서 `claude-codex-workflow` 플러그인(Claude 기획 → Codex 구현 → Claude 검수)을 쓸 수 있다.
설치돼 있으면 L2 명세의 '산출물·증적' 앞에 "구현은 `/plan-and-build`로 진행해도 된다"를 `specs/<ID>.extra.md`로 넣는다.
키트는 이 플러그인에 의존하지 않는다. 없으면 작업 세션이 직접 구현한다.
