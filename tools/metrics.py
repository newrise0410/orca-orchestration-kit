#!/usr/bin/env python3
"""오케스트레이션 효과 지표 — 키트가 이미 남기는 기록만으로 센다(새로 기록하는 것 없음).

  python3 orchestration/tools/metrics.py                  # 지난 30일에 첫 투입된 작업
  python3 orchestration/tools/metrics.py --since 7d --tokens   # 세션 기록에서 작업별 토큰도(느리다)
  python3 orchestration/tools/metrics.py --json

지표
  처리 시간   첫 구현 투입 → 첫 병합('Merge req/<ID>' 커밋). 중앙값·p90.
  겹침 배수   작업 기간(첫 투입 → 병합)의 합 ÷ 작업이 하나라도 열려 있던 시간. 1이면 직렬과 같다.
              한 에이전트가 같은 일을 같은 시간에 한다고 칠 때의 속도 상한 어림이지, 단일 에이전트와 잰 값이 아니다.
  재작업      병합된 작업 중 보완(retry) 없이 끝난 비율 · 작업당 보완 수 · 다시 띄움(new 2회 이상) · 1회 검토로 통과
              · 검토 판정(route_feedback.tsv) 분포
  복구        한도 전환·필터 중단 이어받기(handoff · quota_switch.tsv)가 있었던 작업이 병합까지 갔나
  자동 전이   events.jsonl의 advance [자동]·[판단] 수 — 매니저 판단이 얼마나 줄었나(v0.12.0 이후 기록)
  토큰        --tokens: 작업별 '호출 수 × 문맥' 합계(usage.py와 같은 셈) · 검토 몫 · 시험 관련 몫

읽는 곳: state/dispatch.tsv · tasks.yaml(+보관 파일) · git log --all --merges · state/quota_switch.tsv ·
state/route_feedback.tsv · state/events.jsonl · 세션 기록(--tokens).
"""
import argparse
import datetime as dt
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import routing  # noqa: E402
import usage  # noqa: E402

MERGE_RX = re.compile(r"Merge (?:branch ')?(?:req/)?([A-Za-z0-9][A-Za-z0-9._-]*)")
COMMIT_RX = re.compile(r"^\[([A-Za-z0-9][A-Za-z0-9._-]*)\]")


def ts_of(s):
    """dispatch.tsv 투입 시각('%F %T' · '%F') → epoch. '(이전)' 같은 것은 None."""
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(s).strip(), f).timestamp()
        except ValueError:
            pass
    return None


def merges(repo, ids):
    """({작업 ID: 첫 병합 epoch}, {작업 ID: 마지막 '[ID]' 커밋 epoch}).
    병합 = 'Merge req/<ID>' 또는 'Merge <ID>' 커밋(int/*든 main이든 처음 들어간 때). 병합 커밋이 없는 작업은 마지막 커밋으로 어림한다."""
    p = subprocess.run(["git", "-C", str(repo), "log", "--all", "--format=%ct%x09%P%x09%s"], capture_output=True, text=True)
    mg, last = {}, {}
    for ln in (p.stdout or "").splitlines():
        t, _, rest = ln.partition("\t")
        parents, _, subj = rest.partition("\t")
        if not t.isdigit():
            continue
        t = float(t)
        if " " in parents.strip():   # 병합 커밋
            m = MERGE_RX.search(subj)
            if m and m.group(1) in ids:
                mg[m.group(1)] = min(mg.get(m.group(1), float("inf")), t)
        else:
            m = COMMIT_RX.match(subj)
            if m and m.group(1) in ids:
                last[m.group(1)] = max(last.get(m.group(1), 0), t)
    return mg, last


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def union_len(iv):
    tot, cur = 0.0, None
    for a, b in sorted(iv):
        if cur is None or a > cur[1]:
            if cur:
                tot += cur[1] - cur[0]
            cur = [a, b]
        else:
            cur[1] = max(cur[1], b)
    if cur:
        tot += cur[1] - cur[0]
    return tot


