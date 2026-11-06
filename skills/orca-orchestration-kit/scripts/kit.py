#!/usr/bin/env python3
"""orca-orchestration-kit 설치·운영 도우미 (Claude Code 스킬 보조 스크립트).

명령
  init       대상 저장소에 키트 파일을 깐다(kit.yaml·tasks.yaml W0 초안 생성 포함)
  bootstrap  Orca 단계: 마스터 Run 생성 → 매니저 워커 기동 → worker-abandon → 대시보드 기동
  status     메일함·작업·워커·DAG 현황 요약
  upgrade    키트 새 버전을 기존 프로젝트에 반영 (기본은 diff만 보여 준다. --apply로 적용)

공통: --target <대상 저장소> (기본 현재 디렉터리), --dry-run (아무것도 쓰거나 실행하지 않고 계획만 출력)
예:
  kit.py init --target ~/src/todo-app --name "할 일 관리 웹앱" --slug todo \\
      --modules "api:API 서버:API,web:웹 UI:WEB" --review-preset none --run --remote "" --dry-run
  kit.py bootstrap --target ~/src/todo-app --step master-run
  kit.py upgrade --target ~/src/todo-app            # diff 보기
  kit.py upgrade --target ~/src/todo-app --apply    # 사용자 확인 뒤 적용
"""
import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("오류: PyYAML이 필요하다 (sudo apt install python3-yaml 또는 pip install pyyaml)")

KIT_ROOT = Path(__file__).resolve().parents[3]          # <키트>/skills/orca-orchestration-kit/scripts/kit.py
MANIFEST = KIT_ROOT / "kit-manifest.yaml"
STATE_DIR = ".orca-kit"
INSTALLED = f"{STATE_DIR}/installed.json"
PH = re.compile(r"\{\{([A-Z_]+)\}\}")
GI_BEGIN, GI_END = "# >>> orca-orchestration-kit >>>", "# <<< orca-orchestration-kit <<<"
DEFAULT_FOLDERS = ["01_requirements", "02_interface", "03_design", "04_test"]
FOLDER_DESC = {"01_requirements": "요구사항 추적표(RTM), TBD/TBC 관리대장, 요구 요약",
               "02_interface": "인터페이스 계약(API 스키마·메시지·ICD)", "03_design": "구조·상세 설계",
               "04_test": "시험 계획·절차·결과"}
FIXED_FOLDERS = [("05_reports", "기술 검토 보고서 (단계와 무관한 의사결정 자료)"),
                 ("06_evidence", "작업별 시험 증적 (`<작업ID>/report.md`, 워커가 작성)"),
                 ("07_process", "개발 과정 기록: 프로세스·지표, 의사결정, 운영 이슈·교훈, 대시보드 스냅샷(`snapshots/`)")]
VSTAGES_DEFAULT = [("①", "① 로컬", "개발 머신·컨테이너", "기능 구현, 단위·시나리오 시험"),
                   ("②", "② 통합 환경", "스테이징·모사 환경", "통합 동작, 자원 제한 상태"),
                   ("③", "③ 대상 환경", "실제 대상 하드웨어·플랫폼", "성능·자원 점유"),
                   ("④", "④ 운영", "실사용 환경", "최종 확인")]


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git(target, *args, check=False):
    p = subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 실패: {p.stderr.strip()}")
    return p.stdout.strip() if p.returncode == 0 else None


def kit_version():
    return str(yaml.safe_load(MANIFEST.read_text(encoding="utf-8")).get("version", "0.0.0"))


# ─────────────────────────── 답변 → 자리표시자 값 ───────────────────────────

def parse_modules(spec):
    """'api:API 서버:API,web:웹 UI' → [{key,name,short}]"""
    out = []
    for part in [p.strip() for p in (spec or "").split(",") if p.strip()]:
        f = [x.strip() for x in part.split(":")]
        key = re.sub(r"[^a-z0-9-]", "", f[0].lower())
        if not key or key in ("common", "system"):
            raise SystemExit(f"모듈 키 '{f[0]}'은 쓸 수 없다(영소문자·숫자, common·system 예약)")
        out.append({"key": key, "name": f[1] if len(f) > 1 and f[1] else key,
                    "short": f[2] if len(f) > 2 and f[2] else key.upper()})
    if not out:
        raise SystemExit("--modules가 비었다. 예: --modules 'api:API 서버:API,web:웹 UI:WEB'")
    return out


def review_preset(name):
    if not name or name == "none":
        return []
    presets = yaml.safe_load((KIT_ROOT / "templates/docs/review-stages/stages.yaml").read_text(encoding="utf-8"))["presets"]
    if name not in presets:
        raise SystemExit(f"알 수 없는 검토 단계 프리셋: {name} (있는 것: {', '.join(presets)}, none)")
    return presets[name]


