#!/usr/bin/env python3
"""tasks.yaml(작업 DAG) 검증기.

오류(종료코드 1): YAML 파싱 실패, tasks 형식 오류, id 누락, 중복 ID, 없는 deps, 자기 의존, 순환,
                 한 작업 안의 중복 키(PyYAML은 조용히 덮어써서 값이 사라진다)
경고:            알 수 없는 layer·status·module, 빈 acceptance, L2의 기준 브랜치가 통합 브랜치가 아님,
                 선행작업이 더 늦은 웨이브, 동시에 돌 수 있는 작업끼리 write_scope 겹침

사용:
    python3 orchestration/tools/validate_dag.py [--tasks PATH] [--config PATH] [--repo PATH] [--strict] [--json] [-q]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402

WAVE_ORDER_DEFAULT = ["W0", "G-0", "W1", "W2", "W3"]


def wave_of(t, cfg):
    if t.get("wave"):
        return str(t["wave"])
    tid, layer, mod = str(t.get("id", "")), str(t.get("layer", "")), kitlib.task_module(t)
    if tid.startswith("G-"):
        return "G-0"
    if layer == "L2":
        return "W1"
    if layer == "L1":
        return "W2"
    if mod == "system" or tid.startswith("S-"):
        return "W3"
    return "W0"


def find_cycles(ids, deps):
    """Tarjan SCC로 순환(크기 2 이상 또는 자기 루프)을 찾는다."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = [0]
    sys.setrecursionlimit(max(1000, len(ids) * 4 + 100))

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        for w in deps.get(v, []):
            if w not in index:
                if w in deps:
                    strong(w)
                    low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(sorted(comp))

    for v in ids:
        if v not in index:
            strong(v)
    return out


def scope_overlap(a, b):
    """경로 접두 겹침 (glob은 첫 와일드카드 앞까지만 비교)."""
    def head(p):
        p = p.strip()
        m = re.search(r"[*?\[]", p)
        return (p[:m.start()] if m else p), bool(m)
    ha, ga = head(a)
    hb, gb = head(b)
    if not ha or not hb:
        return True
    if ha == hb:
        return True
    if ha.endswith("/") and hb.startswith(ha):
        return True
    if hb.endswith("/") and ha.startswith(hb):
        return True
    if (ga or gb) and (ha.startswith(hb) or hb.startswith(ha)):
        return True
    return False


