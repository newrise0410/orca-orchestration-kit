#!/usr/bin/env python3
"""tasks.yaml 항목을 명세 템플릿으로 렌더링해 specs/<ID>.md로 저장한다.

템플릿: orchestration/templates/<작업의 spec_template 또는 kit.yaml spec_templates[layer] 또는 default>
  - 작업마다 spec_template: review-spec.md 처럼 주면 그것이 우선한다(검토 작업 등).
  - 자리표시자: {id} {title} {layer} {module} {module_name} {reqs} {branch} {base} {write_scope}
               {acceptance} {perf_line} {deps_block} {env_block} {evidence} {evidence_dir}
               {stage} {rtm} {tbd} {main_branch} {project} {target}(검토 대상, 없으면 '-')
               그 밖에 작업 항목의 문자열 필드도 {필드명}으로 쓸 수 있다.
  - 알려진 이름만 바꾸고 나머지 중괄호는 그대로 둔다(코드 예시가 깨지지 않는다).
실행 환경 블록: orchestration/templates/blocks/
  - _always.md는 항상 넣는다.
  - 작업의 env: [이름, ...] 마다 <이름>.md를 넣는다. tasks.yaml runtime.<이름>이 false이고
    <이름>.pending.md가 있으면 그것을 넣는다('모의로 먼저, 실제로 다시').
인계 사항: specs/<ID>.extra.md가 있으면 명세 끝에 붙인다(--no-extra로 끔).

사용:
  python3 orchestration/tools/render_spec.py <ID> [ID ...]
  python3 orchestration/tools/render_spec.py --ready         # deps가 모두 merged인 todo/ready L2 작업
  python3 orchestration/tools/render_spec.py --list-ready    # 렌더링 없이 ID만 출력
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


def ready_ids(tasks):
    out = []
    for t in tasks.values():
        if t.get("layer") != "L2" or str(t.get("status", "todo")) not in ("todo", "ready"):
            continue
        if all(tasks.get(d, {}).get("status") == "merged" for d in kitlib.as_list(t.get("deps"))):
            out.append(str(t["id"]))
    return out


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


def render(t, tasks, cfg, runtime, tpl_dir, warn):
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
    for k, v in t.items():   # 작업 항목의 다른 문자열 필드
        if isinstance(v, (str, int, float)) and k not in vals:
            vals[k] = v
    vals["env_block"] = env_block(t, runtime, tpl_dir / "blocks", vals, warn)
    # 예전 템플릿 호환: {sim_block}
    vals["sim_block"] = vals["env_block"]
    sel = cfg.get("spec_templates") or {}
    name = t.get("spec_template") or sel.get(str(t.get("layer")), sel.get("default", "worker-spec.md"))
    tpl = tpl_dir / name
    if not tpl.is_file():
        warn(f"{t['id']}: 명세 템플릿 {name} 없음 → worker-spec.md로 대신 렌더링 ({tpl_dir})")
        tpl = tpl_dir / "worker-spec.md"
    return fill(tpl.read_text(encoding="utf-8"), vals)


def main(argv=None):
    ap = argparse.ArgumentParser(description="tasks.yaml → 작업 명세 렌더링")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--ready", action="store_true")
    ap.add_argument("--list-ready", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--no-extra", action="store_true", help="specs/<ID>.extra.md를 붙이지 않는다")
    ap.add_argument("--repo"), ap.add_argument("--config"), ap.add_argument("--tasks")
    ap.add_argument("--templates", help="템플릿 폴더 (기본: kit.yaml paths.templates)")
    ap.add_argument("--out-dir", help="저장 폴더 (기본: kit.yaml paths.specs)")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    data, _ = kitlib.load_tasks_text(kitlib.tasks_path(repo, cfg, a.tasks).read_text(encoding="utf-8"))
    tasks = {str(t["id"]): t for t in data.get("tasks") or []}
    runtime = data.get("runtime") or {}
    tpl_dir = Path(a.templates) if a.templates else repo / kitlib.cfg_get(cfg, "paths.templates", "orchestration/templates")
    out_dir = Path(a.out_dir) if a.out_dir else repo / kitlib.cfg_get(cfg, "paths.specs", "orchestration/specs")
    warn = lambda m: print(f"경고: {m}", file=sys.stderr)  # noqa: E731

    if a.list_ready:
        print("\n".join(ready_ids(tasks)))
        return 0
    ids = ready_ids(tasks) if a.ready else a.ids
    if not ids:
        print("렌더링할 작업이 없다", file=sys.stderr)
        return 1
    for i in ids:
        if i not in tasks:
            print(f"알 수 없는 작업 ID: {i}", file=sys.stderr)
            return 1
        text = render(tasks[i], tasks, cfg, runtime, tpl_dir, warn)
        extra = out_dir / f"{i}.extra.md"
        if not a.no_extra and extra.is_file():
            text = text.rstrip() + "\n\n" + extra.read_text(encoding="utf-8")
        if a.stdout:
            print(text)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{i}.md"
            out.write_text(text, encoding="utf-8")
            try:
                print(out.relative_to(repo))
            except ValueError:
                print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
