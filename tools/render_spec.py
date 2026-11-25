#!/usr/bin/env python3
"""tasks.yaml 항목을 명세 템플릿으로 렌더링해 specs/<ID>.md로 저장한다.

템플릿: orchestration/templates/<작업의 spec_template 또는 kit.yaml spec_templates[layer] 또는 default>
  - 작업마다 spec_template: review-spec.md 처럼 주면 그것이 우선한다(검토 작업 등).
  - 자리표시자: {id} {title} {layer} {module} {module_name} {reqs} {branch} {base} {write_scope}
               {acceptance} {perf_line} {deps_block} {env_block} {evidence} {evidence_dir}
               {stage} {rtm} {tbd} {main_branch} {project} {target}(검토 대상, 없으면 '-')
               {push}(작업 브랜치 올리기 명령) {sync_base}(기준 브랜치 따라가기 명령) {ref}(원격 브랜치 접두, 로컬 전용이면 빈 값)
               그 밖에 작업 항목의 문자열 필드도 {필드명}으로 쓸 수 있다.
  - 알려진 이름만 바꾸고 나머지 중괄호는 그대로 둔다(코드 예시가 깨지지 않는다).
프로젝트 덧붙임: templates/<템플릿 이름>.local.md(예: worker-spec.local.md)가 있으면 렌더링해 명세의 '## 종료' 앞에 넣는다.
  키트 템플릿(managed)을 고치지 않고 프로젝트 규칙을 더하는 곳이다. upgrade가 충돌하지 않는다.
실행 환경 블록: orchestration/templates/blocks/
  - _always.md는 항상 넣는다.
  - 작업의 env: [이름, ...] 마다 <이름>.md를 넣는다. tasks.yaml runtime.<이름>이 false이고
    <이름>.pending.md가 있으면 그것을 넣는다('모의로 먼저, 실제로 다시').
인계 사항: specs/<ID>.extra.md가 있으면 명세 끝에 붙인다(--no-extra로 끔).

사용:
  python3 orchestration/tools/render_spec.py <ID> [ID ...]
  python3 orchestration/tools/render_spec.py --ready         # 투입 가능한 작업 전부(게이트 제외)
  python3 orchestration/tools/render_spec.py --list-ready    # 렌더링 없이 ID만 출력
  python3 orchestration/tools/render_spec.py <ID> --review [--suffix r2]        # <ID>를 검토하는 명세 → specs/<ID>.review[-r2].md
  python3 orchestration/tools/render_spec.py <ID> --retry <지적.md> --suffix r1  # req/<ID>에서 남은 지적 수정 → specs/<ID>.retry-r1.md
옵션:
  --stdout  파일 대신 표준출력      --out-dir DIR  저장 폴더 (기본: kit.yaml paths.specs)
  --repo / --config / --tasks / --templates  경로 지정
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402

LAYER_NAME = {"L0": "L0 공통 기반", "L1": "L1 모듈 통합", "L2": "L2 요구사항"}
PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})")


def fill(text, vals):
    return PLACEHOLDER.sub(lambda m: str(vals[m.group(1)]) if m.group(1) in vals else m.group(0), text)


def ready_ids(tasks, cfg=None):
    """투입 가능한 작업 ID(보관 작업 제외, 게이트 제외). L1은 L2 선행을 기다리지 않는다."""
    cfg = cfg or {}
    return [str(t["id"]) for t in tasks.values() if not t.get("_archived") and kitlib.is_ready(t, tasks, cfg)]


def git_cmds(cfg, branch, base):
    rem = kitlib.git_remote(cfg)
    if rem:
        return {"ref": f"{rem}/",
                "push": f"git push {rem} HEAD:{branch}",
                "sync_base": f'git fetch {rem} && git merge {rem}/{base} -m "[G] Sync {base}"'}
    return {"ref": "",
            "push": f"git push . HEAD:{branch}",
            "sync_base": f'git merge {base} -m "[G] Sync {base}"'}


def env_block(t, runtime, blocks_dir, vals, warn):
    parts = []
    always = blocks_dir / "_always.md"
    if always.is_file():
        parts.append(always.read_text(encoding="utf-8").rstrip())
    for name in kitlib.as_list(t.get("env")):
        f = blocks_dir / f"{name}.md"
        pend = blocks_dir / f"{name}.pending.md"
        if runtime.get(name) is False and pend.is_file():
            f = pend
        if not f.is_file():
            warn(f"{t['id']}: env 블록 {name}.md 없음 ({blocks_dir})")
            continue
        parts.append(f.read_text(encoding="utf-8").rstrip())
    return fill("\n".join(parts), vals) if parts else "- 특별한 실행 환경 요구 없음"


def insert_before_end(text, block):
    """'## 종료' 절 앞에 넣는다. 없으면 끝에 붙인다."""
    m = re.search(r"^## 종료", text, flags=re.M)
    if not m:
        return text.rstrip() + "\n\n" + block.rstrip() + "\n"
    return text[:m.start()] + block.rstrip() + "\n\n" + text[m.start():]


def render(t, tasks, cfg, runtime, tpl_dir, warn, template=None):
    deps = kitlib.as_list(t.get("deps"))
    if deps:
        deps_block = "\n".join(
            f"- `{d}` {tasks.get(d, {}).get('title', '(없는 작업)')} (status: {tasks.get(d, {}).get('status', '?')})"
            for d in deps)
        deps_block += "\n- merged인 선행 작업은 기준 브랜치에 들어 있다. 선행 작업의 경로는 읽기만 한다."
    else:
        deps_block = "- 없음"
    mod = kitlib.task_module(t)
    mod_names = {str(m.get("key")): str(m.get("name", m.get("key"))) for m in (cfg.get("modules") or []) if isinstance(m, dict)}
    mod_names.setdefault("common", "공통")
    mod_names.setdefault("system", "체계")
    ev_pat = str(kitlib.cfg_get(cfg, "paths.evidence", "docs/06_evidence/{id}/report.md"))
    evidence = ev_pat.replace("{id}", str(t["id"]))
    stext = cfg.get("spec_text") or {}
    vals = {
        "id": t["id"], "title": t.get("title", ""),
        "layer": LAYER_NAME.get(str(t.get("layer")), t.get("layer", "")),
        "module": mod, "product": mod, "module_name": mod_names.get(mod, mod),
        "reqs": ", ".join(kitlib.as_list(t.get("reqs"))) or "-",
        "branch": t.get("branch", ""), "base": t.get("base", ""),
        "write_scope": "\n".join(f"- `{p}`" for p in kitlib.as_list(t.get("write_scope"))) or "- (없음)",
        "acceptance": " ".join(str(t.get("acceptance", "")).split()),
        "perf_line": stext.get("perf_line", "처리 지연 p50/p95를 계기별로 측정해 report.md 3절에 남긴다")
        if t.get("perf") else stext.get("no_perf_line", "처리 지연 측정은 필수가 아니다(해당 시 기록)"),
        "deps_block": deps_block,
        "evidence": evidence, "evidence_dir": str(Path(evidence).parent) + "/",
        "stage": t.get("stage", "①"),
        "rtm": kitlib.cfg_get(cfg, "paths.rtm", "docs/01_requirements/01_요구사항추적표_*.md"),
        "tbd": kitlib.cfg_get(cfg, "paths.tbd", "docs/01_requirements/02_TBD관리대장_*.md"),
        "main_branch": kitlib.cfg_get(cfg, "project.main_branch", "main"),
        "project": kitlib.cfg_get(cfg, "project.name", ""),
        "target": t.get("target", "-"),
    }
    vals.update(git_cmds(cfg, vals["branch"], vals["base"]))
    for k, v in t.items():   # 작업 항목의 다른 문자열 필드
        if isinstance(v, (str, int, float)) and k not in vals and not k.startswith("_"):
            vals[k] = v
    vals["env_block"] = env_block(t, runtime, tpl_dir / "blocks", vals, warn)
    vals["sim_block"] = vals["env_block"]   # 예전 템플릿 호환
    sel = cfg.get("spec_templates") or {}
    name = template or t.get("spec_template") or sel.get(str(t.get("layer")), sel.get("default", "worker-spec.md"))
    tpl = tpl_dir / name
    if not tpl.is_file():
        warn(f"{t['id']}: 명세 템플릿 {name} 없음 → worker-spec.md로 대신 렌더링 ({tpl_dir})")
        tpl = tpl_dir / "worker-spec.md"
    text = fill(tpl.read_text(encoding="utf-8"), vals)
    local = tpl.with_name(tpl.stem + ".local.md")
    if local.is_file():
        text = insert_before_end(text, fill(local.read_text(encoding="utf-8"), vals))
    return text


def review_task(t, cfg, suffix=""):
    """작업 t의 구현(작업 브랜치)을 검토하는 가상 작업. 보고서 브랜치는 메인 기준, 쓰기 범위는 검토 증적 폴더뿐."""
    rid = f"{t['id']}-RV" + (f"-{suffix}" if suffix else "")
    main = kitlib.cfg_get(cfg, "project.main_branch", "main")
    ev_pat = str(kitlib.cfg_get(cfg, "paths.evidence", "docs/06_evidence/{id}/report.md"))
    ev_dir = str(Path(ev_pat.replace("{id}", rid)).parent) + "/"
    rem = kitlib.git_remote(cfg)
    target = f"{rem}/{t.get('branch')}" if rem else str(t.get("branch"))
    acc = " ".join(str(t.get("acceptance", "")).split())
    return {
        "id": rid, "title": f"{t['id']} 구현 검토 — {t.get('title', '')}", "layer": "L0",
        "module": kitlib.task_module(t), "reqs": t.get("reqs") or [], "branch": f"req/{rid}", "base": main,
        "deps": [], "write_scope": [ev_dir], "target": target, "status": "todo",
        "acceptance": f"검토 대상 작업 {t['id']}의 완료 판정을 실행으로 다시 확인한다: {acc} "
                      f"명세(orchestration/specs/{t['id']}.md)·쓰기 범위·시험 누락을 본다. 머지 차단 H는 실행 재현 반례만이다.",
        "env": t.get("env") or [],
    }


def retry_section(t, notes_text, cfg):
    g = git_cmds(cfg, t.get("branch", ""), t.get("branch", ""))
    sync = git_cmds(cfg, t.get("branch", ""), t.get("base", ""))["sync_base"]
    return (f"## 재투입 (남은 지적 수정)\n"
            f"이 작업은 이미 구현돼 있다. 이 워크트리는 `{t.get('branch')}`에서 분기했다. **처음부터 다시 만들지 말고** 아래 지적만 모두 해소하라.\n"
            f"- 시작: `{sync}`로 기준을 따라간다.\n"
            f"- 끝: 빌드·전체 시험 통과 → `[{t['id']}] 남은 지적 수정` 커밋 → `{g['push']}` → `worker_done`(해소한 지적 / 못 한 지적과 이유).\n\n"
            f"### 지적\n{notes_text.rstrip()}\n")


def promise_section(t, review=False):
    """보장하는 것 / 범위 밖(tasks.yaml guarantees·out_of_scope). 검토는 이 목록 안에서만 차단한다."""
    g, o = kitlib.as_list(t.get("guarantees")), kitlib.as_list(t.get("out_of_scope"))
    if not g and not o:
        return ""
    head = ("## 보장하는 것 / 범위 밖 (닫힌 목록)\n"
            + ("머지를 막는 H는 **아래 '보장하는 것' 항목을 깨는 실행 반례**만이다. '범위 밖'이나 목록에 없는 약속의 반례는 "
               "'범위 밖'으로 적고 차단 사유로 삼지 않는다(정보로 남긴다).\n" if review else
               "이 작업이 지키는 약속은 아래 '보장하는 것'이 전부다. 항목마다 그것을 확인하는 시험을 두고, `worker_done`에 '항목 → 시험 이름'을 적는다. "
               "'범위 밖'은 하지 않는다(필요해 보이면 `ask`).\n"))
    return (head + ("\n### 보장하는 것\n" + "\n".join(f"- {x}" for x in g) + "\n" if g else "")
            + ("\n### 범위 밖\n" + "\n".join(f"- {x}" for x in o) + "\n" if o else ""))


def checklist_items(repo, cfg):
    """프로젝트 핵심 약속 점검표(orchestration/checklist.md)의 '## 항목' 절 줄(- 로 시작, '- [예]' 제외)."""
    p = repo / kitlib.cfg_get(cfg, "paths.checklist", "orchestration/checklist.md")
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out, on = [], False
    for ln in lines:
        t = ln.strip()
        if t.startswith("#"):
            on = t.lstrip("#").strip() == "항목"
        elif on and t.startswith("- ") and not t.startswith("- [예]"):
            out.append(t[2:].strip())
    return out


def checklist_section(items, review=False):
    if not items:
        return ""
    head = ("## 핵심 약속 점검표 (먼저 본다)\n워커의 '항목 → 시험 이름' 목록부터 확인한다. 시험이 없거나 시험이 항목을 실제로 확인하지 않으면 그것이 첫 지적이다.\n"
            if review else
            "## 핵심 약속 점검표 (완료 보고 전에 스스로 확인)\n지난 검토들이 되풀이해 잡은 항목이다. 이 작업에 해당하는 항목마다 확인하는 시험을 두고, "
            "`worker_done`과 증적 보고서에 '항목 → 시험 이름(또는 해당 없음과 이유)'을 적는다.\n")
    return head + "\n".join(f"- {x}" for x in items) + "\n"


def handoff_section(t, notes_text, cfg, review=False):
    """한도 전환 이어받기: 앞 워커가 구독 한도로 멈춘 같은 워크트리에서 이어서 끝낸다(quota_switch.py가 부른다)."""
    b = t.get("branch", "")
    end = ("검토 명세의 끝 절차대로 보고서를 쓰고 `worker_done`"
           if review else f"원 명세의 끝 절차(빌드·전체 시험 → 커밋 → `{git_cmds(cfg, b, b)['push']}` → `worker_done`)")
    return (f"## 이어받기 (앞 워커가 구독 사용량 한도로 멈췄다)\n"
            f"이 워크트리에는 앞 워커의 진행분이 그대로 있다(커밋 + 커밋하지 않은 변경). **처음부터 다시 하지 말고** 이어서 끝내라.\n"
            f"- 시작: `git status`와 `git log --oneline -15`, 커밋하지 않은 변경의 `git diff`로 어디까지 했는지 본다. "
            f"아래 보존 브랜치는 멈춘 순간의 전체 스냅샷이다(작업 트리가 이상하면 그것과 비교한다. 그 브랜치에는 커밋하지 않는다).\n"
            f"- 앞 워커가 남긴 완료 조건 확인·시험 결과를 믿지 말고 다시 돌려 확인한다.\n"
            f"- 끝: {end}. 보고 첫머리에 `이어받기: 앞 워커 진행분 중 쓴 것 / 고친 것`을 한 줄로 적는다.\n\n"
            f"### 앞 워커\n{notes_text.rstrip()}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="tasks.yaml → 작업 명세 렌더링")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--ready", action="store_true")
    ap.add_argument("--list-ready", action="store_true")
    ap.add_argument("--review", action="store_true", help="작업의 구현을 검토하는 명세를 만든다")
    ap.add_argument("--retry", metavar="NOTES", help="남은 지적 파일: req/<ID>에서 수정하는 재투입 명세를 만든다")
    ap.add_argument("--handoff", metavar="NOTES", help="한도 전환 이어받기 메모: 멈춘 워커의 워크트리에서 이어서 끝내는 명세(--review와 같이 쓸 수 있다)")
    ap.add_argument("--suffix", default="", help="검토·재투입·이어받기 차수 이름 (예: r1, h1)")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--no-extra", action="store_true", help="specs/<ID>.extra.md를 붙이지 않는다")
    ap.add_argument("--repo"), ap.add_argument("--config"), ap.add_argument("--tasks")
    ap.add_argument("--templates", help="템플릿 폴더 (기본: kit.yaml paths.templates)")
    ap.add_argument("--out-dir", help="저장 폴더 (기본: kit.yaml paths.specs)")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    data, tasks = kitlib.all_tasks(repo, cfg, a.tasks)
    active = {str(t["id"]) for t in data.get("tasks") or [] if isinstance(t, dict) and t.get("id") is not None}
    for k, t in tasks.items():
        if k not in active:
            t["_archived"] = True
    runtime = data.get("runtime") or {}
    tpl_dir = Path(a.templates) if a.templates else repo / kitlib.cfg_get(cfg, "paths.templates", "orchestration/templates")
    out_dir = Path(a.out_dir) if a.out_dir else repo / kitlib.cfg_get(cfg, "paths.specs", "orchestration/specs")
    warn = lambda m: print(f"경고: {m}", file=sys.stderr)  # noqa: E731

    if a.list_ready:
        print("\n".join(ready_ids(tasks, cfg)))
        return 0
    if a.review and a.retry:
        print("--review와 --retry는 함께 쓸 수 없다", file=sys.stderr)
        return 2
    if a.retry and not a.suffix:
        print("--retry에는 --suffix(차수 이름, 예: r1)가 필요하다", file=sys.stderr)
        return 2
    if a.handoff and (a.retry or not a.suffix):
        print("--handoff에는 --suffix(예: h1)가 필요하고 --retry와 함께 쓰지 않는다(재투입의 지적은 이어받기 메모에 넣는다)", file=sys.stderr)
        return 2
    ids = ready_ids(tasks, cfg) if a.ready else a.ids
    if not ids:
        print("렌더링할 작업이 없다", file=sys.stderr)
        return 1
    for i in ids:
        if i not in tasks:
            print(f"알 수 없는 작업 ID: {i}", file=sys.stderr)
            return 1
        t = tasks[i]
        if a.review:
            rt = review_task(t, cfg, a.suffix)
            text = render(rt, tasks, cfg, runtime, tpl_dir, warn, template="review-spec.md")
            stem = f"{i}.review" + (f"-{a.suffix}" if a.suffix else "")
            if a.handoff:
                text = insert_before_end(text, handoff_section(t, Path(a.handoff).read_text(encoding="utf-8"), cfg, review=True))
        else:
            text = render(t, tasks, cfg, runtime, tpl_dir, warn)
            stem = i
            if a.retry:
                text = insert_before_end(text, retry_section(t, Path(a.retry).read_text(encoding="utf-8"), cfg))
                stem = f"{i}.retry-{a.suffix}"
            elif a.handoff:
                text = insert_before_end(text, handoff_section(t, Path(a.handoff).read_text(encoding="utf-8"), cfg))
                stem = f"{i}.handoff-{a.suffix}"
        for block in (promise_section(t, review=a.review), checklist_section(checklist_items(repo, cfg), review=a.review)):
            if block:
                text = insert_before_end(text, block)
        # 인계 사항: 원 작업의 <ID>.extra.md(검토 명세 제외) + 이 명세 전용 <stem>.extra.md
        extras = ([] if a.review else [f"{i}.extra.md"]) + ([f"{stem}.extra.md"] if stem != i else [])
        for name in extras:
            extra = out_dir / name
            if not a.no_extra and extra.is_file():
                text = text.rstrip() + "\n\n" + extra.read_text(encoding="utf-8")
        if a.stdout:
            print(text)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{stem}.md"
            out.write_text(text, encoding="utf-8")
            try:
                print(out.relative_to(repo))
            except ValueError:
                print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
