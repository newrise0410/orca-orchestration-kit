#!/usr/bin/env python3
"""세션 기록에서 작업별 API 호출 수·호출당 문맥·합계·시험 관련 비중을 뽑는다.

  python3 orchestration/tools/usage.py                 # 지난 6시간, 이 저장소의 워커·매니저 세션
  python3 orchestration/tools/usage.py --since 24h     # 기간(분 m · 시간 h · 일 d)
  python3 orchestration/tools/usage.py --task F-07 --json

비용은 '호출 수 × 그때의 문맥 크기'다(시험 실행 자체는 토큰을 쓰지 않는다). 문맥 = 캐시 읽기 + 캐시 쓰기 + 입력.
'시험 관련'은 그 호출의 셸 명령이 pytest·test.sh·smoke·build.sh·vitest·slot.sh·run_logged·kill -0·wait "$pid"·.log를
담은 것이다(로그를 읽는 명령도 들어가 위쪽으로 치우친 어림).

읽는 곳
- Claude Code: <설정 폴더>/projects/<워크트리 경로의 영숫자 아닌 글자를 '-'로>/*.jsonl
  설정 폴더 = ~/.claude, $CLAUDE_CONFIG_DIR, Orca 계정 폴더(~/.config/orca/claude-profiles/*/home)
- Codex: ~/.codex/sessions, $CODEX_HOME/sessions, Orca codex 계정 폴더(~/.config/orca/codex-accounts/*/home/sessions)의 rollout-*.jsonl
  (첫 줄 session_meta의 cwd로 워크트리를 맞춘다)
- KIT_TRANSCRIPT_DIRS(콜론 구분)를 주면 Claude 설정 폴더 목록을 그것으로 바꾼다(시험용).
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402

TEST_RX = re.compile(r'pytest|test\.sh|smoke|build\.sh|vitest|slot\.sh|run_logged|kill -0|wait "?\$|\.log\b')
SHELL_TOOLS = {"Bash", "shell", "exec_command", "local_shell", "container.exec"}


def claude_homes():
    env = os.environ.get("KIT_TRANSCRIPT_DIRS")
    if env is not None:
        return [Path(x) for x in env.split(":") if x]
    homes = [Path.home() / ".claude"]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        homes.append(Path(os.environ["CLAUDE_CONFIG_DIR"]))
    homes += [Path(p) for p in glob.glob(str(Path.home() / ".config/orca/claude-profiles/*/home"))]
    out = []
    for h in homes:
        if h.is_dir() and h.resolve() not in [o.resolve() for o in out]:
            out.append(h)
    return out


def codex_roots():
    """codex 세션 기록 폴더들. Orca 안의 세션은 CODEX_HOME이 계정 폴더(codex-accounts/<id>/home)를 가리키지만
    Orca가 띄운 codex 워커의 기록은 ~/.codex/sessions에 남을 수 있다 → 있는 곳을 모두 본다(같은 파일은 한 번)."""
    env = os.environ.get("KIT_CODEX_SESSIONS")
    if env:
        return [Path(x) for x in env.split(":") if x]
    cand = [Path.home() / ".codex" / "sessions"]
    if os.environ.get("CODEX_HOME"):
        cand.append(Path(os.environ["CODEX_HOME"]) / "sessions")
    cand += [Path(p) for p in sorted(glob.glob(str(Path.home() / ".config/orca/codex-accounts/*/home/sessions")))]
    out, seen = [], set()
    for c in cand:
        r = os.path.realpath(c)
        if r not in seen and c.is_dir():
            seen.add(r)
            out.append(c)
    return out


def codex_files():
    return [f for r in codex_roots() for f in glob.glob(str(r / "*/*/*/rollout-*.jsonl"))]


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def session_files(worktree, since=0):
    """[(종류, 파일)] — 이 워크트리에서 돈 세션 기록(since 뒤에 고친 것)."""
    out = []
    try:
        wt = str(Path(worktree).resolve())
    except OSError:
        wt = str(worktree)
    for h in claude_homes():
        for f in glob.glob(str(h / "projects" / slug(wt) / "*.jsonl")):
            if os.path.getmtime(f) >= since and os.path.realpath(f) not in {os.path.realpath(x) for _, x in out}:
                out.append(("claude", f))
    if True:
        for f in codex_files():
            try:
                if os.path.getmtime(f) < since:
                    continue
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    meta = json.loads(fh.readline() or "{}")
            except (OSError, ValueError):
                continue
            if (meta.get("payload") or {}).get("cwd") == wt:
                out.append(("codex", f))
    return out


def _ts(s):
    """'2026-11-26T01:02:03.456Z' → epoch. 모르면 0."""
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return 0


def summarize(kind, path, since=0):
    """세션 기록 하나 → {calls, total, last, test, model, first, end}. 문맥 단위는 토큰."""
    calls, model, first, end = {}, "", 0, 0
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return None
    with fh:
        if kind == "claude":
            for ln in fh:
                try:
                    j = json.loads(ln)
                except ValueError:
                    continue
                if j.get("type") != "assistant":
                    continue
                t = _ts(j.get("timestamp"))
                if since and t and t < since:
                    continue
                m = j.get("message") or {}
                mid, us = m.get("id"), m.get("usage") or {}
                if not mid:
                    continue
                e = calls.setdefault(mid, {"ctx": 0, "test": False})   # 한 API 호출이 여러 줄로 기록된다 — id로 묶는다
                e["ctx"] = max(e["ctx"], sum(int(us.get(k) or 0) for k in
                                             ("cache_read_input_tokens", "cache_creation_input_tokens", "input_tokens")))
                for c in m.get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") in SHELL_TOOLS \
                            and TEST_RX.search(str((c.get("input") or {}).get("command", ""))):
                        e["test"] = True
                model = m.get("model") or model
                first, end = first or t, t or end
        else:   # codex: token_count 이벤트 하나 = API 호출 하나, 그 앞의 셸 호출이 시험이면 시험 관련
            pending_test, n = False, 0
            for ln in fh:
                try:
                    j = json.loads(ln)
                except ValueError:
                    continue
                t = _ts(j.get("timestamp"))
                if since and t and t < since:
                    continue
                p = j.get("payload") or {}
                if j.get("type") == "turn_context":
                    model = p.get("model") or model
                if j.get("type") == "response_item" and p.get("type") in ("function_call", "local_shell_call", "custom_tool_call"):
                    if TEST_RX.search(json.dumps(p.get("arguments") or p.get("action") or p.get("input") or "")):
                        pending_test = True
                if j.get("type") == "event_msg" and p.get("type") == "token_count":
                    lu = ((p.get("info") or {}).get("last_token_usage") or {})
                    if not lu:
                        continue
                    n += 1
                    calls[n] = {"ctx": int(lu.get("input_tokens") or 0),   # codex input_tokens는 캐시 읽기를 포함한다
                                "test": pending_test}
                    pending_test = False
                    first, end = first or t, t or end
    if not calls:
        return None
    vals = list(calls.values())
    return {"kind": kind, "file": str(path), "calls": len(vals), "total": sum(v["ctx"] for v in vals),
            "last": vals[-1]["ctx"], "test": sum(v["ctx"] for v in vals if v["test"]), "model": model,
            "first": first, "end": end}


def latest(worktree, since=0):
    """이 워크트리의 가장 최근 세션 요약(진행 중 워커의 지금 문맥). 없으면 None."""
    fs = sorted(session_files(worktree, since), key=lambda kf: os.path.getmtime(kf[1]))
    for kind, f in reversed(fs):
        s = summarize(kind, f)
        if s:
            return s
    return None


def codex_limits(provider_id, max_files=300):
    """codex 세션 기록(token_count 이벤트의 rate_limits)에서 그 계정의 마지막 한도 → {windows, seen} | None.
    Orca는 고른 계정의 한도만 주므로, 고르지 않은 codex 계정은 이것으로 채운다(session_meta.creator_account_id로 계정을 맞춘다)."""
    if not provider_id:
        return None
    fs = sorted(codex_files(), key=os.path.getmtime, reverse=True)[:max_files]
    for f in fs:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                meta = json.loads(fh.readline() or "{}")
                if (meta.get("payload") or {}).get("creator_account_id") != provider_id:
                    continue
                last = None
                for ln in fh:
                    if '"rate_limits"' in ln:
                        last = ln
        except (OSError, ValueError):
            continue
        if not last:
            continue
        try:
            j = json.loads(last)
        except ValueError:
            continue
        rl = (j.get("payload") or {}).get("rate_limits") or {}
        ws = {}
        for key, name in (("primary", "session"), ("secondary", "weekly")):
            w = rl.get(key) or {}
            if w.get("used_percent") is not None:
                ws[name] = {"used": float(w["used_percent"]), "minutes": int(w.get("window_minutes") or 0),
                            "resets": float(w.get("resets_at") or 0)}
        if ws:
            return {"windows": ws, "seen": _ts(j.get("timestamp")) or os.path.getmtime(f), "plan": rl.get("plan_type")}
    return None


def worktrees(repo, cfg):
    """[(작업 ID 또는 '매니저', 경로)] — git 워크트리 목록(브랜치 req/<ID>·<ID>-RV로 작업을 맞춘다)과 저장소 자신."""
    import subprocess
    out = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"], capture_output=True, text=True)
    res, cur = [], {}
    for ln in (out.stdout or "").splitlines() + [""]:
        if ln.startswith("worktree "):
            cur = {"path": ln[9:]}
        elif ln.startswith("branch "):
            cur["branch"] = ln[7:].replace("refs/heads/", "")
        elif not ln and cur:
            res.append(cur)
            cur = {}
    try:
        _, tasks = kitlib.all_tasks(repo, cfg)
    except (OSError, ValueError):
        tasks = {}
    by_branch = {str(t.get("branch")): str(i) for i, t in tasks.items() if t.get("branch")}
    rows = []
    for w in res:
        p, br = w["path"], w.get("branch", "")
        if Path(p).resolve() == Path(repo).resolve():
            rows.append(("매니저", p))
            continue
        tid = by_branch.get(br)
        if not tid:
            m = re.match(r"(?:req/)?(.+?)(?:-RV(?:-.+)?)?$", br.split("/")[-1] if br else "")
            tid = m.group(1) if m and m.group(1) in tasks else None
            if tid and "-RV" in br:
                tid += " 검토"
        rows.append((tid or Path(p).name, p))
    return rows


def parse_since(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([mhd]?)", str(s).strip())
    if not m:
        raise SystemExit(f"--since 형식: 30m · 6h · 2d ({s})")
    return float(m.group(1)) * {"m": 60, "h": 3600, "d": 86400, "": 3600}[m.group(2)]


def k(n):
    return f"{n / 1e3:.0f}k" if n < 1e6 else f"{n / 1e6:.1f}M"


def main(argv=None):
    ap = argparse.ArgumentParser(description="세션 기록의 작업별 토큰(호출 수 × 문맥)")
    ap.add_argument("--since", default="6h")
    ap.add_argument("--task")
    ap.add_argument("--min-calls", dest="min_calls", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    since = time.time() - parse_since(a.since)
    rows = []
    for tid, wt in worktrees(repo, cfg):
        if a.task and not str(tid).startswith(a.task):
            continue
        for kind, f in session_files(wt, since):
            s = summarize(kind, f, since)
            if s and s["calls"] >= a.min_calls:
                rows.append(dict(s, task=tid, worktree=wt))
    rows.sort(key=lambda r: -r["total"])
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print(f"세션 기록 없음 (지난 {a.since}, 워크트리 {len(worktrees(repo, cfg))}개)")
        return 0
    print(f"| 작업 | 세션 | 모델 | API 호출 | 호출당 문맥 | 마지막 문맥 | 합계 | 시험 관련 비중 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['task']} | {r['kind']} {Path(r['file']).stem[:8]} | {r['model'] or '?'} | {r['calls']} | "
              f"{k(r['total'] / r['calls'])} | {k(r['last'])} | {k(r['total'])} | {r['test'] / r['total'] * 100:.0f}% |")
    tot = sum(r["total"] for r in rows)
    print(f"\n합계 {k(tot)} · 호출 {sum(r['calls'] for r in rows)} · 시험 관련 {sum(r['test'] for r in rows) / tot * 100:.0f}% (지난 {a.since})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
