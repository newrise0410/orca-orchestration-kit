#!/usr/bin/env python3
"""작업 투입: 명세 렌더링 → Orca task-create → worker-start → tasks.yaml running · state/dispatch.tsv 기록.

사용:
  orchestration/tools/launch.sh [옵션] <작업ID> [작업ID ...]
  orchestration/tools/launch.sh --review <작업ID>                 # 구현 검토 워커 (보고서 브랜치 req/<ID>-RV, 메인 기준)
  orchestration/tools/launch.sh --retry 지적.md --suffix r1 <작업ID> # req/<ID>에서 남은 지적 수정
옵션:
  --dry-run            Orca 명령을 실행하지 않고 출력만
  --force              선행 작업·status 확인을 건너뛴다(사유를 issues.md에 남겨라)
  --agent/--model/--effort  이번 투입만 덮어쓴다 (기본: 작업 항목 agent/model/effort → kit.yaml orca.agent)
  --worktree SEL       기본 new-top-level. 기존 워크트리(path:<경로>, id:<id>)를 재사용하면 생성 플래그를 넣지 않는다
  --keep-open          끝난 뒤에도 워커를 연다 (L1 통합 작업은 항상)
설정(orchestration/kit.yaml): orca.work_run, orca.repo, orca.agent, review.{agent,model,effort}
환경변수: KIT_RUN, KIT_REPO, KIT_AGENT, KIT_WORKTREE, KIT_REVIEW_AGENT/MODEL/EFFORT
투입 조건(--force로 끔): status todo/ready, hold 없음, 게이트(G-*) 아님, 선행 작업 merged(L1은 L2 선행을 기다리지 않는다), 기준 브랜치가 있음.
worker-start가 실패하면 다시 실행하지 말고 출력의 failedStage를 본다(Orca 오케스트레이션 가이드 recovery-and-cleanup).
"""
import argparse
import contextlib
import datetime as dt
import io
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import render_spec  # noqa: E402
import task_status  # noqa: E402
import validate_dag  # noqa: E402

NEW_WT = ("new-top-level", "new-child")


def orca():
    return os.environ.get("ORCA_CLI_COMMAND") or "orca"


def ref_exists(repo, name):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", name],
                          capture_output=True).returncode == 0


def branch_exists(repo, cfg, b):
    rem = kitlib.git_remote(cfg)
    return ref_exists(repo, f"refs/heads/{b}") or (bool(rem) and ref_exists(repo, f"refs/remotes/{rem}/{b}"))


def branch_hint(b, cfg):
    main = kitlib.cfg_get(cfg, "project.main_branch", "main")
    rem = kitlib.git_remote(cfg)
    push = f" && git push {rem} {b}" if rem else ""
    if b == "int/common":
        return f"W0 시작 전에 만든다: git branch int/common {main}{push}"
    if b.startswith("int/"):
        return f"G-0에서 만든다: git branch {b} {main}{push}"
    return f"git branch {b} <기준>{push}"


def run(cmd, dry):
    print("  $ " + " ".join(shlex.quote(c) for c in cmd))
    if dry:
        return 0, ""
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")


