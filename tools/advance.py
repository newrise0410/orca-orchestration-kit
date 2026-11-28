#!/usr/bin/env python3
"""결정적 전이: worker_done 보고 → 정해진 다음 단계는 도구가 하고, 판단이 필요한 것만 매니저에게 남긴다 (교훈 L-24).

  python3 orchestration/tools/advance.py              # 작업 Run 메일함의 새 worker_done을 처리한다
  python3 orchestration/tools/advance.py --dry-run    # 할 일만 보인다(아무것도 바꾸지 않는다)
  python3 orchestration/tools/advance.py --message <메일 ID>

매니저는 worker_done이 오면 먼저 이것을 돌리고 '[판단]' 줄만 처리한다(전이마다 상태 확인·명령을 손으로 부르지 않는다).

전이 (kit.yaml flow.*, 없으면 기본값):
  구현 '결과: 통과'           → tasks.yaml review → 검토 투입(launch --review, 2회차부터 --suffix rN)      [flow.auto_review]
  검토 '판정: 통과'           → 실적 pass(M 있으면 minor) 기록 → [판단] 병합 관문(procedures/merge-gate.md)
  검토 '판정: 차단 · 계열: X'  → 실적 major + 계열 기록 → 같은 계열이 review.scope_escalate_after회 연속이면
                                tasks.yaml hold + [판단] 범위 결정, 아니면 보고서를 지적으로 보완 투입(launch --retry)  [flow.auto_retry]
  그 밖(부분·실패·형식 없음·L1·게이트·review: false) → [판단]
안전장치: 작업이 기대한 상태가 아니거나(구현 통과인데 running이 아님 등), 그 보고 뒤에 같은 작업의 투입이 또 있거나,
flow.max_age_hours(기본 24)보다 오래된 메일은 건너뛴다(처음 돌릴 때 예전 메일로 다시 투입하지 않게).
보고 형식: 구현은 본문 첫 줄 `결과: 통과|부분|실패`, 검토는 `판정: 통과|차단 · H <n> · M <n> · 계열: <이름>[, <이름>]`
(명세 '종료' 절). payload의 kit.{result, verdict, categories}가 있으면 그것을 먼저 본다.
처리한 메일 ID는 state/advance.json, 전이는 state/events.jsonl에 남는다.
"""
import argparse
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
import task_status  # noqa: E402

T = Path(__file__).resolve().parent
RES_RX = re.compile(r"결과\s*[:：]\s*(통과|부분|실패)")
VER_RX = re.compile(r"판정\s*[:：]\s*(통과|차단)")
CAT_RX = re.compile(r"계열\s*[:：]\s*([^\n·;|]+)")
H_RX = re.compile(r"(?<![A-Za-z])H\s*[:：]?\s*(\d+)")
M_RX = re.compile(r"(?<![A-Za-z])M\s*[:：]?\s*(\d+)")


def orca(args, timeout=60):
    cli = os.environ.get("ORCA_CLI_COMMAND") or "orca"
    try:
        p = subprocess.run([cli, *args, "--json"], capture_output=True, text=True, timeout=timeout)
        return json.loads(p.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"ok": False, "error": {"message": str(e)[:200]}}


def parse(m):
    """worker_done 메일 → {result, verdict, categories[], h, m, outcome, report}."""
    pl = kitlib.msg_payload(m)
    kit = pl.get("kit") if isinstance(pl.get("kit"), dict) else {}
    body = str(m.get("body") or "")
    head = "\n".join(body.splitlines()[:6])   # 형식 줄은 첫머리에 둔다(본문 속 인용에 걸리지 않게)
    res = kit.get("result") or (RES_RX.search(head).group(1) if RES_RX.search(head) else None)
    ver = kit.get("verdict") or (VER_RX.search(head).group(1) if VER_RX.search(head) else None)
    cats = kit.get("categories") or kit.get("category") or (CAT_RX.search(head).group(1) if CAT_RX.search(head) else "")
    if isinstance(cats, str):
        cats = [c.strip() for c in re.split(r"[,/]", cats) if c.strip() and c.strip() not in ("-", "없음")]
    num = lambda rx: int(rx.search(head).group(1)) if rx.search(head) else None  # noqa: E731
    return {"result": res, "verdict": ver, "categories": cats, "h": num(H_RX), "m": num(M_RX),
            "outcome": pl.get("outcome") or kit.get("outcome"), "report": pl.get("reportPath") or pl.get("report_path") or kit.get("report")}


