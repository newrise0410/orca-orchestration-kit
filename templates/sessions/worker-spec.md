# 작업 {id}: {title}

당신은 {{PROJECT_NAME}} 개발의 **{layer} 작업 세션**이다. 이 작업 하나만 수행한다.
먼저 저장소 루트의 `AGENTS.md`(작업 규칙)와 `orchestration/README.md`(구조)를 읽어라.

## 대상
- 모듈: {module} ({module_name})
- 충족·입증할 요구사항: {reqs}
- 요구사항 요약: 요구사항 추적표(`{rtm}`)의 해당 행
- 관련 TBD: TBD 관리대장(`{tbd}`) 참조

## 브랜치
- 작업 브랜치: `{branch}` (기준: `{base}`). 워크트리는 Orca가 `{base}`에서 분기해 만들어 준다.
  - Orca가 만든 로컬 브랜치 이름은 규칙과 다를 수 있다. 원격에는 반드시 `{branch}`로 푸시한다: `git push origin HEAD:{branch}`
- 기준 브랜치가 갱신되면 `git fetch origin && git merge origin/{base}`로 따라간다(rebase·force push 금지).

## 쓰기 범위 (이 밖은 읽기 전용)
{write_scope}
- 추가 허용: `{evidence_dir}`, 그리고 자기 모듈의 시험 폴더
- 여러 작업이 함께 쓰는 공용 파일(패키지 매니페스트, 의존성 목록, 진입점 등록, 모듈 README)은 꼭 필요한 줄만 추가하고, 기존 줄은 지우거나 재배열하지 마라. 바꾼 줄은 증적 보고서 5절에 적는다.
- 쓰기 범위 밖을 고쳐야 하면 고치지 말고 `orca orchestration ask`로 요청하라. 답을 받을 때까지 그 부분은 모의(stub)로 진행하라.

## 의존 작업
{deps_block}

## 완료 판정 (acceptance)
{acceptance}

## 산출물·증적
1. 빌드·시험 통과 로그 (`{{BUILD_CMD}}`, `{{TEST_CMD}}`)
2. acceptance 시나리오 결과 (검증 단계 {stage})
3. {perf_line}
4. 증적 보고서 `{evidence}` (양식: `orchestration/templates/evidence.md`)
5. `[{id}]` 접두 커밋을 작업 브랜치에 올리고 `git push origin HEAD:{branch}`로 푸시한다.

## 실행 환경
{env_block}

## 금지·질문
- 계약(인터페이스·공통 라이브러리), 단계 문서, `orchestration/`은 수정하지 마라. 변경이 필요하면 `ask`로 요청한다.
- 요구사항 해석이 모호하면 추측으로 확정하지 말고 `ask`로 질문하라.
- 시험 실패를 성공으로 보고하지 마라. 일부만 충족했으면 그대로 보고하라. 모의 입력으로 대신한 부분은 보고서에 '부분(모의)'으로 적는다.

## 종료
위 산출물을 갖추면 `worker_done`에 다음을 담아 보고한다: 결과(통과/부분/실패), 증적 경로, 측정치, 남은 이슈, 새 의존성·라이선스.
비정상 종료(빌드 불가, 선행 작업 결함 등)는 `--outcome failed`로 보고하고 원인을 적는다.
