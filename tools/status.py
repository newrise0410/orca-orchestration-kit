#!/usr/bin/env python3
"""현황과 정지 점검 (마스터·매니저 공용, 교훈 L-07·L-13).

  python3 orchestration/tools/status.py            # 요약: DAG · 투입 가능 · 메일함 미처리 · 진행 중 워커 · 정지 점검
  python3 orchestration/tools/status.py --stall    # 정지 점검만 (깨어날 때·대기 전에)
옵션: --warnings(DAG 경고 목록) --limit N(메시지 수, 기본 10) --stale-min M(커밋 없음 기준 분, 기본 60) --no-orca --json

정지 점검 판정 (한 줄씩, 이상이 없으면 '정상'):
  attention   Orca가 워커에 주의 표시(입력 대기 프롬프트 등) → worker-show / worker-read로 원인 확인
  커밋 없음    진행 중인데 마지막 커밋이 stale-min보다 오래됨 → worker-read로 화면 끝 확인
  running 고아 tasks.yaml은 running인데 진행 중 워커가 없음 → 끝났으면 review/merged, 멈췄으면 재투입
  할당 누락    진행 중 워커 0이고 투입 가능 작업이 있음 → launch.sh (일부러 미룬 작업은 tasks.yaml에 hold: <사유>)
  대기할 것 없음 진행 중 0 · 투입 가능 0 · 미답 질문 0 → 다음 작업을 DAG에 넣거나 종료
메시지 ID를 같이 출력한다: 질문은 `orca orchestration reply --id <ID> --body ...`로 답한다.
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
import validate_dag  # noqa: E402


def orca_json(args):
    cli = os.environ.get("ORCA_CLI_COMMAND") or "orca"
    try:
        p = subprocess.run([cli, *args, "--json"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": {"message": str(e)}}
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": {"message": (p.stderr or p.stdout).strip()[:200]}}


def all_workers(run_id, pages=20):
    out, cursor = [], None
    for _ in range(pages):
        args = ["orchestration", "worker-list", "--run", run_id, "--limit", "100"] + (["--cursor", cursor] if cursor else [])
        d = orca_json(args)
        if not d.get("ok", False):
            return out, (d.get("error") or {}).get("message", "조회 실패")
        r = d.get("result") or {}
        out += r.get("workers") or []
        cursor = r.get("nextCursor")
        if not cursor:
            break
    return out, None


def ago(ts):
    if not ts:
        return "?"
    m = int((time.time() - ts) // 60)
    return f"{m}분 전" if m < 120 else f"{m // 60}시간 {m % 60}분 전"


def last_commit(path):
    p = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%ct"], capture_output=True, text=True)
    return int(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip().isdigit() else None


def kit_id_of(title, ids):
    """Orca 작업 제목 → 키트 작업 ID. 'X-01', 'X-01-RV', 'X-01 retry-r1' 꼴."""
    head = (title or "").split()[0] if title else ""
    if head in ids:
        return head
    m = re.match(r"(.+?)-RV(?:-.+)?$", head)
    return m.group(1) if m and m.group(1) in ids else None


def collect(repo, cfg, a):
    data, tasks = kitlib.all_tasks(repo, cfg)
    active_ids = [str(t["id"]) for t in data.get("tasks") or [] if isinstance(t, dict) and t.get("id") is not None]
    res = validate_dag.validate(kitlib.tasks_path(repo, cfg).read_text(encoding="utf-8"), cfg, kitlib.load_archive(repo, cfg))
    st = {}
    for k in active_ids:
        s = str(tasks[k].get("status", "todo")).lower()
        st[s] = st.get(s, 0) + 1
    out = {"project": kitlib.cfg_get(cfg, "project.name", ""), "dag": {"errors": res["errors"], "warnings": res["warnings"],
           "tasks": len(active_ids), "archived": res["stats"].get("archived", 0), "status": st},
           "ready": [i for i in active_ids if kitlib.is_ready(tasks[i], tasks, cfg)],
           "gates_ready": [i for i in active_ids if kitlib.is_gate(tasks[i], cfg)
                           and str(tasks[i].get("status", "todo")).lower() in ("todo", "ready") and not kitlib.open_deps(tasks[i], tasks)],
           "held": [(i, str(tasks[i]["hold"])) for i in active_ids if tasks[i].get("hold")
                    and str(tasks[i].get("status", "todo")).lower() in ("todo", "ready")],
           "running": [i for i in active_ids if str(tasks[i].get("status", "")).lower() == "running"],
           "orca": None}
    ip = repo / ".orca-kit/installed.json"
    if ip.is_file():
        try:
            out["installed_kit"] = json.loads(ip.read_text(encoding="utf-8")).get("kit_version")
        except json.JSONDecodeError:
            pass
    if a.no_orca:
        return out
    o = {"mail": {}, "workers": [], "settled": {}, "error": None}
    for label, rid in (("마스터", kitlib.cfg_get(cfg, "orca.master_run", "")), ("매니저(작업 Run)", kitlib.cfg_get(cfg, "orca.work_run", ""))):
        if not rid:
            continue
        mb = orca_json(["orchestration", "inbox", "--terminal", f"run:{rid}", "--limit", "100"])
        msgs = (mb.get("result") or {}).get("messages") or []
        o["mail"][label] = {"run": rid, "error": None if mb.get("ok", False) else (mb.get("error") or {}).get("message"),
                            "unhandled": [m for m in msgs if m.get("handled") is False]}
    wr = kitlib.cfg_get(cfg, "orca.work_run", "")
    if wr:
        ws, err = all_workers(wr)
        o["error"] = err
        tl = orca_json(["orchestration", "task-list", "--run", wr])
        titles = {t.get("id"): t.get("title") for t in (tl.get("result") or {}).get("tasks") or []}
        idset = set(tasks)
        for w in ws:
            state = str(w.get("state"))
            if state in kitlib.SETTLED_WORKER_STATES:
                o["settled"][state] = o["settled"].get(state, 0) + 1
                continue
            title = titles.get(w.get("taskId")) or ""
            wsp = w.get("workspace") or ""
            o["workers"].append({"dispatch": w.get("dispatchId"), "title": title, "kit_id": kit_id_of(title, idset),
                                 "state": state, "liveness": w.get("liveness"), "attention": w.get("attention") or [],
                                 "workspace": wsp, "last_commit": last_commit(wsp) if wsp and Path(wsp).is_dir() else None})
    out["orca"] = o
    # 모델 라우팅: 진행 중 워커의 구독·모델, 구독별 동시 상한, 쿨다운
    try:
        catalog, pol = routing.load(repo)
        rows = {r["did"]: r for r in routing.read_dispatch(repo, set(tasks))}
        by = {}
        for w in o["workers"]:
            r = rows.get(w["dispatch"]) or {}
            w["model"] = (r.get("key") or r.get("model") or "") if r else ""
            if r.get("agent"):
                by[r["agent"]] = by.get(r["agent"], 0) + 1
        out["models"] = {"enabled": routing.enabled(pol), "running": by,
                         "caps": routing.rcfg(pol, "max_concurrent") or {}, "cooldowns": routing.read_cooldowns(repo)}
    except Exception as e:  # noqa: BLE001 — 현황은 라우팅 설정이 깨져도 나와야 한다
        out["models"] = {"error": str(e)[:160]}
    return out


def stall_lines(s, stale_min):
    o = s.get("orca")
    if o is None:
        return ["(Orca 조회 안 함: --no-orca)"]
    lines = []
    now = time.time()
    for w in o["workers"]:
        who = f"{w['kit_id'] or w['title'] or '?'} ({w['dispatch']})"
        if w["attention"]:
            lines.append(f"attention {who}: {', '.join(map(str, w['attention']))} → worker-show/worker-read")
        elif w["last_commit"] and now - w["last_commit"] > stale_min * 60:
            lines.append(f"커밋 없음 {who}: 마지막 커밋 {ago(w['last_commit'])} → worker-read로 화면 끝 확인")
    live_ids = {w["kit_id"] for w in o["workers"]}
    orphan = [i for i in s["running"] if i not in live_ids]
    if orphan and not o.get("error"):
        lines.append(f"running 고아 {', '.join(orphan)}: 진행 중 워커 없음 → 끝났으면 review/merged, 멈췄으면 재투입")
    pending_q = sum(1 for m in o["mail"].values() for x in m["unhandled"] if x.get("type") == "question")
    if not o["workers"]:
        if s["ready"]:
            lines.append(f"할당 누락: 진행 중 0, 투입 가능 {len(s['ready'])} ({', '.join(s['ready'][:8])}) → launch.sh")
        elif s["gates_ready"]:
            lines.append(f"게이트 대기: {', '.join(s['gates_ready'])} (매니저 직접)")
        elif not pending_q:
            lines.append("대기할 것 없음: 진행 중 0 · 투입 가능 0 · 미답 질문 0 → 다음 작업을 DAG에 넣거나 종료")
    return lines or ["정상"]


def print_report(s, a):
    d = s["dag"]
    print(f"# {s['project']} 현황 ({dt.datetime.now():%Y-%m-%d %H:%M})")
    stt = ", ".join(f"{k} {v}" for k, v in d["status"].items())
    print(f"\n## DAG: 작업 {d['tasks']}" + (f" (+보관 {d['archived']})" if d["archived"] else "")
          + f" · {stt} · 오류 {len(d['errors'])} · 경고 {len(d['warnings'])}" + ("" if a.warnings or not d["warnings"] else " (--warnings로 보기)"))
    for e in d["errors"]:
        print(f"- 오류: {e}")
    if a.warnings:
        for w in d["warnings"]:
            print(f"- 경고: {w}")
    print(f"## 투입 가능: {', '.join(s['ready']) or '없음'}" + (f" · 게이트: {', '.join(s['gates_ready'])}" if s["gates_ready"] else ""))
    for i, why in s["held"]:
        print(f"- 보류 {i}: {why}")
    o = s.get("orca")
    if o is not None:
        for label, m in o["mail"].items():
            if m["error"]:
                print(f"## {label} 메일함: 조회 실패 ({m['error']})")
                continue
            un = m["unhandled"]
            print(f"## {label} 메일함 (run:{m['run']}): 미처리 {len(un)}")
            for x in un[:a.limit]:
                print(f"- [{x.get('type')}] {x.get('id')} {x.get('subject', '')} — {' '.join(str(x.get('body', '')).split())[:140]}")
        settled = " · ".join(f"{k} {v}" for k, v in sorted(o["settled"].items()))
        print(f"## 워커: 진행 {len(o['workers'])}" + (f" (끝남: {settled})" if settled else "") + (f" — 조회 실패: {o['error']}" if o["error"] else ""))
        for w in o["workers"]:
            att = f" · attention {', '.join(map(str, w['attention']))}" if w["attention"] else ""
            mdl = f" · {w['model']}" if w.get("model") else ""
            print(f"- {w['kit_id'] or w['title'] or '?'} {w['dispatch']} {w['state']}/{w['liveness']}{mdl} · 마지막 커밋 {ago(w['last_commit'])}{att}")
        md = s.get("models") or {}
        if md.get("error"):
            print(f"## 모델: 라우팅 설정 오류 ({md['error']})")
        elif md.get("enabled") or md.get("running") or md.get("cooldowns"):
            parts = [f"{ag} {md['running'].get(ag, 0)}/{cap}" for ag, cap in md["caps"].items()]
            parts += [f"{ag} {n}" for ag, n in md["running"].items() if ag not in md["caps"]]
            cd = [f"{k} {int((v['until'] - time.time()) // 60)}분" for k, v in md["cooldowns"].items()]
            print("## 모델: 진행 " + (" · ".join(parts) or "0") + (f" · 쿨다운 {', '.join(cd)}" if cd else "")
                  + ("" if md.get("enabled") else " (자동 라우팅 꺼짐)"))
    print("## 정지 점검")
    for ln in stall_lines(s, a.stale_min):
        print(f"- {ln}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="현황·정지 점검")
    ap.add_argument("--repo"), ap.add_argument("--config")
    ap.add_argument("--stall", action="store_true", help="정지 점검 결과만")
    ap.add_argument("--warnings", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--stale-min", dest="stale_min", type=int, default=60)
    ap.add_argument("--no-orca", dest="no_orca", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    s = collect(repo, cfg, a)
    if a.json:
        s["stall"] = stall_lines(s, a.stale_min)
        print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    elif a.stall:
        o = s.get("orca") or {"workers": []}
        print(f"정지 점검 ({dt.datetime.now():%H:%M}): 진행 중 {len(o['workers'])} · 투입 가능 {len(s['ready'])}")
        for ln in stall_lines(s, a.stale_min):
            print(f"- {ln}")
    else:
        print_report(s, a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
