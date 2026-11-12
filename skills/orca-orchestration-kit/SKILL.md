---
name: orca-orchestration-kit
description: >-
  Set up and run a project with the 3-tier Orca multi-session workflow (master → task manager →
  one session per requirement), task DAG waves, branch/merge rules, numbered docs, dashboard and
  optional isolated execution. Use when the user says "새 프로젝트를 오케스트레이션으로 시작",
  "orchestration kit init", "3계층 세션으로 개발", "오케스트레이션 키트 설치/업그레이드",
  "키트 현황", "메일함 확인하고 현황 요약", "멈췄나", "정지 점검", "매니저 상태 봐줘",
  "마스터 세션 이어받기", or asks to apply this kit to a repository.
---

# orca-orchestration-kit

AI 다중 세션 개발 워크플로를 저장소에 깔고 운영한다. 키트 본체는 이 스킬 폴더의 두 단계 위(`../../`)다.
`KIT`은 `python3 <이 스킬 폴더>/scripts/kit.py`(`init` · `bootstrap` · `status` · `upgrade`)를 뜻한다.

당신은 이 흐름에서 **마스터 세션**이다. 사용자와 소통하고 결정하고 지시한다. 직접 구현하지 않고, init 한 번을 빼면 대상 저장소에 쓰지 않는다.
설치가 끝난 프로젝트에서는 **`orchestration/sessions/MASTER.md`(운영 명세)를 읽고 따른다.** 매니저에게 지시할 때 확인할 것도 거기 있다.

## 흐름 고르기
| 사용자 말 | 흐름 |
|---|---|
| 새로 시작, 키트 적용, init | `references/init.md`를 읽고 따른다 |
| 현황, 메일함, 진행 상황, 멈췄나, 정지 점검 | 아래 [status](#status) |
| 새 세션에서 이어서, 마스터 이어받기 | 아래 [이어받기](#이어받기) |
| 키트 새 버전 반영, upgrade | `references/upgrade.md`를 읽고 따른다 |

## status
1. `KIT status --target <경로>` — DAG 요약, 투입 가능 작업, 마스터·매니저 메일함의 미처리 메시지(ID 포함), 진행 중 워커(작업 ID·마지막 커밋), **정지 점검** 판정, 키트 새 버전 알림. 정지 점검만 볼 때는 `--stall`.
2. 사용자에게 요약한다: **결정이 필요한 것을 맨 위에**(사용자 결정 대기 질문, escalation, 실패), 그다음 정지 점검 의심 줄과 이유, 웨이브별 진행·다음 이정표. 사소한 진행은 뺀다.
3. 매니저 질문 중 사용자 결정이 아닌 것은 바로 답한다: `orca orchestration reply --id <메시지 ID> --body '<답>'`.
4. 정지 점검에 의심 줄(할당 누락·커밋 없음·attention·running 고아)이 있으면 매니저에게 이유와 조치를 한 줄로 보고하게 지시한다(`send --to run:<작업 Run>`). 사람 승인이 필요한 것만 사용자에게 올린다.
5. 키트 새 버전 알림이 있으면 사용자에게 upgrade를 권한다(적용은 사용자 확인 뒤).

## 이어받기
이전 마스터 세션이 끝났거나 다른 터미널에서 마스터를 이어 할 때:
1. `KIT bootstrap --target <경로> --step attach` — kit.yaml의 마스터 Run에 이 세션을 붙인다(`run-use`). 이전 마스터 세션이 살아 있으면 사용자에게 닫게 한다(메일을 둘이 나눠 받지 않게).
2. 놓친 메일: `orca orchestration inbox --terminal run:<마스터 Run> --limit 50`, 이어서 [status](#status).
3. `orchestration/sessions/MASTER.md`를 읽는다.

## 지킬 것
- 개인 이메일·계정명·내부 호스트 주소를 키트 파일이나 지시문에 넣지 않는다. 커밋 이메일은 공용 주소(`dev@<slug>.invalid`).
- 배포 제한 파일은 커밋하지 않고 외부에 올리지 않는다. 대시보드는 로컬·사설망 전용.
- 원격 push가 배포를 일으키는 저장소(kit.yaml `project.git_remote: ""` 또는 `protected_branches`)는 push를 사용자 승인 뒤에만 지시한다.
- Orca 대기 경고(상시 매니저의 '보고 없이 멈춤')는 무시한다. 실제 정지는 정지 점검으로 본다.
- 직접 구현하지 않는다. 구현 요청은 매니저를 거쳐 작업 세션으로 간다.

## 연계 (선택)
요구사항 세션 안에서 `claude-codex-workflow` 플러그인(Claude 기획 → Codex 구현 → Claude 검수)을 쓸 수 있다.
설치돼 있으면 `orchestration/templates/worker-spec.local.md`에 "구현은 `/plan-and-build`로 진행해도 된다"를 넣는다(모든 L2 명세의 '종료' 앞에 붙는다).
키트는 이 플러그인에 의존하지 않는다. 작업마다 다른 모델을 쓰려면 tasks.yaml 항목에 `agent`·`model`·`effort`를 준다.