def yaml_inline(d):
    return yaml.safe_dump(d, allow_unicode=True, default_flow_style=True, width=1000).strip()


def build_vars(ans, today):
    mods = ans["modules"]
    stages = review_preset(ans.get("review_preset"))
    folders = [s["folder"] for s in stages] if stages else DEFAULT_FOLDERS
    req_folder = folders[0]
    run_on = bool(ans.get("run"))
    restricted = [x.strip() for x in (ans.get("restricted") or "*.pdf,.orca/").split(",") if x.strip()]
    build_cmd = ans.get("build_cmd") or ("scripts/run/run.sh 'scripts/dev/build.sh'" if run_on else "scripts/dev/build.sh")
    test_cmd = ans.get("test_cmd") or ("scripts/run/run.sh 'scripts/dev/test.sh'" if run_on else "scripts/dev/test.sh")
    v = {
        "KIT_VERSION": kit_version(), "PROJECT_NAME": ans["name"], "PROJECT_SLUG": ans["slug"],
        "MAIN_BRANCH": ans["main_branch"], "COMMIT_EMAIL": ans.get("commit_email") or f"dev@{ans['slug']}.invalid",
        "TEAM_NAME": ans.get("team_name") or f"{ans['name']} team",
        "BUILD_CMD": build_cmd, "TEST_CMD": test_cmd,
        "TODAY": today.isoformat(), "TODAY_COMPACT": today.strftime("%Y%m%d"),
        "REQ_FOLDER": req_folder,
        "MODULES_YAML": "\n".join(f"  - {yaml_inline(m)}" for m in mods),
        "MODULE_BRANCHES": ", ".join(f"`int/{m['key']}`({m['name']})" for m in mods),
        "VSTAGES_YAML": "\n".join(f"  - {yaml_inline({'mark': m, 'name': n})}" for m, n, _, _ in VSTAGES_DEFAULT),
        "VSTAGES_TABLE": "\n".join(f"| {n} | {e} | {p} |" for _, n, e, p in VSTAGES_DEFAULT),
        "REVIEW_ENABLED": "true" if stages else "false",
        "REVIEW_STAGES_YAML": "\n".join(
            "    - " + yaml_inline({"code": s["code"], "name": s["name"], "folder": s["folder"], "artifacts": s["artifacts"]})
            for s in stages) or "    []",
        "MASTER_RUN": "", "WORK_RUN": "", "MANAGER_RUN": "", "MANAGER_DISPATCH": "",
        "RUN_ENABLED": "true" if run_on else "false", "REMOTE": ans.get("remote") or "",
        "RUN_PROFILE": ans.get("profile") or "", "RUN_IMAGE": ans.get("image") or f"{ans['slug']}-dev:latest",
        "CLEANUP_HOOK": "scripts/run/cleanup_hook.sh" if run_on else "",
        "RESTRICTED_PATTERNS": ", ".join(f"`{x}`" for x in restricted),
        "RESTRICTED_EXCLUDES": " ".join(shlex.quote(x if "/" in x or x.startswith("*") else f"/{x}") for x in restricted),
        "PROJECT_SUMMARY": ans.get("summary") or f"{ans['name']}을(를) 개발하는 저장소다. 모듈: "
        + ", ".join(f"{m['short']}({m['name']})" for m in mods) + ".",
        "DASHBOARD_URL": "http://127.0.0.1:8765/",
    }
    # 문서 폴더 표
    rows = []
    for k, f in enumerate(folders):
        if stages:
            s = stages[k]
            rows.append(f"| `{f}/` | {s['name']}({s['code']}) 산출물 — {s.get('purpose', '')} |")
        else:
            rows.append(f"| `{f}/` | {FOLDER_DESC.get(f, '')} |")
    rows += [f"| `{f}/` | {d} |" for f, d in FIXED_FOLDERS]
    v["DOC_FOLDERS_TABLE"] = "\n".join(rows)
    v["_DOC_DIRS"] = [f"docs/{f}" for f in folders[1:]]
    if stages:
        lines = ["## 2. 단계 검토 (선택 모듈 사용 중)", "",
                 "| 단계 | 목적 | 진입 조건 | 주요 산출물 | 폴더 |", "|---|---|---|---|---|"]
        for s in stages:
            arts = ", ".join(a["name"] for a in s["artifacts"])
            lines.append(f"| **{s['code']}** {s['name']} | {s.get('purpose', '')} | {s.get('entry', '')} | {arts} | `{s['folder']}/` |")
        lines += ["", "단계 약어가 요구사항 식별자 안의 기호와 같을 수 있다. 문서에서는 단계를 \"SRR 회의\", \"설계 단계\"처럼 쓰고, "
                  "요구사항은 항상 전체 식별자로만 쓴다. 단계 이름은 `orchestration/kit.yaml`의 `review_stages`에서 바꾼다."]
        v["REVIEW_SECTION"] = "\n".join(lines)
    else:
        v["REVIEW_SECTION"] = ("## 2. 진행 단계\n\n단계 검토회의를 쓰지 않는다. 진행 관리는 웨이브(W0~W3)와 작업 DAG로 한다. "
                               "단계 검토가 필요해지면 `kit.py upgrade --review-preset <이름>` 대신, kit.yaml의 `review_stages`를 켜고 "
                               "폴더를 `git mv`로 맞춘다(`docs/05_reports/`는 그대로).")
    # RTM 섹션
    sec = []
    marks = [m for m, _, _, _ in VSTAGES_DEFAULT]
    for k, m in enumerate(mods, 1):
        sec += [f"## {k}. {m['name']} ({m['short']})", "",
                "| 식별자 | 요약 | 검증 | 예비 설계 할당 | 관련 TBD | " + " | ".join(marks) + " |",
                "|---|---|---|---|---|" + "---|" * len(marks),
                f"| R-{m['short'].upper()}-001 | (요구 요약) | 시험 | {m['key']}/ | | " + " | ".join("-" for _ in marks) + " |", ""]
    v["RTM_SECTIONS"] = "\n".join(sec)
    # 실행 환경 규칙
    if run_on:
        env = ["- **빌드·시험·실행은 반드시 컨테이너에서 한다:** `scripts/run/run.sh '<명령>'` (워크트리별 격리 컨테이너, 전용 네트워크). host network 금지.",
               "  - 자원·GPU·이미지는 컨테이너 생성 때만 적용된다. 바꾸려면 `scripts/run/run.sh --stop` 후 다시 띄운다.",
               "  - 무거운 작업(다중 인스턴스, 학습, 대규모 시나리오)에는 `RUN_HEAVY=1`을 붙인다. 슬롯이 빌 때까지 기다린다.",
               "  - 끝나면 `scripts/run/run.sh --stop`으로 정리한다. 사용법: `scripts/run/README.md`."]
        if ans.get("remote"):
            env.insert(1, f"  - 컨테이너 작업은 원격 머신에서 돌린다(`RUN_REMOTE`, 기본값은 `scripts/run/run.conf`). 로컬은 워커 세션 호스트다.")
        if ans.get("profile") == "ros2":
            env.append("  - ROS 2: 격리 변수(`ROS_DOMAIN_ID`·`GZ_PARTITION`)는 프로필이 준다. 시험은 기본 순차(`scripts/run/profiles/ros2/test.sh`). 토픽 확인은 `ros2 topic list --no-daemon --spin-time 10`.")
        v["ENV_RULES"] = "\n".join(env)
        v["CONTAINER_NOTE"] = " 컨테이너에서 실행한다(`scripts/run/run.sh`, 워크트리별 격리). host network 금지."
    else:
        v["ENV_RULES"] = "- 실행 격리 모듈을 쓰지 않는다. 빌드·시험은 정해진 스크립트로만 하고, 동시 실행이 포트·파일·DB를 공유하지 않게 시험마다 고유 자원을 쓴다."
        v["CONTAINER_NOTE"] = ""
    return v


