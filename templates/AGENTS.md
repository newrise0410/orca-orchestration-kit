# 작업 규칙 (모든 에이전트 세션 공통) — {{PROJECT_NAME}}

{{PROJECT_SUMMARY}}

## 읽을 것 (문맥을 아낀다)
- **작업 세션은 받은 명세와 이 파일만 읽고 시작한다.** 명세에 대상·브랜치·쓰기 범위·완료 판정·증적 경로가 다 들어 있다.
- 다른 작업 정보가 필요하면 `orchestration/tasks.yaml`을 통째로 읽지 않는다: `grep -n -A12 'id: <ID>' orchestration/tasks.yaml` 또는 `python3 orchestration/tools/render_spec.py --stdout <ID>`.
- 구조·규약 원문은 `orchestration/README.md`, 문서 규칙은 `docs/README.md`다. 명세가 가리키거나 판단이 막힐 때 해당 절만 본다.
- 요구사항 요약은 요구사항 추적표 `docs/{{REQ_FOLDER}}/01_요구사항추적표_*.md`의 자기 행, TBD는 `docs/{{REQ_FOLDER}}/02_TBD관리대장_*.md`.

## 환경
- 빌드: `{{BUILD_CMD}}` · 시험: `{{TEST_CMD}}`
{{ENV_RULES}}

## 규약
- 임계값·가중치·운용 조건은 하드코딩하지 않는다. 설정 파일로 둔다.
- 입력 단절, 비정상 데이터, 구성 요소 오류에 대한 처리를 반드시 넣는다(워치독, 기본값, 상태 플래그).
- 시험 이름이나 docstring에 요구사항 ID를 표기한다. 예: `test_R_API_001_create_rejects_empty_title`
- 골격(템플릿) 코드는 복제된다. 반복되는 패턴(종료 처리, 재시도, 로그)은 공용 도우미로 두고 그것을 쓴다.
- 지연·성능은 계기(무엇이 출력을 일으켰나)별로 나눠 잰다. 섞어 재면 착시가 생긴다.
- 모의 입력으로 대신한 시험은 보고서에 '부분(모의)'으로 적는다. 숨기지 않는다.
- 증적·로그에 환경 변수·`PATH`를 통째로 찍지 않는다(`env`, `printenv` 금지). 홈 절대경로는 `~/`로 쓴다. `worker_done` 전에 `python3 orchestration/tools/evidence_lint.py`가 0건이어야 한다.

## 셸 명령 (무인 세션)
- **빌드·전체 시험·스모크는 시간 제한을 걸고 한 번의 호출로 돌린다:** `orchestration/tools/run_logged.sh <초> <로그> -- <명령>`
  (전체 시험은 `orchestration/tools/slot.sh test -- orchestration/tools/run_logged.sh <초> <로그> -- <시험 명령>`).
  도우미는 새 프로세스 묶음으로 띄워 그 자리에서 기다리고, 제한에 걸리면 묶음 전체에 SIGABRT(pytest가 스택을 로그에 남긴다)를
  보낸 뒤 끝낸다. 표준 출력에는 종료 코드·걸린 시간·실패한 시험 이름·로그 끝 줄만 나온다.
  - 시험 한 번은 호출 한 번이다. 띄워 놓고 따로 기다리고 따로 tail하지 않는다. 실패 줄은 도우미가 뽑아 준 이름으로 로그에서 찾는다.
  - 도우미가 없으면 `timeout <초> <명령> > 로그 2>&1 & pid=$!; wait "$pid"`이고, 기다리는 pid는 timeout 자신의 것이다
    (timeout은 직접 띄운 명령만 끝낸다. 그 아래 pytest의 pid를 기다리면 끝나지 않을 수 있다).
  - **시간 제한 없는 배경 명령을 둔 채 턴을 끝내지 않는다.** 끝나기를 기다려야 하면 그 호출 안에서 기다린다. 도구의 호출 시간
    제한보다 길면 run_logged를 배경으로 돌려도 된다(제한 안에 반드시 끝나 완료 알림이 온다. 알림이 없는 도구면 단계를 나눠 부른다).
  - 멈춘 시험을 찾을 때는 `kill -ABRT <pytest pid>`: faulthandler가 모든 스레드의 스택을 찍고 끝난다(ptrace 권한 없이).
