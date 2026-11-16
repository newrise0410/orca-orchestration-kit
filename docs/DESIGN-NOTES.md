# 설계 메모 — 일반화하면서 판단한 것, 다음 버전 후보

## 1. 일반화 판단 (v0.1)

| 주제 | 원래(실전 프로젝트) | 키트 | 이유 |
|---|---|---|---|
| 설정 위치 | 스크립트마다 상수(품목, 원격 호스트, Run ID, 경로) | `orchestration/kit.yaml` 한 곳. 도구는 모두 여기서 읽는다 | 값이 흩어지면 프로젝트마다 고칠 곳이 늘어난다 |
| "품목" | 품목(장치 식별자) | **모듈**(`modules[].key/name/short`). tasks.yaml 필드는 `module`, 예전 이름 `product`도 읽는다 | 일반 용어. 실전 tasks.yaml을 그대로 검증·표시할 수 있게 별칭 유지 |
| 메인 브랜치 | `master` 고정 | `project.main_branch`(init이 현재 브랜치로 감지) | 새 저장소는 대부분 `main` |
| 웨이브 추정 | 코드에 하드코딩 | `waves`, `wave_rules`, `gates` 설정 + 작업별 `wave` 명시 가능 | 웨이브 이름·게이트 수가 프로젝트마다 다르다 |
| 명세 환경 블록 | 렌더러 안 문자열(시뮬·원격·GPU 학습) | `orchestration/templates/blocks/<이름>.md` + `<이름>.pending.md`, 작업의 `env: [...]`, `runtime.<이름>` 준비 여부 | '모의로 먼저, 실제로 다시' 패턴을 어떤 인프라에도 쓸 수 있게 |
| 인계 사항 | 투입 스크립트가 `.extra.md`를 붙임 | 렌더러가 붙인다(`--no-extra`로 끔) | 렌더링 결과만 봐도 실제 투입 명세와 같다 |
| 명세 자리표시자 | `str.format` (중괄호 하나만 있어도 깨짐) | 알려진 이름만 바꾸는 정규식 | 명세 안 코드 예시의 `{}`가 깨지지 않는다 |
| DAG 검증 | 없음(대시보드 경고만) | `validate_dag.py`: 오류(중복 ID·없는 deps·자기 의존·순환·**중복 키**), 경고(웨이브 역행·쓰기 범위 겹침·형식) | 중복 키는 PyYAML이 조용히 덮어써서 값이 사라진다 |
| 상태 갱신 | 흐름형 한 줄 치환 | `task_status.py`: 블록형·흐름형 모두, 주석 보존 | 템플릿은 읽기 쉬운 블록형을 쓴다 |
| 정리 도구 | 워크트리 경로·원격 호스트 하드코딩 | `git worktree list`로 경로를 찾고, 원격 정리는 훅(`run.cleanup_hook`)으로 분리. `--dry-run` | 실행 격리 모듈을 안 쓰는 프로젝트도 쓸 수 있게 |
| 매니저 보고 | 첫날 중간에 worker-abandon으로 전환 | 처음부터 메일함 운영: 매니저 기동 보고 → 마스터가 abandon | 대기 경고 소음을 처음부터 없앤다. abandon은 기동 보고 뒤에 해야 명세가 확실히 전달된다 |
| 실행 격리 변수 | 프로젝트 이름 접두 환경변수, ROS 고정 | `RUN_*` + `run.conf`, 프로젝트별 격리 변수는 `profiles/<이름>/profile.sh`의 `profile_env` 훅 | ROS가 아닌 프로젝트(웹 DB 이름, 포트 접두 등)도 같은 틀 |
| HEAVY 슬롯 | 원격만 | 원격·로컬 모두 | 원격 머신이 없는 프로젝트도 무거운 작업을 제한할 수 있다 |
| 원격 제외 목록 | 코드에 고정 | `RUN_EXCLUDES` + init의 `--restricted` 패턴이 자동으로 들어간다 | 배포 제한 파일 종류는 프로젝트마다 다르다 |
| 문서 폴더 | 검토회의 4단계 고정 | 기본 `01_requirements`~`04_test`, 검토 단계는 선택 모듈(프리셋 `defense`/`product`, 이름만 바꿔 쓰기). `05_reports`~`07_process`는 고정 번호 | 방산이 아닌 프로젝트도 같은 번호 체계를 쓴다. 증적 경로가 단계와 무관하게 안정적이다 |
| RTM 상위 요구사항 세부 항목 | 특정 ID 꼴에 하드코딩 | `rtm.parent_pattern` 설정(기본 꺼짐) | 쪼개진 요구사항은 프로젝트마다 꼴이 다르다 |
| 대시보드 CSS | 일반 CSS | 금지어 검사(대소문자 무시)에 걸리는 영단어를 CSS 이스케이프로 표기(`al\69gn-items` 등), 다크 모드는 `color-scheme:dark`를 미디어 쿼리 안에 | 키트 반입 검사(`grep -i`)가 흔한 영단어 안의 철자에도 걸린다. 렌더링 결과는 같다 |
| 마스터 저장소 쓰기 | 쓰지 않음 | init·bootstrap 한 번만 예외(키트 설치와 Run ID 기록). 이후 kit.yaml 커밋은 매니저에게 지시 | 설치 시점에는 매니저가 아직 없다 |
| 업그레이드 | 없음 | `.orca-kit/installed.json`(설치 해시) 기준 3방향 비교: 키트 갱신 / 충돌(→ `.kit-new`) / 새 파일 / seed 변경 알림. 기본은 미리보기 | 프로젝트가 고친 파일을 키트가 덮어쓰지 않게 |

