"""공개 벤치마크 → 카탈로그 품질 근거 (관리자용, models.py bench가 부른다. 라우팅은 결과 파일만 읽는다).

출처
  Terminal-Bench (tbench.ai)  CLI 에이전트 × 모델 × effort 조합의 공식 리더보드. 우리 라우팅이 고르는 단위와 같아서 품질의 주 근거로 쓴다.
                              공개 페이지에 들어 있는 리더보드 데이터를 읽는다(하루 몇 번 이하로, 관리자가 돌릴 때만).
  Artificial Analysis (선택)  무료 Data API(AA_API_KEY 필요, 하루 100회, 출처 표기 필수). 지능 지수·출력 속도를 참고로 남긴다.
                              키가 없으면 건너뛴다. 사이트를 긁지 않는다.
결과는 tools/bench_scores.yaml(키트 소유)에 출처·날짜와 함께 남긴다. 원래 표를 통째로 옮기지 않고 카탈로그에 해당하는 줄만 둔다.
"""
import datetime as dt
import json
import os
import re
import urllib.request

TB_URL = "https://www.tbench.ai/leaderboard/terminal-bench/{ver}"
TB_DEFAULT = "4.1"
AA_URL = os.environ.get("KIT_AA_URL") or "https://artificialanalysis.ai/api/v2/language/models/free"
UA = "orca-orchestration-kit-bench/1"


def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ───────────────────────── Terminal-Bench

def parse_tb(html):
    """리더보드 페이지 → (제목, [{agent, model, effort, acc, ci95, date, tokens_per_task}]).
    페이지가 품고 있는 리더보드 JSON(이스케이프된 Next.js 데이터)에서 줄을 뽑는다. 꼴이 바뀌면 빈 목록."""
    s = html.replace('\\"', '"')
    tm = re.search(r'"name":"[\w.-]+","title":"(Terminal-Bench [^"]+)"', s)
    rows, seen = [], set()
    for m in re.finditer(r'"rank":(\d+),"metadata":\{(.*?)\},"metrics":\{(.*?)\}', s):
        md, mt = m.group(2), m.group(3)

        def lab(k):
            mm = re.search(r'"%s":\{"url":"[^"]*","label":"([^"]*)"\}' % k, md)
            return mm.group(1) if mm else ""

        def num(k, src):
            mm = re.search(r'"%s":([\d.]+)' % k, src)
            return float(mm.group(1)) if mm else None

        eff = re.search(r'"reasoning_effort":"([^"]*)"', md)
        date = re.search(r'"date":"([^"]*)"', md)
        acc, n = num("accuracy", mt), num("n_trials", mt)
        out_tok, dur = num("output_tokens", mt), num("avg_trial_duration_sec", mt)
        r = {"agent": lab("agent_display"), "model": lab("model_display"), "effort": eff.group(1) if eff else "",
             "acc": acc, "ci95": num("accuracy_ci95_half_width", mt), "date": date.group(1) if date else "",
             "out_tokens_per_task": int(out_tok / n) if out_tok and n else None,   # 출력 토큰(구독 한도 소모의 대리값, 같은 구독 안에서만 비교)
             "min_per_task": round(dur / 60, 1) if dur else None}                  # 작업 하나 평균 소요(분)
        key = (r["agent"], r["model"], r["effort"])
        if r["agent"] and r["model"] and acc is not None and key not in seen:
            seen.add(key)
            rows.append(r)
    return (tm.group(1) if tm else ""), rows


def label_regex(pat):
    """'GPT-{v} Sol' → 정규식(판 번호 v). 대소문자는 가리지 않는다."""
    return re.compile("^" + re.escape(pat).replace(r"\{v\}", r"(?P<v>\d+(?:\.\d+)*)") + "$", re.I)


def vkey(v):
    return tuple(int(x) for x in re.split(r"[.\-]", str(v)) if x.isdigit())


def relevant_rows(catalog, rows):
    """카탈로그 에이전트(bench_agent)·모델(bench_name) 꼴에 맞는 줄만."""
    out = []
    for r in rows:
        for m in catalog["models"].values():
            ag = (catalog["agents"].get(m.get("agent")) or {}).get("bench_agent")
            pat = m.get("bench_name")
            if ag and pat and r["agent"].lower() == str(ag).lower() and label_regex(pat).match(r["model"]):
                out.append(r)
                break
    return out


# ───────────────────────── Artificial Analysis (선택)

def fetch_aa(key, pages=3):
    """무료 Data API → [{name, slug, intelligence, tokens_per_second}] (키 필요)."""
    out = []
    for page in range(1, pages + 1):
        d = json.loads(http_get(f"{AA_URL}?page={page}", {"x-api-key": key}))
        for m in d.get("data") or []:
            ev = m.get("evaluations") or {}
            perf = m.get("performance_v3") or {}
            old = m.get("performance") or {}
            out.append({"name": str(m.get("name", "")), "slug": str(m.get("slug", "")),
                        "intelligence": ev.get("artificial_analysis_intelligence_index"),
                        "tokens_per_second": perf.get("mean_output_tokens_per_second") or old.get("median_output_tokens_per_second")})
        pg = d.get("pagination") or {}
        if not pg.get("has_next") and not pg.get("next_page") and (pg.get("total_pages") or page) <= page:
            break
    return out


def relevant_aa(catalog, aa):
    """AA 이름 'Gemini 4 Neon (Xhigh)' → 괄호 앞 이름이 카탈로그 bench_name 꼴에 맞는 것만."""
    pats = [label_regex(m["bench_name"]) for m in catalog["models"].values() if m.get("bench_name")]
    out = []
    for a in aa:
        base = re.sub(r"\s*\([^)]*\)\s*$", "", a["name"]).strip()
        effm = re.search(r"\(([^)]*)\)\s*$", a["name"])
        if any(p.match(base) for p in pats):
            out.append(dict(a, model=base, effort=(effm.group(1).lower() if effm else "")))
    return out


# ───────────────────────── 기록

def build(catalog, tb_html, tb_url, aa=None, today=None):
    title, rows = parse_tb(tb_html)
    keep = relevant_rows(catalog, rows)
    doc = {"generated": (today or dt.date.today()).isoformat(),
           "sources": {"tb": {"name": title or "Terminal-Bench", "url": tb_url, "rows_total": len(rows),
                              "note": "CLI 에이전트 × 모델 × effort 공식 리더보드. acc = 해결률(%), ci95 = 95% 신뢰구간 반폭"}},
           "tb": sorted(keep, key=lambda r: -r["acc"])}
    if aa is not None:
        doc["sources"]["aa"] = {"name": "Artificial Analysis", "url": "https://artificialanalysis.ai/",
                                "note": "무료 Data API. 지능 지수·출력 속도(참고값, 라우팅 점수에는 아직 쓰지 않는다)"}
        doc["aa"] = relevant_aa(catalog, aa)
    return doc
