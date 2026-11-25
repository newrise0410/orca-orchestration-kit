# upgrade 흐름 (키트 새 버전을 기존 프로젝트에 반영)

`KIT` = `python3 <스킬 폴더>/scripts/kit.py`.

1. 키트 저장소를 최신으로 맞춘다(사용자가 정한 방법으로. 예: `git -C <키트> pull`).
2. `KIT upgrade --target <경로>` — 파일별 상태와 diff를 보여 준다(목록만: `--summary`).
   | 상태 | 뜻 | `--apply` 때 |
   |---|---|---|
   | 키트 갱신 | 프로젝트가 안 고친 파일 | 새 판으로 교체 |
   | 자동 병합 | 프로젝트 수정과 키트 변경이 겹치지 않음(설치 당시 원본을 키트 git 이력에서 되살려 3방향 병합) | 병합 결과로 교체 |
   | 충돌 N곳 / 충돌 | 겹침이 있거나 설치 당시 원본을 못 찾음 | `<파일>.kit-new`(병합 표시 포함)를 쓴다. 직접 합친 뒤 지운다 |
   | 키트 밖 파일과 겹침 | 키트가 새로 넣은 경로에 프로젝트 자체 파일이 있음 | `<파일>.kit-new`. 덮어쓰지 않는다 |
   | 새 파일 | 키트에 새로 생긴 파일 | 만든다 |
   | `.gitignore`: 키트 블록 갱신 | 표시 블록 안의 줄이 키트 목록과 다름 | 블록만 새 목록으로 바꾼다(블록 밖의 줄은 그대로). 이제 무시되지 않는 파일은 `git status`로 보고 올린다 |
   | seed | kit.yaml·tasks.yaml·AGENTS.md·문서(프로젝트 소유) | 기본은 알림만. `--include-seed`면 diff를 보고 `.kit-new`로만 쓴다 |
3. **diff를 사용자에게 요약해 보여 주고 확인을 받는다.** 확인 없이 `--apply`하지 않는다. 덮어쓰기(`--overwrite-conflicts`)는 사용자가 원할 때만.
4. **적용 직전에 매니저에게 알린다.** 적용 중에 매니저가 `orchestration/`을 통째로 담아 기록 커밋을 하면 키트 변경이 그 커밋에 섞인다(현장에서 일어났다).
   `orca orchestration send --to run:<작업 Run> --type status --subject '키트 적용 시작' --body 'v<버전> upgrade --apply 시작. 끝 알림까지 기록 커밋은 경로를 지정해 담고 orchestration/tools·templates·sessions는 담지 말 것'`
5. 확인되면 `--apply`. `.kit-new`가 생기면 매니저에게 합치기를 지시한다. 끝나면 '키트 적용 끝' 알림과 함께, 출력 끝의 `git add -- <키트가 바꾼 경로>` 명령을 그대로 넘긴다(그 경로만 담아 커밋한다).
   - 프로젝트가 키트 파일에 넣었던 규칙은 이번에 덧붙임 파일로 옮기면 다음부터 충돌하지 않는다: 명세 템플릿 → `orchestration/templates/<이름>.local.md`, 매니저 → `orchestration/sessions/MANAGER.local.md`(구조 문서 `orchestration/README.md` 9장).
   - v0.5.2 이전 설치본: `.gitignore`의 `dashboard/`가 대시보드 도구 폴더까지 무시했다. 적용 뒤 매니저에게 `git add orchestration/tools/dashboard`를 같은 커밋에 넣게 한다.
   - v0.4의 모델 자동 라우팅: `orchestration/models.yaml`(seed)은 새 파일이라 만들어진다. 만들어지면 그때부터 `launch.sh`가 agent/model 없는 작업을 자동 라우팅한다. 원치 않으면 `routing.enabled: false`. 적용 뒤 `models.py discover`로 구독을 확인한다.
   - 새 kit.yaml 키(예: v0.3의 `project.git_remote`·`restricted`·`review`·`slots`)는 seed라 자동으로 안 들어간다. `--include-seed` diff를 보고 매니저가 손으로 넣는다. 로컬 전용으로 운영해 온 저장소는 `git_remote: ""`를 꼭 넣는다(없으면 기본값 origin으로 명세가 렌더링된다).
6. 커밋은 매니저에게 지시한다: `[L0] orchestration kit v<버전> upgrade`(위 경로만). 매니저는 새 MANAGER.md를 다시 읽는다(상시 세션은 기동 때 읽은 판을 쓰고 있다).
