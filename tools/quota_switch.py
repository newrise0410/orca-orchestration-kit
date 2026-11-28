#!/usr/bin/env python3
"""한도 전환: 사용량 한도가 찬 구독에서 멈춘 워커(또는 안전 필터로 턴이 끊긴 워커)를 다른 구독으로 이어받게 한다
(매니저가 손으로 하던 절차).

  python3 orchestration/tools/quota_switch.py [--dry-run] [작업ID ...]   # 인자가 없으면 정지 점검의 '한도 정지' 전부

워커마다 판정한다(models.yaml routing.quota):
  대기   on_exhausted: wait 이거나, 막은 창이 wait_minutes(기본 30분) 안에 풀리거나, 다른 구독도 한도이거나,
         검토 워커인데 남은 후보가 구현한 구독뿐(검토 독립성)일 때. 아무것도 바꾸지 않는다.
  전환   1) worker-stop  2) 진행분(커밋 + 커밋하지 않은 변경)을 임시 브랜치 wip/<ID>-quota-<시각>에 스냅샷
         (워크트리·작업 브랜치는 건드리지 않는다)  3) worker-release  4) 같은 워크트리에서 launch.sh --handoff로
         이어받기 투입(라우팅이 한도 찬 모델을 빼고 고른다). 기록: state/quota_switch.tsv, 메모: state/handoff/.
  재개   대기로 둔 워커는 state/quota_wait.json에 풀리는 시각을 적는다. 그 시각이 지나고 이 도구가 다시 돌면
         워커에게 '한도가 풀렸다, 이어서 하라'를 보낸다(orca orchestration send --to dispatch:<id>).
  필터   정지 점검의 '필터 중단'(마지막 응답이 안전 필터·이용 정책 차단)도 같은 절차로 넘긴다. 기다려도 풀리지 않으니
         대기 판정(곧 풀림)은 없고, 그 모델은 이 작업의 후보에서 뺀다(state/quota_switch.tsv 9번째 칸 filter).
         설정 routing.on_filter: switch(기본) | wait.
멈춘 워커의 투입은 실적(성공·실패)에 넣지 않는다. 한도·필터는 품질 문제가 아니다.
  --due  할 일(한도 정지 또는 재개할 대기)이 있으면 종료코드 0, 없으면 1. 아무것도 바꾸지 않는다(예약 점검의 사전 확인용).
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import routing  # noqa: E402
import status  # noqa: E402

TOOLS = Path(__file__).resolve().parent


def git(ws, *args, env=None, check=True):
    p = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()[:200]}")
    return p.stdout.strip()


def snapshot(ws, tid, base, reason, did=""):
    """멈춘 순간의 작업 트리 전체(커밋하지 않은 변경·새 파일 포함)를 임시 브랜치로. 작업 브랜치·인덱스·파일은 그대로 둔다."""
    head = git(ws, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(td) / "index"))
        git(ws, "read-tree", "HEAD", env=env)
        git(ws, "add", "-A", env=env)
        tree = git(ws, "write-tree", env=env)
    dirty = tree != git(ws, "rev-parse", "HEAD^{tree}")
    ident = [] if git(ws, "config", "user.name", check=False) else ["-c", "user.name=orchestration-kit", "-c", "user.email=kit@localhost.invalid"]
    commit = head
    if dirty:
        p = subprocess.run(["git", *ident, "-C", str(ws), "commit-tree", tree, "-p", head, "-m", f"[{tid}] 한도 전환 보존(WIP): {reason}"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"commit-tree: {(p.stderr or p.stdout).strip()[:200]}")
        commit = p.stdout.strip()
    # 같은 작업을 같은 초에 두 번 넘겨도 겹치지 않게 dispatch 끝자리를 붙인다(정리 도구는 wip/<ID>-quota-*로 찾는다)
    name = f"wip/{tid}-quota-{dt.datetime.now():%Y%m%d-%H%M%S}" + (f"-{re.sub(r'[^0-9A-Za-z]', '', did)[-6:]}" if did else "")
    git(ws, "branch", name, commit)
    files = [ln[3:] for ln in git(ws, "status", "--porcelain").splitlines() if ln.strip()]
    log = git(ws, "log", "--oneline", "-10", f"{base}..HEAD", check=False) if base else ""
    n = git(ws, "rev-list", "--count", f"{base}..HEAD", check=False) if base else ""
    return {"branch": name, "commit": commit[:12], "dirty_files": files, "log": log, "commits": n}


def prior_notes(repo, cfg, title, tid):
    """앞 워커 명세에 재투입 지적(### 지적)이 있었으면 이어받기에도 넘긴다."""
    specs = repo / kitlib.cfg_get(cfg, "paths.specs", "orchestration/specs")
    m = re.search(r" (retry|handoff)-(\S+)", title or "")
    rv = re.match(r".+?-RV(?:-(.+))?$", (title or "").split()[0] if title else "")
    if m:
        f = specs / f"{tid}.{m.group(1)}-{m.group(2)}.md"
    elif rv:
        f = specs / (f"{tid}.review-{rv.group(1)}.md" if rv.group(1) else f"{tid}.review.md")
    else:
        return ""
    try:
        txt = f.read_text(encoding="utf-8")
    except OSError:
        return ""
    mm = re.search(r"^### 지적\n(.*?)(?=^## |^### |\Z)", txt, re.S | re.M)
    return mm.group(1).strip() if mm else ""


def switch_count(repo, tid):
    p = routing.state_dir(repo) / "quota_switch.tsv"
    if not p.is_file():
        return 0
    return sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.split("\t")[1:2] == [tid])


