"""모델 자동 라우팅: 작업 → (에이전트, 모델, effort). launch.py와 models.py가 쓴다.

입력
  카탈로그  orchestration/tools/model_catalog.yaml (키트 소유, 모델별 quality·speed·cost·강점)
  정책      orchestration/models.yaml (프로젝트 소유, 쓸 구독·목표·동시 상한·덮어쓰기)
  가용성    설치된 CLI와 각 CLI의 모델 목록 (state/models_cache.json에 하루 캐시)
  학습      state/dispatch.tsv(누가 어떤 작업을 맡았나) × tasks.yaml 결과 × state/route_feedback.tsv(검토 판정)
  부하      진행 중 워커 수(에이전트별), 쿨다운(state/cooldown.json: 사용량 한도에 걸린 구독)
결정은 LLM 없이 점수로 한다(빠르고 재현된다). 점수가 왜 그렇게 나왔는지 reasons로 남긴다.
선택: models.yaml jev.enabled이면 TypeSafe JEV(판정 전용 모델)에 작업 제목·완료 조건을 보내 난이도를 한 번 더 본다.
      기본은 꺼져 있고, 켜도 mode: shadow면 기록만 한다(models.py stats가 규칙 분류와 비교). 키·네트워크가 없으면 조용히 건너뛴다.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import kitlib

yaml = kitlib.yaml
TOOLS = Path(__file__).resolve().parent
CATALOG = TOOLS / "model_catalog.yaml"
BENCH = TOOLS / "bench_scores.yaml"
POLICY_REL = "orchestration/models.yaml"
STATE_REL = "orchestration/state"
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CACHE_TTL = 24 * 3600

# 목표별 가중치: under = 능력 부족 벌점(난이도 1 차이당), cost·speed = 소모·느림 벌점, head = 품질 여유 가점(최대 1 차이).
# 구독제라 cost는 돈이 아니라 한도 소모다. 다시 띄우고 검토하는 비용이 더 크므로 balanced는 여유(head)를 소모보다 무겁게 둔다.
OBJECTIVES = {
    "quality": {"under": 4.0, "cost": 0.15, "speed": 0.15, "head": 1.5},
    "balanced": {"under": 3.0, "cost": 0.4, "speed": 0.3, "head": 1.0},
    "economy": {"under": 2.5, "cost": 1.1, "speed": 0.3, "head": 0.15},
    "speed": {"under": 2.5, "cost": 0.4, "speed": 1.0, "head": 0.2},
}
DIFF_WORDS = {"easy": 2, "쉬움": 2, "normal": 3, "보통": 3, "hard": 4, "어려움": 4, "critical": 5, "very-hard": 5}

HARD_WORDS = ["설계", "아키텍처", "프로토콜", "동시성", "경합", "병렬", "분산", "보안", "인증", "권한",
              "암호", "마이그레이션", "성능", "최적화", "실시간", "알고리즘", "상태 기계", "트랜잭션", "스케줄러", "정합성",
              "architecture", "concurren", "race", "security", "auth", "crypto", "migrat", "protocol", "performance",
              "optimi", "distributed", "realtime", "algorithm", "transaction", "consisten"]
EASY_WORDS = ["오타", "주석", "이름 변경", "readme", "버전 올림", "문구", "typo", "comment", "rename", "bump", "lint", "format"]
TAG_WORDS = {
    "design": ["설계", "아키텍처", "architecture", "design"],
    "contract": ["계약", "인터페이스", "스키마", "schema", "contract", "api 명세", "contracts/"],
    "frontend": ["화면", "프런트", "프론트", "ui", "css", "react", "vue", "svelte", "컴포넌트", "frontend", "web/",
                 ".tsx", ".jsx", ".vue", "대시보드"],
    "test": ["시험", "테스트", "test", "e2e", "tests/"],
    "docs": ["문서", "docs/", "readme", "매뉴얼", "가이드", ".md"],
    "refactor": ["리팩터", "리팩토링", "refactor", "구조 개선"],
    "debug": ["버그", "결함", "오류 수정", "fix", "bug", "회귀"],
    "config": ["설정", "배포", "ci", "docker", "compose", "인프라", "infra", "deploy", ".yaml", ".yml"],
    "research": ["조사", "분석", "비교", "research", "survey", "평가"],
    "coding": ["구현", "기능", "implement", "feature", "src/", "lib/"],
}


# ───────────────────────── 설정

def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(repo, catalog_path=None, policy_path=None):
    """(catalog{agents, models}, policy) — models.yaml models:가 카탈로그를 키 단위로 덮는다."""
    cat = yaml.safe_load(Path(catalog_path or CATALOG).read_text(encoding="utf-8")) or {}
    pp = Path(policy_path) if policy_path else Path(repo) / POLICY_REL
    pol = (yaml.safe_load(pp.read_text(encoding="utf-8")) or {}) if pp.is_file() else {}
    pol["_present"] = pp.is_file()
    models = {}
    for k, m in (cat.get("models") or {}).items():
        models[k] = dict(m)
    for k, m in (pol.get("models") or {}).items():
        m = m or {}
        if ("model" in m or "model_by_effort" in m) and "family" not in m:
            m = dict(m, family=None)   # 프로젝트가 모델 ID를 직접 정했으면 계열 따라가기를 끈다
            if "model" in m and "model_by_effort" not in m:
                m["model_by_effort"] = None
        if "quality" in m:
            m = dict(m, quality_pinned=True)   # 프로젝트가 품질을 직접 정했으면 벤치 점수로 바꾸지 않는다
        models[k] = deep_merge(models.get(k, {}), m)
    agents = deep_merge(cat.get("agents") or {}, pol.get("agents") or {})
    bench = {}
    if rcfg(pol, "bench", True):
        # 키트 판(tools/bench_scores.yaml)과 프로젝트가 직접 받은 판(state/bench_scores.yaml) 중 더 새 것
        for bp in (Path(catalog_path).with_name("bench_scores.yaml") if catalog_path else BENCH, Path(repo) / STATE_REL / "bench_scores.yaml"):
            try:
                b = yaml.safe_load(bp.read_text(encoding="utf-8")) or {} if bp.is_file() else {}
            except (OSError, yaml.YAMLError):
                b = {}
            if b.get("tb") and str(b.get("generated", "")) >= str(bench.get("generated", "")):
                bench = dict(b, _path=str(bp))
    return {"agents": agents, "models": models, "version": cat.get("version"), "bench": bench}, pol


def rcfg(pol, key, default=None):
    return (pol.get("routing") or {}).get(key, default)


def enabled(pol):
    return bool(pol.get("_present")) and bool(rcfg(pol, "enabled", True))


def state_dir(repo):
    return Path(repo) / STATE_REL


# ───────────────────────── 가용성

def _run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout or "", p.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def list_models(kind, binp, timeout=30):
    """CLI의 모델 목록 → (set|None, 오류). None은 '목록을 알 수 없음(별칭을 믿는다)'."""
    if kind in (None, "none", ""):
        return None, None
    if kind == "codex":
        code, out, err = _run([binp, "debug", "models"], timeout)
        try:
            return {m["slug"] for m in json.loads(out).get("models", []) if m.get("slug")}, None
        except (json.JSONDecodeError, AttributeError, TypeError):
            return None, (err or out).strip()[:160] or "목록 해석 실패"
    if kind == "agy":
        code, out, err = _run([binp, "models"], timeout)
        ids = {ln.split("\t")[0].strip() for ln in out.splitlines() if "\t" in ln and ln.split("\t")[0].strip()}
        return (ids or None), (None if ids else ((err or out).strip()[:160] or "목록 없음"))
    if kind == "grok":
        code, out, err = _run([binp, "models"], timeout)
        ids = set(re.findall(r"^\s*[*-]\s+([\w.\-/:]+)", out, re.M))
        return (ids or None), (None if ids else ((err or out).strip()[:160] or "목록 없음"))
    return None, f"알 수 없는 목록 방식 {kind}"


def discover(repo, catalog, refresh=False, timeout=30):
    """{agent: {bin, models[list]|None, error, ts}} — state/models_cache.json에 캐시(24시간)."""
    cp = state_dir(repo) / "models_cache.json"
    if not refresh and cp.is_file():
        try:
            c = json.loads(cp.read_text(encoding="utf-8"))
            if c.get("v") == 2 and time.time() - c.get("ts", 0) < CACHE_TTL and set(c.get("agents", {})) >= set(catalog["agents"]):
                return c["agents"]
        except (json.JSONDecodeError, OSError):
            pass
    res = {}
    for ag, spec in catalog["agents"].items():
        binp = shutil.which(str(spec.get("bin") or ag))
        real = os.path.realpath(binp) if binp else ""
        # snap 격리 설치본은 $HOME의 숨김 폴더(Orca CLI 심 ~/.config/orca/…)를 실행하지 못해 worker_done·ask를 보낼 수 없다
        confined = bool(binp) and (binp.startswith("/snap/") or real.startswith("/snap/") or real.endswith("/usr/bin/snap"))
        info = {"bin": binp, "models": None, "error": None, "confined": confined}
        if binp:
            ms, err = list_models(spec.get("list"), binp, timeout)
            info["models"] = sorted(ms) if ms is not None else None
            info["error"] = err
        res[ag] = info
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"v": 2, "ts": time.time(), "agents": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return res


def model_ids(m):
    return [str(m.get("model"))] + [str(x) for x in (m.get("model_by_effort") or {}).values()]


# ───────────────────────── 계열 따라가기 (카탈로그 family:)
# family: 'gpt-{v}-sol' 처럼 판 번호 자리를 {v}로 적으면, CLI 모델 목록에서 그 꼴의 가장 높은 판을 쓴다.
# 'gemini-{v}-neon-{effort}' 처럼 {effort}가 있으면 그 판의 effort별 ID로 model_by_effort를 다시 만든다.
# 카탈로그의 model/model_by_effort는 목록을 모를 때(별칭 CLI·목록 실패) 쓰는 기본값이다.

def family_regex(fam):
    rx = re.escape(str(fam)).replace(r"\{v\}", r"(?P<v>\d+(?:[.\-]\d+)*)").replace(r"\{effort\}", r"(?P<e>" + "|".join(EFFORTS) + ")")
    return re.compile("^" + rx + "$")


def vkey(v):
    return tuple(int(x) for x in re.split(r"[.\-]", v) if x.isdigit())


def family_members(fam, known):
    """{판: {effort|None: id}} — 목록에서 계열에 맞는 ID."""
    rx, out = family_regex(fam), {}
    for i in known or ():
        mm = rx.match(i)
        if mm:
            out.setdefault(mm.group("v"), {})[mm.groupdict().get("e")] = i
    return out


def resolve_model(m, known):
    """family가 있으면 목록의 가장 높은 판으로 바꾼 사본. 바꿨으면 resolved_from에 원래 ID."""
    fam = m.get("family")
    if not fam or known is None:
        return m
    mem = family_members(fam, known)
    if not mem:
        return m
    best = max(mem, key=vkey)
    ids = mem[best]
    out = dict(m)
    if "{effort}" in str(fam):
        mbe = {e: ids[e] for e in EFFORTS if e in ids}
        if not mbe:
            return m
        out["model_by_effort"] = mbe
        out["model"] = mbe.get("medium") or next(iter(mbe.values()))
    else:
        out["model"] = ids.get(None) or next(iter(ids.values()))
    if out["model"] != m.get("model"):
        out["resolved_from"] = m.get("model")
    out["family_version"] = best
    return out


def resolve_catalog(catalog, disc):
    """discover 결과로 모든 family 항목을 최신 판으로 맞추고, 벤치 점수가 있으면 품질을 그것으로 바꾼 카탈로그 사본."""
    models = {k: resolve_model(m, (disc.get(m.get("agent")) or {}).get("models")) for k, m in catalog["models"].items()}
    return apply_bench(dict(catalog, models=models))


# ───────────────────────── 벤치 점수 → 품질 (tools/bench_scores.yaml, models.py bench로 갱신)
# 카탈로그 항목의 bench_name('GPT-{v} Sol')과 에이전트의 bench_agent('Codex')로 Terminal-Bench 줄을 찾는다.
# 판: family로 고른 판(없으면 벤치에 있는 가장 높은 판). 그 판 점수가 아직 없으면 바로 아래 판 점수를 쓰고 '옛 판'으로 적는다.
# 품질 = 5 × (그 모델의 가장 좋은 effort 해결률 ÷ 카탈로그 안 최고 해결률), 최저 1. 다른 CLI의 점수는 빌리지 않는다(실행 환경 차이가 크다).

def bench_pick(m, agents, rows):
    ag = (agents.get(m.get("agent")) or {}).get("bench_agent")
    pat = m.get("bench_name")
    if not (ag and pat and rows):
        return None
    rx = re.compile("^" + re.escape(str(pat)).replace(r"\{v\}", r"(?P<v>\d+(?:\.\d+)*)") + "$", re.I)
    by = {}
    for r in rows:
        mm = rx.match(str(r.get("model", "")))
        if mm and str(r.get("agent", "")).lower() == str(ag).lower() and r.get("acc") is not None:
            by.setdefault(mm.group("v"), []).append(r)
    if not by:
        return None
    want = str(m.get("family_version") or "").replace("-", ".")
    vs = sorted(by, key=vkey)
    if want and want in by:
        v, old = want, False
    elif want:
        lower = [x for x in vs if vkey(x) < vkey(want)]
        if not lower:
            return None
        v, old = lower[-1], True
    else:
        v, old = vs[-1], False
    best = max(by[v], key=lambda r: r["acc"])
    return {"row": best, "version": v, "old": old}


def apply_bench(catalog):
    rows = (catalog.get("bench") or {}).get("tb") or []
    if not rows:
        return catalog
    picks = {k: bench_pick(m, catalog["agents"], rows) for k, m in catalog["models"].items()}
    top = max((p["row"]["acc"] for p in picks.values() if p), default=0)
    if top <= 0:
        return catalog
    src = ((catalog.get("bench") or {}).get("sources") or {}).get("tb") or {}
    models = {}
    for k, m in catalog["models"].items():
        p = picks.get(k)
        if not p or m.get("quality_pinned"):
            models[k] = dict(m, quality_src="models.yaml" if m.get("quality_pinned") else "카탈로그 추정(벤치 없음)")
            continue
        r = p["row"]
        q = round(max(1.0, 5 * r["acc"] / top), 1)   # 해결률에 비례(0%면 0 → 1로 막는다). 1+4x는 약한 모델을 부풀렸다
        models[k] = dict(m, quality=q, quality_catalog=m.get("quality"),
                         quality_src=f"{src.get('name', 'Terminal-Bench')} {r['agent']} · {r['model']} {r.get('effort') or ''} {r['acc']:.1f}%"
                                     + (" (옛 판)" if p["old"] else ""))
    return dict(catalog, models=models)


def unmatched_models(catalog, disc):
    """{agent: (새 모델[], 아는 계열의 옛 판 수)} — 카탈로그 어느 항목에도 안 맞는 ID를 계열 옛 판과 나눈다."""
    out = {}
    for ag, info in disc.items():
        known = info.get("models")
        if not known:
            continue
        used, fams = set(), []
        for m in catalog["models"].values():
            if m.get("agent") == ag:
                used.update(model_ids(m))
                if m.get("family"):
                    fams.append(family_regex(m["family"]))
        new, old = [], 0
        for i in known:
            if i in used:
                continue
            if any(rx.match(i) for rx in fams):
                old += 1
            else:
                new.append(i)
        if new or old:
            out[ag] = (sorted(new), old)
    return out


# ───────────────────────── CLI 업데이트 (카탈로그 agents.*.update)

def cli_version(binp, spec, timeout=20):
    code, out, err = _run([binp] + [str(x) for x in (spec.get("version") or ["--version"])], timeout)
    txt = (out or err).strip().splitlines()
    mm = re.search(r"\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?", txt[0] if txt else "")
    return mm.group(0) if mm else (txt[0][:40] if txt else "")


def update_clis(repo, catalog, pol, agents=None, check_only=False, timeout=300, log=print):
    """설치된 구독 CLI마다 자체 업데이트 명령을 돌린다 → {agent: {bin, before, after, status, detail}}.
    snap 격리 설치본은 snap이 관리하므로 건너뛴다. 결과는 state/cli_update.json에 남긴다."""
    allow = agents or rcfg(pol, "agents") or list(catalog["agents"])
    res = {}
    for ag, spec in catalog["agents"].items():
        if ag not in allow:
            continue
        binp = shutil.which(str(spec.get("bin") or ag))
        if not binp:
            res[ag] = {"bin": None, "status": "없음", "detail": "CLI 없음"}
            continue
        real = os.path.realpath(binp)
        if binp.startswith("/snap/") or real.startswith("/snap/") or real.endswith("/usr/bin/snap"):
            res[ag] = {"bin": binp, "status": "건너뜀", "detail": "snap 설치본(snap refresh가 관리)", "before": cli_version(binp, spec)}
            continue
        before = cli_version(binp, spec)
        cmd = spec.get("update")
        if not cmd or check_only:
            res[ag] = {"bin": binp, "before": before, "after": before, "status": "확인" if cmd else "명령 없음", "detail": ""}
            continue
        log(f"  {ag}: {' '.join([os.path.basename(binp)] + [str(x) for x in cmd])} …")
        code, out, err = _run([binp] + [str(x) for x in cmd], timeout)
        after = cli_version(binp, spec)
        tail = (out + "\n" + err).strip().splitlines()
        res[ag] = {"bin": binp, "before": before, "after": after,
                   "status": ("갱신" if after != before else "최신") if code == 0 else "실패",
                   "detail": "" if code == 0 else (tail[-1][:160] if tail else f"종료코드 {code}")}
    p = state_dir(repo) / "cli_update.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ts": time.time(), "check_only": check_only, "agents": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return res


def update_due(repo, pol):
    """routing.auto_update: daily(기본) | weekly | off — 마지막 업데이트가 그보다 오래됐으면 True."""
    mode = str(rcfg(pol, "auto_update", "daily")).lower()
    span = {"daily": 24 * 3600, "weekly": 7 * 24 * 3600}.get(mode)
    if not span:
        return False
    try:
        d = json.loads((state_dir(repo) / "cli_update.json").read_text(encoding="utf-8"))
        return d.get("check_only") or time.time() - float(d.get("ts", 0)) >= span
    except (OSError, ValueError):
        return True


def availability(key, m, pol, disc):
    """(쓸 수 있나, 이유)."""
    ag = m.get("agent")
    allow = rcfg(pol, "agents")
    if allow is not None and ag not in allow:
        return False, f"{ag}는 routing.agents에 없음"
    if key in (rcfg(pol, "exclude") or []):
        return False, "routing.exclude"
    info = disc.get(ag)
    if not info or not info.get("bin"):
        return False, f"{ag} CLI 없음"
    if info.get("confined") and not (pol.get("agents") or {}).get(ag, {}).get("allow_confined"):
        return False, f"{ag}가 snap 격리 설치본이라 워커가 orca CLI를 못 부른다(worker_done·ask 불가). snap이 아닌 설치본이 필요"
    known = info.get("models")
    if known is not None and not any(i in known for i in model_ids(m)):
        return False, f"{m.get('model')}가 {ag} 모델 목록에 없음"
    return True, ""


# ───────────────────────── 이력·학습

def kit_id_of(title, ids):
    """Orca 작업 제목 → (키트 ID, 모드). 'X-01' new, 'X-01-RV[-s]' review, 'X-01 retry-r1' retry, 'X-01 handoff-h1' handoff(한도 전환 이어받기)."""
    head = (title or "").split()[0] if title else ""
    mode = "retry" if " retry-" in (title or "") else ("handoff" if " handoff-" in (title or "") else "new")
    if head in ids:
        return head, mode
    mm = re.match(r"(.+?)-RV(?:-.+)?$", head)
    if mm and mm.group(1) in ids:
        return mm.group(1), "review"
    return None, mode


def read_dispatch(repo, ids):
    """dispatch.tsv → [{title, kit_id, mode, did, ts, agent, model, effort, key, difficulty}] (오래된 5칸 형식도 읽는다)."""
    p = state_dir(repo) / "dispatch.tsv"
    rows = []
    if not p.is_file():
        return rows
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        c = ln.split("\t")
        if len(c) < 5:
            continue
        kid, mode = kit_id_of(c[0], ids)
        r = {"title": c[0], "kit_id": kid, "mode": mode, "did": c[2], "ts": c[4],
             "agent": c[5] if len(c) > 5 else "", "model": c[6] if len(c) > 6 else "",
             "effort": c[7] if len(c) > 7 else "", "key": c[8] if len(c) > 8 else "",
             "difficulty": c[9] if len(c) > 9 else "", "jev": c[10] if len(c) > 10 else ""}
        rows.append(r)
    return rows


def read_switches(repo):
    """한도 전환으로 멈춘 dispatch ID 집합(state/quota_switch.tsv 3번째 칸). 그 투입은 실적(성공·실패)에 넣지 않는다."""
    p = state_dir(repo) / "quota_switch.tsv"
    out = set()
    if p.is_file():
        for ln in p.read_text(encoding="utf-8").splitlines():
            c = ln.split("\t")
            if len(c) >= 3 and not ln.startswith("#"):
                out.add(c[2])
    return out


def read_feedback(repo):
    p = state_dir(repo) / "route_feedback.tsv"
    out = []
    if p.is_file():
        for ln in p.read_text(encoding="utf-8").splitlines():
            c = ln.split("\t")
            if len(c) >= 3 and not ln.startswith("#"):
                try:
                    out.append({"ts": c[0], "kit_id": c[1], "score": float(c[2]), "note": c[3] if len(c) > 3 else ""})
                except ValueError:
                    pass
    return out


def key_of_row(r, models):
    if r.get("key") in models:
        return r["key"]
    for k, m in models.items():
        if r.get("agent") == m.get("agent") and r.get("model") and r["model"] in model_ids(m):
            return k
    return None


def band(d):
    try:
        d = float(d)
    except (TypeError, ValueError):
        return "mid"
    return "low" if d < 2.5 else ("high" if d >= 3.5 else "mid")


def outcomes(rows, tasks, feedback, models, skip=None):
    """구현 투입(new·handoff·retry) 하나마다 결과 점수 0~1. skip: 셈에서 뺄 dispatch(한도 전환으로 멈춘 것 — 품질 실패가 아니다).
    handoff(한도 전환 이어받기)는 새 투입처럼 센다.
    merged 작업: 마지막 새 투입 1(뒤에 retry가 있으면 0.6), 그 전 새 투입 0(다시 띄웠으니 실패), retry는 마지막 1·앞 0.5.
    todo/ready로 되돌아간 작업: 그 투입들 0. running/review: 마지막은 미정, 그 전 새 투입 0.
    route_feedback.tsv(검토 판정)는 그 시각 이전의 마지막 구현 투입 점수를 덮는다."""
    by = {}
    skip = skip or set()
    for r in rows:
        if r["kit_id"] and r["mode"] in ("new", "retry", "handoff") and r["did"] not in skip:
            by.setdefault(r["kit_id"], []).append(r)
    res = []
    for tid, rs in by.items():
        rs.sort(key=lambda r: r["ts"])
        st = str((tasks.get(tid) or {}).get("status", "")).lower()
        scores = {}
        fresh = [r for r in rs if r["mode"] in ("new", "handoff")]
        retr = [r for r in rs if r["mode"] == "retry"]
        if st == "merged":
            for r in fresh[:-1]:
                scores[id(r)] = 0.0
            if fresh:
                later = [r for r in retr if r["ts"] >= fresh[-1]["ts"]]
                scores[id(fresh[-1])] = 0.6 if later else 1.0
            for r in retr[:-1]:
                scores[id(r)] = 0.5
            if retr:
                scores[id(retr[-1])] = 1.0
        elif st in ("todo", "ready"):
            for r in rs:
                scores[id(r)] = 0.0
        else:
            for r in fresh[:-1]:
                scores[id(r)] = 0.0
        for f in feedback:
            if f["kit_id"] != tid:
                continue
            before = [r for r in rs if r["ts"] <= f["ts"]]
            if before:
                scores[id(before[-1])] = max(0.0, min(1.0, f["score"]))
        for r in rs:
            if id(r) in scores:
                k = key_of_row(r, models)
                if k:
                    res.append({"key": k, "band": band(r["difficulty"]), "score": scores[id(r)], "kit_id": tid,
                                "agent": r["agent"], "did": r["did"], "difficulty": r["difficulty"], "jev": r.get("jev", "")})
    return res


def learned(outs):
    """{(key, band): (합, n)}"""
    acc = {}
    for o in outs:
        s, n = acc.get((o["key"], o["band"]), (0.0, 0))
        acc[(o["key"], o["band"])] = (s + o["score"], n + 1)
    return acc


def failed_keys(outs, tid):
    return {o["key"] for o in outs if o["kit_id"] == tid and o["score"] < 0.5}


# ───────────────────────── 부하·쿨다운·사용량

def running_by_agent(rows, tasks, live_dids=None):
    """에이전트별 진행 중 워커 수. live_dids(Orca가 진행 중이라고 본 dispatch)가 있으면 그것으로, 없으면 tasks.yaml running으로 어림한다."""
    cnt = {}
    if live_dids is not None:
        for r in rows:
            if r["did"] in live_dids and r["agent"]:
                cnt[r["agent"]] = cnt.get(r["agent"], 0) + 1
        return cnt
    last = {}
    for r in rows:
        if r["kit_id"] and r["mode"] in ("new", "handoff"):
            last[r["kit_id"]] = r
    for tid, r in last.items():
        if str((tasks.get(tid) or {}).get("status", "")).lower() == "running" and r["agent"]:
            cnt[r["agent"]] = cnt.get(r["agent"], 0) + 1
    return cnt


def read_cooldowns(repo):
    p = state_dir(repo) / "cooldown.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except json.JSONDecodeError:
        d = {}
    now = time.time()
    return {k: v for k, v in d.items() if isinstance(v, dict) and v.get("until", 0) > now}


def write_cooldown(repo, target, minutes, reason=""):
    p = state_dir(repo) / "cooldown.json"
    d = read_cooldowns(repo)
    if minutes <= 0:
        d.pop(target, None)
    else:
        d[target] = {"until": time.time() + minutes * 60, "reason": reason}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def orca_home():
    return Path(os.environ.get("KIT_ORCA_HOME") or Path.home() / ".config/orca")


def orca_usage(days=7):
    """Orca가 모으는 Claude·Codex 사용량(모든 프로젝트 합, 구독 한도와 같은 범위) → {agent: {model: {day: 출력 토큰}}}.
    Orca 내부 파일이라 형식이 바뀌면 조용히 빈 값을 돌려준다. 모델·날짜 합계만 읽는다(경로는 읽지 않는다)."""
    out = {}
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    for ag, fn in (("claude", "orca-claude-usage.json"), ("codex", "orca-codex-usage.json")):
        p = orca_home() / fn
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in d.get("dailyAggregates") or []:
            day, model = str(row.get("day", "")), str(row.get("model", ""))
            if day < since or not model:
                continue
            tok = int(row.get("outputTokens") or 0)
            out.setdefault(ag, {}).setdefault(model, {})
            out[ag][model][day] = out[ag][model].get(day, 0) + tok
    return out


# ───────────────────────── 잔여 한도 (Orca가 구독마다 모으는 사용률: orca account list --json → rateLimits)
# 창마다 usedPercent·windowMinutes·resetsAt. Claude는 Fable 주간(fableWeekly)을 따로, Antigravity는 모델 묶음(buckets.group)별로 센다.
# 숫자만 state/quota_cache.json에 5분 캐시한다(계정 이메일 등은 읽지도 남기지도 않는다).
QUOTA_TTL = 300
QUOTA_DEFAULT = {"enabled": True, "hard_percent": 98, "session_soft": 70, "pace_margin": 10, "use_it_hours": 24,
                 "on_exhausted": "switch", "wait_minutes": 30,   # 한도 정지 워커: switch(다른 구독으로 이어받기) | wait
                 "switch_min_headroom": 30, "switch_need_minutes": 120}  # 넘겨받을 구독: 창마다 이만큼(%) 남고, 지금 속도로 이 시간(분) 안에 다시 차지 않을 것


def quota_cfg(pol):
    q = rcfg(pol, "quota", {})
    return {**QUOTA_DEFAULT, **(q if isinstance(q, dict) else {"enabled": bool(q)})}


def _win(w):
    if not isinstance(w, dict) or w.get("usedPercent") is None:
        return None
    try:
        return {"used": float(w["usedPercent"]), "minutes": int(w.get("windowMinutes") or 0), "resets": float(w.get("resetsAt") or 0) / 1000}
    except (TypeError, ValueError):
        return None


def parse_quota(res):
    """orca account list 결과 → {provider: {status, plan, windows{이름: 창}, groups{묶음: {session, weekly}}, alt[다른 계정 창]}}."""
    rl = (res or {}).get("rateLimits") or {}
    out = {}
    for prov, v in rl.items():
        if not isinstance(v, dict) or "provider" not in v:
            continue
        wins = {k: _win(x) for k, x in v.items() if isinstance(x, dict) and "usedPercent" in x}
        groups = {}
        for b in v.get("buckets") or []:
            w, g = _win(b), str((b or {}).get("group") or "")
            if w and g:
                slot = "session" if w["minutes"] and w["minutes"] <= 600 else "weekly"
                cur = groups.setdefault(g, {}).get(slot)
                if not cur or w["used"] > cur["used"]:
                    groups[g][slot] = w
        alt = []
        for a in rl.get("inactive" + prov[:1].upper() + prov[1:] + "Accounts") or []:
            r = (a or {}).get("rateLimits") or {}
            if r.get("status") == "ok":
                ws = {k: _win(r.get(k)) for k in ("session", "weekly") if _win(r.get(k))}
                if ws:
                    alt.append(ws)
        out[prov] = {"status": v.get("status"), "plan": v.get("planType"), "windows": {k: w for k, w in wins.items() if w},
                     "groups": groups, "alt": alt, "updated": float(v.get("updatedAt") or 0) / 1000}
    return out


def read_quota(repo, refresh=False, net=True, timeout=15):
    """{provider: …} | {} — 캐시가 5분 안이면 그것을, 아니면 Orca에 묻는다(net=False면 캐시만). 실패도 5분 캐시한다."""
    cp = state_dir(repo) / "quota_cache.json"
    try:
        c = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {}
    except (OSError, json.JSONDecodeError):
        c = {}
    if c and (not net or (not refresh and time.time() - float(c.get("ts", 0)) < QUOTA_TTL)):
        return c.get("providers") or {}
    if not net:
        return {}
    code, out, err = _run([os.environ.get("ORCA_CLI_COMMAND") or "orca", "account", "list", "--json"], timeout)
    prov = {}
    try:
        d = json.loads(out)
        if d.get("ok"):
            prov = parse_quota(d.get("result") or {})
    except (json.JSONDecodeError, AttributeError, TypeError):
        prov = {}
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"ts": time.time(), "providers": prov}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return prov


def _hm(ts):
    t = dt.datetime.fromtimestamp(ts)
    return t.strftime("%H:%M") if t.date() == dt.date.today() else t.strftime("%m-%d %H:%M")


def model_windows(m, agents, quota):
    """그 모델에 걸리는 창들 → (provider, {이름: 창}, 정보) | None."""
    prov = (agents.get(m.get("agent")) or {}).get("quota") or m.get("agent")
    info = quota.get(prov)
    if not info or info.get("status") != "ok":
        return None
    base = {"session", "weekly", "monthly"}   # 공급자 공통 창. 모델별 창(fableWeekly 등)은 quota_extra로 고른 모델에만
    wins = {k: w for k, w in (info.get("windows") or {}).items() if k in base}
    grp = m.get("quota_group")
    if grp and grp in (info.get("groups") or {}):
        wins = dict(info["groups"][grp])
    for extra in m.get("quota_extra") or []:
        if extra in (info.get("windows") or {}):
            wins[extra] = info["windows"][extra]
    return prov, wins, info


def quota_effect(m, agents, quota, pol, now=None):
    """(제외 이유 | None, 점수 보정, 이유[]) — 소진(hard_percent 이상)은 초기화까지 제외, 빠듯하면 벌점, 곧 초기화되는데 많이 남으면 가점."""
    cfg = quota_cfg(pol)
    mw = model_windows(m, agents, quota) if cfg["enabled"] else None
    if not mw:
        return None, 0.0, []
    prov, wins, info = mw
    now = now or time.time()
    adj, why = 0.0, []
    for name, w in sorted(wins.items()):
        if w["resets"] and w["resets"] <= now:
            continue   # 이미 초기화된 창(캐시가 그 전 값)
        short = bool(w["minutes"]) and w["minutes"] <= 600
        label = "세션" if short else ("Fable 주간" if name == "fableWeekly" else "주간")
        if w["used"] >= float(cfg["hard_percent"]):
            alt = [a for a in info.get("alt") or [] if all(x["used"] < float(cfg["hard_percent"]) for x in a.values())]
            hint = f" · 다른 {prov} 계정은 여유(Orca 계정 전환)" if alt else ""
            return f"{prov} {label} 한도 {w['used']:.0f}% 소진 ({_hm(w['resets'])} 초기화){hint}" if w["resets"] else f"{prov} {label} 한도 소진{hint}", 0.0, []
        if short:
            if w["used"] > float(cfg["session_soft"]):
                adj -= 2 * (w["used"] - float(cfg["session_soft"])) / (100 - float(cfg["session_soft"]))
                why.append(f"{label} {w['used']:.0f}%")
        elif w["minutes"] and w["resets"]:
            left_h = (w["resets"] - now) / 3600
            elapsed = max(0.0, min(1.0, 1 - left_h * 60 / w["minutes"]))
            over = w["used"] - 100 * elapsed
            if over > float(cfg["pace_margin"]):
                adj -= min(2.0, 0.5 + (over - float(cfg["pace_margin"])) / 20)
                why.append(f"{label} {w['used']:.0f}% (기간 {100 * elapsed:.0f}% 지남)")
            elif left_h <= float(cfg["use_it_hours"]) and w["used"] < 60:
                adj += 0.5
                why.append(f"{label} {100 - w['used']:.0f}% 남고 {left_h:.0f}시간 뒤 초기화")
    return None, adj, why


def headroom(m, agents, quota, pol, need_minutes, now=None):
    """한도 전환에서 넘겨받을 모델의 여유 → (넘겨도 되나, 이유).
    창마다 남은 양이 switch_min_headroom(%) 이상이어야 하고, 5시간 창은 지금까지의 소모 속도(사용률 ÷ 지난 시간)로
    need_minutes 안에 다시 차지 않아야 한다(곧 다시 찰 구독으로 넘기면 또 멈춘다). 한도를 모르면 넘긴다."""
    cfg = quota_cfg(pol)
    mw = model_windows(m, agents, quota)
    if not mw:
        return True, ""
    prov, wins, _ = mw
    now = now or time.time()
    for name, w in sorted(wins.items()):
        if w["resets"] and w["resets"] <= now:
            continue
        short = bool(w["minutes"]) and w["minutes"] <= 600
        label = f"{prov} " + ("세션" if short else ("Fable 주간" if name == "fableWeekly" else "주간"))
        rem = 100 - w["used"]
        if rem < float(cfg["switch_min_headroom"]):
            return False, f"{label} 남은 {rem:.0f}% < {cfg['switch_min_headroom']}%"
        if short and w["minutes"] and w["resets"] and w["used"] > 0:
            elapsed = w["minutes"] - (w["resets"] - now) / 60
            if elapsed >= 30:   # 막 열린 창은 속도를 믿지 않는다
                fill = rem / (w["used"] / elapsed)
                left = (w["resets"] - now) / 60
                if fill < min(need_minutes, left):
                    return False, f"{label} {w['used']:.0f}%, 지금 속도면 {fill:.0f}분 뒤 다시 찬다(필요 {min(need_minutes, left):.0f}분)"
    return True, ""


def exhausted(m, agents, quota, min_used=100.0, now=None):
    """그 모델을 막고 있는 창 [(이름표, 사용률, 초기화 시각)] — 정지 점검·한도 전환이 쓴다(min_used 이상, 아직 초기화 전)."""
    mw = model_windows(m, agents, quota)
    if not mw:
        return []
    prov, wins, _ = mw
    now = now or time.time()
    out = []
    for name, w in sorted(wins.items()):
        if w["used"] >= min_used and (not w["resets"] or w["resets"] > now):
            short = bool(w["minutes"]) and w["minutes"] <= 600
            out.append((f"{prov} " + ("세션" if short else ("Fable 주간" if name == "fableWeekly" else "주간")), w["used"], w["resets"]))
    return out


def today_tokens(usage, agent):
    day = dt.date.today().isoformat()
    return sum(v.get(day, 0) for v in (usage.get(agent) or {}).values())


# ───────────────────────── 작업 분류

def _text(v):
    if isinstance(v, (list, tuple)):
        return " ".join(map(str, v))
    return str(v or "")


def _hits(text, words):
    found = []
    for w in words:
        w2 = w.lower()
        if re.fullmatch(r"[a-z]{1,3}", w2):
            if re.search(rf"(?<![a-z]){re.escape(w2)}(?![a-z])", text):
                found.append(w)
        elif w2 in text:
            found.append(w)
    return found


def dependents(tid, tasks):
    """tid에 (전이적으로) 기대는 미완료 작업 수."""
    rev = {}
    for k, t in tasks.items():
        for d in kitlib.as_list(t.get("deps")):
            rev.setdefault(d, set()).add(k)
    seen, stack = set(), [tid]
    while stack:
        for n in rev.get(stack.pop(), ()):
            if n not in seen and str(tasks[n].get("status", "todo")).lower() != "merged":
                seen.add(n)
                stack.append(n)
    return len(seen)


_DF = {}


def hard_df(tasks, words):
    """어려운 말마다 그 말이 나오는 작업 비율. 프로젝트 대부분에 나오는 말(그 분야의 일상어)은 난이도 신호가 아니다."""
    key = (id(tasks), len(tasks), tuple(words))
    if key not in _DF:
        n = max(1, len(tasks))
        cnt = {}
        for t in tasks.values():
            txt = (_text(t.get("title")) + " " + _text(t.get("acceptance")) + " " + _text(t.get("notes"))).lower()
            for w in set(_hits(txt, words)):
                cnt[w] = cnt.get(w, 0) + 1
        _DF.clear()
        _DF[key] = {w: c / n for w, c in cnt.items()} if n >= 10 else {}
    return _DF[key]


def classify(t, tasks, pol, mode="new", prior_fails=0):
    """작업 → {difficulty(1~5), tags, critical, reasons}."""
    reasons = []
    clf = pol.get("classifier") or {}
    words = (_text(t.get("title")) + " " + _text(t.get("acceptance")) + " " + _text(t.get("notes"))).lower()
    paths = [str(p) for p in kitlib.as_list(t.get("write_scope"))]
    alltext = words + " " + " ".join(paths).lower()
    tags = set(kitlib.as_list(t.get("tags")))
    for tag, ws in TAG_WORDS.items():
        if _hits(alltext, ws + list((clf.get("tag_words") or {}).get(tag, []))):
            tags.add(tag)
    tid = str(t.get("id", ""))
    d = 2.0
    if str(t.get("layer")) == "L1":
        d = 4.0
        tags.add("integration")
        reasons.append("L1 통합")
    elif tid.startswith("S-") or kitlib.task_module(t) == "system":
        d = 4.0
        tags.add("integration")
        reasons.append("체계 통합")
    title = _text(t.get("title")).lower()
    if _hits(title, TAG_WORDS["contract"]) or any(p.startswith(("contracts/", "contract/")) for p in paths):
        d += 1
        reasons.append("계약 작업")
    elif tid.startswith("F-"):
        d += 0.5
        reasons.append("공통 기반")
    n = len(paths)
    dirs = {p.strip("/").split("/")[0] for p in paths if p.strip("/")}
    if n >= 12:
        d += 1
        reasons.append(f"쓰기 범위 {n}곳")
    elif n >= 6:
        d += 0.5
        reasons.append(f"쓰기 범위 {n}곳")
    if len(dirs) >= 3:
        d += 0.5
        reasons.append(f"최상위 폴더 {len(dirs)}개에 걸침")
    if any("**" in p or p.strip() in (".", "/", "./") for p in paths):
        d += 0.5
        reasons.append("넓은 쓰기 범위(**)")
    acc = t.get("acceptance")
    na = len(acc) if isinstance(acc, list) else len([s for s in re.split(r"[.;\n]\s+|\n", _text(acc)) if s.strip()])
    if na >= 12:
        d += 1
        reasons.append(f"완료 조건 {na}개")
    elif na >= 6:
        d += 0.5
        reasons.append(f"완료 조건 {na}개")
    hw = HARD_WORDS + list(clf.get("hard_words") or [])
    df = hard_df(tasks, hw)
    common = float(clf.get("common_ratio", 0.3))
    hard = [w for w in _hits(words, hw) if df.get(w, 0) <= common]
    easy = _hits(title, EASY_WORDS + list(clf.get("easy_words") or []))   # 쉬운 말은 제목만 본다(완료 조건엔 흔하다)
    if hard:
        d += min(1.0, 0.5 * len(hard))
        reasons.append("어려운 말: " + ", ".join(hard[:4]))
    elif easy:
        d -= 1
        reasons.append("쉬운 말: " + ", ".join(easy[:3]))
    if t.get("perf"):
        d += 0.5
        reasons.append("성능 측정")
    if len(kitlib.as_list(t.get("deps"))) >= 4:
        d += 0.5
        reasons.append(f"선행 {len(kitlib.as_list(t.get('deps')))}개를 합침")
    ex = t.get("difficulty")
    if ex is not None and str(ex).strip():
        v = DIFF_WORDS.get(str(ex).strip().lower())
        if v is None:
            try:
                v = float(ex)
            except ValueError:
                v = None
        if v is not None:
            d = float(v)
            reasons = [f"difficulty: {ex} (작업 항목)"]
    if prior_fails and rcfg(pol, "escalate_on_retry", True):
        d += min(2, prior_fails)
        reasons.append(f"앞선 시도 실패 {prior_fails}회 → 올림")
    if mode == "review":
        tags.add("review")
        d = max(d, 3.0)
    elif mode == "retry":
        tags.add("debug")
        d = max(1.0, d - 0.5)
    deps_n = dependents(tid, tasks) if tid in tasks else 0
    critical = deps_n >= int(rcfg(pol, "critical_dependents", 5))
    if critical:
        reasons.append(f"뒤에 기대는 작업 {deps_n}개(임계 경로: 품질 여유 +0.5)")
    return {"difficulty": max(1.0, min(5.0, d)), "tags": sorted(tags), "critical": critical, "reasons": reasons}


# ───────────────────────── JEV (선택, 기본 꺼짐)

JEV_DEFAULT = {"enabled": False, "mode": "shadow", "min_confidence": 0.6, "timeout": 8, "model": "jev-latest",
               "url": "https://api.typesafe.ai/v1/systemone", "key_env": "TYPESAFE_API_KEY"}
JEV_LEVELS = [
    "Trivial: a mechanical edit such as a typo, rename, version bump or small config change",
    "Easy: a small, well-specified change in one place with obvious tests",
    "Moderate: a typical feature or fix touching a few files; needs some design and tests",
    "Hard: cross-module work, concurrency, protocols, security or unclear requirements; easy to get subtly wrong",
    "Very hard: foundational design or contracts many other tasks depend on; mistakes are costly and hard to undo",
]


def jev_cfg(pol):
    return {**JEV_DEFAULT, **(pol.get("jev") or {})}


def jev_state(t):
    """외부로 보내는 것은 제목과 완료 조건뿐이다(경로·메모·코드는 보내지 않는다)."""
    acc = t.get("acceptance")
    acc = "\n".join(f"- {x}" for x in acc) if isinstance(acc, list) else _text(acc)
    return f"Software task for an AI coding agent working alone in a git worktree.\nTitle: {_text(t.get('title'))}\nAcceptance criteria:\n{acc}"


def jev_judge(repo, t, pol, net=True):
    """JEV 난이도 판정 → None(꺼짐) | {difficulty 1~5, confidence, model, cached} | {error}.
    같은 내용은 state/jev_cache.json에서 다시 쓴다. net=False면 캐시만 본다(대시보드처럼 자주 도는 곳)."""
    cfg = jev_cfg(pol)
    if not cfg.get("enabled"):
        return None
    state = jev_state(t)
    h = hashlib.sha256(json.dumps([cfg["model"], state, JEV_LEVELS], ensure_ascii=False).encode()).hexdigest()[:20]
    cp = state_dir(repo) / "jev_cache.json"
    try:
        cache = json.loads(cp.read_text(encoding="utf-8")) if cp.is_file() else {}
    except (OSError, json.JSONDecodeError):
        cache = {}
    if h in cache:
        return dict(cache[h], cached=True)
    if not net:
        return {"error": "캐시에 없음"}
    key = os.environ.get(str(cfg["key_env"]))
    if not key:
        return {"error": f"{cfg['key_env']} 없음"}
    body = {"state": state, "model": cfg["model"],
            "questions": {"difficulty": {"type": "score", "criteria": JEV_LEVELS,
                                         "instructions": "How difficult is this task for an AI coding agent to complete correctly on the first attempt?"}}}
    req = urllib.request.Request(str(cfg["url"]), data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=float(cfg["timeout"])) as r:
            resp = json.loads(r.read().decode("utf-8"))
        ans = resp["answers"]["difficulty"]
        res = {"difficulty": round(1.0 + float(ans["score"]), 2), "confidence": round(float(ans.get("confidence", 0)), 3),
               "model": str(resp.get("model", cfg["model"]))}
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        return {"error": f"JEV 호출 실패: {str(e)[:120]}"}
    cache[h] = dict(res, ts=time.time())
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return dict(res, cached=False)


def apply_jev(c, t, repo, pol, mode, net=True):
    """classify 결과에 JEV 판정을 붙인다. live이고 신뢰도가 기준 이상이면 난이도를 바꾼다. 작업에 difficulty를 직접 줬으면 묻지 않는다."""
    if mode != "new" or t.get("difficulty") is not None:
        return None
    j = jev_judge(repo, t, pol, net=net)
    if not j:
        return None
    if "difficulty" not in j:
        if net:
            c["reasons"].append(f"JEV 건너뜀({j['error']})")
        return j
    cfg = jev_cfg(pol)
    live = cfg.get("mode") == "live" and j["confidence"] >= float(cfg.get("min_confidence", 0.6))
    j["applied"] = live
    c["reasons"].append(f"JEV {j['difficulty']:.1f}(신뢰 {j['confidence']:.2f}, {'적용' if live else '기록만'})")
    if live:
        c["rule_difficulty"] = c["difficulty"]
        c["difficulty"] = max(1.0, min(5.0, j["difficulty"]))
    return j


# ───────────────────────── 점수와 결정

def pick_effort(m, d, objective, q):
    """난이도 → effort. 강한 모델에는 한 단계 낮게, quality 목표면 한 단계 높게."""
    idx = {1: 0, 2: 0, 3: 1, 4: 2, 5: 3}[max(1, min(5, int(d + 0.5)))]
    idx += 1 if objective == "quality" else (-1 if objective in ("economy", "speed") else 0)
    if q - d >= 2:
        idx -= 1
    idx = max(0, min(len(EFFORTS) - 2, idx))   # max는 명시할 때만
    want = EFFORTS[idx]
    mbe = m.get("model_by_effort") or {}
    if mbe:
        order = sorted(mbe, key=lambda e: abs(EFFORTS.index(e) - idx) if e in EFFORTS else 9)
        return None, str(mbe[order[0]]), order[0]
    effs = [e for e in (m.get("efforts") or []) if e in EFFORTS]
    if not effs:
        return None, str(m.get("model")), None
    best = min(effs, key=lambda e: abs(EFFORTS.index(e) - idx))
    return best, str(m.get("model")), want


def score(key, m, c, pol, ctx):
    w = dict(OBJECTIVES.get(ctx["objective"], OBJECTIVES["balanced"]))
    if c["critical"]:
        w["speed"] += 0.3
    q, sp, co = float(m.get("quality", 3)), float(m.get("speed", 3)), float(m.get("cost", 3))
    d = c["difficulty"] + (0.5 if c["critical"] else 0)   # 임계 경로는 여유를 둔다(실패하면 뒤가 다 밀린다)
    why = []
    s = -w["under"] * max(0.0, d - q) - w["cost"] * co - w["speed"] * (5 - sp)
    s += w["head"] * min(1.0, max(0.0, q - d))
    if d - q > 0:
        why.append(f"능력 부족 {d - q:.1f}")
    st = set(m.get("strengths") or []) & set(c["tags"])
    if st:
        s += min(1.5, 0.75 * len(st))
        why.append("강점 " + ",".join(sorted(st)))
    for tag in c["tags"]:
        if (rcfg(pol, "prefer") or {}).get(tag) == key:
            s += 2
            why.append(f"prefer.{tag}")
    stats = ctx["learned"].get((key, band(c["difficulty"])))   # dispatch.tsv에 남는 난이도와 같은 대역(임계 경로 +0.5 전)
    if stats and rcfg(pol, "learn", True):
        tot, n = stats
        rate = (tot + 2) / (n + 3)
        s += 4 * (rate - 0.67)
        why.append(f"실적 {tot:.1f}/{n}")
    run = ctx["running"].get(m.get("agent"), 0)
    if run:
        s -= 0.4 * run
        why.append(f"{m.get('agent')} 진행 {run}")
    soft = (rcfg(pol, "daily_soft_tokens") or {}).get(m.get("agent"))
    if soft:
        used = today_tokens(ctx["usage"], m.get("agent"))
        if used > soft:
            s -= min(4.0, 2 * (used / soft - 1) + 1)
            why.append(f"오늘 사용량 {used // 1000}k > {int(soft) // 1000}k")
    if key in ctx["failed"]:
        s -= 2.5
        why.append("이 작업에서 실패한 모델")
    if m.get("agent") in (ctx.get("avoid_agents") or []):
        s -= 3
        why.append(f"교차 검토(구현 {m.get('agent')})")
    return s, why


def route(repo, cfg, tid, tasks, mode="new", live_dids=None, refresh=False, catalog=None, pol=None, disc=None,
          extra_running=None, jev_net=True, quota=None, need_minutes=None, exclude_agents=None):
    """작업 하나의 결정. 반환: {key, agent, model, effort, difficulty, tags, reasons, why, alts, excluded}.
    후보가 없으면 key가 None. extra_running: 이번 호출에서 방금 띄운(아직 집계에 없는) 에이전트별 수.
    need_minutes: 한도 전환(quota_switch)일 때 넘겨받을 구독에 필요한 여유 시간 — 곧 다시 찰 후보를 뺀다(headroom).
    exclude_agents: 아예 뺄 구독(검토 이어받기의 검토 독립성: 구현한 구독)."""
    if catalog is None or pol is None:
        catalog, pol = load(repo)
    disc = disc if disc is not None else discover(repo, catalog, refresh=refresh)
    catalog = resolve_catalog(catalog, disc)
    t = tasks[tid]
    rows = read_dispatch(repo, set(tasks))
    outs = outcomes(rows, tasks, read_feedback(repo), catalog["models"], skip=read_switches(repo))
    fails = failed_keys(outs, tid)
    prior = len([o for o in outs if o["kit_id"] == tid and o["score"] < 0.5])
    c = classify(t, tasks, pol, mode, prior_fails=prior if mode == "new" else 0)
    jv = apply_jev(c, t, repo, pol, mode, net=jev_net)
    impl = [r for r in rows if r["kit_id"] == tid and r["mode"] in ("new", "retry", "handoff") and r["agent"]]
    ctx = {"objective": str(t.get("objective") or rcfg(pol, "objective", "balanced")),
           "learned": learned(outs), "running": running_by_agent(rows, tasks, live_dids),
           "usage": orca_usage(1) if rcfg(pol, "daily_soft_tokens") else {},
           "failed": fails if mode in ("new", "handoff") else set(),
           # 교차 검토: 이 작업의 코드를 쓴 구독 전부(한도로 이어받았으면 앞뒤 구독 모두)
           "avoid_agents": sorted({r["agent"] for r in impl}) if mode == "review" and rcfg(pol, "cross_review", True) else []}
    for ag, n in (extra_running or {}).items():
        ctx["running"][ag] = ctx["running"].get(ag, 0) + n
    cools = read_cooldowns(repo)
    if quota is None:
        quota = read_quota(repo, net=jev_net) if quota_cfg(pol)["enabled"] else {}
    caps = rcfg(pol, "max_concurrent") or {}
    minq = (rcfg(pol, "min_quality") or {}).get(str(t.get("layer")))
    pin = (rcfg(pol, "pin") or {}).get(mode) or (rcfg(pol, "pin") or {}).get(str(t.get("layer")))
    cands, excluded, quota_out = [], {}, set()
    for key, m in catalog["models"].items():
        ok, why = availability(key, m, pol, disc)
        if not ok:
            excluded[key] = why
            continue
        ag = m.get("agent")
        if key in cools or ag in cools:
            cd = cools.get(key) or cools.get(ag)
            excluded[key] = f"쿨다운 {int((cd['until'] - time.time()) // 60)}분 남음" + (f" ({cd.get('reason')})" if cd.get("reason") else "")
            continue
        if minq and float(m.get("quality", 3)) < float(minq):
            excluded[key] = f"min_quality.{t.get('layer')}={minq}"
            continue
        if ag in (exclude_agents or ()):
            excluded[key] = f"검토 독립성: {ag}가 구현했다"
            continue
        qx, qadj, qwhy = quota_effect(m, catalog["agents"], quota, pol)
        if not qx and need_minutes is not None:
            ok, hw = headroom(m, catalog["agents"], quota, pol, need_minutes)
            qx = None if ok else f"넘겨받기엔 빠듯: {hw}"
        if qx:
            excluded[key] = qx
            quota_out.add(key)
            continue
        full = caps.get(ag) is not None and ctx["running"].get(ag, 0) >= int(caps[ag])
        s, why = score(key, m, c, pol, ctx)
        if qadj:
            s += qadj
            why.extend(qwhy)
        if pin == key:
            s += 100
            why.append(f"pin.{mode if (rcfg(pol, 'pin') or {}).get(mode) else t.get('layer')}")
        cands.append((s, key, why, full))
    if not cands:
        # 쓸 수 있는 구독이 모두 한도 소진이면 기본 에이전트로 띄우지 말고 기다리게 한다
        return {"key": None, "difficulty": c["difficulty"], "tags": c["tags"], "reasons": c["reasons"], "excluded": excluded, "alts": [],
                "jev": jv, "quota_blocked": bool(quota_out)}
    open_c = [x for x in cands if not x[3]] or cands      # 모두 상한이면 상한 무시(가장 덜 붐비는 쪽이 점수로 이긴다)
    for x in cands:
        if x[3] and x not in open_c:
            excluded[x[1]] = f"{catalog['models'][x[1]].get('agent')} 동시 상한 {caps.get(catalog['models'][x[1]].get('agent'))}"
    def tiekey(x):
        """점수(부동소수 오차는 같은 점수로) → 부하 적은 구독 → 이 대역 실적 → 소모 적은 쪽 → 품질 → 키 이름."""
        sc, k, _, _ = x
        mm = catalog["models"][k]
        st = ctx["learned"].get((k, band(c["difficulty"])))
        rate = (st[0] + 2) / (st[1] + 3) if st else 0.67
        return (-round(sc, 6), ctx["running"].get(mm.get("agent"), 0), -rate, float(mm.get("cost", 3)), -float(mm.get("quality", 3)), k)
    open_c.sort(key=tiekey)
    s, key, why, _ = open_c[0]
    if len(open_c) > 1 and round(open_c[1][0], 6) == round(s, 6):
        why = why + [f"{open_c[1][1]}와 동점 → 부하·실적·소모 순"]
    m = catalog["models"][key]
    eff_d = c["difficulty"] + (0.5 if c["critical"] else 0)   # 임계 경로는 effort도 여유 있게
    effort, model, _want = pick_effort(m, eff_d, ctx["objective"], float(m.get("quality", 3)))
    return {"key": key, "agent": m.get("agent"), "model": model, "effort": effort, "score": round(s, 2),
            "difficulty": c["difficulty"], "tags": c["tags"], "critical": c["critical"], "reasons": c["reasons"],
            "why": why, "alts": [(k, round(sc, 2)) for sc, k, _, _ in open_c[1:5]], "excluded": excluded,
            "objective": ctx["objective"], "jev": jv, "rule_difficulty": c.get("rule_difficulty"),
            "resolved_from": m.get("resolved_from"), "quality": m.get("quality"), "quality_src": m.get("quality_src")}


def describe(dec):
    if not dec.get("key"):
        return "후보 없음 (" + "; ".join(f"{k}: {v}" for k, v in list(dec.get("excluded", {}).items())[:6]) + ")"
    eff = f" · {dec['effort']}" if dec.get("effort") else ""
    return (f"{dec['key']} → {dec['agent']} {dec['model']}{eff} · 난이도 {dec['difficulty']:.1f}"
            + (f" · 태그 {','.join(dec['tags'])}" if dec["tags"] else "") + (" · 임계 경로" if dec.get("critical") else "")
            + (f" · JEV {dec['jev']['difficulty']:.1f}{'(적용)' if dec['jev'].get('applied') else '(기록)'}"
               if (dec.get("jev") or {}).get("difficulty") is not None else ""))
