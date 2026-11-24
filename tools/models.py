#!/usr/bin/env python3
"""모델 자동 라우팅 도구 (정책: orchestration/models.yaml, 카탈로그: orchestration/tools/model_catalog.yaml).

  python3 orchestration/tools/models.py discover            # 쓸 수 있는 구독·모델 확인 (CLI 모델 목록을 다시 읽는다)
  python3 orchestration/tools/models.py route <ID> [...]    # 그 작업을 누구에게 줄지와 이유 (--review/--retry 모드)
  python3 orchestration/tools/models.py plan                # 투입 가능 작업 전체의 라우팅 미리보기
  python3 orchestration/tools/models.py stats               # 모델별 실적(학습 보정)·에이전트별 사용량
  python3 orchestration/tools/models.py cooldown <에이전트|키> [--min 90] [--reason ..]   # 사용량 한도 → 잠시 빼기
  python3 orchestration/tools/models.py cooldown --clear <에이전트|키>
  python3 orchestration/tools/models.py feedback <ID> pass|minor|major|fail [--note ..]   # 검토 판정을 실적에 반영

launch.sh는 작업에 agent/model이 없으면 이 라우팅으로 고른다(--no-route로 끔).
"""
import argparse
import json
import sys
import time
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import routing  # noqa: E402

VERDICT = {"pass": 1.0, "minor": 0.7, "major": 0.3, "fail": 0.0}


def ctx(a):
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo)
    catalog, pol = routing.load(repo)
    return repo, cfg, catalog, pol


def cmd_discover(a):
    repo, cfg, catalog, pol = ctx(a)
    disc = routing.discover(repo, catalog, refresh=True)
    print(f"# 구독(에이전트) — routing.agents: {routing.rcfg(pol, 'agents', '모두')}")
    for ag, info in disc.items():
        ms = info.get("models")
        st = "CLI 없음" if not info.get("bin") else (f"모델 {len(ms)}개" if ms is not None else "모델 목록 없음(별칭을 믿는다)")
        conf = " · snap 격리(워커로 못 씀)" if info.get("confined") else ""
        print(f"- {ag}: {st}{conf}" + (f" · {info['error']}" if info.get("error") else ""))
    print("\n# 카탈로그 모델")
    known = set()
    for key, m in catalog["models"].items():
        ok, why = routing.availability(key, m, pol, disc)
        known.update(routing.model_ids(m))
        print(f"- {'✓' if ok else '✗'} {key:18} {m.get('agent'):11} {m.get('model'):26} q{m.get('quality')} s{m.get('speed')} c{m.get('cost')}"
              + ("" if ok else f"  ({why})"))
    extra = {ag: sorted(set(info.get("models") or []) - known) for ag, info in disc.items()}
    extra = {k: v for k, v in extra.items() if v}
    if extra:
        print("\n# 카탈로그에 없는 모델 (쓰려면 models.yaml models:에 넣는다)")
        for ag, v in extra.items():
            print(f"- {ag}: {', '.join(v[:12])}" + (" …" if len(v) > 12 else ""))
    if not pol.get("_present"):
        print("\n참고: orchestration/models.yaml이 없어 launch.sh는 라우팅을 쓰지 않는다")
    return 0


def print_decision(tid, dec, verbose=True):
    print(f"[{tid}] {routing.describe(dec)}")
    if not verbose:
        return
    if dec.get("reasons"):
        print("  난이도 근거: " + " · ".join(dec["reasons"]))
    if dec.get("why"):
        print(f"  선택 근거: 점수 {dec.get('score')} · " + " · ".join(dec["why"]))
    if dec.get("alts"):
        print("  다음 후보: " + ", ".join(f"{k}({s})" for k, s in dec["alts"]))
    if dec.get("excluded"):
        print("  제외: " + "; ".join(f"{k} {v}" for k, v in list(dec["excluded"].items())[:8]))


def cmd_route(a):
    repo, cfg, catalog, pol = ctx(a)
    _, tasks = kitlib.all_tasks(repo, cfg)
    mode = "review" if a.review else ("retry" if a.retry else "new")
    disc = routing.discover(repo, catalog)
    rc = 0
    for tid in a.ids:
        if tid not in tasks:
            print(f"알 수 없는 작업 {tid}", file=sys.stderr)
            rc = 1
            continue
        dec = routing.route(repo, cfg, tid, tasks, mode, catalog=catalog, pol=pol, disc=disc)
        if a.json:
            print(json.dumps({"id": tid, **dec}, ensure_ascii=False))
        else:
            print_decision(tid, dec)
    return rc