def hours(s):
    return None if s is None else round(s / 3600, 1)


def token_rows(repo, cfg, rows, since, ids):
    """{작업 ID: {total, calls, review, test}} — 지금 있는 워크트리와, 지워진 워커 워크트리(같은 부모 폴더 + dispatch.tsv 이름)."""
    wts = usage.worktrees(repo, cfg)
    parents = {str(Path(p).parent) for tid, p in wts if tid != "매니저"}
    by_name = {r["name"]: (r["kit_id"], r["mode"] == "review") for r in rows if r["kit_id"] and r["name"]}
    paths = {}   # 경로 → (작업 ID, 검토인가)
    for tid, p in wts:
        if tid != "매니저":
            base = str(tid).replace(" 검토", "")
            paths[p] = (base, str(tid).endswith(" 검토")) if base in ids else by_name.get(Path(p).name, (None, False))
    for r in rows:
        if not r["kit_id"] or not r["name"]:
            continue
        for par in parents:
            paths.setdefault(str(Path(par) / r["name"]), (r["kit_id"], r["mode"] == "review"))
    out = {}
    for p, (tid, rv) in paths.items():
        if not tid:
            continue
        for kind, f in usage.session_files(p, since):
            s = usage.summarize(kind, f, since)
            if not s:
                continue
            e = out.setdefault(tid, {"total": 0, "calls": 0, "review": 0, "test": 0})
            e["total"] += s["total"]
            e["calls"] += s["calls"]
            e["test"] += s["test"]
            if rv:
                e["review"] += s["total"]
    return out