## 2. 키트에 넣지 않은 것
- 실전 프로젝트의 요구 문구, ID, 고객·품번, BOM·가격, 내부 호스트·주소, 계정 정보. 예제는 가상 프로젝트다.
- 프로젝트 고유 인프라(특정 외부 스택 빌드 우회, 특정 시뮬 모델 수정 등) — 교훈만 일반화해 ROS 2 프로필 README에 남겼다.
- 라이선스 파일(비공개, 사용자 결정).

## 3. v0.2 반영 (실전 프로젝트 교훈, 2026-11-09)

실전 프로젝트 후반부의 운영 이슈·결정에서 나온 규칙이다. 각 규칙의 현상·원인·확인 방법은 [`LESSONS.md`](LESSONS.md)(L-nn)에 있다. 여기에는 **왜 그 형태로 일반화했는지**와 출처를 남긴다.

| # | 규칙 | 근거(일반화 판단) | 출처 | 반영 위치 |
|---|---|---|---|---|
| L-01 | 긴 명령은 pid·`[s]` 패턴·로그 끝 줄로 기다린다 | 자기 명령줄 일치는 셸·도구와 무관하게 재현되는 함정이라 모든 세션 공통 규칙(AGENTS)으로 둔다. 프로젝트 고유 스크립트 이름은 예시 `scripts/test.sh`로 바꿨다 | I-39 | `templates/AGENTS.md` '셸 명령', MANAGER 5장, SKILL '운영 점검' |
| L-02 | 변수 경로 삭제는 `"${VAR:?}"`, 리터럴 경로 | 무인 세션은 훅 프롬프트에 답할 사람이 없다. 검사기 종류와 무관하게 '빈 변수 rm'은 막히므로 셸 차원 규칙으로 둔다. 키트 명세 템플릿의 rm 예시도 이 꼴로 썼다 | I-41 | `templates/AGENTS.md`, `review-spec.md` |
| L-03 | main 머지 관문 = build → 전체 시험 → 체계 스모크(기본 설정, 덧씌움 금지) | 274b01f에서 MANAGER에 넣은 문구를 관문 절차(3장)로 확장하고 구조 문서 완료 기준에도 넣었다. 스모크 내용은 프로젝트마다 달라 '핵심 흐름 하나가 실제 값으로 끝까지'로만 규정한다 | I-43, D-127 | MANAGER 3장, `orchestration/README.md` 6장 |
| L-04 | 관문용 체크아웃과 문서 작업 워크트리 분리, 결과에 실행 HEAD | 원격 동기화 방식이 아니어도 '실행 중 작업 트리 변경'은 관문 대상을 바꾼다. 그래서 원격 여부와 무관한 규칙으로 쓴다 | I-44 | MANAGER 3장, integration-spec, AGENTS |
| L-05 | 관문 도구는 전제 상태를 기다리고 실패 로그를 보존 | 원래 사례의 '원점 확정'은 도메인 고유라 '전제 상태(준비 보고·기준값 확정·헬스)'로 일반화했다. 'FAIL을 부하 탓으로 넘기지 않는다'를 함께 넣었다 | I-45 | MANAGER 3장, integration-spec |
| L-06 | 실행 중 send는 후속 작업이 된다. 범위는 명세에 처음부터 | Orca 디스패치 모델의 성질이라 모든 프로젝트에 해당한다. 정리 조건(끝나지 않은 디스패치 없음)과 연결했다 | I-46 | MANAGER 2장, worker-spec, `orchestration/README.md` 7장·7.1 |
| L-07 | 끝난 작업 뒤의 일은 새 작업. 대기 전 실행 중 워커/사용자 답 확인 | 교착이 양쪽 '정상 대기'로 보여 늦게 발견된다. 그래서 대기 전 점검을 절차로 넣었다. 워커 쪽에도 '이어서 입력이 와도 ask'를 넣어 양방향으로 막는다 | I-47 | MANAGER 2·5장, MASTER 2장, AGENTS '소통', SKILL |
| L-08 | 검토 브랜치는 기준 브랜치에서, 대상은 읽기 전용 체크아웃, 범위 확인은 실패하면 멈춤, 보고서는 경로만, 되돌림-의-되돌림 | 검토 작업 전용 명세가 v0.1에 없어 매니저가 매번 손으로 썼다 → `review-spec.md`를 추가하고 렌더러가 작업별 `spec_template`을 읽게 했다. 범위 확인 예시는 비ASCII 경로 오탐을 겪은 형태(`core.quotePath=false`)를 그대로 넣었다 | I-48 | `templates/sessions/review-spec.md`(신규), MANAGER 3·4장, `tools/render_spec.py`, tasks.yaml 스키마 주석 |
| L-09 | 코드 검토는 실행 재현 반례만 차단. 설계 검토는 마지막 좁은 검토로 닫음. 같은 계열 반복 → 구조 수정 → 범위 축소(사용자 선택지) | 검토를 몇 회 할지는 프로젝트마다 다르다. 그래서 횟수 대신 '수렴 모양'(회차마다 새 장치에서 새 H)을 종결 신호로 썼다. 범위 축소는 사용자 결정이라 MASTER 3장에 둔다 | D-120, D-139, D-144, D-147 | MANAGER 4장, `review-spec.md`, MASTER 3장 |
| L-10 | 결함 수정과 새 기능을 한 작업에 섞지 않는다 | 검토 결과의 원인 구분이 목적이다. 작업 크기 규칙과 함께 tasks.yaml 주석에 넣었다 | D-125 | MANAGER 2장, tasks.yaml 주석 |
| L-11 | 시뮬 관문은 유효 회차 기준(예: 실시간 비율 ≥ 0.8) 미만이면 PASS라도 무효. 시작 전 부하 확인, 회차별 기록, 관문 뒤 미추적 증적 확인 | 기준값은 프로젝트·장비마다 다르다. 그래서 0.8은 '예'로만 두고 '기준을 미리 정하고 기록한다'를 규칙으로 했다. 원래 프로젝트는 기능 판정에는 '부하 산물 0 직접 확인'을 썼다(D-103). 관문처럼 짧은 회차에는 실시간 비율이 더 다루기 쉬워 그쪽을 예로 들었다 | 운영 이슈 메모(관문 RTF 기준·증적 회수), D-103 | MANAGER 3장, `blocks/sim.md` |
| L-12 | 매니저 → 마스터는 `--to run:<마스터 Run>` | v0.1에도 주소는 있었지만 '`dispatch:` 금지'와 이유가 없어 실수가 났다. 주소가 나오는 모든 곳(MANAGER 0장·부록, MASTER 2장, 구조 문서 7장)에 같이 적었다 | D-149 정리 항목 | MANAGER 0장·부록, MASTER 2장, `orchestration/README.md` 7장 |
| L-13 | 정지 점검 루틴(실행 중 워커 수·마지막 커밋 시각·멈춘 이유 한 줄), 대형 구현은 세션당 4묶음 안팎, 부분 완료 + 남은 목록 | 원래 프로젝트에서 멈춤은 매번 마스터 정지 점검으로 발견됐다. 그래서 매니저 절차(대기 전·깨어날 때)와 마스터 절차(주기적) 양쪽에 넣었다. '4묶음'은 한 프로젝트의 경험값이라 '경험상'으로 표기했다 | 정지 점검 운영, issues 메모(결선 한 세션 = 4묶음) | MANAGER 2·5장, MASTER 2.1, worker-spec '부분 완료', SKILL status·운영 점검 |

