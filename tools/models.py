#!/usr/bin/env python3
"""모델 자동 라우팅 도구 (정책: orchestration/models.yaml, 카탈로그: orchestration/tools/model_catalog.yaml).

  python3 orchestration/tools/models.py discover            # 쓸 수 있는 구독·모델 확인 (CLI 모델 목록을 다시 읽는다)
  python3 orchestration/tools/models.py update [에이전트..] [--check]   # 구독 CLI 업데이트 → 모델 목록·카탈로그 계열 최신화
  python3 orchestration/tools/models.py bench [--aa]        # 공개 벤치마크(Terminal-Bench, 선택 AA) → 품질 근거 갱신·비교
  python3 orchestration/tools/models.py quota               # 구독별 잔여 한도(Orca 집계)와 라우팅 영향(소진 제외·빠듯 벌점)
  python3 orchestration/tools/models.py route <ID> [...]    # 그 작업을 누구에게 줄지와 이유 (--review/--retry 모드)
  python3 orchestration/tools/models.py plan                # 투입 가능 작업 전체의 라우팅 미리보기
  python3 orchestration/tools/models.py stats               # 모델별 실적(학습 보정)·에이전트별 사용량
  python3 orchestration/tools/models.py cooldown <에이전트|키> [--min 90] [--reason ..]   # 사용량 한도 → 잠시 빼기
  python3 orchestration/tools/models.py cooldown --clear <에이전트|키>
  python3 orchestration/tools/models.py feedback <ID> pass|minor|major|fail [--note ..] [--category 계열]   # 검토 판정을 실적에 반영

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
import bench  # noqa: E402

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
    rcat = routing.resolve_catalog(catalog, disc)
    for key, m in rcat["models"].items():
        ok, why = routing.availability(key, m, pol, disc)
        fam = f"  (계열 {m['family']} → {m.get('family_version')}판" + (f", 카탈로그 {m['resolved_from']}" if m.get("resolved_from") else "") + ")" \
            if m.get("family_version") else ""
        print(f"- {'✓' if ok else '✗'} {key:18} {m.get('agent'):11} {m.get('model'):26} q{m.get('quality')} s{m.get('speed')} c{m.get('cost')}"
              + (fam if ok else f"  ({why})"))
        if ok and m.get("quality_src") and a.__dict__.get("verbose", True):
            print(f"      품질 근거: {m['quality_src']}" + (f" (카탈로그 추정 {m['quality_catalog']})" if m.get("quality_catalog") is not None else ""))
    extra = routing.unmatched_models(rcat, disc)
    news = {ag: v[0] for ag, v in extra.items() if v[0]}
    if news:
        print("\n# 카탈로그에 없는 모델 (새 계열일 수 있다. 쓰려면 models.yaml models:에 넣는다)")
        for ag, v in news.items():
            print(f"- {ag}: {', '.join(v[:12])}" + (" …" if len(v) > 12 else ""))
    olds = sum(v[1] for v in extra.values())
    if olds:
        print(f"(아는 계열의 옛 판 {olds}개는 뺐다 — 계열은 가장 높은 판을 쓴다)")
    b = catalog.get("bench") or {}
    if b.get("tb"):
        src = (b.get("sources") or {}).get("tb") or {}
        print(f"\n벤치 점수: {src.get('name')} · {b.get('generated')} 받음 · {b.get('_path')} (갱신: models.py bench)")
    else:
        print("\n벤치 점수 없음: 품질은 카탈로그 추정값이다 (models.py bench로 받는다)")
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
    if dec.get("quality_src"):
        print(f"  품질 {dec.get('quality')}: {dec['quality_src']}")
    if dec.get("alts"):
        print("  다음 후보: " + ", ".join(f"{k}({s})" for k, s in dec["alts"]))
    if dec.get("excluded"):
        print("  제외: " + "; ".join(f"{k} {v}" for k, v in list(dec["excluded"].items())[:8]))


def cmd_update(a):
    """구독 CLI 업데이트 → 모델 목록 다시 읽기 → 카탈로그와 대조."""
    repo, cfg, catalog, pol = ctx(a)
    print("# 구독 CLI " + ("버전 확인" if a.check else "업데이트"))
    res = routing.update_clis(repo, catalog, pol, agents=a.agents or None, check_only=a.check,
                              log=(lambda m: None) if a.quiet else (lambda m: print(m, flush=True)))
    for ag, r in res.items():
        ver = r.get("before") or ""
        if r.get("status") == "갱신":
            ver = f"{r['before']} → {r['after']}"
        print(f"- {ag}: {r['status']}" + (f" {ver}" if ver else "") + (f" · {r['detail']}" if r.get("detail") else ""))
    print()
    return cmd_discover(a)


def cmd_bench(a):
    """공개 벤치마크(Terminal-Bench, 선택 AA)를 받아 bench_scores.yaml로 남기고 카탈로그 품질과 비교한다."""
    import os
    repo, cfg, catalog, pol = ctx(a)
    url = bench.TB_URL.format(ver=a.tb_version)
    try:
        html = Path(a.tb_file).read_text(encoding="utf-8") if a.tb_file else bench.http_get(url)
    except OSError as e:
        print(f"Terminal-Bench를 받지 못했다: {e}", file=sys.stderr)
        return 1
    aa = None
    if a.aa:
        key = os.environ.get("AA_API_KEY")
        if not key:
            print("AA_API_KEY가 없어 Artificial Analysis는 건너뛴다 (무료 키: artificialanalysis.ai Insights Platform)", file=sys.stderr)
        else:
            try:
                aa = bench.fetch_aa(key)
            except (OSError, ValueError) as e:
                print(f"Artificial Analysis 실패(건너뜀): {str(e)[:160]}", file=sys.stderr)
    doc = bench.build(catalog, html, url, aa=aa)
    if not doc["tb"]:
        print(f"Terminal-Bench 페이지에서 카탈로그에 맞는 줄을 찾지 못했다(전체 {doc['sources']['tb']['rows_total']}줄). 페이지 꼴이 바뀌었을 수 있다", file=sys.stderr)
        return 1
    out = Path(a.out) if a.out else routing.state_dir(repo) / "bench_scores.yaml"
    head = ("# 공개 벤치마크 점수 (models.py bench가 만든다. 손으로 고치지 말고 다시 받는다)\n"
            "# 출처: Terminal-Bench " + url + (" · Artificial Analysis https://artificialanalysis.ai/" if aa is not None else "") + "\n")
    if not a.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(head + kitlib.yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
    print(f"# {doc['sources']['tb']['name']} · 전체 {doc['sources']['tb']['rows_total']}줄 중 카탈로그에 맞는 {len(doc['tb'])}줄"
          + (f" · AA {len(doc.get('aa') or [])}개" if aa is not None else "") + ("" if a.dry_run else f" → {out}"))
    cat2 = dict(catalog, bench=doc)
    rcat = routing.resolve_catalog(cat2, routing.discover(repo, catalog))
    print("키                  모델                      추정 → 벤치  근거")
    for k, m in rcat["models"].items():
        qc = m.get("quality_catalog")
        print(f"{k:19} {str(m.get('model')):25} {('' if qc is None else str(qc)):>4} → {m.get('quality')!s:4}  {m.get('quality_src', '')}")
    return 0


def cmd_quota(a):
    """구독별 잔여 한도(Orca 집계)와 그것이 라우팅에 주는 영향."""
    repo, cfg, catalog, pol = ctx(a)
    q = routing.read_quota(repo, refresh=True)
    if not q:
        print("Orca에서 한도를 읽지 못했다(orca account list --json). 라우팅은 한도를 보지 않고 고른다")
        return 1
    now = time.time()
    print("# 잔여 한도 (Orca 집계 · 사용률 · 초기화 시각)")
    for ag, spec in catalog["agents"].items():
        info = q.get(spec.get("quota") or ag)
        if not info:
            print(f"- {ag}: 정보 없음")
            continue
        if info.get("status") != "ok":
            print(f"- {ag}: {info.get('status')}")
            continue
        def fmt(ws):
            return ", ".join(f"{n} {w['used']:.0f}%" + (f"({routing._hm(w['resets'])})" if w['resets'] > now else "") for n, w in sorted(ws.items()))
        line = f"- {ag} ({info.get('plan') or '-'}): " + (fmt(info.get("windows") or {}) or "창 없음")
        for g, ws in (info.get("groups") or {}).items():
            line += f" · [{g}] " + fmt(ws)
        if info.get("alt"):
            line += f" · 다른 계정 {len(info['alt'])}개: " + " / ".join(fmt(x) for x in info["alt"])
        print(line)
    rcat = routing.resolve_catalog(catalog, routing.discover(repo, catalog))
    print("\n# 라우팅 영향")
    for k, m in rcat["models"].items():
        ex, adj, why = routing.quota_effect(m, rcat["agents"], q, pol)
        if ex or adj:
            print(f"- {k:18} " + (f"제외: {ex}" if ex else f"{adj:+.2f} ({', '.join(why)})"))
    cf = routing.quota_cfg(pol)
    print(f"(기준: {cf['hard_percent']}% 이상 제외 · 세션 {cf['session_soft']}% 넘으면 벌점 · 주간은 기간 대비 {cf['pace_margin']}%p 넘게 앞서면 벌점,"
          f" {cf['use_it_hours']}시간 안에 초기화되는데 40% 넘게 남으면 가점 — models.yaml routing.quota)")
    return 0


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
    outs = routing.outcomes(rows, tasks, routing.read_feedback(repo), catalog["models"], skip=routing.read_switches(repo))
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
    jcfg = routing.jev_cfg(pol)
    jo = [o for o in outs if o.get("jev")]
    if jcfg.get("enabled") or jo:
        print(f"\n# JEV 난이도 비교 (jev.enabled={str(bool(jcfg.get('enabled'))).lower()}, mode={jcfg.get('mode')})")
        if not jo:
            print("- 결과가 정해진 투입 중 JEV 기록이 있는 것이 아직 없다")
        else:
            def f(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None
            pairs = [(f(o["difficulty"]), f(o["jev"]), o["score"]) for o in jo if f(o["difficulty"]) is not None and f(o["jev"]) is not None]
            gap = sum(abs(a - b) for a, b, _ in pairs) / max(len(pairs), 1)
            print(f"- 비교 {len(pairs)}건 · 라우팅에 쓴 난이도와 JEV의 평균 차이 {gap:.2f}")
            for label, sel in (("JEV가 1 이상 더 어렵게 봄", lambda a, b: b - a >= 1), ("JEV가 1 이상 더 쉽게 봄", lambda a, b: a - b >= 1),
                               ("거의 같음(차이 1 미만)", lambda a, b: abs(a - b) < 1)):
                g = [sc for a, b, sc in pairs if sel(a, b)]
                print(f"  {label}: {len(g)}건" + (f" · 평균 결과 {sum(g) / len(g):.2f}" if g else ""))
            print("  (JEV가 더 어렵게 본 작업의 결과가 낮으면 JEV가 규칙이 놓친 난이도를 잡는다는 뜻이다 → mode: live 검토)")
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
        f.write("\t".join([dt.datetime.now().strftime("%F %T"), a.id, str(VERDICT[a.verdict]), (a.note or "").replace("\t", " "),
                           (a.category or "").replace("\t", " ").strip()]) + "\n")
    print(f"{a.id}: {a.verdict} ({VERDICT[a.verdict]})" + (f" · 계열 {a.category}" if a.category else "") + " 기록 → 마지막 구현 투입의 실적에 반영")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="모델 자동 라우팅")
    ap.add_argument("--repo")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("discover")
    p = sp.add_parser("update")
    p.add_argument("agents", nargs="*")
    p.add_argument("--check", action="store_true", help="업데이트하지 않고 버전과 카탈로그만 본다")
    p.add_argument("-q", "--quiet", action="store_true")
    sp.add_parser("quota")
    p = sp.add_parser("bench")
    p.add_argument("--tb-version", default=bench.TB_DEFAULT)
    p.add_argument("--tb-file", help="받아 둔 리더보드 페이지(오프라인·시험)")
    p.add_argument("--aa", action="store_true", help="Artificial Analysis 무료 API도 본다(AA_API_KEY)")
    p.add_argument("--out", help="기본: orchestration/state/bench_scores.yaml (키트 관리자는 tools/bench_scores.yaml)")
    p.add_argument("--dry-run", action="store_true")
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
    p.add_argument("--category", help="차단 원인 계열(같은 구조적 원인이면 같은 이름. 예: 영역분리). 같은 계열이 연속되면 정지 점검이 범위 결정을 권한다")
    a = ap.parse_args(argv)
    return {"discover": cmd_discover, "update": cmd_update, "bench": cmd_bench, "quota": cmd_quota, "route": cmd_route, "plan": cmd_plan, "stats": cmd_stats,
            "cooldown": cmd_cooldown, "feedback": cmd_feedback}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