def collect(repo, cfg, since, tokens=False):
    _, tasks = kitlib.all_tasks(repo, cfg)
    rows = routing.read_dispatch(repo, set(tasks))
    switched = routing.read_switches(repo)
    fb = routing.read_feedback(repo)
    mg, last = merges(repo, set(tasks))
    by = {}
    for r in rows:
        if r["kit_id"]:
            by.setdefault(r["kit_id"], []).append(r)
    now = time.time()
    per = {}
    for tid, rs in by.items():
        impl = [r for r in rs if r["mode"] in ("new", "retry", "handoff")]
        starts = [t for t in (ts_of(r["ts"]) for r in impl) if t]
        if not starts or min(starts) < since:
            continue
        t = tasks.get(tid) or {}
        st = str(t.get("status", "")).lower()
        start, end, approx = min(starts), mg.get(tid), False
        if not end and st == "merged" and last.get(tid, 0) >= min(starts):
            end, approx = last[tid], True
        coord = str(t.get("layer")) == "L1" or kitlib.is_gate(t, cfg)   # 통합 세션·게이트: 며칠씩 열어 둔다 → 처리 시간·겹침에서 뺀다
        n = {m: sum(1 for r in rs if r["mode"] == m) for m in ("new", "retry", "handoff", "review")}
        sw = sum(1 for r in rs if r["did"] in switched)
        per[tid] = {"status": st, "merged": bool(end) or st == "merged", "start": start, "end": end, "approx": approx, "coord": coord,
                    "lead_h": hours(end - start) if end and end >= start else None, **n,
                    "recovered": n["handoff"] + sw,
                    "verdicts": [f["score"] for f in fb if f["kit_id"] == tid]}
    merged = [p for p in per.values() if p["merged"]]
    work = [p for p in per.values() if not p["coord"]]
    leads = [p["lead_h"] for p in merged if p["lead_h"] is not None and not p["coord"]]
    # 병합 시각을 아는 작업과 아직 열린 작업만(병합됐는데 시각을 모르면 기간을 모른다)
    iv = [(p["start"], p["end"] or now) for p in work
          if (p["end"] and p["end"] >= p["start"]) or (not p["merged"] and p["status"] in ("running", "review"))]
    busy = sum(b - a for a, b in iv)
    span = union_len(iv)
    reviewed = [p for p in merged if p["review"]]
    rec = [p for p in per.values() if p["recovered"]]
    vmap = {1.0: "pass", 0.7: "minor", 0.3: "major", 0.0: "fail"}
    vd = {}
    for p in per.values():
        for v in p["verdicts"]:
            k = vmap.get(round(v, 1), str(v))
            vd[k] = vd.get(k, 0) + 1
    first_pass = [p for p in reviewed if p["retry"] == 0 and (not p["verdicts"] or p["verdicts"][0] >= 0.7)]
    ev = {"자동": 0, "판단": 0}
    evp = routing.state_dir(repo) / "events.jsonl"
    if evp.is_file():
        for ln in evp.read_text(encoding="utf-8").splitlines():
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            k = str(j.get("kind", ""))
            et = ts_of(str(j.get("ts", "")).replace("T", " ")[:19])
            if k.startswith("advance:") and (et or now) >= since:
                ev[k.split(":", 1)[1]] = ev.get(k.split(":", 1)[1], 0) + 1
    out = {
        "since": since, "tasks": len(per), "merged": len(merged),
        "open": sum(1 for p in per.values() if not p["merged"]),
        "coord": len(per) - len(work),
        "lead_h": {"n": len(leads), "approx": sum(1 for p in merged if p["approx"] and p["lead_h"] is not None and not p["coord"]), "median": statistics.median(leads) if leads else None,
                   "p90": pct(leads, 0.9), "max": max(leads) if leads else None},
        "overlap": round(busy / span, 2) if span else None,
        "busy_h": hours(busy), "span_h": hours(span),
        "rework": {"no_retry": sum(1 for p in merged if p["retry"] == 0), "merged": len(merged),
                   "retry_per_task": round(sum(p["retry"] for p in merged) / len(merged), 2) if merged else None,
                   "relaunched": sum(1 for p in per.values() if p["new"] > 1),
                   "reviewed": len(reviewed), "review_first_pass": len(first_pass),
                   "reviews_per_task": round(sum(p["review"] for p in reviewed) / len(reviewed), 2) if reviewed else None,
                   "verdicts": vd},
        "recovery": {"tasks": len(rec), "events": sum(p["recovered"] for p in rec),
                     "merged": sum(1 for p in rec if p["merged"]),
                     "open": sum(1 for p in rec if not p["merged"] and p["status"] in ("running", "review")),
                     "reset": sum(1 for p in rec if not p["merged"] and p["status"] in ("todo", "ready"))},
        "advance": ev,
        "per_task": per,
    }
    if tokens:
        tk = token_rows(repo, cfg, rows, since, set(tasks))
        mt = [tk[t]["total"] for t in tk if t in per and per[t]["merged"]]
        out["tokens"] = {"per_task": tk, "merged_median": statistics.median(mt) if mt else None,
                         "total": sum(e["total"] for e in tk.values()),
                         "review": sum(e["review"] for e in tk.values()),
                         "test": sum(e["test"] for e in tk.values())}
    return out


def ratio(a, b):
    return f"{a}/{b} ({a / b * 100:.0f}%)" if b else "-"