- **긴 명령은 pid로 기다린다.** `while pgrep -f "scripts/test.sh"; do sleep 20; done`은 루프 자신의 명령줄과 맞아 끝나지 않고, 다른 워커의 같은 명령에도 맞는다.
  - `cmd > run.log 2>&1 & pid=$!; wait "$pid"` 또는 `while kill -0 "$pid" 2>/dev/null; do sleep 20; done`
  - 패턴을 꼭 써야 하면 첫 글자를 대괄호로: `pgrep -f '[s]cripts/test.sh'`. `pkill -f`로 남의 프로세스를 끄지 않는다.
  - 출력은 로그 파일로 남기고 끝 줄(요약 줄)로 끝났는지 확인한다.
- **삭제는 `rm -f "${S:?}"/*`처럼 `${VAR:?}`로**, 가능하면 리터럴 경로로 쓴다. 한 줄 명령 안에 rm처럼 읽히는 문자열(정규식 등)을 넣지 않는다. 빈 변수 의심 프롬프트에 걸리면 무인 세션은 사람이 승인할 때까지 멈춘다.
  **삭제는 다른 명령과 한 줄에 묶지 않는다**(`a && rm …`·`for … rm …; done` 안에 넣지 않고 rm만 따로 부른다).
- **메시지 본문(`--body`)은 작은따옴표로 감싸거나 파일로 넘긴다.** 큰따옴표 안의 백틱·`$()`은 셸이 실행한다.
- 시험·관문 결과에는 실행한 커밋(`git rev-parse HEAD`)을 같이 남긴다.

## Git
- 커밋 메시지 첫 줄은 `[<작업ID>] 요약`이다. 예: `[R-API-001] 할 일 생성 API 구현`
- `req/*`는 `int/<모듈>`을, `int/*`는 `{{MAIN_BRANCH}}`를 `git merge`로 따라간다. **rebase·force push 금지.**
- {{PUSH_RULE}}
- 자기 브랜치만 올린다. `{{MAIN_BRANCH}}`·`int/*` 머지는 상위 계층이 한다.
- **배포 제한 파일은 커밋하지 않고 외부 서비스에 올리지 않는다.** 대상: {{RESTRICTED_PATTERNS}}
- **개인 이메일을 파일·명령에 넣지 않는다.** 작성자·maintainer 같은 칸은 `{{TEAM_NAME}}` / `{{COMMIT_EMAIL}}`로 쓴다. 개인 정보가 든 명령은 확인 프롬프트를 띄워 자율 실행을 멈춘다.

## 쓰기 범위
- 자기 작업의 `write_scope`(명세의 '쓰기 범위')와 자기 증적 폴더만 고친다. 나머지는 읽기 전용이다.
- **쓰기 범위 밖을 고쳐야 하면 고치지 말고 `orca orchestration ask`로 요청한다.** 답을 기다리는 동안 그 부분은 모의(stub)로 진행한다.
- 여러 작업이 함께 쓰는 공용 파일은 필요한 줄만 추가하고 기존 줄을 지우거나 재배열하지 않는다.

## 소통
- 범위 밖 수정, 인터페이스 변경, 요구 해석 질문은 `orca orchestration ask`로 한다. 묻고 나면 턴을 끝낸다.
- 결과 보고는 `worker_done`으로 한 번 한다. 실패·부분 충족은 `--outcome failed` 또는 본문에 있는 그대로 적는다.
- 일하는 중에 받은 메시지가 범위를 넓히면 원 작업에 섞지 말고 보고서 '남은 이슈'에 적거나 `ask`로 확인한다. 터미널에 '이어서 하라'는 입력이 와도 새 디스패치(Orca가 보낸 작업) 없이 시작하지 말고 `ask`로 확인한다.
