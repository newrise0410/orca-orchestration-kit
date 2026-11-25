# 현장 적용 묶음 (이정표 때 한 번에)

키트는 계속 고치고 올리지만, 운영 중인 프로젝트에는 **이정표(예: 1차 관문)에서 한 번에** 적용한다. 적용·확인·매니저 재학습이
작업 중간에 끼어드는 비용이 크기 때문이다(하루 오전에 여섯 번 올린 적이 있다). 예외는 지금 동작이 현장을 해치는 결함뿐이다.

적용 절차는 `skills/orca-orchestration-kit/references/upgrade.md`(적용 직전·끝에 매니저에게 알리고, 키트가 바꾼 경로만 커밋).
아래는 매니저가 **한 번만 다시 익히면 되게** 묶음마다 바뀐 운영을 모은 것이다.

## 다음 묶음: v0.8.4 → v0.9.1 (v0.8.5 + v0.9.0 + v0.9.1)

### 매니저가 새로 할 일
1. **투입 전에 약속을 닫는다.** L1·L2 작업에 `guarantees`(보장하는 것)와 `out_of_scope`(범위 밖)를 적고 낸다. `validate_dag.py`가 빠진 작업과 '언제나·모든·완전히' 같은 열린 약속을 경고한다. 이미 돌고 있는 작업은 다음 차수(보완·이어받기)를 낼 때 적는다.
2. **검토 판정에 계열을 남긴다.** `models.py feedback <ID> major|fail --category <계열>`(같은 구조적 원인이면 같은 이름). 정지 점검에 '범위 결정 필요'가 나오면 보완을 한 번 더 내지 말고 범위 선택지를 마스터에 올리고 작업에 `hold`.
3. **핵심 약속 점검표를 채운다.** 적용하면 `orchestration/checklist.md`가 새로 생긴다(예시 줄만 있음). 지금까지 검토가 되풀이해 잡은 지적(영역 분리, CLI 차단, 승인 무결성 등)을 '## 항목'에 한 줄씩 넣는다. 다음 구현·보완 명세부터 붙는다.
4. **pre-push 훅을 다시 깐다**: `orchestration/tools/install_hooks.sh`(훅은 복사본이라 upgrade만으로는 안 바뀐다). 새 훅은 kit.yaml·PyYAML을 못 읽으면 push를 막는다(v0.9.1).
5. **기록 커밋은 경로를 지정해 담는다**(`git add -- <파일>`, `git add -A`·`orchestration/` 통째 금지). 키트 적용 중 커밋 섞임(v0.8.5, L-21).

### 저절로 바뀌는 것
- escalation 메일을 `--full`로 읽어 필터 중단(codex 안전 필터)을 메일 Last output에서 잡는다. 한도 메일(quota reached·Resets in)은 필터·오류 중단으로 잡지 않는다(v0.8.5).
- 검토 라우팅에서 구현한 모델 자신은 더 뒤로 간다.
- `kit.py upgrade --apply`가 끝에 매니저 커밋용 `git add -- <키트가 바꾼 경로>`를 출력한다.
- (v0.9.1) launch가 worker-start 실패 때 Orca 고아 Task를 닫고, dispatch.tsv·tasks.yaml 쓰기를 잠근다. cleanup이 워커 목록 전부를 본다. evidence_lint 이메일 도메인 비교가 엄격해져 전에 통과하던 증적이 걸릴 수 있다. quota_watch는 예약 실행이 에이전트를 띄우면 스스로 꺼진다.

### 고를 수 있는 설정 (models.yaml · kit.yaml, 프로젝트 소유라 upgrade가 안 바꾼다)
- `routing.cross_review: model` — 독립 검토 구독(codex·agy)이 모자랄 때 같은 구독의 다른 모델로 검토를 허용한다(예: sonnet 구현 → opus 검토). 기본 `true`(다른 구독 우선, 없으면 대기).
- `routing.auto_update: off` — 관문·측정처럼 재현성이 중요한 구간에서는 CLI 자동 업데이트를 끈다(기본 daily).
- `kit.yaml review.scope_escalate_after: 2` — 범위 결정을 권하는 연속 차단 수(없으면 2).
- 쉬는 구독(grok)을 Orca에서 켜 두면 검토 대기가 준다. grok은 계정 설정에서 학습용 데이터 보관(coding data retention)을 꺼 둔다.

### 확인
- `python3 orchestration/tools/validate_dag.py` — 투입 전 작업의 guarantees 경고 목록
- `python3 orchestration/tools/status.py --stall` — 범위 결정 필요·검토 반복, 필터 중단(메일)
- 커밋: `[L0] orchestration kit v0.9.0 upgrade`(apply 출력의 경로만)
