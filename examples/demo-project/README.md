# 예제: 할 일 관리 웹앱 (가상)

키트를 가상의 작은 프로젝트에 적용한 결과물이다. 모듈 2개(API 서버 `api`, 웹 UI `web`), 작업 6개 + 게이트 1개.

| 파일 | 내용 |
|---|---|
| `answers.yaml` | `kit.py init --answers`에 넘긴 답변 (검토 단계 없음, 실행 격리 없음) |
| `orchestration/tasks.yaml` | 작업 DAG: F-01 계약·골격 → G-0 → R-API-001 · R-WEB-001 → I-API · I-WEB → S-01 |
| `docs/01_requirements/` | RTM(요구사항 3개), TBD 관리대장(2건) |
| `docs/06_evidence/F-01/report.md` | 증적 보고서 예 |
| `orchestration/specs/R-API-001.md` | 렌더링된 L2 명세 (perf: true → 지연 측정 문구) |
| `orchestration/specs/R-WEB-001.md` | 렌더링된 L2 명세 (env: [sim], runtime.sim: false → '모의로 먼저' 블록) |
| `orchestration/specs/I-WEB.md` | 렌더링된 L1 통합 명세 |
| `dashboard/index.html` | 대시보드 생성 결과 (브라우저로 연다. 오프라인) |
| `installed-tree.txt` | init이 깐 파일 목록 |
| `validate_dag.txt` | DAG 검증 결과 |
| `make_demo.sh` | 위 결과를 다시 만드는 스크립트 |

`make_demo.sh`는 임시 폴더에 빈 git 저장소를 만들고 → init → 이 폴더의 내용으로 덮어쓰고 → 진행 상황을 흉내 낸 git 이력(F-01 머지, G-0 통합 브랜치 생성, R-API-001 진행 중)을 만든 뒤 → 검증·렌더링·대시보드를 돌려 결과를 이 폴더로 복사한다.

```bash
examples/demo-project/make_demo.sh
```
대시보드에서 확인할 것: F-01·G-0 머지됨(git 근거), R-API-001 진행(`[R-API-001]` 커밋), R-WEB-001 착수 가능(선행 머지 파생), RTM 모듈별 섹션, TBD 필터.
