# 현장 적용 묶음 (이정표 때 한 번에)

키트는 계속 고치고 올리지만, 운영 중인 프로젝트에는 **이정표(예: 1차 관문)에서 한 번에** 적용한다. 적용·확인·매니저 재학습이
작업 중간에 끼어드는 비용이 크기 때문이다(하루 오전에 여섯 번 올린 적이 있다). 예외는 지금 동작이 현장을 해치는 결함뿐이다.

적용 절차는 `skills/orca-orchestration-kit/references/upgrade.md`(적용 직전·끝에 매니저에게 알리고, 키트가 바꾼 경로만 커밋).
아래는 매니저가 **한 번만 다시 익히면 되게** 묶음마다 바뀐 운영을 모은 것이다.

## 다음 묶음: v0.8.4 → v0.12.0 (v0.8.5 · v0.9.0 · v0.9.1 · v0.10.0 · v0.10.1 · v0.11.0 · v0.12.0)

### 매니저가 새로 할 일
1. **투입 전에 약속을 닫는다.** L1·L2 작업에 `guarantees`(보장하는 것)와 `out_of_scope`(범위 밖)를 적고 낸다. `validate_dag.py`가 빠진 작업과 '언제나·모든·완전히' 같은 열린 약속을 경고한다. 이미 돌고 있는 작업은 다음 차수(보완·이어받기)를 낼 때 적는다.
2. **검토 판정에 계열을 남긴다.** `models.py feedback <ID> major|fail --category <계열>`(같은 구조적 원인이면 같은 이름). 정지 점검에 '범위 결정 필요'가 나오면 보완을 한 번 더 내지 말고 범위 선택지를 마스터에 올리고 작업에 `hold`.
3. **핵심 약속 점검표를 채운다.** 적용하면 `orchestration/checklist.md`가 새로 생긴다(예시 줄만 있음). 지금까지 검토가 되풀이해 잡은 지적(영역 분리, CLI 차단, 승인 무결성 등)을 '## 항목'에 한 줄씩 넣는다. 다음 구현·보완 명세부터 붙는다.
4. **pre-push 훅을 다시 깐다**: `orchestration/tools/install_hooks.sh`(훅은 복사본이라 upgrade만으로는 안 바뀐다). 새 훅은 kit.yaml·PyYAML을 못 읽으면 push를 막는다(v0.9.1).
5. **워크트리를 정리한다(v0.10.0).** 적용 뒤 `python3 orchestration/tools/worktree_sweep.py`로 미리 보고 `--apply`. 그다음부터 launch.sh가 한 시간에 한 번 '정리'(merged·변경 없음·진행 중 없음)만 저절로 지운다. '확인 필요'(커밋하지 않은 변경, 머지 안 된 커밋, 주인 없음)는 이정표마다 목록을 보고 닫는다. 끄려면 kit.yaml `cleanup.auto_sweep: false`.
6. **전체 시험 슬롯 수를 정한다(v0.10.1).** kit.yaml은 프로젝트 소유라 `slots.test: 1`이 그대로 남는다. 정지 점검의 '슬롯 줄'이 이 기계 기준 제안값을 보이면(코어 4개·메모리 8 GiB당 1) 시험 하나의 최대 메모리를 한 번 재 보고 `slots.test`를 올린다. 이제 슬롯이 여럿이어도 먼저 빈 것을 잡는다.
7. **긴 명령은 `run_logged.sh`로(v0.11.0).** 병합 관문의 빌드·전체 시험·스모크를 `orchestration/tools/run_logged.sh <초> <로그> -- …`로 돌린다(전에 손으로 쓰던 step() 대신). 워커·검토자 규칙은 AGENTS.md와 명세가 바뀌어 새 투입부터 따른다. 이미 도는 워커에는 필요하면 `send`로 한 줄 알린다.
8. **예약 점검을 켠다/다시 깐다(v0.11.0).** `quota_watch.py install`(= `stall_watch.py`)이 이제 한도 말고 '커밋 없음'(60분)·'긴 명령'(시간 제한 없이 30분)·'확인 창 대기'로도 매니저를 깨우고, 빈 변수 의심 rm 확인 창은 승인 ID를 찾으면 스스로 거절한다. 이미 깔았으면 그대로 새 판정이 적용된다. 처음 '확인 창 대기'가 걸리면 `status.py --json`의 `human_wait`(승인 ID를 찾았는지)를 키트에 알려 준다.
9. **작업 크기를 문맥으로 본다(v0.11.0).** 현황 워커 줄의 '문맥 Nk·호출 M', 정지 점검 '문맥 큼'(30만 토큰). 작업별 실측은 `usage.py --since 6h`(지표 문서의 작업별 행).
10. **worker_done이 오면 먼저 `advance.py`(v0.12.0).** 처음에는 `--dry-run`으로 몇 번 보고 맞으면 그냥 돌린다. `[자동]`은 손으로 다시 하지 않고 `[판단]`만 처리한다. 새 명세부터 워커 보고 첫 줄이 `결과: …` / `판정: … · 계열: …`로 고정되므로, 적용 전에 낸 워커의 보고는 `[판단]`으로 나온다(예전처럼 손으로). 검토 없이 가는 작업은 tasks.yaml에 `review: false`.
11. **기록 커밋은 경로를 지정해 담는다**(`git add -- <파일>`, `git add -A`·`orchestration/` 통째 금지). 키트 적용 중 커밋 섞임(v0.8.5, L-21).