**하지 않은 것(v0.2):** 관문·범위 확인을 스크립트 도구로 만들지는 않았다. 문구와 예시 명령만 넣었다(아래 v0.3 후보 1·2). AGENTS.md는 seed라 기존 설치본은 `upgrade --include-seed`로 diff를 보고 '셸 명령' 절을 손으로 옮겨야 한다.

## 4. v0.3 반영 (효율 검토, 2026-11-12)

키트 검토(설치본 dry-run, 현재 Orca CLI 1.4 대조, 두 번째 실사용 프로젝트 읽기 전용 조사)에서 나온 결함과 효율 개선이다. 교훈은 `LESSONS.md` L-14 ~ L-19. 아래 '후보 n'은 5장 번호다.

### 4.1 결함 수정
| 결함 | 원인 | 수정 |
|---|---|---|
| `upgrade --apply`가 '키트 밖 파일과 겹침'을 덮어씀 | 충돌만 `.kit-new`로 보냈다 | 키트 밖 파일·충돌·seed는 모두 `.kit-new`. 덮어쓰기는 `--overwrite-conflicts`만 |
| `status`의 진행 워커 수 오판(끝난 워커를 진행으로 셈), 100개에서 잘림, 이름 대신 dispatch ID | 끝난 상태를 `succeeded`·`failed`로만 봤다. 페이지를 안 넘겼다 | `kitlib.SETTLED_WORKER_STATES`(abandoned·stopped 포함), `nextCursor` 페이지 순회, Orca 작업 제목 → 키트 ID 대응. `cleanup_worker.sh`도 같은 집합 |
| `launch.sh`가 선행·status를 안 보고 투입, W0에서 투입 가능 목록이 빔, `int/common` 생성 안내 없음 | 투입 가능 판정이 L2 전용이었다 | `launch.py`: status todo/ready·hold·게이트·선행 merged(L1은 L2 선행 제외)·기준 브랜치 존재를 확인(`--force`로 끔). 투입 가능 목록은 모든 계층. 없는 브랜치는 만드는 명령을 알려 준다 |
| `bootstrap` 기본값 `all`이 매니저 기동 보고 전에 abandon | 단계 사이에 기다림이 필요한데 한 번에 돌았다 | `--step` 필수, `all` 삭제. abandon은 `--work-run` 필요 |
| 매니저 기동이 Orca에 거부될 수 있음 | 기존 워크트리(`path:`)에 생성 전용 플래그 `--display-name`을 줬다(Orca: 생성 플래그는 새 워크트리에만) | 뺐다. `launch.py`도 기존 워크트리 재사용 때는 `--name`·`--base-branch`·`--repo`·`--display-name`을 넣지 않는다 |
| `sync_main.sh`가 현재 체크아웃의 브랜치를 바꾸고 원격에 자동 push | 체크아웃 전환 방식 | 임시 워크트리에서 머지하고 로컬 브랜치만 옮긴다(체크아웃된 브랜치는 깨끗할 때만 ff). 원격은 `--push`일 때만 |

