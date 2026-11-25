#!/usr/bin/env python3
"""현황과 정지 점검 (마스터·매니저 공용, 교훈 L-07·L-13).

  python3 orchestration/tools/status.py            # 요약: DAG · 투입 가능 · 메일함 미처리 · 진행 중 워커 · 정지 점검
  python3 orchestration/tools/status.py --stall    # 정지 점검만 (깨어날 때·대기 전에)
옵션: --warnings(DAG 경고 목록) --limit N(메시지 수, 기본 10) --stale-min M(커밋 없음 기준 분, 기본 60) --no-orca --json

정지 점검 판정 (한 줄씩, 이상이 없으면 '정상'):
  한도 정지   워커가 도는 구독(그 모델의 창)이 사용량 한도 100% → quota_switch.py (자동 전환, 곧 풀리면 대기)
  한도 풀림   대기로 둔 워커의 한도가 풀렸다 → quota_switch.py가 '이어서 하라'를 보낸다
  필터 중단   워커가 안전 필터·이용 정책 차단으로 끊겼다(살아 있어 보여도 일하지 않는다). 응답 블록·터미널 화면 끝·
              Orca escalation 메일(stopReason=errored)의 Last output에서 찾는다
              → quota_switch.py (진행분 보존 → 다른 구독으로 이어받기, 그 모델은 이 작업 후보에서 뺀다)
  오류 중단   errored escalation인데 한도도 차단 문구도 아님 → 메일의 Last output·worker-read로 직접 확인
  attention   Orca가 워커에 주의 표시(입력 대기 프롬프트 등) → worker-show / worker-read로 원인 확인
  커밋 없음    진행 중인데 마지막 커밋과 이 투입의 시작 중 늦은 쪽이 stale-min보다 오래됨 → worker-read로 화면 끝 확인
              (보완 워커가 앞 차수와 같은 브랜치를 쓰면 앞 차수 커밋이 마지막 커밋이라, 투입 시각이 더 늦으면 그것을 기준으로 잰다)
  running 고아 tasks.yaml은 running인데 진행 중 워커가 없음 → 끝났으면 review/merged, 멈췄으면 재투입
  할당 누락    진행 중 워커 0이고 투입 가능 작업이 있음 → launch.sh (일부러 미룬 작업은 tasks.yaml에 hold: <사유>)
  대기할 것 없음 진행 중 0 · 투입 가능 0 · 미답 질문 0 → 다음 작업을 DAG에 넣거나 종료
  범위 결정 필요 같은 작업에서 같은 차단 계열(models.py feedback --category)이 review.scope_escalate_after(기본 2)회 연속
              → 한 번 더 고치기 대신 보장/범위 밖을 바꾸는 선택지(축소·기본 차단·범위 밖)를 마스터에 올린다(교훈 L-22)
  검토 반복   계열이 없거나 달라도 차단이 그보다 한 번 더 연속 → 같은 원인인지 보고 범위 선택지를 검토
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
    try:
        out["scope_flags"] = scope_flags(repo, cfg, tasks)
    except Exception:  # noqa: BLE001 — 현황은 기록이 깨져도 나와야 한다
        out["scope_flags"] = []
    if a.no_orca:
        return out
    o = {"mail": {}, "workers": [], "settled": {}, "error": None}
    for label, rid in (("마스터", kitlib.cfg_get(cfg, "orca.master_run", "")), ("매니저(작업 Run)", kitlib.cfg_get(cfg, "orca.work_run", ""))):
        if not rid:
            continue
        # --full: 없으면 본문이 221자에서 잘려(… truncated) escalation의 Last output이 빠진다(현장 확인)
        mb = orca_json(["orchestration", "inbox", "--terminal", f"run:{rid}", "--limit", "100", "--full"])
        msgs = (mb.get("result") or {}).get("messages") or []
        o["mail"][label] = {"run": rid, "error": None if mb.get("ok", False) else (mb.get("error") or {}).get("message"),
                            "unhandled": [m for m in msgs if m.get("handled") is False]}
        if rid == kitlib.cfg_get(cfg, "orca.work_run", ""):
            o["escalations"] = [m for m in msgs if m.get("type") == "escalation"]
    wr = kitlib.cfg_get(cfg, "orca.work_run", "")
    if wr:
        ws, err = all_workers(wr)
        o["error"] = err
        tl = orca_json(["orchestration", "task-list", "--run", wr])
        titles = {t.get("id"): t.get("title") for t in (tl.get("result") or {}).get("tasks") or []}
        idset = set(tasks)
        o["by_dispatch"] = {}
        for w in ws:
            state = str(w.get("state"))
            t_ = titles.get(w.get("taskId")) or ""
            o["by_dispatch"][w.get("dispatchId")] = {"dispatch": w.get("dispatchId"), "title": t_, "kit_id": kit_id_of(t_, idset),
                                                     "state": state, "liveness": w.get("liveness"),
                                                     "workspace": kitlib.workspace_path(w.get("workspace"))}
            if state in kitlib.SETTLED_WORKER_STATES:
                o["settled"][state] = o["settled"].get(state, 0) + 1
                continue
            title = titles.get(w.get("taskId")) or ""
            wsp = kitlib.workspace_path(w.get("workspace"))
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
            w["agent"], w["key"], w["mode"] = r.get("agent", ""), r.get("key", ""), r.get("mode", "")
            w["model_id"] = r.get("model", "")
            w["started"] = started_ts(r.get("ts"))
            if r.get("agent"):
                by[r["agent"]] = by.get(r["agent"], 0) + 1
        out["quota_stalls"] = quota_stalls(repo, catalog, pol, o["workers"])
        out["turn_stops"], out["error_stops"] = ([], []) if getattr(a, "no_read", False) else \
            turn_stops(repo, o, rows, {q["dispatch"] for q in out["quota_stalls"]})
        try:
            out["quota_waits"] = json.loads((routing.state_dir(repo) / "quota_wait.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            out["quota_waits"] = {}
        out["models"] = {"enabled": routing.enabled(pol), "running": by,
                         "caps": routing.rcfg(pol, "max_concurrent") or {}, "cooldowns": routing.read_cooldowns(repo)}
    except Exception as e:  # noqa: BLE001 — 현황은 라우팅 설정이 깨져도 나와야 한다
        out["models"] = {"error": str(e)[:160]}
    return out


# 안전 필터·이용 정책 차단 문구(CLI마다 다르다).
# transcript(응답 블록): 마지막 assistant 응답 두 개의 글·오류 블록에서 부분 일치. 도구 결과(읽은 파일·시험 출력)는 보지 않는다.
FILTER_PATTERNS = [
    r"usage polic(?:y|ies)",
    r"content (?:polic(?:y|ies)|filter(?:ing)?)",
    r"(?:prompt|request|content|response|input|output|message)s? (?:was|were|has been|is|are) (?:flagged|blocked|filtered)",
    r"flagged as (?:potentially )?(?:violating|unsafe|harmful)",
    r"blocked (?:due to|by|for) (?:safety|the safety|content|polic)",
    r"finish_?reason\W{0,4}(?:safety|content_filter|recitation|prohibited_content)",
    r"(?:cyber ?security|safety) polic(?:y|ies)",
    r"invalid_prompt",
    r"this content can['’]t be shown",
    r"extra care with some [\w-]+ requests",
]
FILTER_RX = re.compile("|".join(FILTER_PATTERNS), re.I)
# 화면(터미널 끝 · escalation 메일의 Last output): 검토 대상 문서 글이 섞이므로 안내 블록의 고정 줄과 **줄 전체가** 맞을 때만.
# codex는 이 안내를 응답 블록으로 남기지 않고 화면에만 찍는다(현장 확인).
SCREEN_LINES = [
    r"This content can['’]t be shown\.?",                         # codex 안전 필터 안내 첫 줄
    r"We take extra care with some [\w-]+ requests\b.*",           # 그다음 줄
    r"API Error:.*\bUsage Policy\b.*",                             # Claude Code 이용 정책 차단
]
SCREEN_RX = [re.compile(x, re.I) for x in SCREEN_LINES]


def screen_match(text):
    """화면 글에서 차단 안내 줄 → 그 줄 | None. 줄 앞의 아이콘·글머리(ⓘ • * - >)와 공백은 떼고 줄 전체로 맞춘다."""
    for ln in str(text or "").splitlines():
        t = re.sub(r"^[\sⓘℹ•*>\-│|]+", "", ln).strip()
        if t and any(rx.fullmatch(t) for rx in SCREEN_RX):
            return " ".join(t.split())[:160]
    return None


def _strings(o):
    """worker-read --source terminal 결과에서 글을 모두 모은다(꼴이 정해져 있지 않다). 메타데이터 칸만 뺀다 —
    판정은 고정 안내 줄과 줄 전체가 맞아야 하므로 글을 넉넉히 모아도 오탐하지 않는다."""
    skip = {"cursor", "dispatchId", "taskId", "runId", "source", "provider", "status", "sourceIdentity", "fallbackReason",
            "id", "warnings", "clipping", "projection", "model", "host"}
    if isinstance(o, str):
        return [o]
    if isinstance(o, list):
        return [x for v in o for x in _strings(v)]
    if isinstance(o, dict):
        return [x for k, v in o.items() if k not in skip for x in _strings(v)]
    return []


def turn_stop(did, tail=2, screen=True):
    """워커 중단 사유 → {'kind': 'filter', 'evidence', 'source'} | None.
    transcript의 마지막 응답 → (없으면) 터미널 화면 끝. 끝난 워커는 화면이 해제돼 읽을 수 없다(메일 쪽은 turn_stops가 본다)."""
    d = orca_json(["orchestration", "worker-read", "--dispatch", did, "--limit", "8"])
    msgs = (((d.get("result") or {}).get("transcript") or {}).get("messages") or []) if d.get("ok") else []
    texts = []
    for m in [x for x in msgs if x.get("role") != "tool"][-tail:]:
        for b in m.get("blocks") or []:
            if b.get("type") in ("text", "error", "notice", "status") or "error" in str(b.get("type", "")):
                texts.append(str(b.get("text") or b.get("message") or b.get("error") or ""))
        if m.get("error"):
            texts.append(str(m.get("error")))
    for t in texts:
        for ln in t.splitlines():
            if FILTER_RX.search(ln):
                return {"kind": "filter", "evidence": " ".join(ln.split())[:160], "source": "응답"}
    if screen:
        d = orca_json(["orchestration", "worker-read", "--dispatch", did, "--source", "terminal", "--limit", "80"])
        if d.get("ok"):
            r = d.get("result") or {}
            tail = (r.get("terminal") or {}).get("tail") if isinstance(r.get("terminal"), dict) else None   # 실제 꼴: terminal.tail = 화면 줄 목록
            ev = screen_match("\n".join(map(str, tail)) if isinstance(tail, list) else "\n".join(_strings(r)))
            if ev:
                return {"kind": "filter", "evidence": ev, "source": "화면"}
    return None


def last_output(body):
    """escalation 메일 본문의 'Last output:' 칸(Orca가 붙이는 화면 끝 캡처)."""
    m = re.search(r"Last output:\s*(.*)", str(body or ""), re.S | re.I)
    return m.group(1) if m else ""


# 한도로 멈춘 워커도 errored escalation을 보낸다(예: agy "⚠ Individual quota reached … Resets in 2h22m"). 한도는 '한도 정지'가 맡는다.
QUOTA_SCREEN_RX = re.compile(r"quota (?:reached|exceeded)|usage limit|rate limit|hit your (?:usage )?limit|resets in \d|limit will reset", re.I)


def errored(m):
    """errored escalation인가: payload stopReason(--full일 때만 온다) · 제목('Worker stopped on an error') · 본문('ended its turn on an error')."""
    pl = kitlib.msg_payload(m)
    text = f"{m.get('subject') or ''}\n{m.get('body') or ''}"
    return str(pl.get("stopReason") or "").lower() == "errored" or bool(
        re.search(r"stopReason\W{0,3}errored|stopped on an error|ended (?:its|the) turn on an error", text, re.I))


def turn_stops(repo, o, rows, skip):
    """(필터 중단[], 오류 중단[]) — 진행 중 워커의 응답·화면, 그리고 errored escalation 메일의 Last output.
    이미 넘긴 dispatch(quota_switch.tsv)와 같은 작업에 더 새 투입이 있는 옛 dispatch는 뺀다."""
    done = routing.read_switches(repo) | set(skip)
    latest = {}
    for r in rows.values():
        if r["kit_id"] and (r["kit_id"] not in latest or r["ts"] >= latest[r["kit_id"]]["ts"]):
            latest[r["kit_id"]] = r
    def info(did):
        w = dict((o.get("by_dispatch") or {}).get(did) or {"dispatch": did})
        r = rows.get(did) or {}
        w.update(agent=r.get("agent", ""), key=r.get("key", ""), mode=r.get("mode", "") or "new", model_id=r.get("model", ""),
                 model=r.get("key") or r.get("model") or "")
        w.setdefault("kit_id", r.get("kit_id")); w.setdefault("title", r.get("title"))
        return w
    def stale(did):
        r = rows.get(did)
        return bool(r and r["kit_id"] and latest.get(r["kit_id"], r)["did"] != did)
    found, errs = {}, {}
    live = {w["dispatch"] for w in o.get("workers") or []}
    for m in o.get("escalations") or []:
        did = kitlib.msg_payload(m).get("dispatchId")
        if not did or did in done or did in found or stale(did) or not errored(m):
            continue
        lo = last_output(m.get("body"))
        ev = screen_match(lo)
        if not ev and QUOTA_SCREEN_RX.search(lo):
            continue   # 한도로 멈춘 것 — 한도 정지·quota_switch가 맡는다
        if ev:
            found[did] = dict(info(did), kind="filter", evidence=ev, source="메일 Last output", message=m.get("id"))
        else:
            errs[did] = dict(info(did), message=m.get("id"), handled=m.get("handled"))
    for did in live:
        if did in done or did in found:
            continue
        ts = turn_stop(did, screen=True)
        if ts:
            found[did] = dict(info(did), **ts)
            errs.pop(did, None)
    return list(found.values()), [e for d, e in errs.items() if d not in found]


def scope_flags(repo, cfg, tasks, n=None):
    """같은 작업의 검토 차단(major·fail)이 이어질 때 → [(작업 ID, 종류, 연속 수, 계열)]. 끝없는 약속을 고치기로 돌지 않게."""
    n = int(n or kitlib.cfg_get(cfg, "review.scope_escalate_after", 2) or 2)
    by = {}
    for f in routing.read_feedback(repo):
        by.setdefault(f["kit_id"], []).append(f)
    out = []
    for tid, fs in by.items():
        t = tasks.get(tid) or {}
        if str(t.get("status", "")).lower() == "merged" or t.get("hold"):
            continue   # 끝났거나 이미 결정을 기다린다
        fs.sort(key=lambda f: f["ts"])
        tail = []
        for f in reversed(fs):
            if f["score"] >= 0.7:   # pass·minor에서 끊긴다
                break
            tail.append(f)
        cats = [f["category"] for f in tail[:n]]
        if len(tail) >= n and cats[0] and all(c == cats[0] for c in cats):
            out.append((tid, "범위 결정 필요", len(tail), cats[0]))
        elif len(tail) >= n + 1:
            out.append((tid, "검토 반복", len(tail), ", ".join(sorted({c for c in (f["category"] for f in tail) if c})) or ""))
    return out


def started_ts(ts):
    """dispatch.tsv의 투입 시각('YYYY-MM-DD HH:MM:SS', 로컬) → epoch | None."""
    try:
        return dt.datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return None


def quota_stalls(repo, catalog, pol, workers, quota=None):
    """한도가 찬 구독에서 도는 워커 → [{dispatch, kit_id, mode, agent, key, windows[(이름표, 사용률, 초기화)], resets}].
    모델 키가 없으면(직접 고른 agent/model) 공급자 공통 창만 본다. 한도를 못 읽으면 빈 목록."""
    if not routing.quota_cfg(pol)["enabled"]:
        return []
    quota = routing.read_quota(repo) if quota is None else quota
    if not quota:
        return []
    rcat = routing.resolve_catalog(catalog, routing.discover(repo, catalog))
    out = []
    for w in workers:
        if not w.get("agent"):
            continue
        key = w.get("key") or routing.key_of_row({"agent": w["agent"], "model": w.get("model_id", "")}, rcat["models"])
        m = rcat["models"].get(key) if key else None
        ex = routing.exhausted(m or {"agent": w["agent"]}, rcat["agents"], quota)
        if ex:
            resets = [r for _, _, r in ex if r]
            out.append({"dispatch": w["dispatch"], "kit_id": w.get("kit_id"), "title": w.get("title"), "mode": w.get("mode") or "new",
                        "agent": w["agent"], "key": key or "", "workspace": w.get("workspace"), "windows": ex,
                        "resets": max(resets) if resets else None})
    return out


def scope_lines(s):
    out = []
    for tid, kind, k, cat in s.get("scope_flags") or []:
        if kind == "범위 결정 필요":
            out.append(f"범위 결정 필요 {tid}: '{cat}' 차단 {k}회 연속 → 한 번 더 고치지 말고 보장/범위 밖을 바꾸는 선택지(축소·기본 차단·범위 밖)를 "
                       "마스터에 올린다. 결정 전까지 tasks.yaml에 hold")
        else:
            out.append(f"검토 반복 {tid}: 차단 {k}회 연속" + (f"(계열 {cat})" if cat else "(계열 기록 없음: feedback --category)")
                       + " → 같은 원인인지 보고 범위 선택지를 검토")
    return out


def stall_lines(s, stale_min):
    o = s.get("orca")
    if o is None:
        return scope_lines(s) + ["(Orca 조회 안 함: --no-orca)"]
    lines = scope_lines(s)
    now = time.time()
    qs = {q["dispatch"]: q for q in s.get("quota_stalls") or []}
    tsd = {x["dispatch"]: x for x in s.get("turn_stops") or []}
    for x in tsd.values():
        who_ = f"{x.get('kit_id') or x.get('title') or '?'} ({x['dispatch']})"
        lines.append(f"필터 중단 {who_}: \"{x['evidence']}\" ({x.get('source', '?')}) · {x.get('model') or x.get('agent') or '?'} → "
                     "python3 orchestration/tools/quota_switch.py (다른 구독으로 이어받기, 이 모델은 이 작업 후보에서 뺀다)")
    for x in s.get("error_stops") or []:
        lines.append(f"오류 중단 {x.get('kit_id') or x.get('title') or '?'} ({x['dispatch']}): Orca escalation stopReason=errored, "
                     f"차단 문구는 못 찾음 → 메일 {x.get('message')}의 Last output·worker-read로 확인(한도면 '한도 정지', 필터면 문구를 키트에 알린다)")
    for w in o["workers"]:
        who = f"{w['kit_id'] or w['title'] or '?'} ({w['dispatch']})"
        if w["dispatch"] in tsd:
            continue   # 아래에서 한 번에
        elif w["dispatch"] in qs:
            q = qs[w["dispatch"]]
            win = ", ".join(f"{lab} {u:.0f}%" + (f"({routing._hm(r)} 초기화)" if r else "") for lab, u, r in q["windows"])
            wt = (s.get("quota_waits") or {}).get(w["dispatch"])
            lines.append(f"한도 정지 {who}: {win} · {q['key'] or q['agent']} → "
                         + (f"대기 중({routing._hm(wt['resets'])}에 풀리면 재개 메시지)" if wt and wt.get("resets")
                            else "python3 orchestration/tools/quota_switch.py (자동 전환, 곧 풀리면 대기)"))
        elif w["attention"]:
            lines.append(f"attention {who}: {', '.join(map(str, w['attention']))} → worker-show/worker-read")
        elif w["last_commit"] and now - max(w["last_commit"], w.get("started") or 0) > stale_min * 60:
            lines.append(f"커밋 없음 {who}: 마지막 커밋 {ago(w['last_commit'])}"
                         + (f" · 투입 {ago(w['started'])}" if w.get("started") else "") + " → worker-read로 화면 끝 확인")
    live_d = {w["dispatch"] for w in o["workers"]}
    for did, wt in (s.get("quota_waits") or {}).items():
        if wt.get("resets") and wt["resets"] <= now and did in live_d and did not in qs:
            lines.append(f"한도 풀림 {wt.get('kit_id') or wt.get('title') or '?'} ({did}): {routing._hm(wt['resets'])}에 풀렸다 → "
                         "python3 orchestration/tools/quota_switch.py (이어서 하라고 보낸다)")
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
    ap.add_argument("--no-read", dest="no_read", action="store_true", help="워커 마지막 응답을 읽지 않는다(필터 중단 판정 생략)")
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