def cmd_plan(a):
    repo, cfg, catalog, pol = ctx(a)
    _, tasks = kitlib.all_tasks(repo, cfg)
    disc = routing.discover(repo, catalog)
    ready = [i for i, t in tasks.items() if kitlib.is_ready(t, tasks, cfg)]
    if not ready:
        print("투입 가능 작업 없음")
        return 0
    tally = {}
    for tid in ready:
        dec = routing.route(repo, cfg, tid, tasks, catalog=catalog, pol=pol, disc=disc, extra_running=tally)
        print_decision(tid, dec, verbose=a.verbose)
        if dec.get("agent"):
            tally[dec["agent"]] = tally.get(dec["agent"], 0) + 1
    print("\n배분: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    print("(앞 작업을 띄웠다고 치고 다음 작업의 부하에 더한 미리보기다. 실제 투입은 띄울 때마다 다시 계산한다)")
    return 0


def cmd_stats(a):
    repo, cfg, catalog, pol = ctx(a)
    _, tasks = kitlib.all_tasks(repo, cfg)
    rows = routing.read_dispatch(repo, set(tasks))
    outs = routing.outcomes(rows, tasks, routing.read_feedback(repo), catalog["models"])
    lv = routing.learned(outs)
    cnt = {}
    for r in rows:
        k = routing.key_of_row(r, catalog["models"]) or (f"{r['agent']}:{r['model']}" if r["agent"] else "(기록 없음)")
        cnt[k] = cnt.get(k, 0) + 1
    print(f"# 투입 {len(rows)}건 · 결과가 정해진 구현 투입 {len(outs)}건")
    print("모델                 투입  낮음(1~2)     보통(3)       높음(4~5)")
    for k in sorted(set(cnt) | {o['key'] for o in outs}):
        cells = []
        for b in ("low", "mid", "high"):
            s, n = lv.get((k, b), (0, 0))
            cells.append(f"{s / n:4.2f} ({n:2d})" if n else "    -     ")
        print(f"{k:20} {cnt.get(k, 0):4}  " + "   ".join(cells))
    print("(칸: 평균 결과 점수 1=한 번에 머지, 0.6=검토 지적 뒤 머지, 0=다시 띄움. 괄호는 건수)")
    cools = routing.read_cooldowns(repo)
    if cools:
        print("\n# 쿨다운")
        for k, v in cools.items():
            print(f"- {k}: {int((v['until'] - time.time()) // 60)}분 남음" + (f" ({v.get('reason')})" if v.get("reason") else ""))
    use = routing.orca_usage(a.days)
    if use:
        print(f"\n# 구독 사용량 (Orca 집계, 모든 프로젝트 합, 출력 토큰, 최근 {a.days}일)")
        today = dt.date.today().isoformat()
        for ag, models in use.items():
            tot = sum(sum(d.values()) for d in models.values())
            td = sum(d.get(today, 0) for d in models.values())
            top = sorted(((sum(d.values()), m) for m, d in models.items()), reverse=True)[:4]
            print(f"- {ag}: 오늘 {td // 1000}k · {a.days}일 {tot // 1000}k  ("
                  + ", ".join(f"{m} {v // 1000}k" for v, m in top) + ")")
        soft = routing.rcfg(pol, "daily_soft_tokens") or {}
        if soft:
            print("  하루 기준(daily_soft_tokens): " + ", ".join(f"{k} {int(v) // 1000}k" for k, v in soft.items()))
    return 0


def cmd_cooldown(a):
    repo = kitlib.repo_root(a.repo)
    target = a.clear or a.target
    if not target:
        print("대상(에이전트나 카탈로그 키)이 필요하다", file=sys.stderr)
        return 2
    d = routing.write_cooldown(repo, target, 0 if a.clear else a.min, a.reason or "")
    left = [k + " " + str(int((v["until"] - time.time()) // 60)) + "분" for k, v in d.items()]
    print("쿨다운: " + (", ".join(left) or "없음"))
    return 0


def cmd_feedback(a):
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo)
    _, tasks = kitlib.all_tasks(repo, cfg)
    if a.id not in tasks:
        print(f"알 수 없는 작업 {a.id}", file=sys.stderr)
        return 1
    p = routing.state_dir(repo) / "route_feedback.tsv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write("\t".join([dt.datetime.now().strftime("%F %T"), a.id, str(VERDICT[a.verdict]), (a.note or "").replace("\t", " ")]) + "\n")
    print(f"{a.id}: {a.verdict} ({VERDICT[a.verdict]}) 기록 → 마지막 구현 투입의 실적에 반영")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="모델 자동 라우팅")
    ap.add_argument("--repo")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("discover")
    p = sp.add_parser("route")
    p.add_argument("ids", nargs="+")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--review", action="store_true")
    g.add_argument("--retry", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sp.add_parser("plan")
    p.add_argument("-v", "--verbose", action="store_true")
    p = sp.add_parser("stats")
    p.add_argument("--days", type=int, default=7)
    p = sp.add_parser("cooldown")
    p.add_argument("target", nargs="?")
    p.add_argument("--min", type=int, default=90)
    p.add_argument("--reason")
    p.add_argument("--clear", metavar="대상")
    p = sp.add_parser("feedback")
    p.add_argument("id")
    p.add_argument("verdict", choices=list(VERDICT))
    p.add_argument("--note")
    a = ap.parse_args(argv)
    return {"discover": cmd_discover, "route": cmd_route, "plan": cmd_plan, "stats": cmd_stats,
            "cooldown": cmd_cooldown, "feedback": cmd_feedback}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