def render(text, v, keep=()):
    missing = set()

    def rep(m):
        k = m.group(1)
        if k in keep:
            return m.group(0)
        if k in v:
            return str(v[k])
        missing.add(k)
        return m.group(0)
    return PH.sub(rep, text), missing


def modules_on(ans):
    on = {"core"}
    if ans.get("run"):
        on.add("run")
        if ans.get("profile") == "ros2":
            on.add("ros2")
    if ans.get("review_preset") and ans["review_preset"] != "none":
        on.add("review")
    return on


def plan_files(ans, v):
    """[(dst, content, entry)] — 설치할 파일 내용(렌더링 결과)."""
    man = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    on = modules_on(ans)
    out, warn = [], set()
    for e in man["files"]:
        if e.get("module", "core") not in on:
            continue
        src = KIT_ROOT / e["src"]
        text = src.read_text(encoding="utf-8")
        if e.get("render"):
            text, miss = render(text, v, keep=set(e.get("keep") or []))
            warn |= {f"{e['src']}: {m}" for m in miss}
        dst, _ = render(e["dst"], v)
        dst = dst.replace("YYYYMMDD", v["TODAY_COMPACT"])
        out.append((dst, text, e))
    out.append(("orchestration/tasks.yaml", w0_tasks(ans, v), {"kind": "seed", "module": "core"}))
    return out, man, sorted(warn)