### 4.2 효율 개선
| 주제 | 판단 | 반영 |
|---|---|---|
| 고정 문맥 | 모든 세션이 tasks.yaml(실사용 103KB)·구조 문서를 먼저 읽었다. 명세가 자족적이면 필요 없다 | AGENTS.md '읽을 것', 명세 템플릿 첫 줄, `archive_tasks.py`(merged 보관, 검산 후 쓰기). 도구(validate·render·launch·status·대시보드)는 보관 파일을 함께 읽는다 |
| 매니저 명세 | 17KB 한 파일을 상시 문맥에 두고, 관문·검토 절차는 가끔 쓴다 | 핵심 `MANAGER.md` + `procedures/merge-gate.md`·`review.md`. 같은 규칙의 반복 서술을 줄였다 |
| 스킬 본문 | 현황 확인에도 init 10단계·upgrade가 매번 로드됐다 | `SKILL.md`는 흐름 고르기·status·이어받기만, init·upgrade는 `references/`. '운영 점검'은 설치본 `MASTER.md` 5장으로 |
| 정지 점검 | 문장 절차였다. 대기 중 매니저는 사람이 시켜야 점검했다 | `status.py`(설치본에 들어가 매니저도 쓴다): attention·커밋 없음·running 고아·할당 누락·대기할 것 없음, 메시지 ID. `hold` 필드로 일부러 미룬 작업을 구분 |
| 실사용 프로젝트가 직접 만든 것 | 작업별 모델 지정(75개 중 73개), 교차 검토·재투입 도구, 전체 시험 잠금, 증적 검사, pre-push, 로컬 전용 운영 | `agent/model/effort` 필드, `launch.sh --review/--retry`, `slot.sh`, `evidence_lint.py`, `hooks/pre-push` + `install_hooks.sh`, `project.git_remote`(명세의 `{push}`·`{sync_base}`·`{ref}`). 계정 전환·사용량 감시처럼 특정 제공자에 묶인 도구는 넣지 않았다 |
| upgrade 마찰 | 프로젝트가 managed 파일을 고치면 매번 충돌 | 덧붙임 파일(`templates/<이름>.local.md`, `MANAGER.local.md`, `MASTER.local.md`), 3방향 자동 병합(설치 당시 원본은 키트 git 이력에서 sha로 찾는다. 못 찾으면 예전처럼 `.kit-new`), `status`의 새 버전 알림 |
| Orca 의미 변화 | 현재 가이드: 일하는 워커에 send → 턴 끝난 뒤 전달, 끝난 워커 → 후속 Task | L-06·L-07 규칙은 유지(원 범위에 섞이지 않는 점은 같다). 문구를 현재 동작에 맞추고, 근거(tasks.yaml 정본)를 적었다 |

