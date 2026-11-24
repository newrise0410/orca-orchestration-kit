# 개발 현황 대시보드 — `orchestration/tools/dashboard/`

저장소의 문서·작업 DAG·git 상태(·Orca 작업 상태)를 모아 **단일 HTML**(`dashboard/index.html`)로 만든다.
JS·CSS를 모두 인라인으로 넣었고 외부 CDN을 쓰지 않는다(오프라인, `file://`로 열어도 동작).
배포 제한 자료가 있는 프로젝트를 전제로 한다: **외부 서비스에 올리지 말고 로컬 또는 사설망에서만 연다.** `dashboard/`는 생성물이라 git에서 제외한다.

## 실행
```bash
python3 orchestration/tools/dashboard/build.py                 # 한 번 생성 (설정: orchestration/kit.yaml)
python3 orchestration/tools/dashboard/build.py --orca          # Orca 작업 상태도 반영 (Run: kit.yaml orca.work_run)
orchestration/tools/dashboard/serve.sh [-p 8765] [-i 30] [--orca]   # 127.0.0.1에 띄우고 30초마다 재빌드
orchestration/tools/dashboard/snapshot.sh W1완료               # 이정표 스냅샷 → docs/07_process/snapshots/*.html.gz
```
옵션: `--config <yaml>`, `--out <경로>`, `--tasks-ref main`(tasks.yaml을 git ref에서 읽음), `--repo <경로>`, `-q`.
의존성: Python 3 표준 라이브러리 + PyYAML. git은 읽기 명령만 `--no-optional-locks`로 쓴다(다른 세션의 인덱스를 건드리지 않는다).

## 설정 (kit.yaml에서 읽는 키)
| 키 | 내용 |
|---|---|
| `project.name`, `project.main_branch` | 제목, 머지 판정 기준 브랜치 |
| `modules[]` (`key`·`name`·`short`) | DAG 레인, 모듈별 집계, RTM 섹션 구분(섹션 제목의 `(<short>)`) |
| `waves[]`, `gates[]` | DAG 열 순서, 게이트 판정 |
| `paths.docs`, `paths.evidence_dir`, `paths.skip_dirs` | 번호 폴더 루트, 증적 폴더, 검사 제외 하위 폴더 |
| `paths.rtm`, `paths.tbd` | RTM·TBD 관리대장 파일 글롭 |
| `verification_stages[]` | RTM 검증 단계 열 |
| `review_stages` | 검토 단계(선택 모듈). 단계별 산출물 카드 |
| `orca.work_run` | `--orca` 기본 Run ID |
| `dashboard.title`·`subtitle`·`port`·`storage_key` | 화면 문구, serve 포트 |

전체 키와 기본값은 `dashboard.example.yaml`.

## 탭
| 탭 | 내용 |
|---|---|
| 개요 | 현재 웨이브와 머지 진행, 구독 칩(`orchestration/models.yaml`이 있을 때: 진행 수/동시 상한, 쿨다운, 제외 사유, 오늘 출력 토큰), 웨이브 × 모듈 격자(접기, 접은 상태는 브라우저에 기억), 모듈 거르기, 상태 열 보드(대기·착수 가능·진행·검토·머지. 카드에 배정 모델·경과 시간, 착수 가능 카드에 라우팅 예정 모델과 `launch.sh` 명령 복사), 주의 사항, 집계(웨이브·모듈별 표, 검토 단계 산출물) |
| 작업 DAG | 웨이브×모듈 그래프(SVG)와 표. 노드를 누르면 상세(요구사항·브랜치·write_scope·acceptance·증적·판정 근거) |
| 요구사항 추적 | RTM 모듈별 표, 검증 단계별 충족률, 요구사항 → 작업 → 증적 링크 |
| TBD/TBC | 관리대장, 확정 여부·구분 필터 |
| 문서 | docs 트리, 최신 판·git 수정일, 파일명 규칙 경고, 본문 보기 |
| Git 활동 | 최근 커밋 30개(`[작업ID]` 연결), 브랜치별 base 대비 ahead/behind |

## 작업 상태 판정
순위 `todo < ready < running < review < merged`에서 아래 근거 중 **가장 높은 것**을 쓴다. 각 작업 상세의 "상태 판정 근거"에 출처가 남는다.
1. tasks.yaml `status`
2. git: 전용 브랜치(`req/<ID>`)가 base에 `--no-ff`로 머지됨 → merged, base보다 앞섬 → running. Orca 워커 브랜치는 이름의 작업 ID 토큰으로 연결한다. 커밋 첫 줄 `[<작업ID>]` → running(모두 병합 대상에 있으면 merged). 게이트는 선행 merged + 통합 브랜치 모두 존재 → merged
3. 증적 `docs/06_evidence/<ID>/report.md`가 있으면 review
4. `--orca`: Orca 작업 제목·명세의 작업 ID로 연결(`dispatched/running` → running, `completed` → review)
5. 위 결과가 todo이고 선행이 모두 merged이면 ready(파생)

## 상시 기동 (systemd user 서비스)
`systemd/orchestration-dashboard.service` 머리말의 설치 명령을 따른다. 요지:
```bash
sed "s#@REPO@#$PWD#g; s#@ORCA@#$ORCA_CLI_COMMAND#g" orchestration/tools/dashboard/systemd/orchestration-dashboard.service \
  > ~/.config/systemd/user/<프로젝트>-dashboard.service
systemctl --user daemon-reload && systemctl --user enable --now <프로젝트>-dashboard.service
```

## 사설망 전용 공개 (`tailscale serve`)
대시보드는 127.0.0.1에만 바인드한다. 같은 tailnet의 기기에서만 보게 하려면 `tailscale serve`로 앞에 HTTPS 프록시를 둔다.
**`tailscale funnel`은 인터넷 공개이므로 쓰지 않는다.**

1. **기존 serve 설정 확인 (충돌 방지)** — 이미 다른 서비스가 쓰는 포트·경로를 덮어쓰지 않는다.
   ```bash
   tailscale serve status            # 현재 열린 포트·경로 목록
   tailscale serve status --json     # 기계 판독용
   ```
   - 쓰려는 HTTPS 포트(예: 8444)가 목록에 있으면 다른 포트를 고른다.
   - 기본 443(`/`)을 다른 서비스가 쓰고 있으면 그 설정을 건드리지 말고 별도 포트를 쓴다.
   - `Funnel on`으로 표시된 항목이 있으면 그 포트는 공개 상태다. 대시보드를 그 포트에 얹지 않는다.
2. **공개 (백그라운드, 별도 포트)**
   ```bash
   tailscale serve --bg --https=8444 http://127.0.0.1:8765
   tailscale serve status            # https://<기기 이름>.<tailnet>.ts.net:8444 확인
   ```
   - 구버전 CLI는 문법이 다르다(`tailscale serve https:8444 / http://127.0.0.1:8765`). `tailscale version`과 `tailscale serve --help`로 확인한다.
3. **접근 범위 확인** — tailnet ACL(grants)로 볼 수 있는 사용자·기기를 제한한다. 공유 기기(외부 공유 노드)에도 보이는지 확인한다.
4. **내리기**
   ```bash
   tailscale serve --https=8444 off
   ```
주소(기기 이름·tailnet 이름)는 저장소 문서에 적지 않는다. 사설망 주소도 내부 정보다.
