#!/usr/bin/env python3
"""작업 투입: 명세 렌더링 → Orca task-create → worker-start → tasks.yaml running · state/dispatch.tsv 기록.

사용:
  orchestration/tools/launch.sh [옵션] <작업ID> [작업ID ...]
  orchestration/tools/launch.sh --review <작업ID>                 # 구현 검토 워커 (보고서 브랜치 req/<ID>-RV, 메인 기준)
  orchestration/tools/launch.sh --retry 지적.md --suffix r1 <작업ID> # req/<ID>에서 남은 지적 수정
  (한도 전환 이어받기 --handoff 메모 --suffix h1 --worktree path:<멈춘 워커 워크트리> 는 quota_switch.py가 부른다)
옵션:
  --dry-run            Orca 명령을 실행하지 않고 출력만
  --force              선행 작업·status 확인을 건너뛴다(사유를 issues.md에 남겨라)
  --agent/--model/--effort  이번 투입만 덮어쓴다
                       (기본: 작업 항목 agent/model/effort 또는 route: <카탈로그 키> → 검토는 kit.yaml review.*
                        → 모델 자동 라우팅(orchestration/models.yaml) → kit.yaml orca.agent)
  --no-route           자동 라우팅을 끈다 · --explain  라우팅 근거와 다음 후보를 출력
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
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import render_spec  # noqa: E402
import routing  # noqa: E402
import status  # noqa: E402
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


def live_dispatches(run_id, dry):
    """Orca가 진행 중이라고 보는 dispatch ID 집합. 조회할 수 없으면 None(라우터가 tasks.yaml로 어림한다)."""
    if dry or not run_id or run_id.startswith("<"):
        return None
    ws, err = status.all_workers(run_id)
    if err:
        return None
    return {w.get("dispatchId") for w in ws if str(w.get("state")) not in kitlib.SETTLED_WORKER_STATES}


def first(pattern, text):
    m = re.search(pattern, text or "")
    return m.group(0) if m else ""


def orca_id(out, keys, pattern):
    """Orca --json 출력에서 ID를 JSON으로 읽는다(result.<키>, result.task.id 등). JSON이 아니면 정규식으로 찾는다."""
    txt = (out or "").strip()
    for cand in (txt, txt.split("\n\n")[0], txt.splitlines()[0] if txt else ""):
        try:
            d = json.loads(cand)
        except (ValueError, TypeError):
            continue
        r = d.get("result") if isinstance(d, dict) else None
        if isinstance(r, dict):
            for k in keys:
                v = r.get(k)
                if isinstance(v, dict):
                    v = v.get("id")
                if isinstance(v, str) and re.fullmatch(pattern, v):
                    return v
        break
    return first(pattern, txt)


def main(argv=None):
    ap = argparse.ArgumentParser(description="작업 투입 (렌더링 → task-create → worker-start)")
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--review", action="store_true")
    g.add_argument("--retry", metavar="NOTES")
    ap.add_argument("--handoff", metavar="NOTES", help="한도 전환 이어받기(quota_switch.py가 부른다): 멈춘 워커의 워크트리(--worktree path:..)에서 이어서")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--agent"), ap.add_argument("--model"), ap.add_argument("--effort")
    ap.add_argument("--worktree", default=os.environ.get("KIT_WORKTREE") or "new-top-level")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--no-route", dest="no_route", action="store_true", help="자동 라우팅을 쓰지 않는다")
    ap.add_argument("--explain", action="store_true", help="라우팅 근거와 다음 후보를 출력")
    a = ap.parse_args(argv)
    if a.retry and not a.suffix:
        sys.exit("--retry에는 --suffix(예: r1)가 필요하다")
    if a.handoff and (a.retry or not a.suffix):
        sys.exit("--handoff에는 --suffix(예: h1)가 필요하고 --retry와 함께 쓰지 않는다")
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
    live, launched = None, {}
    # ── 구독 CLI 자동 업데이트 (routing.auto_update: daily 기본 · weekly · off). 하루 한 번, 워커를 띄우기 전에만
    catalog0, pol0 = routing.load(repo)
    if routing.enabled(pol0) and not a.dry_run and not os.environ.get("KIT_NO_CLI_UPDATE") and routing.update_due(repo, pol0):
        print("구독 CLI 업데이트 (하루 한 번 · 끄려면 models.yaml routing.auto_update: off)")
        res = routing.update_clis(repo, catalog0, pol0, log=lambda m: print(m, flush=True))
        for ag, r in res.items():
            if r.get("status") in ("갱신", "실패"):
                print(f"  {ag}: {r['status']} {r.get('before', '')}" + (f" → {r['after']}" if r.get("status") == "갱신" else f" ({r.get('detail')})"))
        routing.discover(repo, catalog0, refresh=True)
    for tid in a.ids:
        t = tasks.get(tid)
        print(f"\n[{tid}]")
        if t is None:
            print(f"  알 수 없는 작업 {tid}", file=sys.stderr)
            return 1
        mode = "review" if a.review else ("retry" if a.retry else ("handoff" if a.handoff else "new"))
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
        elif mode == "handoff":
            base, title = b, f"{tid} handoff-{a.suffix}"
        else:
            base, title = (b if b.startswith(ip) else (t.get("base") or main_b)), tid
        new_wt = a.worktree in NEW_WT
        if new_wt and not branch_exists(repo, cfg, base):
            print(f"  보류: 기준 브랜치 {base}가 없다. {branch_hint(base, cfg)}", file=sys.stderr)
            rc = 1
            continue
        rargs = [tid] + (["--review"] if mode == "review" else []) + (["--retry", a.retry] if a.retry else []) \
            + (["--handoff", a.handoff] if a.handoff else []) + (["--suffix", a.suffix] if a.suffix else [])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            if render_spec.main(rargs) != 0:
                print("  명세 렌더링 실패", file=sys.stderr)
                return 1
        spec = buf.getvalue().strip().splitlines()[-1]
        print(f"  명세: {spec}")
        # ── 에이전트·모델: 명시 > 작업 항목 > review.* > 자동 라우팅 > orca.agent
        if mode == "review":
            agent = a.agent or os.environ.get("KIT_REVIEW_AGENT") or rv.get("agent")
            model = a.model or os.environ.get("KIT_REVIEW_MODEL") or rv.get("model")
            effort = a.effort or os.environ.get("KIT_REVIEW_EFFORT") or rv.get("effort")
        elif mode == "handoff":   # 한도로 멈춘 구독을 다시 고르지 않게 작업 항목의 고정(agent/model/route)은 쓰지 않는다
            agent, model, effort = a.agent, a.model, a.effort
        else:
            agent = a.agent or os.environ.get("KIT_AGENT") or t.get("agent")
            model = a.model or t.get("model")
            effort = a.effort or t.get("effort")
        rkey, rdiff, rjev = "", "", ""
        catalog, pol = routing.load(repo)
        catalog = routing.resolve_catalog(catalog, routing.discover(repo, catalog))
        pinned = t.get("route") if mode not in ("review", "handoff") else None
        if pinned and not (agent or model):
            m = catalog["models"].get(str(pinned))
            if m is None:
                print(f"  보류: route: {pinned}는 카탈로그에 없다 (models.py discover)", file=sys.stderr)
                rc = 1
                continue
            c = routing.classify(t, tasks, pol, mode)
            e2, model, _ = routing.pick_effort(m, c["difficulty"], routing.rcfg(pol, "objective", "balanced"), float(m.get("quality", 3)))
            agent, effort, rkey, rdiff = m.get("agent"), effort or e2, str(pinned), f"{c['difficulty']:.1f}"
            print(f"  라우팅: route: {pinned} (작업 항목) → {agent} {model}" + (f" · {effort}" if effort else ""))
        elif not (agent or model) and not a.no_route and routing.enabled(pol):
            if live is None:
                live = live_dispatches(run_id, a.dry_run)
            dec = routing.route(repo, cfg, tid, tasks, mode, live_dids=live, catalog=catalog, pol=pol,
                                extra_running=launched)
            if dec.get("key"):
                agent, model, effort = dec["agent"], dec["model"], effort or dec["effort"]
                rkey, rdiff = dec["key"], f"{dec['difficulty']:.1f}"
                rjev = f"{dec['jev']['difficulty']:.1f}" if (dec.get("jev") or {}).get("difficulty") is not None else ""
                print(f"  라우팅: {routing.describe(dec)}")
                if a.explain:
                    print("    난이도 근거: " + (" · ".join(dec["reasons"]) or "기본"))
                    print(f"    선택 근거: 점수 {dec['score']} · " + " · ".join(dec["why"]))
                    if dec["alts"]:
                        print("    다음 후보: " + ", ".join(f"{k}({s})" for k, s in dec["alts"]))
            elif dec.get("quota_blocked"):
                print(f"  보류: {routing.describe(dec)} — 쓸 수 있는 구독의 한도가 모두 찼다. 초기화 뒤 다시 띄운다 (--agent로 강제)", file=sys.stderr)
                rc = 1
                continue
            else:
                print(f"  라우팅: {routing.describe(dec)} → orca.agent 기본값으로 띄운다", file=sys.stderr)
        agent = agent or kitlib.cfg_get(cfg, "orca.agent", "claude") or "claude"
        if effort and not model:
            print("  참고: model 없이 effort는 쓸 수 없어 effort를 뺀다", file=sys.stderr)
            effort = None
        name = re.sub(r"[^a-z0-9._-]", "-", title.lower())
        keep = a.keep_open or str(t.get("layer")) == "L1" and mode in ("new", "handoff")
        # ── Orca
        code, out = run([orca(), "orchestration", "task-create", "--run", run_id, "--spec-file", spec,
                         "--task-title", title, "--display-name", title, "--json"], a.dry_run)
        otid = orca_id(out, ("id", "taskId", "task"), r"task_[0-9A-Za-z]+") or ("<task_id>" if a.dry_run else "")
        if code != 0 or not otid:
            print(f"  task-create 실패: {out.strip()[:400]}", file=sys.stderr)
            return 1
        for attempt in (1, 2):
            ws = [orca(), "orchestration", "worker-start", "--run", run_id, "--task", otid, "--agent", agent]
            if model:
                ws += ["--model", str(model)] + (["--effort", str(effort)] if effort else [])
            ws += ["--worktree", a.worktree]
            if new_wt:   # 생성 플래그는 새 워크트리에만 (기존 워크트리에 주면 Orca가 거부한다)
                ws += (["--repo", repo_sel] if repo_sel else []) + ["--base-branch", base, "--name", name, "--display-name", title]
            ws += (["--keep-open"] if keep else []) + ["--json"]
            code, out = run(ws, a.dry_run)
            did = orca_id(out, ("dispatchId", "dispatch", "id"), r"ctx_[0-9A-Za-z]+") or ("<dispatch>" if a.dry_run else "")
            # Orca에서 꺼진 에이전트: 워커를 만들기 전에 거부된다. 자동 라우팅이었으면 그 구독을 빼고 한 번 더 고른다.
            if code != 0 and not did and "agent_unconfigured" in out:
                routing.write_cooldown(repo, agent, 24 * 60, "Orca에서 꺼진 에이전트(Orca 설정에서 켠 뒤 models.py cooldown --clear)")
                print(f"  {agent}: Orca에서 꺼져 있다 → 24시간 쿨다운", file=sys.stderr)
                if attempt == 1 and rkey and not pinned:
                    dec = routing.route(repo, cfg, tid, tasks, mode, live_dids=live, catalog=catalog, pol=pol,
                                        extra_running=launched)
                    if dec.get("key"):
                        agent, model, effort = dec["agent"], dec["model"], dec["effort"]
                        rkey, rdiff = dec["key"], f"{dec['difficulty']:.1f}"
                        rjev = f"{dec['jev']['difficulty']:.1f}" if (dec.get("jev") or {}).get("difficulty") is not None else ""
                        print(f"  다시 라우팅: {routing.describe(dec)}")
                        continue
            break
        if code != 0 or not did:
            print(f"  worker-start 실패({otid}). 다시 실행하지 말고 failedStage를 확인하라:\n{out.strip()[:1200]}", file=sys.stderr)
            if not a.dry_run and otid:   # task-create만 되고 워커가 없으면 Orca에 고아 Task가 남는다 → 취소로 닫는다
                c2, o2 = run([orca(), "orchestration", "task-update", "--run", run_id, "--id", otid, "--status", "failed",
                              "--result", "cancelled", "--json"], False)
                print(f"  Orca Task {otid}: " + ("취소로 닫았다(worker-start 실패)" if c2 == 0 else f"닫지 못했다 — 손으로 task-update ({o2.strip()[:160]})"),
                      file=sys.stderr)
            return 1
        if a.dry_run:
            print(f"  (dry-run) dispatch.tsv 기록" + (", tasks.yaml status running" if mode == "new" else ""))
            launched[agent] = launched.get(agent, 0) + 1
            continue
        with kitlib.locked(repo):   # 매니저·quota_switch·다른 launch가 같은 파일을 동시에 고치지 않게
            with open(repo / "orchestration/state/dispatch.tsv", "a", encoding="utf-8") as f:
                f.write("\t".join([title, otid, did, name, dt.datetime.now().strftime("%F %T"),
                                   agent, str(model or ""), str(effort or ""), rkey, rdiff, rjev]) + "\n")
            if mode == "new" and tid in active:
                tpath.write_text(task_status.set_status(tpath.read_text(encoding="utf-8"), tid, "running"), encoding="utf-8")
        launched[agent] = launched.get(agent, 0) + 1
        print(f"  {title} {otid} {did}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