**하지 않은 것(v0.3):** 동시 실행 한도 자동 제한(후보 4), cleanup 일괄 모드(5), doctor(6), 대시보드 워커 탭(7), RTM 자동 갱신(8), 추가 프로필(9), 영문 템플릿(11), 공개판 생성기(12), CI. Orca 실기동(워커 생성·send 의미) 재확인은 하지 않았다(dry-run과 CLI 도움말·가이드 대조만).

## 5. v0.2 때 정리한 다음 버전 후보 (처리 결과 표시)
1. **체계 스모크 골격 도구(I-43):** `tools/`에 기동 → 핵심 흐름 → 판정 → 정리 골격(종료코드 0 통과 / 1 실패 / 75 환경 문제), 전제 상태 대기·실패 로그 보존·실시간 비율 기록 내장.
2. **머지 범위 확인 도구(I-48):** `tools/merge_scope.sh <A> <B> <허용 경로>...` — quotePath 끔, 범위 밖이면 목록과 종료코드 1. 매니저 관문과 검토 보고서 가져오기에 쓴다. (v0.3: `merge-gate.md` 2의 명령 예시 유지, 도구화는 보류)
3. **정지 점검 도구(L-13):** 실행 중 워커 수·워커 브랜치 마지막 커밋 시각·escalation을 한 줄로 내는 `kit.py status --stall`. **→ v0.3 반영** (`status.py --stall`)
4. **launch.sh 동시 실행 한도 연동:** 실측 한도·HEAVY 슬롯 사용량을 보고 투입 수를 자동 제한(`--max-active`).
5. **cleanup 일괄 모드:** merged 작업을 tasks.yaml·dispatch.tsv에서 찾아 조건 맞는 워커를 한 번에 정리. (보류: 정리 조건 판단은 사람이 보도록 둔다)
6. **kit.py doctor:** Orca CLI·PyYAML·docker·rsync·ssh BatchMode·tailscale serve 충돌을 한 번에 점검. (보류)
7. **대시보드:** Orca 워커 목록(진행·대기·escalation) 탭, 마스터 메일함 요약, 지표 표(07_process 01) 그래프, 스냅샷 비교.
8. **RTM 자동 갱신 도우미:** 머지 커밋·증적 판정으로 RTM 검증 칸 갱신 후보를 제시(매니저 확인 후 반영).
9. **추가 실행 프로필:** `web`(DB 이름·포트 접두 격리), `ml`(학습 이미지·GPU 기본값), `node`/`python` 시험 러너 병렬 격리 예시.
10. **upgrade 3방향 자동 병합:** 설치 당시 렌더링 원본을 저장해 `git merge-file`로 충돌을 줄인다. **→ v0.3 반영** (설치 당시 원본을 키트 git 이력에서 되살려 `git merge-file`)
11. **영문 템플릿:** 문서·명세 템플릿 영어판(언어 선택 `--lang`).
12. **과정 기록 공개판 생성기:** 07_process에서 요구 문구·고객 정보·가격을 걸러 낸 공개용 판을 만드는 도구.
13. **검증:** 키트 자체의 회귀 시험(`tests/`: init 배치, 렌더링, DAG 검증 사례, 대시보드 스냅샷 비교)과 CI. **→ v0.3 일부 반영** (`tests/smoke.sh`, CI는 보류)