def orca(*args):
    cli = os.environ.get("ORCA_CLI_COMMAND") or "orca"
    p = subprocess.run([cli, *args, "--json"], capture_output=True, text=True, timeout=120)
    try:
        d = json.loads((p.stdout or "").splitlines()[0]) if p.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        d = {}
    return bool(d.get("ok")) and p.returncode == 0, (d.get("error") or {}).get("message") or (p.stderr or p.stdout).strip()[:200]


WAIT_REL = "quota_wait.json"


def read_waits(repo):
    try:
        d = json.loads((routing.state_dir(repo) / WAIT_REL).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_waits(repo, d):
    p = routing.state_dir(repo) / WAIT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def due_waits(waits, stalls, now=None):
    """풀리는 시각이 지났고 지금은 한도 정지가 아닌 대기 → 재개할 dispatch 목록."""
    now = now or time.time()
    still = {q["dispatch"] for q in stalls}
    return [did for did, w in waits.items() if w.get("resets") and w["resets"] <= now and did not in still]


def resume(repo, waits, dids, live, dry):
    """대기로 둔 워커에 재개 메시지를 보낸다. 이미 끝난 워커는 기록만 지운다."""
    for did in dids:
        w = waits[did]
        who = f"{w.get('kit_id') or w.get('title') or '?'} ({did})"
        if did not in live:
            print(f"[재개 생략] {who}: 진행 중 워커가 아니다(이미 끝났거나 정리됨)")
            waits.pop(did, None)
            continue
        body = (f"구독 사용량 한도가 풀렸다({routing._hm(w['resets'])}). 멈춘 곳에서 하던 일을 이어서 하라. "
                f"처음부터 다시 하지 말고 git status·git log로 어디까지 했는지 확인한 뒤 계속한다. 끝나면 원 명세대로 worker_done을 보낸다.")
        if dry:
            print(f"(dry-run) [재개] {who}: send --to dispatch:{did}")
            continue
        ok, err = orca("orchestration", "send", "--to", f"dispatch:{did}", "--type", "status", "--subject", "한도 풀림: 이어서 진행", "--body", body)
        if ok:
            print(f"[재개] {who}: 한도가 풀려 이어서 하라고 보냈다")
            waits.pop(did, None)
        else:
            print(f"[재개 실패] {who}: {err} — 다음에 다시 시도한다", file=sys.stderr)


def decide(repo, cfg, tasks, catalog, pol, q, quota, live):
    """→ (전환|대기, 이유, 결정(route 결과)|None, review 여부)."""
    qc = routing.quota_cfg(pol)
    tid = q["kit_id"]
    filt = q.get("kind") == "filter"
    win = f"필터 중단(\"{q.get('evidence', '')[:70]}\")" if filt else ", ".join(f"{lab} {u:.0f}%" for lab, u, _ in q["windows"])
    if not tid or tid not in tasks:
        return "대기", f"키트 작업이 아니다({q.get('title')}) — 손으로 처리", None, False
    if filt and str(routing.rcfg(pol, "on_filter", "switch")).lower() == "wait":
        return "대기", f"{win} · 설정 on_filter: wait", None, False
    if not filt and str(qc.get("on_exhausted", "switch")).lower() == "wait":
        return "대기", f"{win} · 설정 on_exhausted: wait", None, False
    if not filt and q["resets"] and q["resets"] - time.time() <= float(qc.get("wait_minutes", 30)) * 60:
        left = max(0, int((q["resets"] - time.time()) // 60))
        return "대기", f"{win} · {routing._hm(q['resets'])}에 풀린다({left}분 뒤, 기준 {qc.get('wait_minutes', 30)}분)", None, False
    review = q["mode"] == "review"
    # 넘겨받을 구독은 앞 워커가 풀릴 때까지(최대 switch_need_minutes) 버텨야 한다 — 곧 다시 찰 구독으로 넘기면 또 멈춘다
    left = (q["resets"] - time.time()) / 60 if q["resets"] else float(qc.get("switch_need_minutes", 120))
    need = min(float(qc.get("switch_need_minutes", 120)), max(left, 0))
    impl, impl_keys = set(), set()
    xr = routing.rcfg(pol, "cross_review", True)
    if review and xr:   # 검토 독립성: 이 작업의 코드를 쓴 구독(이어받기 앞뒤 모두)은 아예 뺀다
        rows_ = [r for r in routing.read_dispatch(repo, set(tasks))
                 if r["kit_id"] == tid and r["mode"] in ("new", "retry", "handoff") and r["agent"]]
        impl = {r["agent"] for r in rows_}
        impl_keys = {routing.key_of_row(r, catalog["models"]) for r in rows_} - {None}
    ex_keys = ({q["key"]} if filt and q.get("key") else set())
    ex_agents = impl
    if review and str(xr).lower() == "model":   # 대체: 같은 구독이라도 구현한 모델만 아니면 검토할 수 있다
        ex_agents, ex_keys = set(), ex_keys | impl_keys
    dec = routing.route(repo, cfg, tid, tasks, "review" if review else "handoff", live_dids=live, catalog=catalog, pol=pol, quota=quota,
                        need_minutes=need, exclude_agents=ex_agents, exclude_keys=ex_keys or None)
    if not dec.get("key"):
        ex = dec.get("excluded", {}).values()
        tight = sorted({v for v in ex if v.startswith("넘겨받기엔 빠듯")})
        indep = any(v.startswith("검토 독립성") for v in ex)
        if tight:
            why = "넘겨받을 구독도 곧 다시 찬다: " + "; ".join(tight)[:200]
        elif indep:
            others = sorted({f"{k.split('-')[0]}: {v[:60]}" for k, v in dec.get("excluded", {}).items() if not v.startswith("검토 독립성")})
            why = f"검토 독립성: 구현한 구독({', '.join(sorted(impl))}) 밖에 넘겨받을 후보가 없다" + (f" (나머지: {'; '.join(others)[:240]})" if others else "")
        else:
            why = "다른 구독도 한도" if dec.get("quota_blocked") else "다른 후보 없음"
        return "대기", f"{win} · {why}", dec, review
    return "전환", f"{win} → {routing.describe(dec)}", dec, review


def main(argv=None):
    ap = argparse.ArgumentParser(description="한도 전환")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--due", action="store_true", help="할 일이 있으면 0, 없으면 1 (아무것도 바꾸지 않는다)")
    ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    os.chdir(repo)
    cfg = kitlib.load_config(repo, a.config)
    _, tasks = kitlib.all_tasks(repo, cfg)
    catalog, pol = routing.load(repo)
    quota = routing.read_quota(repo, refresh=True)
    s = status.collect(repo, cfg, argparse.Namespace(no_orca=False))
    o = s.get("orca") or {}
    if o.get("error"):
        print(f"Orca 워커 목록을 읽지 못했다: {o['error']}", file=sys.stderr)
        return 1
    stalls = status.quota_stalls(repo, catalog, pol, o.get("workers") or [], quota=quota)
    rcat = routing.resolve_catalog(catalog, routing.discover(repo, catalog))
    for w in s.get("turn_stops") or []:   # 필터 중단: 한도 정지와 같은 꼴로
        key = w.get("key") or (routing.key_of_row({"agent": w.get("agent", ""), "model": w.get("model_id", "")}, rcat["models"]) or "")
        stalls.append({"dispatch": w["dispatch"], "kit_id": w.get("kit_id"), "title": w.get("title"), "mode": w.get("mode") or "new",
                       "agent": w.get("agent", ""), "key": key, "workspace": w.get("workspace"), "windows": [], "resets": None,
                       "kind": "filter", "evidence": w.get("evidence", ""), "state": w.get("state"), "liveness": w.get("liveness")})
    if a.ids:
        stalls = [x for x in stalls if x["kit_id"] in a.ids]
    live = {w["dispatch"] for w in o.get("workers") or []}
    waits = read_waits(repo)
    due = due_waits(waits, stalls)
    if a.ids:
        due = [d for d in due if waits[d].get("kit_id") in a.ids]
    if a.due:
        print(f"한도 정지 {len(stalls)} · 재개할 대기 {len(due)}")
        return 0 if (stalls or due) else 1
    if due:
        resume(repo, waits, due, live, a.dry_run)
    if not stalls:
        if not a.dry_run:
            write_waits(repo, waits)
        print("한도 정지·필터 중단 워커 없음" + (f" · 대기 {len(waits)}건(다음 풀림 {routing._hm(min(w['resets'] for w in waits.values()))})"
                                        if waits and all(w.get("resets") for w in waits.values()) else ""))
        return 0
    rc = 0
    for q in stalls:
        tid = q["kit_id"]
        who = f"{tid or q.get('title')} ({q['dispatch']}, {q['key'] or q['agent']})"
        act, why, dec, review = decide(repo, cfg, tasks, catalog, pol, q, quota, live)
        print(f"[{act}] {who}: {why}")
        if act != "전환":
            if q.get("resets"):
                waits[q["dispatch"]] = {"kit_id": tid, "title": q.get("title"), "resets": q["resets"], "reason": why[:200],
                                        "since": waits.get(q["dispatch"], {}).get("since") or time.time()}
                print(f"  재개 예약: {routing._hm(q['resets'])}에 풀리면 이 도구가 다음에 돌 때 이어서 하라고 보낸다")
            continue
        waits.pop(q["dispatch"], None)
        ws = q.get("workspace") or ""
        if not ws or not Path(ws).is_dir():
            print(f"  보류: 워커 워크트리를 찾지 못했다({ws or '없음'}) — 손으로 처리", file=sys.stderr)
            rc = 1
            continue
        t = tasks[tid]
        n = switch_count(repo, tid) + 1
        rv = re.match(r".+?-RV-(.+)$", (q.get("title") or "").split()[0]) if review else None
        suffix = (f"{rv.group(1)}-h{n}" if rv else f"h{n}") if review else f"h{n}"
        base = str(t.get("base") or "")
        filt = q.get("kind") == "filter"
        reason = f"안전 필터: {q.get('evidence', '')[:120]}" if filt else ", ".join(f"{lab} {u:.0f}%" for lab, u, _ in q["windows"])
        steps = [f"worker-stop --dispatch {q['dispatch']}", f"보존: wip/{tid}-quota-<시각> (작업 트리 스냅샷)",
                 f"worker-release --dispatch {q['dispatch']}",
                 f"launch.sh --handoff <메모> --suffix {suffix} --worktree path:{ws}" + (" --review" if review else "") + f" {tid}"]
        if a.dry_run:
            for st in steps:
                print(f"  (dry-run) {st}")
            continue
        stopped = str(q.get("state")) in kitlib.SETTLED_WORKER_STATES or str(q.get("liveness")) == "exited"
        if stopped:
            print(f"  워커는 이미 멈췄다({q.get('state')}/{q.get('liveness')}) — worker-stop 생략")
        else:
            ok, err = orca("orchestration", "worker-stop", "--dispatch", q["dispatch"])
            if not ok:
                print(f"  worker-stop 실패: {err} — 전환하지 않는다(워커가 아직 돌 수 있다)", file=sys.stderr)
                rc = 1
                continue
        try:
            ref = next((r for r in (base, f"origin/{base}") if base and git(ws, "rev-parse", "-q", "--verify", r, check=False)), "")
            snap = snapshot(ws, tid, ref, reason, q["dispatch"])
        except RuntimeError as e:
            print(f"  보존 실패: {e} — 워커는 멈췄고 작업 트리는 그대로다. 손으로 보존한 뒤 launch.sh --handoff", file=sys.stderr)
            rc = 1
            continue
        print(f"  보존: {snap['branch']} ({snap['commit']}, 기준 이후 커밋 {snap['commits'] or '?'}개, 커밋하지 않은 파일 {len(snap['dirty_files'])}개)")
        ok, err = orca("orchestration", "worker-release", "--dispatch", q["dispatch"])
        if not ok:
            print(f"  참고: worker-release 실패({err}). 터미널은 나중에 정리", file=sys.stderr)
        hd = routing.state_dir(repo) / "handoff"
        hd.mkdir(parents=True, exist_ok=True)
        notes = hd / f"{tid}-{suffix}.md"
        prev = prior_notes(repo, cfg, q.get("title"), tid)
        lines = [f"- 멈춘 워커: {q['key'] or q['agent']} (dispatch {q['dispatch']}) — " + (reason if filt else f"사용량 한도 {reason}"),
                 f"- 보존 브랜치: `{snap['branch']}` (멈춘 순간 스냅샷 {snap['commit']})",
                 f"- 기준(`{base or '?'}`) 이후 커밋 {snap['commits'] or '?'}개" + (":\n" + "\n".join(f"  - {x}" for x in snap["log"].splitlines()) if snap["log"] else ""),
                 f"- 커밋하지 않은 파일 {len(snap['dirty_files'])}개" + (":\n" + "\n".join(f"  - `{x}`" for x in snap["dirty_files"][:30]) if snap["dirty_files"] else "")]
        if filt:
            lines.append("- 앞 워커의 응답이 위 문구로 끊겼다(같은 모델의 다른 작업은 정상일 수 있다). 같은 입력·표현을 그대로 되풀이하지 말고, "
                         "경계·악성 입력이 필요한 시험은 검증 목적을 밝히고 최소한으로 쓴다. 다시 끊기면 그 지점과 문구를 보고에 적는다.")
        if prev:
            lines.append(f"\n### 지적\n{prev}")
        notes.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with kitlib.locked(repo), open(routing.state_dir(repo) / "quota_switch.tsv", "a", encoding="utf-8") as f:
            f.write("\t".join([dt.datetime.now().strftime("%F %T"), tid, q["dispatch"], q["agent"], q["key"], reason.replace("\t", " "),
                               snap["branch"], suffix, "filter" if filt else "quota"]) + "\n")
        kitlib.log_event(repo, "switch", tid, did=q["dispatch"], agent=q["agent"], key=q["key"], stop="filter" if filt else "quota",
                         reason=reason, wip=snap["branch"], suffix=suffix)
        # 고른 구독을 그대로 넘긴다(kit.yaml review.agent 같은 고정이 한도 찬 구독을 다시 고르지 않게)
        pick = ["--agent", dec["agent"]] + (["--model", str(dec["model"])] if dec.get("model") else []) \
            + (["--effort", str(dec["effort"])] if dec.get("effort") else [])
        cmd = [sys.executable, str(TOOLS / "launch.py"), "--handoff", str(notes), "--suffix", suffix, "--worktree", f"path:{ws}"] \
            + pick + (["--review"] if review else []) + [tid]
        p = subprocess.run(cmd, capture_output=True, text=True)
        for ln in (p.stdout + p.stderr).strip().splitlines():
            print("  " + ln)
        if p.returncode != 0:
            print(f"  이어받기 투입 실패. 진행분은 {ws}와 {snap['branch']}에 있다. 원인을 고친 뒤: "
                  f"orchestration/tools/launch.sh --handoff {notes.relative_to(repo)} --suffix {suffix} --worktree path:{ws}"
                  + (" --review" if review else "") + f" {tid}", file=sys.stderr)
            rc = 1
    if not a.dry_run:
        write_waits(repo, waits)
    return rc


if __name__ == "__main__":
    sys.exit(main())