def render(m, label):
    L = [f"# 오케스트레이션 지표 (지난 {label} 첫 투입 · 작업 {m['tasks']}개: 병합 {m['merged']} · 열림 {m['open']})", ""]
    ld = m["lead_h"]
    L.append("## 처리 시간")
    if ld["n"]:
        L.append(f"- 첫 투입 → 첫 병합: 중앙값 {ld['median']:.1f}시간 · p90 {ld['p90']:.1f}시간 · 최대 {ld['max']:.1f}시간 (작업 {ld['n']}개"
                 + (f", 그중 {ld['approx']}개는 병합 커밋이 없어 마지막 커밋으로 어림" if ld["approx"] else "")
                 + (f" · L1·게이트 {m['coord']}개는 뺌" if m["coord"] else "") + ")")
    else:
        L.append("- 병합 시각을 아는 작업 없음('Merge req/<ID>' 커밋)")
    if m["overlap"]:
        L.append(f"- 겹침 배수 {m['overlap']} — 작업 기간 합 {m['busy_h']}시간 ÷ 열려 있던 시간 {m['span_h']}시간 "
                 "(1이면 직렬. 단일 에이전트 속도와 잰 값이 아니라 상한 어림)")
    rw = m["rework"]
    L += ["", "## 재작업"]
    L.append(f"- 보완 없이 병합: {ratio(rw['no_retry'], rw['merged'])} · 병합 작업당 보완 {rw['retry_per_task'] if rw['retry_per_task'] is not None else '-'}회")
    L.append(f"- 다시 띄움(새 투입 2회 이상): {rw['relaunched']}개")
    if rw["reviewed"]:
        L.append(f"- 검토를 거친 병합 작업 {rw['reviewed']}개: 1회 검토로 통과 {ratio(rw['review_first_pass'], rw['reviewed'])} · 작업당 검토 {rw['reviews_per_task']}회")
    if rw["verdicts"]:
        L.append("- 검토 판정: " + " · ".join(f"{k} {v}" for k, v in sorted(rw["verdicts"].items())))
    rc = m["recovery"]
    L += ["", "## 복구(한도 전환·필터 중단 이어받기)"]
    if rc["tasks"]:
        L.append(f"- 이어받기 {rc['events']}회 · 작업 {rc['tasks']}개 → 병합 {ratio(rc['merged'], rc['tasks'])} · 진행 중 {rc['open']} · 처음으로 되돌림 {rc['reset']}")
    else:
        L.append("- 이어받기 기록 없음")
    if sum(m["advance"].values()):
        L += ["", "## 자동 전이(advance)"]
        a = m["advance"]
        L.append(f"- [자동] {a.get('자동', 0)} · [판단] {a.get('판단', 0)} — 매니저가 손으로 하던 전이 중 도구가 맡은 몫 "
                 f"{ratio(a.get('자동', 0), a.get('자동', 0) + a.get('판단', 0))}")
    if "tokens" in m:
        tk = m["tokens"]
        L += ["", "## 토큰(호출 수 × 문맥)"]
        if tk["total"]:
            L.append(f"- 합계 {usage.k(tk['total'])} · 검토 몫 {tk['review'] / tk['total'] * 100:.0f}% · 시험 관련 {tk['test'] / tk['total'] * 100:.0f}%"
                     + (f" · 병합 작업당 중앙값 {usage.k(tk['merged_median'])}" if tk["merged_median"] else ""))
            top = sorted(tk["per_task"].items(), key=lambda kv: -kv[1]["total"])[:5]
            L.append("- 많이 쓴 작업: " + " · ".join(f"{t} {usage.k(e['total'])}" for t, e in top))
        else:
            L.append("- 세션 기록 없음")
    L += ["", "단일 에이전트와 비교하려면 같은 작업을 한 세션으로도 돌린 기준선이 따로 있어야 한다(키트 docs/DESIGN-NOTES.md 13장)."]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="오케스트레이션 효과 지표(처리 시간·겹침·재작업·복구·토큰)")
    ap.add_argument("--since", default="30d")
    ap.add_argument("--tokens", action="store_true", help="세션 기록에서 작업별 토큰도 센다(느리다)")
    ap.add_argument("--task", help="이 작업 하나의 줄만(--json과 함께)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    since = time.time() - usage.parse_since(a.since)
    m = collect(repo, cfg, since, a.tokens)
    if a.task:
        m["per_task"] = {k: v for k, v in m["per_task"].items() if k == a.task}
    if a.json:
        print(json.dumps(m, ensure_ascii=False, indent=1, default=str))
    else:
        print(render(m, a.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
