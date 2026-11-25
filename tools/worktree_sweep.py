#!/usr/bin/env python3
"""워크트리 정리: 일이 끝나 필요 없어진 워커 워크트리를 찾아 지운다(잃을 것이 없을 때만).

  python3 orchestration/tools/worktree_sweep.py              # 미리 보기: 정리할 것 · 확인 필요 · 남겨 둠(이유)
  python3 orchestration/tools/worktree_sweep.py --apply      # '정리' 판정만 지운다
  python3 orchestration/tools/worktree_sweep.py --apply --orphans   # 어느 작업 것인지 모르는 것도(main에 다 들어 있을 때만)
옵션: --idle-min 60 (마지막 활동이 이보다 최근이면 건드리지 않는다) --json
launch.sh가 kit.yaml cleanup.auto_sweep(기본 true)이면 투입 전에 한 시간에 한 번 '정리'만 돌린다(주인 없는 것은 안 지운다).

'정리' = 아래를 모두 만족할 때만:
  - Orca 메인 워크트리가 아니고, 사용자가 고정(pin)하지 않았고, 자식 워크트리가 없다
  - 진행 중인 디스패치가 없다(이 워크트리의 Orca 워커가 모두 끝남)
  - 커밋하지 않은 변경이 없다
  - 마지막 활동이 idle-min보다 오래됐다
  - 일이 끝났다: 구현(new·retry·handoff)은 작업이 merged이고 HEAD가 기준·메인 브랜치에 들어 있다.
    검토(-RV)는 보고서 폴더가 메인에 들어왔거나 대상 작업이 merged다. 통합(L1)은 작업이 merged이고 HEAD가 메인에 있다.
지울 때: 끝난 워커의 터미널 해제(worker-release) → orca worktree rm --delete-branch(워크트리가 쓰던 로컬 브랜치만) →
         kit.yaml run.cleanup_hook → 그 작업의 한도 전환 보존 브랜치(wip/<ID>-quota-*). 기록: state/sweep.log
작업 브랜치(req/<ID>)·통합 브랜치는 지우지 않는다(이력이다).
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import routing  # noqa: E402
import status  # noqa: E402

SAFE, CHECK, KEEP = "정리", "확인 필요", "남겨 둠"


def git(repo, *a):
    p = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def ref(repo, cfg, b):
    """브랜치 → 비교에 쓸 참조(원격 운영이면 <원격>/<브랜치>). 없으면 None."""
    rem = kitlib.git_remote(cfg)
    for r in ([f"{rem}/{b}"] if rem else []) + [b]:
        if git(repo, "rev-parse", "-q", "--verify", r)[0] == 0:
            return r
    return None


def contained(repo, head, r):
    return bool(r) and git(repo, "merge-base", "--is-ancestor", head, r)[0] == 0


def orca_worktrees(repo):
    d = status.orca_json(["worktree", "list", "--repo", f"path:{repo}", "--limit", "1000"])
    if not d.get("ok", False):
        return None, (d.get("error") or {}).get("message", "worktree list 실패")
    return (d.get("result") or {}).get("worktrees") or [], None


def classify(repo, cfg, tasks, workers, wts, idle_min=60, now=None):
    """워크트리마다 (판정, 이유, 정보). workers: Orca 워커 기록 전부(끝난 것 포함, status.all_workers)."""
    now = now or time.time()
    main_b = kitlib.cfg_get(cfg, "project.main_branch", "") or ""
    if not main_b or not ref(repo, cfg, main_b):   # 설정이 없거나 그 브랜치가 없으면 메인 워크트리의 현재 브랜치(예: master)
        main_b = git(repo, "symbolic-ref", "--short", "HEAD")[1] or main_b or "main"
    main_r = ref(repo, cfg, main_b)
    rows = routing.read_dispatch(repo, set(tasks))
    by_name, by_did = {}, {r["did"]: r for r in rows}
    for r in rows:   # launch.sh가 정한 워크트리 이름(dispatch.tsv 4번째 칸)
        if r.get("kit_id") and r.get("name"):
            by_name.setdefault(r["name"], []).append(r)
    by_path = {}
    for w in workers:
        by_path.setdefault(kitlib.workspace_path(w.get("workspace")).rstrip("/"), []).append(w)
    ids = set(tasks)
    ev_pat = str(kitlib.cfg_get(cfg, "paths.evidence", "docs/06_evidence/{id}/report.md"))
    out = []
    for wt in wts:
        path = str(wt.get("path") or "").rstrip("/")
        name = os.path.basename(path)
        info = {"path": path, "name": name, "branch": str(wt.get("branch") or "").replace("refs/heads/", ""), "kit_id": None, "mode": None,
                "dispatches": []}
        if wt.get("isMainWorktree") or Path(path).resolve() == Path(repo).resolve():
            continue
        if wt.get("isPinned"):
            out.append((KEEP, "사용자가 고정함", info))
            continue
        if wt.get("childWorktreeIds"):
            out.append((KEEP, "자식 워크트리가 있다", info))
            continue
        if not info["branch"] and Path(path).is_dir():   # Orca가 브랜치를 안 주면 워크트리의 실제 브랜치
            info["branch"] = git(path, "symbolic-ref", "--short", "-q", "HEAD")[1]
        ws = by_path.get(path, [])
        info["dispatches"] = [{"dispatch": w.get("dispatchId"), "state": w.get("state"), "terminal": w.get("terminalState")} for w in ws]
        if any(str(w.get("state")) not in kitlib.SETTLED_WORKER_STATES for w in ws):
            out.append((KEEP, "진행 중인 디스패치", info))
            continue
        # 어느 작업 것인가: Orca 워커 제목 → dispatch.tsv 워크트리 이름 → 브랜치 이름(req/<ID>)
        cand = []
        for w in ws:
            r = by_did.get(w.get("dispatchId"))
            if r and r.get("kit_id"):
                cand.append(r)
        cand += by_name.get(name, [])
        if cand:
            info["kit_id"], info["mode"] = cand[-1]["kit_id"], cand[-1]["mode"]
        else:
            m = re.match(r"(?:req/)?(.+?)(?:-RV(?:-.+)?)?$", info["branch"])
            if m and m.group(1) in ids:
                info["kit_id"], info["mode"] = m.group(1), ("review" if "-RV" in info["branch"] else "new")
        if not Path(path).is_dir():
            out.append((SAFE if info["kit_id"] else CHECK, "폴더가 이미 없다(Orca 기록만 남음)", info))
            continue
        rc, dirty = git(path, "status", "--porcelain")
        if rc != 0:
            out.append((CHECK, "git 상태를 읽지 못했다", info))
            continue
        if dirty:
            out.append((CHECK if info["kit_id"] and str(tasks[info["kit_id"]].get("status")).lower() == "merged" else KEEP,
                        f"커밋하지 않은 변경 {len(dirty.splitlines())}개", info))
            continue
        last = float(wt.get("lastActivityAt") or 0) / 1000
        if last and now - last < idle_min * 60:
            out.append((KEEP, f"최근 활동 {int((now - last) // 60)}분 전(기준 {idle_min}분)", info))
            continue
        head = git(path, "rev-parse", "HEAD")[1]
        tid = info["kit_id"]
        if not tid:
            ok = contained(repo, head, main_r)
            out.append((CHECK, "어느 작업 것인지 모른다" + (f" — HEAD가 {main_b}에 들어 있어 --orphans로 지울 수 있다" if ok else
                                                           f" — {main_b}에 없는 커밋이 있다"), dict(info, orphan_ok=ok)))
            continue
        t = tasks[tid]
        st = str(t.get("status", "")).lower()
        if info["mode"] == "review":
            rid = info["branch"].replace("req/", "", 1) if "-RV" in info["branch"] else f"{tid}-RV"
            ev = str(Path(ev_pat.replace("{id}", rid)).parent)
            in_main = bool(main_r) and git(repo, "cat-file", "-e", f"{main_r}:{ev}")[0] == 0
            if in_main or st == "merged":
                out.append((SAFE, "검토 보고서가 " + (f"{main_b}에 들어왔다" if in_main else "끝났다(대상 작업 merged)"), info))
            else:
                out.append((KEEP, f"검토 보고서({ev})가 아직 {main_b}에 없다", info))
            continue
        if st != "merged":
            out.append((KEEP, f"작업 {tid}가 아직 {st}", info))
            continue
        # 구현은 기준(int/<모듈>)이나 메인에, 통합(L1, 자기 브랜치가 int/*)은 메인에 들어 있어야 한다
        bases = [r for r in ([main_r] if str(t.get("layer")) == "L1" else [ref(repo, cfg, str(t.get("base") or "")), main_r]) if r]
        if any(contained(repo, head, r) for r in bases):
            out.append((SAFE, f"작업 {tid} merged · HEAD가 기준에 들어 있다", info))
        else:
            n = git(path, "rev-list", "--count", head, "--not", *bases)[1] if bases else "?"
            out.append((CHECK, f"작업 {tid}는 merged인데 이 워크트리에 기준에 없는 커밋 {n}개", info))
    return out


def remove(repo, cfg, info, log):
    for d in info["dispatches"]:
        if d.get("terminal") != "released":
            status.orca_json(["orchestration", "worker-release", "--dispatch", d["dispatch"]])
    d = status.orca_json(["worktree", "rm", "--worktree", f"path:{info['path']}", "--delete-branch"])   # 폴더가 없어도 Orca 기록을 지운다
    if not d.get("ok", False):
        return False, (d.get("error") or {}).get("message", "worktree rm 실패")
    hook = os.environ.get("KIT_CLEANUP_HOOK") or kitlib.cfg_get(cfg, "run.cleanup_hook", "")
    if hook and os.access(hook if os.path.isabs(hook) else repo / hook, os.X_OK):
        subprocess.run([str(hook if os.path.isabs(hook) else repo / hook), info["name"]], capture_output=True)
    if info.get("kit_id"):
        _, wips = git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/wip/{info['kit_id']}-quota-*")
        for b in wips.split():
            git(repo, "branch", "-q", "-D", b)
    log.write("\t".join([dt.datetime.now().strftime("%F %T"), info["name"], info.get("kit_id") or "", info["branch"]]) + "\n")
    return True, ""


def sweep(repo, cfg, apply=False, orphans=False, idle_min=60, quiet=False):
    _, tasks = kitlib.all_tasks(repo, cfg)
    wts, err = orca_worktrees(repo)
    if err:
        return None, err
    run = kitlib.cfg_get(cfg, "orca.work_run", "")
    workers, werr = status.all_workers(run) if run else ([], None)
    if werr:
        return None, f"워커 목록을 읽지 못해 정리하지 않는다: {werr}"
    res = classify(repo, cfg, tasks, workers, wts, idle_min)
    done = []
    if apply:
        rem = kitlib.git_remote(cfg)
        if rem:
            git(repo, "fetch", "-q", rem)
        logp = routing.state_dir(repo) / "sweep.log"
        logp.parent.mkdir(parents=True, exist_ok=True)
        with open(logp, "a", encoding="utf-8") as log:
            for verdict, why, info in res:
                if verdict == SAFE or (orphans and info.get("orphan_ok")):
                    ok, e = remove(repo, cfg, info, log)
                    done.append((info, ok, e))
    st = routing.state_dir(repo) / "sweep.json"
    try:
        st.write_text(json.dumps({"ts": time.time(), "safe": sum(1 for v, _, _ in res if v == SAFE),
                                  "check": sum(1 for v, _, _ in res if v == CHECK)}), encoding="utf-8")
    except OSError:
        pass
    return (res, done), None


def sweep_due(repo, cfg, minutes=60):
    if not kitlib.cfg_get(cfg, "cleanup.auto_sweep", True):
        return False
    try:
        return time.time() - float(json.loads((routing.state_dir(repo) / "sweep.json").read_text(encoding="utf-8")).get("ts", 0)) >= minutes * 60
    except (OSError, ValueError):
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="워크트리 정리")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--orphans", action="store_true", help="어느 작업 것인지 모르는 워크트리도(HEAD가 메인에 다 들어 있을 때만)")
    ap.add_argument("--idle-min", dest="idle_min", type=int, default=60)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    r, err = sweep(repo, cfg, a.apply, a.orphans, a.idle_min)
    if err:
        print(err, file=sys.stderr)
        return 1
    res, done = r
    if a.json:
        print(json.dumps({"worktrees": [{"verdict": v, "why": w, **i} for v, w, i in res],
                          "removed": [{"path": i["path"], "ok": ok, "error": e} for i, ok, e in done]}, ensure_ascii=False, indent=1))
        return 0
    for label in (SAFE, CHECK, KEEP):
        g = [(w, i) for v, w, i in res if v == label]
        if not g:
            continue
        print(f"# {label} {len(g)}")
        for why, i in g:
            print(f"- {i['name']}" + (f" [{i['kit_id']}{'·' + i['mode'] if i.get('mode') else ''}]" if i.get("kit_id") else "") + f": {why}")
    if not res:
        print("이 저장소의 워커 워크트리 없음")
    if a.apply:
        ok = [i for i, k, _ in done if k]
        bad = [(i, e) for i, k, e in done if not k]
        print(f"\n정리함 {len(ok)}" + (f" · 실패 {len(bad)}" if bad else ""))
        for i, e in bad:
            print(f"  실패 {i['name']}: {e}")
    elif any(v == SAFE for v, _, _ in res):
        print("\n지우려면 --apply ('정리'만 지운다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