## 6. v0.4 반영: 모델 자동 라우팅 (2026-11-16)

**요청:** 작업 난이도·속도에 맞는 모델과 effort를 자동으로 고르고, Claude 말고도 구독제 CLI(Codex OAuth, Antigravity `agy`, Grok CLI)를 Orca처럼 섞어 쓴다. 핵심은 '모델에 최적화된 자동 라우팅'.

**실행 계층은 Orca가 이미 갖고 있다.** Orca 1.4의 `worker-start --agent <claude|codex|antigravity|grok …> --model <id> --effort <수준>`가 구독 CLI를 워크트리별로 띄운다(Conductor가 하는 일). OpenCode식 API 관문은 쓰지 않는다. 키트가 더하는 것은 **작업마다 무엇으로 띄울지 정하는 결정 계층**이다.

### 6.1 구성
| 부분 | 파일 | 소유 |
|---|---|---|
| 모델 카탈로그: 모델별 quality·speed·cost(1~5)·강점 태그·effort 목록(또는 `model_by_effort`) | `tools/model_catalog.yaml` | 키트(upgrade로 갱신) |
| 정책: 쓸 구독, 목표(quality/balanced/economy/speed), 구독별 동시 상한, 교차 검토, 고정(pin)·선호(prefer)·최저 품질, 카탈로그 덮어쓰기, 분류 단어 | `orchestration/models.yaml` | 프로젝트(seed) |
| 결정: 분류 → 점수 → 제외 → effort | `tools/routing.py` | 키트 |
| 도구: discover / route / plan / stats / cooldown / feedback | `tools/models.py` | 키트 |
| 연결: agent/model 없는 작업은 투입 때 라우팅, 결정 근거 한 줄, dispatch.tsv에 에이전트·모델·effort·키·난이도 | `tools/launch.py` | 키트 |

### 6.2 결정 방법 (LLM을 부르지 않는다)
1. **분류:** 기본 난이도 2에서 L1·체계 통합(4), 계약 작업(제목에 계약 말이 있거나 `contracts/`를 씀, +1) 또는 그 밖의 공통 기반 F-*(+0.5), 쓰기 범위 넓이(6곳 +0.5, 12곳 +1), 최상위 폴더 3개 이상(+0.5), 완료 조건 수(6개 +0.5, 12개 +1), 어려운 말(동시성·보안·마이그레이션 등 0.5씩 최대 +1. **프로젝트 작업의 30%보다 많이 나오는 말은 그 분야의 일상어로 보고 뺀다**), 제목의 쉬운 말(−1, 어려운 말이 없을 때), 성능 측정(+0.5), 선행 4개 이상(+0.5), 앞선 시도 실패(+1씩, 최대 +2). 작업 항목 `difficulty`가 있으면 그것을 쓴다. 태그(design·contract·frontend·test·docs·refactor·debug·config·research·coding·integration·review)는 제목·완료 조건·쓰기 범위에서 뽑는다. 뒤에 기대는 미완료 작업이 5개 이상이면 임계 경로(품질 여유 +0.5, 속도 가중 +0.3).
   - 보정: 실사용 프로젝트 작업 74개로 돌려 맞췄다. 첫 판은 '계약'·'권한'·'성능'처럼 그 분야에 늘 나오는 말과 긴 완료 조건 때문에 난이도 4~5가 80%, Opus가 39건이었다. 빈도 거르기·문턱 조정 뒤 난이도 분포 2:11 · 3:36 · 4:17 · 5:10(`docs/VERIFY.md` 9장).