def state_path(repo):
    return routing.state_dir(repo) / "advance.json"


def read_state(repo):
    try:
        return json.loads(state_path(repo).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def next_suffix(rows, tid, mode, repo, kind):
    """같은 작업의 그 모드 투입 수 + 1 → rN (명세 파일이 이미 있으면 그다음)."""
    n = sum(1 for r in rows if r["kit_id"] == tid and r["mode"] == mode)
    k = n + 1
    while (repo / "orchestration/specs" / (f"{tid}.review-r{k}.md" if kind == "review" else f"{tid}.retry-r{k}.md")).exists():
        k += 1
    return f"r{k}"


def report_text(repo, cfg, rid, rep_path, workspace):
    """검토 보고서 내용: 검토 워커의 워크트리 → 보고서 브랜치(git show) 순. 없으면 None."""
    ev = rep_path or str(kitlib.cfg_get(cfg, "paths.evidence", "docs/06_evidence/{id}/report.md")).replace("{id}", rid)
    for base in [workspace] if workspace else []:
        f = Path(base) / ev
        if f.is_file():
            return f.read_text(encoding="utf-8", errors="replace")
    rem = kitlib.git_remote(cfg)
    for ref in [f"req/{rid}"] + ([f"{rem}/req/{rid}"] if rem else []):
        p = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{ev}"], capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
    return None


def run_tool(args, dry):
    if dry:
        return 0, "(dry-run) " + " ".join(args)
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="worker_done → 정해진 다음 단계(판단이 필요한 것만 남긴다)")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true")
    ap.add_argument("--message", help="이 메일 하나만")
    ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    os.chdir(repo)
    cfg = kitlib.load_config(repo, a.config)
    wr = kitlib.cfg_get(cfg, "orca.work_run", "")
    if not wr:
        print("kit.yaml orca.work_run이 비어 있다", file=sys.stderr)
        return 1
    flow = lambda k, d: kitlib.cfg_get(cfg, f"flow.{k}", d)  # noqa: E731
    max_age = float(flow("max_age_hours", 24) or 24) * 3600
    mb = orca(["orchestration", "inbox", "--terminal", f"run:{wr}", "--limit", "100", "--full"])
    if not mb.get("ok"):
        print(f"메일함 조회 실패: {(mb.get('error') or {}).get('message')}", file=sys.stderr)
        return 1
    st = read_state(repo)
    done = set(st.get("processed") or [])
    _, tasks = kitlib.all_tasks(repo, cfg)
    rows = routing.read_dispatch(repo, set(tasks))
    by_did = {r["did"]: r for r in rows}
    ws = {}
    try:
        import status
        for w in status.all_workers(wr)[0]:
            ws[w.get("dispatchId")] = kitlib.workspace_path(w.get("workspace"))
    except Exception:  # noqa: BLE001
        pass
    msgs = [m for m in (mb.get("result") or {}).get("messages") or [] if m.get("type") == "worker_done"]
    if a.message:
        msgs = [m for m in msgs if m.get("id") == a.message]
    out, now = [], time.time()
    tpath = kitlib.tasks_path(repo, cfg)

    def note(tag, tid, text, **ev):
        out.append(f"[{tag}] {tid}: {text}")
        if not a.dry_run and tag != "건너뜀":
            kitlib.log_event(repo, "advance:" + tag, tid, text=text, **ev)

    for m in sorted(msgs, key=lambda x: str(x.get("createdAt") or x.get("id"))):
        mid = m.get("id")
        if mid in done and not a.message:
            continue
        pl = kitlib.msg_payload(m)
        did = pl.get("dispatchId")
        r = by_did.get(did)
        ts = float(m.get("createdAt") or 0) / (1000 if float(m.get("createdAt") or 0) > 1e11 else 1)
        mark = lambda: done.add(mid)  # noqa: E731
        if ts and now - ts > max_age:
            mark()
            continue
        if not r or not r["kit_id"]:
            note("판단", did or mid, "dispatch.tsv에 없는 투입의 보고 → 직접 본다", message=mid)
            mark()
            continue
        tid, mode = r["kit_id"], r["mode"]
        t = tasks.get(tid) or {}
        later = [x for x in rows if x["kit_id"] == tid and x["ts"] > r["ts"]]
        if later:
            note("건너뜀", tid, f"이 보고({did}) 뒤에 같은 작업 투입이 또 있다({later[-1]['did']})")
            mark()
            continue
        p = parse(m)
        stat = str(t.get("status", "")).lower()
        if mode == "review":
            if stat != "review":
                note("건너뜀", tid, f"검토 보고인데 작업 status가 {stat or '?'}(review 아님)")
                mark()
                continue
            if p["verdict"] == "통과":
                verdict = "minor" if (p["m"] or 0) > 0 else "pass"
                rc, o = run_tool([sys.executable, str(T / "models.py"), "feedback", tid, verdict, "--note", f"advance: 검토 {r['title']} 통과"], a.dry_run)
                note("판단", tid, f"검토 통과(실적 {verdict}) → 병합 관문(procedures/merge-gate.md): 보고서 경로 가져오기 → build → 전체 시험 → 스모크",
                     message=mid, did=did, verdict=verdict)
            elif p["verdict"] == "차단":
                cats = p["categories"]
                verdict = "major"
                args = [sys.executable, str(T / "models.py"), "feedback", tid, verdict, "--note", f"advance: 검토 {r['title']} 차단 H {p['h'] if p['h'] is not None else '?'}"]
                if cats:
                    args += ["--category", cats[0]]
                rc, o = run_tool(args, a.dry_run)
                import status
                flag = None
                if not a.dry_run:
                    flag = next((f for f in status.scope_flags(repo, cfg, kitlib.all_tasks(repo, cfg)[1]) if f[0] == tid), None)
                if flag and flag[1] == "범위 결정 필요":
                    reason = f"범위 결정 필요: 계열 {flag[3]} {flag[2]}회 연속(advance)"
                    if not a.dry_run:
                        with kitlib.locked(repo):
                            tpath.write_text(task_status.set_field(tpath.read_text(encoding="utf-8"), tid, "hold", json.dumps(reason, ensure_ascii=False)),
                                             encoding="utf-8")
                    note("판단", tid, f"{reason} → hold로 묶었다. 보완을 또 내지 말고 범위 선택지(축소·기본 차단·범위 밖)를 마스터에 올린다(procedures/review.md 3)",
                         message=mid, did=did, category=flag[3])
                elif not cats:
                    note("판단", tid, "검토 차단인데 '계열:'이 없다 → 계열을 정해 models.py feedback --category로 고치고 보완을 낸다", message=mid, did=did)
                elif not flow("auto_retry", True):
                    note("판단", tid, f"검토 차단(계열 {', '.join(cats)}) → 보완 투입(flow.auto_retry 꺼짐)", message=mid, did=did)
                else:
                    rid = r["title"].split()[0]
                    txt = report_text(repo, cfg, rid, p["report"], ws.get(did))
                    if not txt:
                        note("판단", tid, f"검토 차단인데 보고서({rid})를 찾지 못했다 → 지적을 정리해 launch --retry", message=mid, did=did)
                    else:
                        nf = routing.state_dir(repo) / "advance" / f"{rid}.report.md"
                        sfx = next_suffix(rows, tid, "retry", repo, "retry")
                        if not a.dry_run:
                            nf.parent.mkdir(parents=True, exist_ok=True)
                            nf.write_text(txt, encoding="utf-8")
                        rc, o = run_tool([str(T / "launch.sh"), "--retry", str(nf.relative_to(repo)), "--suffix", sfx, tid], a.dry_run)
                        if rc == 0 and not a.dry_run:   # 보완이 도는 동안은 running(그 보고가 오면 다시 review로)
                            with kitlib.locked(repo):
                                tpath.write_text(task_status.set_status(tpath.read_text(encoding="utf-8"), tid, "running"), encoding="utf-8")
                        if rc == 0:
                            note("자동", tid, f"검토 차단(계열 {', '.join(cats)}, H {p['h'] if p['h'] is not None else '?'}) → 실적 major 기록 · 보완 투입 --suffix {sfx}",
                                 message=mid, did=did, category=cats[0], suffix=sfx)
                        else:
                            note("판단", tid, f"보완 투입 실패(실적 major는 기록됨, status review 그대로) → failedStage 확인 뒤 "
                                 f"tools/launch.sh --retry {nf.relative_to(repo)} --suffix {sfx} {tid}: {o.splitlines()[-1] if o else '?'}", message=mid, did=did)
            else:
                note("판단", tid, "검토 보고에 '판정: 통과|차단'이 없다 → 보고서를 직접 본다", message=mid, did=did)
            mark()
            continue
        # 구현(new·retry·handoff)
        if str(t.get("layer")) == "L1" or kitlib.is_gate(t, cfg):
            note("판단", tid, f"L1·게이트 보고(결과 {p['result'] or '?'}) → 직접 처리", message=mid, did=did)
        elif stat != "running" and not (mode in ("retry", "handoff") and stat == "review"):
            note("건너뜀", tid, f"구현 보고인데 작업 status가 {stat or '?'}(running 아님)")
        elif p["result"] == "통과" and str(p["outcome"] or "succeeded") != "failed":
            if t.get("review") is False or not flow("auto_review", True):
                note("판단", tid, "구현 통과 → 검토 없이 가는 작업(review: false 또는 flow.auto_review 꺼짐): 병합 관문", message=mid, did=did)
            else:
                prior = sum(1 for x in rows if x["kit_id"] == tid and x["mode"] == "review")
                args = [str(T / "launch.sh"), "--review"] + (["--suffix", next_suffix(rows, tid, "review", repo, "review")] if prior else []) + [tid]
                rc, o = run_tool(args, a.dry_run)
                # status는 투입이 된 뒤에 바꾼다. launch가 실패로 끝나도 dispatch.tsv에 줄이 생겼으면 검토 워커는 떴다.
                # 실패면 status는 그대로(running) — 메일은 처리한 것으로 두고 다시 자동 투입하지 않는다(launch: 다시 실행 전에 failedStage 확인).
                dids = {x["did"] for x in rows}
                fresh = [x for x in routing.read_dispatch(repo, set(tasks)) if x["kit_id"] == tid and x["mode"] == "review" and x["did"] not in dids]
                if not a.dry_run and (rc == 0 or fresh):
                    with kitlib.locked(repo):
                        tpath.write_text(task_status.set_status(tpath.read_text(encoding="utf-8"), tid, "review"), encoding="utf-8")
                why = o.splitlines()[-1] if o else "?"
                if rc == 0:
                    note("자동", tid, f"구현 통과 → status review · 검토 투입" + (f" {prior + 1}회차" if prior else ""), message=mid, did=did)
                elif fresh:
                    note("판단", tid, f"구현 통과 → 검토 워커({fresh[-1]['did']})는 떴지만 launch가 실패로 끝났다(status review) → 워커 상태 확인: {why}",
                         message=mid, did=did)
                else:
                    note("판단", tid, f"구현 통과 → 검토 투입 실패, status는 {stat} 그대로 → failedStage 확인 뒤 {' '.join(['tools/launch.sh'] + args[1:])}: {why}",
                         message=mid, did=did)
        elif p["result"] == "부분":
            note("판단", tid, "부분 완료 → 보고의 남은 목록으로 다음 차수(<ID>a/b)를 낸다", message=mid, did=did)
        else:
            note("판단", tid, f"구현 결과 {p['result'] or '형식 없음'}(outcome {p['outcome'] or '?'}) → 원인을 보고 보완·재투입·질문", message=mid, did=did)
        mark()
    if not a.dry_run:
        st["processed"] = sorted(done)[-1000:]
        st["last_run"] = now
        p_ = state_path(repo)
        p_.parent.mkdir(parents=True, exist_ok=True)
        p_.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    if not out:
        print("새 worker_done 없음")
        return 0
    for ln in out:
        print(ln)
    n = sum(1 for ln in out if ln.startswith("[판단]"))
    print(f"\n자동 {sum(1 for ln in out if ln.startswith('[자동]'))} · 판단 {n} · 건너뜀 {sum(1 for ln in out if ln.startswith('[건너뜀]'))}"
          + (" — '[판단]' 줄만 처리한다" if n else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