# ─────────────────────────── tasks.yaml W0 초안 ───────────────────────────

def w0_tasks(ans, v):
    tpl = (KIT_ROOT / "templates/orchestration/tasks.yaml").read_text(encoding="utf-8")
    header = tpl[:tpl.index("tasks:")]
    header = header.replace('evidence: "docs/06_evidence/{id}/report.md"', 'evidence: "docs/06_evidence/{id}/report.md"')
    if ans.get("run"):
        header = header.replace("remote: false", "remote: " + ("true" if ans.get("remote") else "false"))
    main = ans["main_branch"]
    mods = ans["modules"]
    iface = "docs/02_interface/" if not ans.get("review_preset") or ans["review_preset"] == "none" \
        else f"docs/{review_preset(ans['review_preset'])[1]['folder']}/"
    T = []

    def task(**kw):
        T.append(kw)
    task(id="F-01", title="계약 정의 (모듈 간 인터페이스·스키마·공통 규약)", layer="L0", module="common", reqs=[],
         branch="int/common", base=main, deps=[], write_scope=["contracts/", iface],
         acceptance="모듈 사이의 모든 인터페이스(요청·응답·이벤트·오류 형식)와 공통 규약(이름, 시각, 식별자, 로그 형식)을 "
                    "정의·문서화하고 계약 검사(스키마 검증·코드 생성)가 통과한다.", status="todo")
    task(id="F-02", title="공통 라이브러리 (로그·설정·헬스·오류 처리·처리시간 측정)", layer="L0", module="common", reqs=[],
         branch="int/common", base=main, deps=["F-01"], write_scope=["common/"],
         acceptance="구조화 로그, 설정 로더, 헬스 체크, 처리시간 측정, 종료 처리 공용 도우미를 제공하고 단위시험이 통과한다.",
         status="todo")
    task(id="F-03", title="모듈 골격·빌드/시험 스크립트", layer="L0", module="common", reqs=[],
         branch="int/common", base=main, deps=["F-01", "F-02"],
         write_scope=[f"{m['key']}/" for m in mods] + ["scripts/dev/"],
         acceptance="W1 작업이 쓸 골격(빈 진입점, 설정 파일, 기동 스크립트)을 미리 만들어 두 작업이 같은 파일을 새로 만들지 않게 한다. "
                    "골격은 F-02 공용 도우미를 쓴다. 전체 빌드·시험 스크립트가 통과한다.", status="todo")
    task(id="G-0", title=f"기반 머지 → int/<모듈> 생성", layer="L0", module="common", reqs=[],
         branch=main, base=main, deps=["F-01", "F-02", "F-03"], write_scope=[],
         acceptance=f"int/common → {main} 머지, {main}에서 " + "·".join(f"int/{m['key']}" for m in mods)
         + " 생성·푸시, 공통 스모크 통과", status="todo")
    for m in mods:
        task(id=f"I-{m['short'].upper()}", title=f"{m['name']} 모듈 통합", layer="L1", module=m["key"], reqs=[],
             branch=f"int/{m['key']}", base=main, deps=["G-0"],
             write_scope=[f"{m['key']}/bringup/", f"docs/06_evidence/I-{m['short'].upper()}/"],
             acceptance=f"req/R-{m['short'].upper()}-* 전부 머지, 모듈 종단 스모크 통과", status="todo")
    task(id="S-01", title="체계 통합 (종단 시나리오)", layer="L0", module="system", reqs=[], branch=main, base=main,
         deps=[f"I-{m['short'].upper()}" for m in mods], write_scope=["system/", "docs/06_evidence/S-01/"],
         acceptance=f"int/* → {main} 머지, 모듈 전체 종단 시나리오 통과", status="todo")
    body = yaml.safe_dump({"tasks": T}, allow_unicode=True, sort_keys=False, width=110)
    m0 = mods[0]
    w1 = (f"\n  # ───────────────── W1 요구사항 (L2) — 요구사항마다 하나씩 추가하고, I-<모듈>의 deps에 넣는다 ─────────────────\n"
          f"  # - id: R-{m0['short'].upper()}-001\n"
          f"  #   title: (요구 요약)\n  #   layer: L2\n  #   module: {m0['key']}\n  #   reqs: [R-{m0['short'].upper()}-001]\n"
          f"  #   branch: req/R-{m0['short'].upper()}-001\n  #   base: int/{m0['key']}\n  #   deps: [G-0]\n"
          f"  #   write_scope: [{m0['key']}/<기능>/]\n  #   acceptance: \"(측정 가능한 완료 판정)\"\n  #   perf: false\n  #   status: todo\n")
    return header + "# W0 초안 — init이 만들었다. 프로젝트에 맞게 경로·acceptance를 고치고 W1 작업을 채운다.\n" + body + w1