def validate(text, cfg):
    errors, warnings = [], []
    try:
        data, dups = kitlib.load_tasks_text(text)
    except Exception as e:  # yaml.YAMLError 등
        return {"errors": [f"YAML 파싱 실패: {e}"], "warnings": [], "stats": {}}
    raw = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return {"errors": ["최상위 'tasks:' 목록이 없다"], "warnings": [], "stats": {}}
    for key, line, first in dups:
        errors.append(f"중복 키 '{key}' ({line}행, 처음 {first}행): 뒤의 값이 앞의 값을 덮어쓴다")

    tasks, seen = [], {}
    for k, t in enumerate(raw):
        if not isinstance(t, dict):
            errors.append(f"tasks[{k}]: 매핑이 아니다")
            continue
        if not t.get("id"):
            errors.append(f"tasks[{k}]: id 없음 (title={t.get('title')!r})")
            continue
        tid = str(t["id"])
        if tid in seen:
            errors.append(f"중복 ID {tid}: tasks[{seen[tid]}]와 tasks[{k}]")
            continue
        seen[tid] = k
        tasks.append(t)
    ids = [str(t["id"]) for t in tasks]
    idset = set(ids)
    deps = {str(t["id"]): kitlib.as_list(t.get("deps")) for t in tasks}

    module_keys = {str(m.get("key")) for m in (cfg.get("modules") or []) if isinstance(m, dict)}
    if module_keys:
        module_keys |= {"common", "system"}
    wave_keys = [str(w.get("key")) for w in (cfg.get("waves") or []) if isinstance(w, dict)] or WAVE_ORDER_DEFAULT
    int_prefix = str(cfg.get("integration_prefix") or "int/")

    edges = 0
    for t in tasks:
        tid = str(t["id"])
        for d in deps[tid]:
            edges += 1
            if d == tid:
                errors.append(f"{tid}: 자기 자신에 의존")
            elif d not in idset:
                errors.append(f"{tid}: 없는 선행작업 {d}")
        layer = str(t.get("layer", ""))
        if layer not in kitlib.LAYERS:
            warnings.append(f"{tid}: 알 수 없는 layer {layer!r} (L0/L1/L2)")
        st = str(t.get("status", "todo")).strip().lower()
        if st not in kitlib.STATUSES:
            warnings.append(f"{tid}: 알 수 없는 status {st!r}")
        mod = kitlib.task_module(t)
        if module_keys and mod not in module_keys:
            warnings.append(f"{tid}: kit.yaml modules에 없는 모듈 {mod!r}")
        if not str(t.get("acceptance") or "").strip():
            warnings.append(f"{tid}: acceptance 비어 있음")
        if layer == "L2" and t.get("base") and not str(t["base"]).startswith(int_prefix):
            warnings.append(f"{tid}: L2인데 base가 통합 브랜치({int_prefix}*)가 아님: {t['base']}")
    cycles = find_cycles(ids, {k: [d for d in v if d in idset] for k, v in deps.items()})
    for c in cycles:
        errors.append("순환 의존: " + " → ".join(c))

    # 웨이브 순서 (선행작업이 더 늦은 웨이브면 경고)
    wave = {str(t["id"]): wave_of(t, cfg) for t in tasks}
    worder = {w: i for i, w in enumerate(wave_keys)}
    for t in tasks:
        tid = str(t["id"])
        for d in deps[tid]:
            if d in wave and wave[d] in worder and wave[tid] in worder and worder[wave[d]] > worder[wave[tid]]:
                guess = "" if t.get("wave") else " — wave 미지정이라 추정값. tasks.yaml에 wave를 적으면 정확해진다"
                warnings.append(f"{tid}({wave[tid]}): 선행 {d}가 더 늦은 웨이브({wave[d]}){guess}")

    # 도달 가능성 (순환이 없을 때만)
    reach = {}
    if not cycles:
        def anc(v, memo={}):
            if v in memo:
                return memo[v]
            s = set()
            for d in deps.get(v, []):
                if d in idset:
                    s.add(d)
                    s |= anc(d)
            memo[v] = s
            return s
        for v in ids:
            reach[v] = anc(v)

        # write_scope 겹침: 서로 선후 관계가 없는(동시에 돌 수 있는) L2·L0 작업끼리
        cand = [t for t in tasks if str(t.get("layer")) in ("L0", "L2") and kitlib.as_list(t.get("write_scope"))]
        for i in range(len(cand)):
            for j in range(i + 1, len(cand)):
                a, b = cand[i], cand[j]
                ia, ib = str(a["id"]), str(b["id"])
                if ia in reach.get(ib, ()) or ib in reach.get(ia, ()):
                    continue
                if a.get("branch") and a.get("branch") == b.get("branch") and str(a.get("layer")) == "L0":
                    continue  # 같은 통합 브랜치에서 이어서 하는 L0 작업
                hits = [(x, y) for x in kitlib.as_list(a.get("write_scope")) for y in kitlib.as_list(b.get("write_scope"))
                        if scope_overlap(x, y)]
                if hits:
                    x, y = hits[0]
                    warnings.append(f"write_scope 겹침(동시 실행 가능): {ia} `{x}` ↔ {ib} `{y}`")

    # 통계
    depth = {}
    if not cycles:
        def dep_depth(v):
            if v in depth:
                return depth[v]
            ds = [dep_depth(d) for d in deps.get(v, []) if d in idset]
            depth[v] = 1 + max(ds) if ds else 1
            return depth[v]
        for v in ids:
            dep_depth(v)
    dependents = {v: 0 for v in ids}
    for v in ids:
        for d in deps[v]:
            if d in dependents:
                dependents[d] += 1

    def count(key):
        out = {}
        for t in tasks:
            k = key(t)
            out[k] = out.get(k, 0) + 1
        return out

    stats = {
        "tasks": len(tasks), "edges": edges,
        "roots": sorted(v for v in ids if not [d for d in deps[v] if d in idset]),
        "leaves": sorted(v for v in ids if dependents[v] == 0),
        "longest_chain": max(depth.values()) if depth else None,
        "waves": count(lambda t: wave_of(t, cfg)),
        "layers": count(lambda t: str(t.get("layer", "?"))),
        "status": count(lambda t: str(t.get("status", "todo")).strip().lower()),
        "modules": count(kitlib.task_module),
    }
    return {"errors": errors, "warnings": warnings, "stats": stats}


def main(argv=None):
    ap = argparse.ArgumentParser(description="tasks.yaml DAG 검증 (중복 ID · 없는 deps · 순환)")
    ap.add_argument("--tasks", help="tasks.yaml 경로 (기본: kit.yaml paths.tasks)")
    ap.add_argument("--config", help="kit.yaml 경로 (기본: <저장소>/orchestration/kit.yaml)")
    ap.add_argument("--repo", help="저장소 경로 (기본: 현재 디렉터리의 git 최상위)")
    ap.add_argument("--strict", action="store_true", help="경고도 실패로 본다")
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    ap.add_argument("-q", "--quiet", action="store_true", help="오류·경고 목록만 출력")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    path = kitlib.tasks_path(repo, cfg, a.tasks)
    if not path.is_file():
        print(f"오류: {path} 없음", file=sys.stderr)
        return 2
    res = validate(path.read_text(encoding="utf-8"), cfg)
    res["file"] = str(path)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        s = res["stats"]
        if s and not a.quiet:
            print(f"{path}")
            print(f"  작업 {s['tasks']} · 의존 간선 {s['edges']} · 시작점 {len(s['roots'])} · 끝점 {len(s['leaves'])} · 최장 사슬 {s['longest_chain']}")
            print("  웨이브 " + ", ".join(f"{k} {v}" for k, v in s["waves"].items()))
            print("  계층 " + ", ".join(f"{k} {v}" for k, v in sorted(s["layers"].items())))
            print("  상태 " + ", ".join(f"{k} {v}" for k, v in s["status"].items()))
        for e in res["errors"]:
            print(f"  오류: {e}")
        for w in res["warnings"]:
            print(f"  경고: {w}")
        print(f"  결과: 오류 {len(res['errors'])} · 경고 {len(res['warnings'])}")
    if res["errors"] or (a.strict and res["warnings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
