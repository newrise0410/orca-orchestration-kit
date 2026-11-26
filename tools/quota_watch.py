#!/usr/bin/env python3
"""예약 점검: 매니저가 자는 동안 생긴 '한도 정지'·풀린 대기·멈춤을 놓치지 않게 Orca 예약 실행으로 깨운다.

  python3 orchestration/tools/quota_watch.py install [--every 10]   # Orca 예약 실행을 등록한다(이 저장소 작업 폴더)
  python3 orchestration/tools/quota_watch.py status                 # 등록 상태·마지막 알림
  python3 orchestration/tools/quota_watch.py remove                 # 등록을 지운다
  python3 orchestration/tools/quota_watch.py precheck               # 예약 실행의 사전 확인이 부른다

동작: Orca는 예약 시각마다 사전 확인(precheck)을 돌리고 종료코드가 0일 때만 에이전트를 띄운다.
precheck는 할 일(한도 정지 · 한도가 풀린 대기, quota_switch.py --due와 같은 판정)이 있으면 매니저 Run
(kit.yaml orca.work_run)에 메일을 보내 매니저를 깨우고, **언제나 1로 끝나** 에이전트를 띄우지 않는다(구독을 쓰지 않는다).
같은 내용은 remind_minutes(기본 30분)마다 한 번만 다시 알린다. 판단과 전환은 깨어난 매니저가 quota_switch.py로 한다.
멈춤(kit.yaml watch.stall, 기본 true): 정지 점검의 '커밋 없음'(watch.stale_min, 기본 60분)·'긴 명령'(시간 제한 없이
watch.long_cmd_min분 넘게)·'긴 명령 제한 넘김'·'확인 창 대기'도 깨운다. 워커는 보고가 와야 매니저를 깨우므로, 끝나지 않는
명령이나 확인 창에 걸린 워커는 이것 없이는 사람이 물을 때까지 그대로다(현장 3시간 20분).
빈 변수 의심 rm 확인 창(watch.auto_deny_rm, 기본 true): escalation 메일에서 승인 ID를 찾으면 `orchestration deny`로 한 번
거절하고 고쳐 쓸 꼴을 이유로 보낸다(워커는 계속한다. Orca는 그 창의 '이번만 거절'만 고른다).
안전장치: 사전 확인에 부수 효과를 얹는 것은 원래 용도와 다르다. Orca가 이 의미를 바꿔 실행 기록에 에이전트 실행
(skipped_precheck가 아닌 것)이 생기면, 다음 사전 확인이 예약 실행을 스스로 끄고 매니저에게 알린다(10분마다 에이전트가 뜨지 않게).
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import routing  # noqa: E402

STATE_REL = "quota_watch.json"
REMIND_MIN = 30


def orca_cli():
    return os.environ.get("ORCA_CLI_COMMAND") or shutil.which("orca") or "orca"


def run_json(args, timeout=120):
    try:
        p = subprocess.run([orca_cli(), *args, "--json"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": {"message": str(e)}}
    txt = (p.stdout or "").strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        try:
            return json.loads(txt.splitlines()[0]) if txt else {"ok": False, "error": {"message": (p.stderr or "").strip()[:200]}}
        except (json.JSONDecodeError, IndexError):
            return {"ok": False, "error": {"message": (p.stderr or txt).strip()[:200]}}


def read_state(repo):
    try:
        return json.loads((routing.state_dir(repo) / STATE_REL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(repo, d):
    p = routing.state_dir(repo) / STATE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


WAKE_KINDS = ("긴 명령 제한 넘김", "긴 명령", "확인 창 대기", "커밋 없음")


def wake_lines(s, cfg):
    """정지 점검 줄 중 매니저를 깨울 멈춤 → [(서명, 줄)]. 서명은 종류 + dispatch(분 수가 바뀌어도 같은 알림으로 본다)."""
    if not kitlib.cfg_get(cfg, "watch.stall", True):
        return []
    import re
    import status
    out = []
    for ln in status.stall_lines(s, int(kitlib.cfg_get(cfg, "watch.stale_min", 60) or 60)):
        kind = next((k for k in WAKE_KINDS if ln.startswith(k + " ")), None)
        if kind:
            m = re.search(r"\((ctx_[^)\s]+|[^()\s]+)\)", ln)
            out.append((f"{kind}:{m.group(1) if m else ln[:40]}", ln))
    return out


def auto_deny(repo, cfg, s, st):
    """빈 변수 의심 rm 확인 창을 거절한다 → [줄]. 승인 ID를 모르면 손대지 않는다(정지 점검이 deny 명령을 보인다)."""
    import status
    if not kitlib.cfg_get(cfg, "watch.auto_deny_rm", True):
        return []
    done, out = set(st.get("denied") or []), []
    for w in (s.get("orca") or {}).get("workers") or []:
        hw = w.get("human_wait") or {}
        if not (hw.get("rm_unsafe") and hw.get("approval")) or hw["approval"] in done:
            continue
        d = run_json(["orchestration", "deny", "--dispatch", w["dispatch"], "--prompt", hw["approval"], "--reason",
                      status.RM_REWRITE + ". 고쳐서 다시 부른다."])
        done.add(hw["approval"])
        out.append(f"{w.get('kit_id') or w['dispatch']} 빈 변수 의심 rm 확인 창 " + ("거절함(고쳐 쓸 꼴을 보냄)" if d.get("ok")
                   else f"거절 실패: {(d.get('error') or {}).get('message', '?')}"))
    st["denied"] = sorted(done)[-200:]
    return out


def pending(repo, cfg):
    """(한도 정지 목록, 풀린 대기 목록, 멈춤 [(서명, 줄)], 현황) — 한도는 quota_switch.py --due와 같은 판정."""
    import argparse as ap_
    import quota_switch
    import status
    catalog, pol = routing.load(repo)
    s = status.collect(repo, cfg, ap_.Namespace(no_orca=False))
    o = s.get("orca") or {}
    if o.get("error"):
        return [], [], [], s
    stalls = status.quota_stalls(repo, catalog, pol, o.get("workers") or [], quota=routing.read_quota(repo))
    waits = quota_switch.read_waits(repo)
    due = [(d, waits[d]) for d in quota_switch.due_waits(waits, stalls)]
    waiting = {q["dispatch"] for q in stalls if q["dispatch"] in waits}
    filt = [dict(w, windows=[("필터 중단", 100.0, None)]) for w in s.get("turn_stops") or []]
    filt += [dict(w, evidence="오류 중단: errored, 차단 문구 없음 — 확인 필요", windows=[]) for w in s.get("error_stops") or []]
    return [q for q in stalls if q["dispatch"] not in waiting] + filt, due, wake_lines(s, cfg), s


def guard(repo, run):
    """이 예약 실행이 에이전트를 띄운 적이 있으면(사전 확인 의미가 바뀜) 끄고 알린다 → 꺼졌으면 True."""
    st = read_state(repo)
    aid = st.get("automation_id")
    if not aid or st.get("guard_disabled"):
        return bool(st.get("guard_disabled"))
    d = run_json(["automations", "runs", "--id", aid])
    runs = (d.get("result") or {}).get("runs") or [] if d.get("ok") else []
    since = float(st.get("installed", 0)) * 1000
    ran = [r for r in runs if not str(r.get("status", "")).startswith("skipped") and float(r.get("scheduledFor") or r.get("startedAt") or 0) >= since]
    if not ran:
        return False
    off = run_json(["automations", "edit", "--id", aid, "--disabled"])
    st.update(guard_disabled=time.time(), guard_status=str(ran[0].get("status")))
    write_state(repo, st)
    if run:
        run_json(["orchestration", "send", "--to", f"run:{run}", "--type", "status", "--subject", "한도 점검 예약을 껐다",
                  "--body", f"예약 실행 {aid}이 에이전트를 띄웠다(실행 상태 {ran[0].get('status')}). Orca의 사전 확인 의미가 바뀐 것 같아 "
                            + ("예약을 껐다" if off.get("ok") else "끄려 했으나 실패했다 — Orca에서 직접 끈다")
                            + ". 키트 쪽에 알리고, 확인 전까지 정지 점검(status.py --stall)을 손으로 본다."])
    return True


def cmd_precheck(repo, cfg, a):
    run = kitlib.cfg_get(cfg, "orca.work_run", "")
    if not run:
        return 1
    if guard(repo, run):
        return 1
    stalls, due, wake, s = pending(repo, cfg)
    st = read_state(repo)
    denied = auto_deny(repo, cfg, s, st) if s else []
    if denied:
        write_state(repo, st)
    if not stalls and not due and not wake and not denied:
        return 1
    sig = sorted([f"stall:{q['dispatch']}" for q in stalls] + [f"due:{d}" for d, _ in due] + [w for w, _ in wake]
                 + [f"deny:{x}" for x in denied])
    remind = float(a.remind or REMIND_MIN) * 60
    if st.get("last_sig") == sig and time.time() - float(st.get("last_sent", 0)) < remind:
        return 1
    parts = [f"{q.get('kit_id') or q.get('title') or '?'} " + (f"필터 중단(\"{q.get('evidence', '')[:60]}\")" if q.get("evidence")
             else ", ".join(f"{lab} {u:.0f}%" for lab, u, _ in q["windows"])) for q in stalls]
    parts += [f"{w.get('kit_id') or '?'} 한도 풀림({routing._hm(w['resets'])})" for _, w in due]
    body = "예약 점검(quota_watch): "
    if parts:
        body += "; ".join(parts)[:600] + " → python3 orchestration/tools/quota_switch.py (판정·전환·재개). "
    if wake:
        body += "멈춤: " + " | ".join(ln for _, ln in wake)[:900] + " "
    if denied:
        body += "자동 거절: " + "; ".join(denied)[:300] + " "
    body += "자세히: status.py --stall"
    subj = " · ".join(x for x in [f"한도 정지·필터 중단 {len(stalls)}" if stalls else "", f"한도 풀림 {len(due)}" if due else "",
                                  f"멈춤 {len(wake)}" if wake else "", f"자동 거절 {len(denied)}" if denied else ""] if x)
    d = run_json(["orchestration", "send", "--to", f"run:{run}", "--type", "status",
                  "--subject", f"예약 점검: {subj}", "--body", body])
    if d.get("ok"):
        st.update(last_sig=sig, last_sent=time.time(), last_body=body)
        write_state(repo, st)
    return 1   # 언제나 1: Orca가 에이전트를 띄우지 않게


def precheck_command(repo):
    """Orca 사전 확인 명령. Orca 앱 환경에는 ORCA_CLI_COMMAND가 없고 PATH의 orca가 다른 프로그램일 수 있어 절대 경로로 고정한다."""
    py = shutil.which("python3") or sys.executable
    return (f"cd {shlex.quote(str(repo))} && ORCA_CLI_COMMAND={shlex.quote(orca_cli())} "
            f"{shlex.quote(py)} orchestration/tools/quota_watch.py precheck")


def cmd_install(repo, cfg, a):
    st = read_state(repo)
    if st.get("automation_id"):
        d = run_json(["automations", "show", "--id", st["automation_id"]])
        if d.get("ok"):
            print(f"이미 등록됨: {st['automation_id']} (지우려면 remove)")
            return 0
    if not kitlib.cfg_get(cfg, "orca.work_run", ""):
        print("kit.yaml orca.work_run이 비어 있다: 깨울 매니저 Run이 없다", file=sys.stderr)
        return 1
    name = f"{kitlib.cfg_get(cfg, 'project.name', repo.name)} 한도 점검"
    agent = a.agent or kitlib.cfg_get(cfg, "orca.agent", "claude") or "claude"
    args = ["automations", "create", "--name", name, "--trigger", f"*/{int(a.every)} * * * *",
            "--prompt", "이 예약 실행은 사전 확인(quota_watch.py precheck)만 쓴다. 에이전트로 실행됐다면 아무것도 하지 말고 바로 끝내라.",
            "--provider", agent, "--workspace", f"path:{repo}", "--precheck", precheck_command(repo),
            "--precheck-timeout", "120", "--enabled"]
    if a.dry_run:
        print("(dry-run) " + " ".join(shlex.quote(x) for x in [orca_cli(), *args]))
        return 0
    d = run_json(args)
    aid = ((d.get("result") or {}).get("automation") or {}).get("id")
    if not d.get("ok") or not aid:
        print(f"등록 실패: {(d.get('error') or {}).get('message')}", file=sys.stderr)
        return 1
    st.update(automation_id=aid, every=int(a.every), installed=time.time())
    write_state(repo, st)
    print(f"등록: {name} ({aid}) · {a.every}분마다 사전 확인 · 할 일이 있으면 매니저 Run에 알림 · 에이전트는 띄우지 않는다")
    return 0


def cmd_remove(repo, cfg, a):
    st = read_state(repo)
    aid = st.get("automation_id")
    if not aid:
        print("등록된 예약 점검 없음")
        return 0
    d = run_json(["automations", "remove", "--id", aid])
    if not d.get("ok"):
        print(f"지우기 실패: {(d.get('error') or {}).get('message')}", file=sys.stderr)
        return 1
    st.pop("automation_id", None)
    write_state(repo, st)
    print(f"지움: {aid}")
    return 0


def cmd_status(repo, cfg, a):
    st = read_state(repo)
    aid = st.get("automation_id")
    if not aid:
        print("등록된 예약 점검 없음 (install로 등록)")
    else:
        d = run_json(["automations", "show", "--id", aid])
        au = (d.get("result") or {}).get("automation") or {}
        print(f"예약 점검: {au.get('name', '?')} ({aid}) · " + ("켜짐" if au.get("enabled") else "꺼짐" if d.get("ok") else "Orca에서 찾지 못함"))
    if st.get("last_sent"):
        print(f"마지막 알림 {time.strftime('%m-%d %H:%M', time.localtime(st['last_sent']))}: {st.get('last_body', '')[:200]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="한도 점검 예약 (Orca 예약 실행 + 사전 확인)")
    ap.add_argument("cmd", choices=["install", "remove", "status", "precheck"])
    ap.add_argument("--every", type=int, default=10, help="분 간격(기본 10)")
    ap.add_argument("--agent", help="예약 실행에 적을 에이전트(실제로 띄우지 않는다. 기본 kit.yaml orca.agent)")
    ap.add_argument("--remind", type=float, help=f"같은 알림을 다시 보내는 간격(분, 기본 {REMIND_MIN})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    os.chdir(repo)
    cfg = kitlib.load_config(repo, a.config)
    return {"install": cmd_install, "remove": cmd_remove, "status": cmd_status, "precheck": cmd_precheck}[a.cmd](repo, cfg, a)


if __name__ == "__main__":
    sys.exit(main())