def first(pattern, text):
    m = re.search(pattern, text or "")
    return m.group(0) if m else ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="작업 투입 (렌더링 → task-create → worker-start)")
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--review", action="store_true")
    g.add_argument("--retry", metavar="NOTES")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--agent"), ap.add_argument("--model"), ap.add_argument("--effort")
    ap.add_argument("--worktree", default=os.environ.get("KIT_WORKTREE") or "new-top-level")
    ap.add_argument("--keep-open", action="store_true")
    a = ap.parse_args(argv)
    if a.retry and not a.suffix:
        sys.exit("--retry에는 --suffix(예: r1)가 필요하다")
    repo = kitlib.repo_root()
    os.chdir(repo)
    cfg = kitlib.load_config(repo)
    run_id = os.environ.get("KIT_RUN") or kitlib.cfg_get(cfg, "orca.work_run", "")
    repo_sel = os.environ.get("KIT_REPO") or kitlib.cfg_get(cfg, "orca.repo", "")
    if not run_id and not a.dry_run:
        sys.exit("orca.work_run이 비어 있다 (kit.yaml 또는 KIT_RUN)")
    run_id = run_id or "<work_run>"
    tpath = kitlib.tasks_path(repo, cfg)
    res = validate_dag.validate(tpath.read_text(encoding="utf-8"), cfg, kitlib.load_archive(repo, cfg))
    if res["errors"]:
        print("validate_dag 오류: tasks.yaml을 먼저 고쳐라", file=sys.stderr)
        for e in res["errors"]:
            print("  " + e, file=sys.stderr)
        return 1
    data, tasks = kitlib.all_tasks(repo, cfg)
    active = {str(t["id"]) for t in data.get("tasks") or [] if isinstance(t, dict)}
    rv = cfg.get("review") or {}
    rc = 0
    for tid in a.ids:
        t = tasks.get(tid)
        print(f"\n[{tid}]")
        if t is None:
            print(f"  알 수 없는 작업 {tid}", file=sys.stderr)
            return 1
        mode = "review" if a.review else ("retry" if a.retry else "new")
        # ── 투입 조건
        if mode == "new" and not a.force:
            why = []
            if tid not in active:
                why.append("보관 파일에 있는 작업(이미 merged)")
            st = str(t.get("status", "todo")).strip().lower()
            if st not in ("todo", "ready"):
                why.append(f"status {st} (todo/ready만 투입. 후속 일은 새 ID나 --retry)")
            if t.get("hold"):
                why.append(f"보류 중: {t['hold']}")
            if kitlib.is_gate(t, cfg):
                why.append("게이트 작업은 매니저가 직접 한다")
            od = kitlib.open_deps(t, tasks)
            if od:
                why.append("선행 미완료: " + ", ".join(f"{d}({tasks.get(d, {}).get('status', '없음')})" for d in od))
            if why:
                print("  보류: " + " / ".join(why) + "  (무시하려면 --force)", file=sys.stderr)
                rc = 1
                continue
        # ── 기준 브랜치와 명세
        main_b = kitlib.cfg_get(cfg, "project.main_branch", "main")
        b = str(t.get("branch") or "")
        ip = str(cfg.get("integration_prefix") or "int/")
        if mode == "review":
            base, title = main_b, f"{tid}-RV" + (f"-{a.suffix}" if a.suffix else "")
        elif mode == "retry":
            base, title = b, f"{tid} retry-{a.suffix}"
        else:
            base, title = (b if b.startswith(ip) else (t.get("base") or main_b)), tid
        new_wt = a.worktree in NEW_WT
        if new_wt and not branch_exists(repo, cfg, base):
            print(f"  보류: 기준 브랜치 {base}가 없다. {branch_hint(base, cfg)}", file=sys.stderr)
            rc = 1
            continue
        rargs = [tid] + (["--review"] if mode == "review" else []) + (["--retry", a.retry] if a.retry else []) \
            + (["--suffix", a.suffix] if a.suffix else [])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if render_spec.main(rargs) != 0:
                print("  명세 렌더링 실패", file=sys.stderr)
                return 1
        spec = buf.getvalue().strip().splitlines()[-1]
        print(f"  명세: {spec}")
        # ── 에이전트·모델
        if mode == "review":
            agent = a.agent or os.environ.get("KIT_REVIEW_AGENT") or rv.get("agent") or t.get("agent")
            model = a.model or os.environ.get("KIT_REVIEW_MODEL") or rv.get("model")
            effort = a.effort or os.environ.get("KIT_REVIEW_EFFORT") or rv.get("effort")
        else:
            agent = a.agent or os.environ.get("KIT_AGENT") or t.get("agent")
            model = a.model or t.get("model")
            effort = a.effort or t.get("effort")
        agent = agent or kitlib.cfg_get(cfg, "orca.agent", "claude") or "claude"
        if effort and not model:
            print("  참고: model 없이 effort는 쓸 수 없어 effort를 뺀다", file=sys.stderr)
            effort = None
        name = re.sub(r"[^a-z0-9._-]", "-", title.lower())
        keep = a.keep_open or str(t.get("layer")) == "L1" and mode == "new"
        # ── Orca
        code, out = run([orca(), "orchestration", "task-create", "--run", run_id, "--spec-file", spec,
                         "--task-title", title, "--display-name", title, "--json"], a.dry_run)
        otid = first(r"task_[0-9a-f]+", out) or ("<task_id>" if a.dry_run else "")
        if code != 0 or not otid:
            print(f"  task-create 실패: {out.strip()[:400]}", file=sys.stderr)
            return 1
        ws = [orca(), "orchestration", "worker-start", "--run", run_id, "--task", otid, "--agent", agent]
        if model:
            ws += ["--model", str(model)] + (["--effort", str(effort)] if effort else [])
        ws += ["--worktree", a.worktree]
        if new_wt:   # 생성 플래그는 새 워크트리에만 (기존 워크트리에 주면 Orca가 거부한다)
            ws += (["--repo", repo_sel] if repo_sel else []) + ["--base-branch", base, "--name", name, "--display-name", title]
        ws += (["--keep-open"] if keep else []) + ["--json"]
        code, out = run(ws, a.dry_run)
        did = first(r"ctx_[0-9a-f]+", out) or ("<dispatch>" if a.dry_run else "")
        if code != 0 or not did:
            print(f"  worker-start 실패({otid}). 다시 실행하지 말고 failedStage를 확인하라:\n{out.strip()[:1200]}", file=sys.stderr)
            return 1
        if a.dry_run:
            print(f"  (dry-run) dispatch.tsv 기록" + (", tasks.yaml status running" if mode == "new" else ""))
            continue
        with open(repo / "orchestration/state/dispatch.tsv", "a", encoding="utf-8") as f:
            f.write("\t".join([title, otid, did, name, dt.datetime.now().strftime("%F %T")]) + "\n")
        if mode == "new" and tid in active:
            tpath.write_text(task_status.set_status(tpath.read_text(encoding="utf-8"), tid, "running"), encoding="utf-8")
        print(f"  {title} {otid} {did}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