2. **점수:** `−under×max(0, 난이도−quality) − cost가중×cost − speed가중×(5−speed) + head×min(1, quality−난이도) + 강점(태그당 0.75, 최대 1.5) + 실적 + prefer(+2)` − 진행 중 워커(0.4씩) − 하루 사용량 기준 초과 − 이 작업에서 실패한 모델(2.5) − 교차 검토 위반(3). 목표별 가중은 `routing.py OBJECTIVES`. 구독제라 cost는 돈이 아니라 한도 소모이고, 다시 띄우고 검토하는 비용이 더 크므로 balanced는 품질 여유(head 1.0)를 소모(0.4)보다 무겁게 둔다. 결과적으로 **난이도를 여유 있게 넘는 모델 가운데 싼 쪽**이 이기고, 실패·부하·한도가 쌓이면 다른 구독으로 넘어간다.
3. **제외:** `routing.agents` 밖, CLI 없음, CLI 모델 목록에 없는 모델(Claude는 별칭이라 목록 확인 없이 믿는다), 쿨다운, 최저 품질 미달, 동시 상한(모두 차면 상한 무시).
4. **effort:** 난이도 1~2 low, 3 medium, 4 high, 5 xhigh. 목표 quality면 한 단계 위, economy·speed면 아래. 모델이 난이도보다 2 이상 강하면 한 단계 아래. 임계 경로는 난이도 +0.5로 본다(강한 모델이라고 effort를 낮추지 않게). `max`는 명시할 때만. Antigravity Gemini처럼 추론 수준이 모델 ID에 든 모델은 ID 변형을 고른다.
5. **학습:** 구현 투입마다 결과 점수를 매긴다. merged인데 뒤에 retry가 없으면 1, retry가 있으면 0.6, 다시 띄운 앞 시도와 todo로 되돌아온 시도는 0, retry 마지막은 1. 검토 판정(`models.py feedback`: pass 1 · minor 0.7 · major 0.3 · fail 0)이 있으면 그 점수로 덮는다. (모델, 난이도 대역)별 평균을 사전값 0.67로 평활해 `4×(평균−0.67)`을 더한다.

### 6.3 판단과 한계
- **결정적 점수 + 학습 보정을 택했다.** 투입마다 LLM으로 분류하면 느리고 비싸며 재현되지 않는다. 매니저는 분류가 틀린 작업에만 `difficulty`를 적는다. 판단이 필요한 몫은 매니저(LLM)가 tasks.yaml을 쓸 때 이미 들어간다.
- **카탈로그 점수는 작성 시점의 추정이다**(2026-11 기준 Claude fable/opus/sonnet/haiku 별칭, Codex gpt-6-astra·gpt-6.1-sol·gpt-6.1-terra·gpt-6-luna, Antigravity gemini-3.8-flash·claude-opus-4-6, Grok grok-4.7·build-fast). `discover`가 카탈로그에 없는 새 모델을 알려 주고, 프로젝트는 `models.yaml models:`로 덮어쓴다.
- **탐색(일부러 다른 모델에 맡겨 보기)은 하지 않는다.** 결과가 쌓이는 모델만 학습된다. 비교가 필요하면 `pin`·`prefer`로 잠시 바꾼다.
- **구독 계정 전환은 하지 않는다.** `orca account select`는 호스트 전체 설정이라 동시 투입과 사용자 세션에 번진다. 한도는 구독 단위 쿨다운으로 다룬다. 한도 감지는 사람(매니저)이 워커 보고·화면으로 하고 `cooldown`을 건다(자동 감지는 다음 후보).
- **Orca 사용량 파일**(`~/.config/orca/orca-{claude,codex}-usage.json`)은 Orca 내부 형식이다. 모델·날짜별 출력 토큰 합만 읽고, 형식이 바뀌면 조용히 빈 값으로 둔다. Antigravity·Grok은 집계가 없다.
- **다음 후보:** 워커 화면에서 한도 문구를 찾아 쿨다운을 자동으로 거는 것(status.py), 작업 소요 시간(투입 → merged) 학습, 대시보드 모델 배분 탭.
