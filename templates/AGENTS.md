# 작업 규칙 (모든 에이전트 세션 공통) — {{PROJECT_NAME}}

{{PROJECT_SUMMARY}}

## 먼저 읽을 것
- `orchestration/README.md`: 세션 계층, 브랜치, 쓰기 범위, 완료 기준
- `orchestration/tasks.yaml`: 작업 목록과 의존성 (정본)
- `docs/README.md`: 문서 번호·파일명 규칙
- 요구사항 추적표 `docs/{{REQ_FOLDER}}/01_요구사항추적표_*.md`, TBD 관리대장 `docs/{{REQ_FOLDER}}/02_TBD관리대장_*.md`

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

## Git
- 커밋 메시지 첫 줄은 `[<작업ID>] 요약`이다. 예: `[R-API-001] 할 일 생성 API 구현`
- `req/*`는 `int/<모듈>`을, `int/*`는 `{{MAIN_BRANCH}}`를 `git merge`로 따라간다. **rebase·force push 금지.**
- 자기 브랜치만 푸시한다. `{{MAIN_BRANCH}}`·`int/*` 머지는 상위 계층이 한다.
- **배포 제한 파일은 커밋하지 않고 외부 서비스에 올리지 않는다.** 대상: {{RESTRICTED_PATTERNS}}
- **개인 이메일을 파일·명령에 넣지 않는다.** 작성자·maintainer 같은 칸은 `{{TEAM_NAME}}` / `{{COMMIT_EMAIL}}`로 쓴다. 개인 정보가 든 명령은 확인 프롬프트를 띄워 자율 실행을 멈춘다.

## 쓰기 범위
- 자기 작업의 `write_scope`(tasks.yaml)와 `docs/06_evidence/<작업ID>/`만 고친다. 나머지는 읽기 전용이다.
- **쓰기 범위 밖을 고쳐야 하면 고치지 말고 `orca orchestration ask`로 요청한다.** 답을 기다리는 동안 그 부분은 모의(stub)로 진행한다.
- 여러 작업이 함께 쓰는 공용 파일은 필요한 줄만 추가하고 기존 줄을 지우거나 재배열하지 않는다.

## 소통
- 범위 밖 수정, 인터페이스 변경, 요구 해석 질문은 `orca orchestration ask`로 한다. 묻고 나면 턴을 끝낸다.
- 결과 보고는 `worker_done`으로 한 번 한다. 실패·부분 충족은 `--outcome failed` 또는 본문에 있는 그대로 적는다.