### 저절로 바뀌는 것
- escalation 메일을 `--full`로 읽어 필터 중단(codex 안전 필터)을 메일 Last output에서 잡는다. 한도 메일(quota reached·Resets in)은 필터·오류 중단으로 잡지 않는다(v0.8.5).
- 검토 라우팅에서 구현한 모델 자신은 더 뒤로 간다.
- (v0.11.0) 검토 2회차부터 effort가 한 단계 낮아진다(`--effort`를 주면 그것). `models.py quota`가 계정별로(고른 계정 말고 매니저·마스터가 도는 계정도, 마지막 값과 나이) 보인다.
- (v0.10.1) 슬롯 줄에 선 워커는 '진행 중·최근 커밋'이나 '커밋 없음' 대신 '슬롯 대기 N분째'로 보인다.
- `kit.py upgrade --apply`가 끝에 매니저 커밋용 `git add -- <키트가 바꾼 경로>`를 출력한다.
- (v0.9.1) launch가 worker-start 실패 때 Orca 고아 Task를 닫고, dispatch.tsv·tasks.yaml 쓰기를 잠근다. cleanup이 워커 목록 전부를 본다. evidence_lint 이메일 도메인 비교가 엄격해져 전에 통과하던 증적이 걸릴 수 있다. quota_watch는 예약 실행이 에이전트를 띄우면 스스로 꺼진다.

### 고를 수 있는 설정 (models.yaml · kit.yaml, 프로젝트 소유라 upgrade가 안 바꾼다)
- `routing.cross_review: model` — 독립 검토 구독(codex·agy)이 모자랄 때 같은 구독의 다른 모델로 검토를 허용한다(예: sonnet 구현 → opus 검토). 기본 `true`(다른 구독 우선, 없으면 대기).
- `routing.auto_update: off` — 관문·측정처럼 재현성이 중요한 구간에서는 CLI 자동 업데이트를 끈다(기본 daily).
- `kit.yaml review.scope_escalate_after: 2` — 범위 결정을 권하는 연속 차단 수(없으면 2).
- (v0.11.0) kit.yaml에 없으면 기본값으로 돈다: `review.recheck_effort_step: 1`, `watch.{stall: true, stale_min: 60, long_cmd_min: 30, long_cmd_pattern, auto_deny_rm: true}`, `status.context_warn_k: 300`. 바꾸려면 그 키를 적는다.
- 쉬는 구독(grok)을 Orca에서 켜 두면 검토 대기가 준다. grok은 계정 설정에서 학습용 데이터 보관(coding data retention)을 꺼 둔다.

### 확인
- `python3 orchestration/tools/validate_dag.py` — 투입 전 작업의 guarantees 경고 목록
- `python3 orchestration/tools/status.py --stall` — 범위 결정 필요·검토 반복, 필터 중단(메일), 긴 명령·확인 창 대기·문맥 큼
- `orchestration/tools/run_logged.sh 5 /tmp/t.log -- sleep 10` — rc 124와 요약 한 덩어리가 나오면 된다
- `python3 orchestration/tools/advance.py --dry-run` — 지난 24시간 worker_done이 어떻게 처리될지(이미 진행된 것은 건너뜀)
- 커밋: `[L0] orchestration kit v0.12.0 upgrade`(apply 출력의 경로만)
