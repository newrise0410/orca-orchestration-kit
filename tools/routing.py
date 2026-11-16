"""모델 자동 라우팅: 작업 → (에이전트, 모델, effort). launch.py와 models.py가 쓴다.

입력
  카탈로그  orchestration/tools/model_catalog.yaml (키트 소유, 모델별 quality·speed·cost·강점)
  정책      orchestration/models.yaml (프로젝트 소유, 쓸 구독·목표·동시 상한·덮어쓰기)
  가용성    설치된 CLI와 각 CLI의 모델 목록 (state/models_cache.json에 하루 캐시)
  학습      state/dispatch.tsv(누가 어떤 작업을 맡았나) × tasks.yaml 결과 × state/route_feedback.tsv(검토 판정)
  부하      진행 중 워커 수(에이전트별), 쿨다운(state/cooldown.json: 사용량 한도에 걸린 구독)
결정은 LLM 없이 점수로 한다(빠르고 재현된다). 점수가 왜 그렇게 나왔는지 reasons로 남긴다.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import kitlib

yaml = kitlib.yaml
TOOLS = Path(__file__).resolve().parent
CATALOG = TOOLS / "model_catalog.yaml"
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
        models[k] = deep_merge(models.get(k, {}), m or {})
    agents = deep_merge(cat.get("agents") or {}, pol.get("agents") or {})
    return {"agents": agents, "models": models, "version": cat.get("version")}, pol


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
            if time.time() - c.get("ts", 0) < CACHE_TTL and set(c.get("agents", {})) >= set(catalog["agents"]):
                return c["agents"]
        except (json.JSONDecodeError, OSError):
            pass
    res = {}
    for ag, spec in catalog["agents"].items():
        binp = shutil.which(str(spec.get("bin") or ag))
        info = {"bin": binp, "models": None, "error": None}
        if binp:
            ms, err = list_models(spec.get("list"), binp, timeout)
            info["models"] = sorted(ms) if ms is not None else None
            info["error"] = err
        res[ag] = info
    try:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({"ts": time.time(), "agents": res}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return res


def model_ids(m):
    return [str(m.get("model"))] + [str(x) for x in (m.get("model_by_effort") or {}).values()]


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
    known = info.get("models")
    if known is not None and not any(i in known for i in model_ids(m)):
        return False, f"{m.get('model')}가 {ag} 모델 목록에 없음"
    return True, ""


# ───────────────────────── 이력·학습

def kit_id_of(title, ids):
    """Orca 작업 제목 → (키트 ID, 모드). 'X-01' new, 'X-01-RV[-s]' review, 'X-01 retry-r1' retry."""
    head = (title or "").split()[0] if title else ""
    mode = "retry" if " retry-" in (title or "") else "new"
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
             "difficulty": c[9] if len(c) > 9 else ""}
        rows.append(r)
    return rows


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


def outcomes(rows, tasks, feedback, models):
    """구현 투입(new·retry) 하나마다 결과 점수 0~1.
    merged 작업: 마지막 새 투입 1(뒤에 retry가 있으면 0.6), 그 전 새 투입 0(다시 띄웠으니 실패), retry는 마지막 1·앞 0.5.
    todo/ready로 되돌아간 작업: 그 투입들 0. running/review: 마지막은 미정, 그 전 새 투입 0.
    route_feedback.tsv(검토 판정)는 그 시각 이전의 마지막 구현 투입 점수를 덮는다."""
    by = {}
    for r in rows:
        if r["kit_id"] and r["mode"] in ("new", "retry"):
            by.setdefault(r["kit_id"], []).append(r)
    res = []
    for tid, rs in by.items():
        rs.sort(key=lambda r: r["ts"])
        st = str((tasks.get(tid) or {}).get("status", "")).lower()
        scores = {}
        fresh = [r for r in rs if r["mode"] == "new"]
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
                                "agent": r["agent"], "did": r["did"]})
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
        if r["kit_id"] and r["mode"] == "new":
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
    stats = ctx["learned"].get((key, band(d)))
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
    if ctx.get("avoid_agent") and m.get("agent") == ctx["avoid_agent"]:
        s -= 3
        why.append(f"교차 검토(구현 {ctx['avoid_agent']})")
    return s, why


def route(repo, cfg, tid, tasks, mode="new", live_dids=None, refresh=False, catalog=None, pol=None, disc=None,
          extra_running=None):
    """작업 하나의 결정. 반환: {key, agent, model, effort, difficulty, tags, reasons, why, alts, excluded}.
    후보가 없으면 key가 None. extra_running: 이번 호출에서 방금 띄운(아직 집계에 없는) 에이전트별 수."""
    if catalog is None or pol is None:
        catalog, pol = load(repo)
    disc = disc if disc is not None else discover(repo, catalog, refresh=refresh)
    t = tasks[tid]
    rows = read_dispatch(repo, set(tasks))
    outs = outcomes(rows, tasks, read_feedback(repo), catalog["models"])
    fails = failed_keys(outs, tid)
    prior = len([o for o in outs if o["kit_id"] == tid and o["score"] < 0.5])
    c = classify(t, tasks, pol, mode, prior_fails=prior if mode == "new" else 0)
    impl = [r for r in rows if r["kit_id"] == tid and r["mode"] in ("new", "retry") and r["agent"]]
    ctx = {"objective": str(t.get("objective") or rcfg(pol, "objective", "balanced")),
           "learned": learned(outs), "running": running_by_agent(rows, tasks, live_dids),
           "usage": orca_usage(1) if rcfg(pol, "daily_soft_tokens") else {},
           "failed": fails if mode == "new" else set(),
           "avoid_agent": impl[-1]["agent"] if mode == "review" and impl and rcfg(pol, "cross_review", True) else None}
    for ag, n in (extra_running or {}).items():
        ctx["running"][ag] = ctx["running"].get(ag, 0) + n
    cools = read_cooldowns(repo)
    caps = rcfg(pol, "max_concurrent") or {}
    minq = (rcfg(pol, "min_quality") or {}).get(str(t.get("layer")))
    pin = (rcfg(pol, "pin") or {}).get(mode) or (rcfg(pol, "pin") or {}).get(str(t.get("layer")))
    cands, excluded = [], {}
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
        full = caps.get(ag) is not None and ctx["running"].get(ag, 0) >= int(caps[ag])
        s, why = score(key, m, c, pol, ctx)
        if pin == key:
            s += 100
            why.append(f"pin.{mode if (rcfg(pol, 'pin') or {}).get(mode) else t.get('layer')}")
        cands.append((s, key, why, full))
    if not cands:
        return {"key": None, "difficulty": c["difficulty"], "tags": c["tags"], "reasons": c["reasons"], "excluded": excluded, "alts": []}
    open_c = [x for x in cands if not x[3]] or cands      # 모두 상한이면 상한 무시(가장 덜 붐비는 쪽이 점수로 이긴다)
    for x in cands:
        if x[3] and x not in open_c:
            excluded[x[1]] = f"{catalog['models'][x[1]].get('agent')} 동시 상한 {caps.get(catalog['models'][x[1]].get('agent'))}"
    open_c.sort(key=lambda x: -x[0])
    s, key, why, _ = open_c[0]
    m = catalog["models"][key]
    eff_d = c["difficulty"] + (0.5 if c["critical"] else 0)   # 임계 경로는 effort도 여유 있게
    effort, model, _want = pick_effort(m, eff_d, ctx["objective"], float(m.get("quality", 3)))
    return {"key": key, "agent": m.get("agent"), "model": model, "effort": effort, "score": round(s, 2),
            "difficulty": c["difficulty"], "tags": c["tags"], "critical": c["critical"], "reasons": c["reasons"],
            "why": why, "alts": [(k, round(sc, 2)) for sc, k, _, _ in open_c[1:5]], "excluded": excluded,
            "objective": ctx["objective"]}


def describe(dec):
    if not dec.get("key"):
        return "후보 없음 (" + "; ".join(f"{k}: {v}" for k, v in list(dec.get("excluded", {}).items())[:6]) + ")"
    eff = f" · {dec['effort']}" if dec.get("effort") else ""
    return (f"{dec['key']} → {dec['agent']} {dec['model']}{eff} · 난이도 {dec['difficulty']:.1f}"
            + (f" · 태그 {','.join(dec['tags'])}" if dec["tags"] else "") + (" · 임계 경로" if dec.get("critical") else ""))
