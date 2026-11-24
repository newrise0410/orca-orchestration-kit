# init 흐름 (새 프로젝트에 키트 적용)

`KIT` = `python3 <스킬 폴더>/scripts/kit.py`. 어느 단계든 `--dry-run`을 붙이면 쓰거나 실행하지 않고 계획만 출력한다.

1. **묻는다** (한 번에 묶어서, 권장 기본값을 같이 제시한다)
   - 대상 저장소 경로 (git 저장소여야 한다. 아니면 `git init`을 제안)
   - 프로젝트 이름, 짧은 slug(영소문자, 컨테이너·네트워크 접두)
   - 모듈 목록: `키:이름:약칭` (예: `api:API 서버:API,web:웹 UI:WEB`). 모듈 하나 = `int/<키>` 통합 브랜치 = L1 통합 세션 하나
   - **원격 운영:** 브랜치를 `origin`에 올리는가(기본), 아니면 로컬 전용인가(`--git-remote ''`: 원격 push가 운영 재배포를 일으키는 저장소). 원격이면 승인이 필요한 보호 브랜치(`--protected main`)
   - 검토 단계: `none`(기본) / `defense`(SRR→SFR→PDR→CDR) / `product`(REQ→SPEC→DESIGN→RELEASE). 이름만 바꾸려면 설치 후 kit.yaml
   - 실행 격리(`--run`): 동시 세션이 빌드·시험·시뮬을 돌리면 권장. 프로필(예: `ros2`). 원격 머신(`--remote user@host`)
   - **모델 자동 라우팅에 쓸 구독**(`--agents claude,codex,antigravity,grok`). 기본은 설치된 CLI 전부. 목표는 balanced(설치 후 `orchestration/models.yaml`)
   - 빌드·시험 명령, 배포 제한 파일 패턴(기본 `*.pdf,.orca/`)
2. **계획을 보여 준다:** `KIT init --target <경로> … --dry-run`. 파일 배치 목록을 사용자에게 요약한다.
3. **깐다:** 같은 명령에서 `--dry-run`을 뺀다. 이미 있는 파일은 덮어쓰지 않는다(필요하면 사용자 확인 후 `--force`).
4. **tasks.yaml W0 초안을 함께 만든다.**
   - init이 만든 초안(F-00a 기획 → F-00b 유사 오픈소스 조사·반영 → F-01 계약 → F-02 공통 라이브러리 → F-03 골격 → G-0 → I-<모듈> → S-01)을 사용자에게 보여 준다.
   - **기획과 유사 오픈소스 조사는 개발 착수 전의 기본 단계다.** 사용자가 따로 말하지 않아도 거친다. `F-00a`는 hold로 깔린다: 요구를 사용자와 인터뷰해(제품과 사용자, 핵심 기능, 실행 환경, 제약) 매니저에게 넘기고(`specs/F-00a.extra.md`) hold를 풀게 한다. `F-00b`가 낸 사용자 결정 사항(범위·모듈 구성·토대 프로젝트 채택·기술 스택의 큰 방향)을 확정받은 뒤에 `F-01`을 낸다. 요구·설계 문서가 이미 있는 프로젝트는 `F-00a`를 지우거나 merged로 두고 `F-00b`만 돌린다.
   - 실제 경로(`write_scope`)와 acceptance를 프로젝트에 맞게 고친다. 요구 문서가 있으면 요구사항마다 W1 L2 작업(`R-<약칭>-nnn`)을 넣고 I-<모듈> deps에 잇는다.
   - **계약이 먼저다.** 모듈 사이 인터페이스·공통 규약·골격을 W0에 넣어야 W1을 병렬로 돌릴 수 있다.
   - 작업마다 모델을 정하려면 `agent`·`model`·`effort`를 준다. 큰 구현은 4묶음 안팎으로 나눠 차수(`<ID>a`, `<ID>b`)로.
   - 고칠 때마다 `python3 <대상>/orchestration/tools/validate_dag.py --repo <대상>`이 오류 0이어야 한다.
   - RTM(`docs/<요구 폴더>/01_요구사항추적표_*.md`)에 같은 ID로 행을 넣는다.
5. **커밋과 W0 브랜치:** 사용자 확인 뒤 `git add -A && git commit -m "[L0] orchestration kit v<버전> init"`, 이어서 `git branch int/common <메인>`(원격 운영이면 `git push origin <메인> int/common`은 사용자에게 묻는다).
6. **모델 확인:** `python3 <대상>/orchestration/tools/models.py discover` — 구독별 CLI·모델 목록. ✗인 카탈로그 모델은 이유를 사용자에게 알린다(로그인 안 됨, 구독에 없는 모델). 이어서 `models.py plan`으로 W0 배분을 보여 준다.
7. **훅:** `KIT bootstrap --target <경로> --step hooks` — pre-push 훅(로컬 전용·보호 브랜치·배포 제한 파일 검사)을 공용 git 디렉터리에 깐다. 다른 훅이 있으면 멈추니 사용자와 정한다.
8. **마스터 Run:** `KIT bootstrap --target <경로> --step master-run` → kit.yaml `orca.master_run`.
9. **매니저 워커:** `KIT bootstrap --target <경로> --step manager`. `MANAGER.md`(+`MANAGER.local.md`)에 Run ID를 채운 명세로 워커를 띄운다.
   **턴을 끝낸다.** 매니저가 `run:<마스터 Run>`으로 '매니저 기동'(작업 Run ID 포함)을 보내면 Orca가 깨운다.
10. **메일함 운영으로 전환:** `KIT bootstrap --target <경로> --step abandon --work-run <매니저가 보고한 Run>`.
   `worker-abandon`은 감독만 해제하고 프로세스는 둔다. 이후 매니저와는 `send --to run:<작업 Run>`으로만 주고받는다. kit.yaml이 바뀌었으니 매니저에게 커밋을 지시한다.
11. **대시보드:** `KIT bootstrap --target <경로> --step dashboard` → `http://127.0.0.1:<port>/`. 상시 기동(systemd user)과 사설망 공개는 `orchestration/tools/dashboard/README.md`. 공개는 사용자 결정이다.
12. 사용자에게 보고: 설치한 모듈, 원격 운영 방식, W0 작업 수, Run ID, 대시보드 주소, 다음 할 일(매니저가 W0 투입).