# ─────────────────────────── init ───────────────────────────

def load_answers(a):
    ans = {}
    if a.answers:
        ans.update(yaml.safe_load(Path(a.answers).read_text(encoding="utf-8")) or {})
    for k in ("name", "slug", "review_preset", "remote", "profile", "build_cmd", "test_cmd", "image",
              "restricted", "summary", "commit_email", "team_name", "main_branch"):
        val = getattr(a, k, None)
        if val is not None:
            ans[k] = val
    if a.modules:
        ans["modules"] = a.modules
    if a.run is not None:
        ans["run"] = a.run
    for req in ("name", "slug", "modules"):
        if not ans.get(req):
            raise SystemExit(f"필수 답변 없음: {req} (--{req} 또는 --answers)")
    if isinstance(ans["modules"], str):
        ans["modules"] = parse_modules(ans["modules"])
    ans["slug"] = re.sub(r"[^a-z0-9-]", "-", ans["slug"].lower()).strip("-")
    return ans


def cmd_init(a):
    target = Path(a.target).resolve()
    if not (target / ".git").exists() and git(target, "rev-parse", "--git-dir") is None:
        raise SystemExit(f"{target}는 git 저장소가 아니다 (git init 먼저)")
    ans = load_answers(a)
    if not ans.get("main_branch"):
        ans["main_branch"] = git(target, "symbolic-ref", "--short", "HEAD") or "main"
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    v = build_vars(ans, today)
    files, man, warn = plan_files(ans, v)
    exists = [d for d, _, _ in files if (target / d).exists()]
    if (target / INSTALLED).exists() and not a.force:
        raise SystemExit(f"이미 설치돼 있다({INSTALLED}). 새 버전 반영은 'kit.py upgrade'를 쓴다. 다시 깔려면 --force")
    print(f"키트 v{v['KIT_VERSION']} → {target}")
    print(f"  프로젝트 {ans['name']} ({ans['slug']}) · 메인 {ans['main_branch']} · 모듈 "
          + ", ".join(f"{m['key']}({m['short']})" for m in ans["modules"]))
    print(f"  모듈: {', '.join(sorted(modules_on(ans)))} · 검토 단계 {ans.get('review_preset') or 'none'} · "
          f"실행 격리 {'사용' if ans.get('run') else '안 씀'}{' · 원격 ' + ans['remote'] if ans.get('remote') else ''}"
          f"{' · 프로필 ' + ans['profile'] if ans.get('profile') else ''}")
    if exists and not a.force:
        print("  이미 있는 파일(덮어쓰지 않음, --force로 덮어씀):")
        for d in exists:
            print(f"    = {d}")
    record = {}
    for dst, text, e in files:
        p = target / dst
        skip = p.exists() and not a.force
        mark = "=" if skip else ("+" if not p.exists() else "~")
        print(f"  {mark} {dst}  [{e.get('kind')}/{e.get('module', 'core')}]")
        if not skip:
            record[dst] = sha(text)
            if not a.dry_run:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
                if e.get("mode"):
                    os.chmod(p, int(str(e["mode"]), 8))
        elif dst in record:
            pass
    for d in (man.get("dirs") or []) + v["_DOC_DIRS"]:
        print(f"  + {d}/.gitkeep")
        if not a.dry_run:
            (target / d).mkdir(parents=True, exist_ok=True)
            (target / d / ".gitkeep").touch()
    gi = target / ".gitignore"
    block = "\n".join([GI_BEGIN, *man.get("gitignore", []), GI_END]) + "\n"
    cur = gi.read_text(encoding="utf-8") if gi.exists() else ""
    if GI_BEGIN not in cur:
        print("  ~ .gitignore (키트 블록 추가)")
        if not a.dry_run:
            gi.write_text(cur + ("\n" if cur and not cur.endswith("\n") else "") + block, encoding="utf-8")
    inst = {"kit_version": v["KIT_VERSION"], "installed": today.isoformat(), "kit_root_hint": "skills/orca-orchestration-kit",
            "answers": {k: ans[k] for k in ans}, "files": record}
    print(f"  + {INSTALLED}")
    if not a.dry_run:
        (target / STATE_DIR).mkdir(exist_ok=True)
        (target / INSTALLED).write_text(json.dumps(inst, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for w in warn:
        print(f"  경고: 채우지 못한 자리표시자 {w}")
    print("\n다음 단계")
    print(f"  1) tasks.yaml W0 초안 검토: python3 {target}/orchestration/tools/validate_dag.py --repo {target}")
    print(f"  2) 커밋: git -C {target} add -A && git -C {target} commit -m '[L0] orchestration kit v{v['KIT_VERSION']} init'")
    print(f"  3) Orca: kit.py bootstrap --target {target} --step master-run   (--dry-run으로 명령만 보기)")
    if a.dry_run:
        print("\n(dry-run: 아무 파일도 쓰지 않았다)")
    return 0


# ─────────────────────────── bootstrap ───────────────────────────

def set_kit_yaml(target, key, value, dry):
    """kit.yaml의 orca.<key> 한 줄만 바꾼다(주석 보존)."""
    p = target / "orchestration/kit.yaml"
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(rf'^(\s*{re.escape(key)}:\s*)"[^"]*"', rf'\g<1>"{value}"', text, count=1, flags=re.M)
    if not n:
        raise SystemExit(f"kit.yaml에서 {key}를 찾지 못했다")
    print(f"  kit.yaml orca.{key} = {value}")
    if not dry:
        p.write_text(new, encoding="utf-8")


def orca_cmd():
    return os.environ.get("ORCA_CLI_COMMAND") or "orca"


def run_orca(args, dry):
    cmd = [orca_cmd(), *args]
    print("  $ " + " ".join(shlex.quote(c) for c in cmd))
    if dry:
        return ""
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"실패({p.returncode}): {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout


def cmd_bootstrap(a):
    target = Path(a.target).resolve()
    sys.path.insert(0, str(target / "orchestration/tools"))
    cfg = yaml.safe_load((target / "orchestration/kit.yaml").read_text(encoding="utf-8"))
    orca = cfg.get("orca") or {}
    name = cfg["project"]["name"]
    steps = ["master-run", "manager", "abandon", "dashboard"] if a.step == "all" else [a.step]
    dry = a.dry_run
    for st in steps:
        print(f"\n[{st}]")
        if st == "master-run":
            if orca.get("master_run") and not a.force:
                print(f"  이미 있음: {orca['master_run']} (--force로 새로)")
                continue
            out = run_orca(["orchestration", "run-create", "--objective", f"{name} 마스터 Run (사용자 소통·결정)", "--json"], dry)
            rid = (re.findall(r"run_[0-9a-f]{6,}", out) or ["<master_run_id>"])[0]
            set_kit_yaml(target, "master_run", rid, dry)
            orca["master_run"] = rid
        elif st == "manager":
            mr = orca.get("master_run") or "<master_run_id>"
            spec = (target / "orchestration/sessions/MANAGER.md").read_text(encoding="utf-8")
            spec = spec.replace("{{MASTER_RUN}}", mr).replace("{{WORK_RUN}}", orca.get("work_run") or "")
            gdir = Path(git(target, "rev-parse", "--absolute-git-dir") or target / ".git")
            launch = gdir / "orca-kit" / "MANAGER.launch.md"     # git 밖(커밋되지 않음)
            print(f"  명세: {launch} (MANAGER.md + Run ID)")
            if not dry:
                launch.parent.mkdir(parents=True, exist_ok=True)
                launch.write_text(spec, encoding="utf-8")
            out = run_orca(["orchestration", "worker-start", "--spec-file", str(launch), "--task-title", "작업관리 매니저",
                            "--display-name", "매니저", "--agent", orca.get("agent") or "claude",
                            "--worktree", f"path:{target}", "--json"], dry)
            did = (re.findall(r"ctx_[0-9a-f]{6,}", out) or ["<manager_dispatch>"])[0]
            set_kit_yaml(target, "manager_dispatch", did, dry)
            orca["manager_dispatch"] = did
            print("  → 턴을 끝내고 매니저의 '매니저 기동' 보고(run 메일함)를 기다린 뒤 --step abandon을 실행한다.")
        elif st == "abandon":
            did = a.dispatch or orca.get("manager_dispatch") or "<manager_dispatch>"
            run_orca(["orchestration", "worker-abandon", "--dispatch", did, "--json"], dry)
            if a.work_run:
                set_kit_yaml(target, "work_run", a.work_run, dry)
            print("  → 매니저는 이제 상시 세션이다. 보고·질문은 run:<마스터 Run>, 지시는 run:<작업 Run>으로 주고받는다.")
        elif st == "dashboard":
            port = str((cfg.get("dashboard") or {}).get("port") or 8765)
            gdir = Path(git(target, "rev-parse", "--absolute-git-dir") or target / ".git")
            log = gdir / "orca-kit" / "dashboard.log"
            cmd = f"setsid nohup {target}/orchestration/tools/dashboard/serve.sh --orca -p {port} > {log} 2>&1 &"
            print(f"  $ {cmd}")
            print(f"  상시 기동은 systemd user 서비스: orchestration/tools/dashboard/systemd/ (README)")
            if not dry:
                log.parent.mkdir(parents=True, exist_ok=True)
                subprocess.Popen(["bash", "-c", cmd], cwd=target, env={**os.environ, "ORCA_CLI_COMMAND": orca_cmd()})
            print(f"  대시보드: http://127.0.0.1:{port}/")
    if dry:
        print("\n(dry-run: Orca 명령을 실행하지 않았고 kit.yaml도 바꾸지 않았다)")
    return 0


# ─────────────────────────── status ───────────────────────────

def try_json(args):
    p = subprocess.run([orca_cmd(), *args, "--json"], capture_output=True, text=True)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": {"message": (p.stderr or p.stdout).strip()[:200]}}


def cmd_status(a):
    target = Path(a.target).resolve()
    cfg = yaml.safe_load((target / "orchestration/kit.yaml").read_text(encoding="utf-8"))
    orca = cfg.get("orca") or {}
    print(f"# {cfg['project']['name']} 현황 ({dt.datetime.now():%Y-%m-%d %H:%M})")
    v = subprocess.run([sys.executable, str(target / "orchestration/tools/validate_dag.py"), "--repo", str(target)],
                       capture_output=True, text=True)
    print("\n## 작업 DAG\n" + v.stdout.rstrip())
    if a.no_orca:
        return 0
    if orca.get("master_run"):
        mb = try_json(["orchestration", "inbox", "--terminal", f"run:{orca['master_run']}", "--limit", str(a.limit)])
        msgs = (mb.get("result") or {}).get("messages") or []
        print(f"\n## 마스터 메일함 (최근 {len(msgs)})")
        for m in msgs[:a.limit]:
            print(f"- [{m.get('type', '?')}] {m.get('subject', '')} — {str(m.get('body', ''))[:120]}")
        if not mb.get("ok", True):
            print(f"  (조회 실패: {(mb.get('error') or {}).get('message')})")
    if orca.get("work_run"):
        tl = try_json(["orchestration", "task-list", "--run", orca["work_run"]])
        ts = (tl.get("result") or {}).get("tasks") or []
        cnt = {}
        for t in ts:
            cnt[t.get("status")] = cnt.get(t.get("status"), 0) + 1
        print(f"\n## Orca 작업 (Run {orca['work_run']}): " + (", ".join(f"{k} {n}" for k, n in cnt.items()) or "없음"))
        wl = try_json(["orchestration", "worker-list", "--run", orca["work_run"]])
        ws = (wl.get("result") or {}).get("workers") or []
        live = [w for w in ws if w.get("state") not in ("succeeded", "failed")]
        print(f"## 워커: 전체 {len(ws)} · 진행 {len(live)}")
        for w in live[:20]:
            print(f"- {w.get('displayName') or w.get('dispatchId')} {w.get('state')}")
    else:
        print("\n(orca.work_run 미설정: 매니저 기동 보고 후 bootstrap --step abandon --work-run <id>)")
    return 0


# ─────────────────────────── upgrade ───────────────────────────

def cmd_upgrade(a):
    target = Path(a.target).resolve()
    ip = target / INSTALLED
    if not ip.exists():
        raise SystemExit(f"{INSTALLED}가 없다. 먼저 init")
    inst = json.loads(ip.read_text(encoding="utf-8"))
    ans = inst["answers"]
    cfg = yaml.safe_load((target / "orchestration/kit.yaml").read_text(encoding="utf-8"))
    # kit.yaml이 정본: 사용자가 바꾼 값(이름·모듈·메인 브랜치)을 반영한다
    ans["name"] = cfg["project"].get("name", ans["name"])
    ans["main_branch"] = cfg["project"].get("main_branch", ans.get("main_branch"))
    ans["modules"] = [m for m in cfg.get("modules") or ans["modules"] if m.get("key") not in ("common", "system")]
    v = build_vars(ans, dt.date.fromisoformat(inst["installed"]))
    files, man, _ = plan_files(ans, v)
    base = inst.get("files") or {}
    print(f"키트 v{inst['kit_version']} → v{v['KIT_VERSION']} ({target})")
    changes = []
    for dst, new, e in files:
        p = target / dst
        cur = p.read_text(encoding="utf-8") if p.exists() else None
        kind = e.get("kind")
        if cur is not None and sha(cur) == sha(new):
            continue
        if cur is None:
            state = "삭제됨(사용자)" if dst in base else "새 파일"
        elif dst not in base:
            state = "키트 밖 파일과 겹침"
        elif sha(cur) == base[dst]:
            state = "키트 갱신"
        else:
            state = "충돌(사용자 수정 + 키트 변경)" if sha(new) != base[dst] else "사용자 수정(키트 변경 없음)"
        if state == "사용자 수정(키트 변경 없음)":
            continue
        if kind == "seed" and state != "새 파일" and not a.include_seed:
            changes.append((dst, state + " · seed(프로젝트 소유) — --include-seed로 보기", None, None, e))
            continue
        changes.append((dst, state, cur, new, e))
    if not changes:
        print("변경 없음")
        return 0
    for dst, state, cur, new, e in changes:
        print(f"\n=== {dst}: {state}")
        if new is not None and not a.summary:
            diff = difflib.unified_diff((cur or "").splitlines(True), new.splitlines(True),
                                        fromfile=f"현재/{dst}", tofile=f"키트 v{v['KIT_VERSION']}/{dst}")
            sys.stdout.writelines(diff)
    if not a.apply:
        print("\n(미리보기: 적용하려면 사용자 확인 뒤 --apply. 충돌 파일은 <파일>.kit-new로 쓴다. 덮어쓰려면 --overwrite-conflicts)")
        return 0
    for dst, state, cur, new, e in changes:
        if new is None or state == "삭제됨(사용자)":
            continue
        p = target / dst
        if state.startswith("충돌") and not a.overwrite_conflicts:
            p.with_name(p.name + ".kit-new").write_text(new, encoding="utf-8")
            print(f"  ! {dst}.kit-new (직접 합친 뒤 지운다)")
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new, encoding="utf-8")
        if e.get("mode"):
            os.chmod(p, int(str(e["mode"]), 8))
        base[dst] = sha(new)
        print(f"  ~ {dst}")
    inst["files"], inst["kit_version"] = base, v["KIT_VERSION"]
    ip.write_text(json.dumps(inst, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"적용 완료. 커밋: git -C {target} commit -am '[L0] orchestration kit v{v['KIT_VERSION']} upgrade'")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="orca-orchestration-kit 설치·운영 도우미")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("init", help="키트 파일 설치")
    pi.add_argument("--target", default=".")
    pi.add_argument("--answers", help="답변 YAML (name, slug, modules, review_preset, run, remote, profile, ...)")
    pi.add_argument("--name"), pi.add_argument("--slug")
    pi.add_argument("--modules", help="'키:이름:약칭,…' 예: 'api:API 서버:API,web:웹 UI:WEB'")
    pi.add_argument("--review-preset", dest="review_preset", help="none | defense | product")
    g = pi.add_mutually_exclusive_group()
    g.add_argument("--run", dest="run", action="store_true", default=None, help="실행 격리 모듈 설치")
    g.add_argument("--no-run", dest="run", action="store_false")
    pi.add_argument("--remote", help="원격 실행 머신 user@host (없으면 빈 값)")
    pi.add_argument("--profile", help="실행 격리 프로필 (예: ros2)")
    pi.add_argument("--build-cmd", dest="build_cmd"), pi.add_argument("--test-cmd", dest="test_cmd")
    pi.add_argument("--image"), pi.add_argument("--restricted", help="배포 제한 파일 패턴 (쉼표). 기본 '*.pdf,.orca/'")
    pi.add_argument("--summary"), pi.add_argument("--commit-email", dest="commit_email")
    pi.add_argument("--team-name", dest="team_name"), pi.add_argument("--main-branch", dest="main_branch")
    pi.add_argument("--date", help="설치일 YYYY-MM-DD (기본 오늘)")
    pi.add_argument("--force", action="store_true"), pi.add_argument("--dry-run", action="store_true")
    pb = sub.add_parser("bootstrap", help="Orca 단계 실행")
    pb.add_argument("--target", default=".")
    pb.add_argument("--step", default="all", choices=["all", "master-run", "manager", "abandon", "dashboard"])
    pb.add_argument("--dispatch", help="abandon 대상 dispatch (기본 kit.yaml orca.manager_dispatch)")
    pb.add_argument("--work-run", dest="work_run", help="매니저가 보고한 작업 Run ID (abandon 때 kit.yaml에 기록)")
    pb.add_argument("--force", action="store_true"), pb.add_argument("--dry-run", action="store_true")
    ps = sub.add_parser("status", help="현황 요약")
    ps.add_argument("--target", default="."), ps.add_argument("--limit", type=int, default=10)
    ps.add_argument("--no-orca", action="store_true")
    pu = sub.add_parser("upgrade", help="키트 새 버전 반영 (diff → 확인 → --apply)")
    pu.add_argument("--target", default=".")
    pu.add_argument("--apply", action="store_true"), pu.add_argument("--include-seed", action="store_true")
    pu.add_argument("--overwrite-conflicts", action="store_true"), pu.add_argument("--summary", action="store_true",
                                                                                   help="diff 없이 목록만")
    a = ap.parse_args(argv)
    return {"init": cmd_init, "bootstrap": cmd_bootstrap, "status": cmd_status, "upgrade": cmd_upgrade}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
