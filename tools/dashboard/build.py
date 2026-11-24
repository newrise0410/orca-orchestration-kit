#!/usr/bin/env python3
"""개발 현황 대시보드 생성기 (orca-orchestration-kit).

저장소의 문서(docs/), 작업 DAG(tasks.yaml), git 상태를 읽어
JS·CSS를 모두 인라인으로 넣은 단일 HTML(dashboard/index.html)을 만든다.
외부 서비스·CDN에 의존하지 않으므로 오프라인에서 연다.

프로젝트별 값(모듈 목록, 웨이브, 폴더 번호, RTM·TBD 파일 패턴, 검토 단계, Orca Run ID)은
설정 파일에서 읽는다. 기본 설정 경로는 `<저장소>/orchestration/kit.yaml`의 `dashboard`·공통 절이다.
설정 키는 tools/dashboard/dashboard.example.yaml 참고.

사용:
    python3 orchestration/tools/dashboard/build.py [--config orchestration/kit.yaml]
        [--out dashboard/index.html] [--orca] [--orca-run RUN_ID] [--tasks-ref REF] [--repo PATH]

git은 읽기 명령(log/branch/show/rev-list/for-each-ref/status/cat-file)만 쓴다.
`--no-optional-locks`로 실행해서 다른 세션의 인덱스를 건드리지 않는다.
"""

import argparse
import datetime as dt
import fnmatch
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "오류: PyYAML이 필요합니다.\n"
        "  Ubuntu: sudo apt install python3-yaml\n"
        "  pip:    python3 -m pip install --user pyyaml\n")
    sys.exit(2)

# ─────────────────────────── 설정 ───────────────────────────
# 아래 값은 기본값이다. load_config()가 설정 파일 값으로 덮어쓴다.

STATUSES = ["todo", "ready", "running", "review", "merged"]
RANK = {s: i for i, s in enumerate(STATUSES)}
STATUS_KO = {"todo": "대기", "ready": "착수 가능", "running": "진행",
             "review": "검토", "merged": "머지됨"}

DEFAULT_CONFIG = {
    "project": {"name": "프로젝트", "main_branch": "main"},
    "modules": [{"key": "common", "name": "공통", "short": "공통"},
                {"key": "system", "name": "체계", "short": "체계"}],
    "waves": [{"key": "W0", "name": "W0 계약·공통기반"}, {"key": "G-0", "name": "G-0 기반머지"},
              {"key": "W1", "name": "W1 요구사항"}, {"key": "W2", "name": "W2 모듈통합"},
              {"key": "W3", "name": "W3 체계통합"}],
    # tasks.yaml에 wave가 없을 때의 추정 규칙
    "wave_rules": {"gate_prefix": "G-", "gate_wave": "G-0", "layers": {"L2": "W1", "L1": "W2"},
                   "system_modules": ["system"], "system_prefix": "S-", "system_wave": "W3",
                   "default_wave": "W0"},
    "gates": ["G-0"],                 # 게이트 작업: 선행이 모두 머지되고 L2 기준 브랜치(int/*)가 모두 있으면 merged
    "integration_prefix": "int/",     # 모듈 통합 브랜치 접두
    "request_prefix": "req/",         # 요구사항 작업 브랜치 접두
    "paths": {
        "tasks": "orchestration/tasks.yaml",
        "tasks_archive": "orchestration/tasks.archive.yaml",   # archive_tasks.py가 옮긴 merged 작업
        "docs": "docs",
        "evidence": "docs/06_evidence/{id}/report.md",
        "evidence_dir": "06_evidence",
        "rtm": "docs/01_requirements/01_요구사항추적표_*.md",
        "tbd": "docs/01_requirements/02_TBD관리대장_*.md",
        "skip_dirs": ["snapshots"],   # 번호 문서 검사에서 뺄 하위 폴더(대시보드 스냅샷 등)
    },
    "verification_stages": [{"mark": "①", "name": "① 로컬"}, {"mark": "②", "name": "② 통합 환경"},
                            {"mark": "③", "name": "③ 대상 환경"}, {"mark": "④", "name": "④ 운영"}],
    "rtm": {
        "columns": {"id": None, "summary": "요약", "verify": "검증", "alloc": "예비 설계 할당", "tbd": "관련 TBD"},
        # 요구사항 ID의 '접두-번호' 꼴 (TBD 표의 '요구사양 식별자' 칸 펼치기에 쓴다. 예: REQ-API-001·002)
        "ref_pattern": r"^([A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-)(\d+)((?:·\d+)*)$",
        # 상위 요구사항(세부 항목 a, b, c…로 쪼개진 것)의 ID 패턴. 없으면 null
        "parent_pattern": None,
    },
    "tbd": {"columns": {"id": "순번", "reqs": "요구사양 식별자", "kind": "구분", "title": "제목",
                        "reason": "사유 및 계획", "due": "확정 예정", "state": "확정 여부"},
            "open_state": "미확정", "closed_state": "확정"},
    # 검토 단계(선택 모듈). enabled: false면 개요의 '단계별 산출물' 카드를 숨긴다
    "review_stages": {"enabled": False, "stages": []},
    "orca": {"work_run": ""},
    "dashboard": {"title": "", "subtitle": "로컬 전용", "storage_key": "kit-dash"},
}

WAVES, PRODUCTS, PRODUCT_KO, PRODUCT_SHORT = [], [], {}, {}
VSTAGES, VSTAGE_KO = [], {}
VMARK_KO = {"●": "통과", "◐": "진행", "✕": "불합격", "-": "미착수"}
REVIEW_STAGES = []
CFG = {}
MAIN = "main"


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path):
    """설정 파일(YAML)을 읽어 전역 설정을 채운다. 파일이 없으면 기본값을 쓴다."""
    global CFG, MAIN, WAVES, PRODUCTS, PRODUCT_KO, PRODUCT_SHORT, VSTAGES, VSTAGE_KO, REVIEW_STAGES
    raw = {}
    if path and Path(path).is_file():
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        # kit.yaml의 dashboard 절 안에 덮어쓸 값이 있으면 그것이 우선한다
        raw = deep_merge(raw, raw.get("dashboard_overrides") or {})
    CFG = deep_merge(DEFAULT_CONFIG, raw)
    MAIN = str(CFG["project"].get("main_branch") or "main")
    WAVES = [(str(w["key"]), str(w.get("name", w["key"]))) for w in CFG["waves"]]
    mods = list(CFG["modules"])
    if not any(m["key"] == "common" for m in mods):
        mods.insert(0, {"key": "common", "name": "공통", "short": "공통"})
    if not any(m["key"] == "system" for m in mods):
        mods.append({"key": "system", "name": "체계", "short": "체계"})
    PRODUCTS = [(str(m["key"]), str(m.get("name", m["key"])), str(m.get("short", m["key"]))) for m in mods]
    PRODUCT_KO = {p: ko for p, ko, _ in PRODUCTS}
    PRODUCT_SHORT = {p: sh for p, _, sh in PRODUCTS}
    VSTAGES = [str(v["mark"]) for v in CFG["verification_stages"]]
    VSTAGE_KO = {str(v["mark"]): str(v.get("name", v["mark"])) for v in CFG["verification_stages"]}
    rs = CFG.get("review_stages") or {}
    REVIEW_STAGES = []
    if rs.get("enabled"):
        for st in rs.get("stages") or []:
            REVIEW_STAGES.append((str(st["code"]), str(st.get("name", "")), str(st["folder"]),
                                  [(str(a["name"]), [str(k) for k in a.get("keywords", [])])
                                   for a in st.get("artifacts") or []]))
    return CFG


# Orca 작업 상태 → 대시보드 상태
ORCA_STATUS = {"pending": "todo", "blocked": "todo", "waiting": "todo",
               "ready": "ready", "dispatched": "running", "running": "running",
               "in_progress": "running", "completed": "review", "done": "review"}

DOC_NAME_RE = re.compile(r"^(\d{2})_(.+)_(\d{8})\.md$")
# 보관 사본(docs/README 1.2): <순번>_<문서명>_v<판>_<YYYYMMDD>.md. 문서 목록·순번 중복 검사 대상이 아니다
ARCHIVE_NAME_RE = re.compile(r"^(\d{2})_(.+)_v(\d+(?:[._]\d+)*)_(\d{8})\.md$")
FOLDER_RE = re.compile(r"^(\d{2})_([^\s]+)$")
TAG_RE = re.compile(r"^\s*((?:\[[^\]]+\]\s*)+)")


# ─────────────────────────── 공통 유틸 ───────────────────────────

def esc(s):
    return html.escape("" if s is None else str(s), quote=True)


def disp_width(ch):
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def trunc(s, units):
    w, out = 0, []
    for ch in s:
        cw = disp_width(ch)
        if w + cw > units:
            return "".join(out).rstrip() + "…"
        out.append(ch)
        w += cw
    return s


def short_date(iso):
    if not iso:
        return ""
    return iso.replace("T", " ")[:16]


def norm_key(s):
    return re.sub(r"[\s\-_/·()]", "", s).lower()


class Git:
    """읽기 전용 git 래퍼. 실패하면 None을 돌려준다."""

    def __init__(self, repo):
        self.repo = str(repo)
        self._cache = {}
        self.ok = self.run("rev-parse", "--git-dir") is not None

    def run(self, *args):
        cmd = ["git", "--no-optional-locks", "-C", self.repo,
               "-c", "core.quotepath=off", *args]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if p.returncode != 0:
            return None
        return p.stdout

    def cached(self, *args):
        if args not in self._cache:
            self._cache[args] = self.run(*args)
        return self._cache[args]

    def rev_set(self, ref, first_parent=False):
        args = ("rev-list", "--first-parent", ref) if first_parent else ("rev-list", ref)
        out = self.cached(*args)
        return set(out.split()) if out else set()

    def file_at(self, ref, path):
        if self.run("cat-file", "-e", f"{ref}:{path}") is None:
            return None
        return self.run("show", f"{ref}:{path}")


# ─────────────────────────── 마크다운 ───────────────────────────

CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def split_row(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, in_code, i = [], [], False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def is_sep_row(line):
    s = line.strip()
    return bool(s.startswith("|") and re.fullmatch(r"\|?(\s*:?-{2,}:?\s*\|)+\s*:?-*:?\s*\|?", s))


def parse_tables(text):
    """마크다운 표 목록: {heading, header, rows, line}."""
    lines = text.split("\n")
    tables, heading, i = [], "", 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            heading = m.group(2).strip()
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and is_sep_row(lines[i + 1]):
            header = split_row(ln)
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                r = split_row(lines[j])
                r += [""] * (len(header) - len(r))
                rows.append(r[:len(header)])
                j += 1
            tables.append({"heading": heading, "header": header, "rows": rows, "line": i})
            i = j
            continue
        i += 1
    return tables


def md_inline(s, linkfn=None):
    codes = []

    def keep(m):
        codes.append("<code>" + html.escape(m.group(2).strip(" ")) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = CODE_SPAN_RE.sub(keep, s)
    s = html.escape(s, quote=False)

    def link(m):
        text, url = m.group(1), html.unescape(m.group(2))
        if linkfn:
            return linkfn(text, url)
        if re.match(r"^https?://", url):
            return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{text}</a>'
        return f'<span class="xref" title="{esc(url)}">{text}</span>'

    s = LINK_RE.sub(link, s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"~~(.+?)~~", r"<del>\1</del>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)
    return s


def render_list(items, inl):
    out, stack = [], []  # stack: [indent, tag]
    for indent, ordered, text in items:
        tag = "ol" if ordered else "ul"
        if not stack or indent > stack[-1][0]:
            out.append(f"<{tag}><li>{inl(text)}")
            stack.append([indent, tag])
            continue
        while len(stack) > 1 and indent < stack[-1][0] and indent <= stack[-2][0]:
            out.append(f"</li></{stack[-1][1]}>")
            stack.pop()
        out.append(f"</li><li>{inl(text)}")
    while stack:
        out.append(f"</li></{stack[-1][1]}>")
        stack.pop()
    return "".join(out)


LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def md_to_html(text, linkfn=None):
    def inl(s):
        return md_inline(s, linkfn)

    lines = text.replace("\r\n", "\n").split("\n")
    out, para, i, n = [], [], 0, len(lines)

    def flush():
        if para:
            out.append("<p>" + inl("\n".join(para)) + "</p>")
            para.clear()

    while i < n:
        line = lines[i]
        fm = re.match(r"^\s*(`{3,}|~{3,})\s*([\w+-]*)", line)
        if fm:
            flush()
            fence, lang, body = fm.group(1), fm.group(2), []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{esc(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>")
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        hm = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if hm:
            flush()
            lv = len(hm.group(1))
            out.append(f"<h{lv}>{inl(hm.group(2))}</h{lv}>")
            i += 1
            continue
        if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < n and is_sep_row(lines[i + 1]):
            flush()
            header = split_row(line)
            rows, i = [], i + 2
            while i < n and lines[i].lstrip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            h = "".join(f"<th>{inl(c)}</th>" for c in header)
            b = ""
            for r in rows:
                r = (r + [""] * len(header))[:max(len(header), len(r))]
                b += "<tr>" + "".join(f"<td>{inl(c)}</td>" for c in r) + "</tr>"
            out.append(f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>')
            continue
        if line.lstrip().startswith(">"):
            flush()
            quote_lines = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + md_to_html("\n".join(quote_lines), linkfn) + "</blockquote>")
            continue
        lm = LIST_RE.match(line)
        if lm:
            flush()
            items = []
            while i < n:
                cur = lines[i]
                m2 = LIST_RE.match(cur)
                if m2 and not re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", cur):
                    items.append((len(m2.group(1).expandtabs(4)),
                                  m2.group(2)[0].isdigit(), m2.group(3)))
                    i += 1
                elif cur.strip() and items and (cur.startswith("  ") or cur.startswith("\t")):
                    ind, o, t = items[-1]
                    items[-1] = (ind, o, t + "\n" + cur.strip())
                    i += 1
                else:
                    break
            out.append(render_list(items, inl))
            continue
        para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out)


# ─────────────────────────── 작업(tasks.yaml) ───────────────────────────

def wave_of(t):
    if t.get("wave"):
        return str(t["wave"])
    tid, layer, prod = t["id"], t.get("layer", ""), t.get("product", "")
    wr = CFG["wave_rules"]
    if wr.get("gate_prefix") and tid.startswith(wr["gate_prefix"]):
        return wr.get("gate_wave", "G-0")
    if layer in (wr.get("layers") or {}):
        return wr["layers"][layer]
    if prod in (wr.get("system_modules") or []) or (wr.get("system_prefix") and tid.startswith(wr["system_prefix"])):
        return wr.get("system_wave", "W3")
    return wr.get("default_wave", "W0")


def as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]


def parse_req_comments(text):
    """reqs 줄 주석의 상위 요구사항 세부 항목(a~k)을 작업별로 뽑는다. 예: '# e(namespace), f(연동)'.
    rtm.parent_pattern을 설정한 프로젝트만 쓴다."""
    letters, cur = {}, None
    for ln in text.split("\n"):
        m = re.match(r"^\s*-\s*\{?\s*id:\s*([^,\s}]+)", ln)
        if m:
            cur = m.group(1)
        if cur and "reqs:" in ln and "#" in ln:
            comment = ln.split("#", 1)[1]
            found = re.findall(r"(?:^|[\s,])([a-k])(?=\(|,|\s|$)", comment)
            if found:
                letters[cur] = sorted(set(found))
    return letters


def load_tasks(repo, git, ref):
    rel = CFG["paths"]["tasks"]
    path = repo / rel
    if ref:
        text = git.run("show", f"{ref}:{rel}")
        if text is None:
            raise SystemExit(f"오류: {ref}:{rel}을 읽지 못했습니다.")
        src = f"git {ref}:{rel}"
    else:
        if not path.is_file():
            raise SystemExit(f"오류: {rel}이 없습니다 (설정 paths.tasks).")
        text = path.read_text(encoding="utf-8")
        src = f"작업 트리 {rel}"
    data = yaml.safe_load(text) or {}
    defaults = data.get("defaults") or {}
    letters = parse_req_comments(text)
    warnings, tasks = [], []
    # 보관 파일(merged 작업)도 같은 기준(작업 트리 또는 ref)에서 읽는다
    arel = CFG["paths"].get("tasks_archive") or "orchestration/tasks.archive.yaml"
    atext = git.run("show", f"{ref}:{arel}") if ref else ((repo / arel).read_text(encoding="utf-8") if (repo / arel).is_file() else None)
    archived = [r for r in ((yaml.safe_load(atext) or {}).get("tasks") or []) if isinstance(r, dict)] if atext else []
    if atext:
        letters = {**parse_req_comments(atext), **letters}
    active_ids = {str(r.get("id")) for r in data.get("tasks") or [] if isinstance(r, dict)}
    archived = [r for r in archived if str(r.get("id")) not in active_ids]
    for raw in archived + list(data.get("tasks") or []):
        t = {
            "id": str(raw.get("id")),
            "title": str(raw.get("title", "")),
            "layer": str(raw.get("layer", "")),
            "product": str(raw.get("module", raw.get("product", "common"))),
            "reqs": as_list(raw.get("reqs")),
            "branch": str(raw.get("branch") or ""),
            "base": str(raw.get("base") or ""),
            "deps": as_list(raw.get("deps")),
            "write_scope": as_list(raw.get("write_scope")),
            "acceptance": " ".join(str(raw.get("acceptance") or "").split()),
            "perf": bool(raw.get("perf", False)),
            "stage": str(raw.get("stage", defaults.get("stage", VSTAGES[0] if VSTAGES else "①"))),
            "yaml_status": str(raw.get("status", "todo")).strip().lower(),
            "sub_letters": letters.get(str(raw.get("id")), []),
            "raw": raw,
        }
        if t["yaml_status"] not in RANK:
            warnings.append(f"{t['id']}: 알 수 없는 status '{t['yaml_status']}' → todo로 취급")
            t["yaml_status"] = "todo"
        t["wave"] = wave_of(dict(raw, id=t["id"]))
        t["evidence_path"] = str(defaults.get("evidence", CFG["paths"]["evidence"])).format(id=t["id"])
        tasks.append(t)
    ids = {t["id"] for t in tasks}
    for t in tasks:
        for d in t["deps"]:
            if d not in ids:
                warnings.append(f"{t['id']}: 선행작업 {d}가 tasks.yaml에 없음")
    # 다른 브랜치와 비교 (정본은 메인 브랜치)
    master_note = None
    if not ref:
        mt = git.run("show", f"{MAIN}:{rel}")
        if mt is not None and mt != text:
            master_note = f"작업 트리 tasks.yaml이 {MAIN}와 다릅니다 (커밋 전 변경 또는 브랜치 차이)."
    return tasks, src, warnings, master_note


def compute_depths(tasks):
    by_id = {t["id"]: t for t in tasks}
    memo = {}

    def depth(tid, seen=()):
        if tid in memo:
            return memo[tid]
        t = by_id[tid]
        if tid in seen:
            return 1
        ds = [depth(d, seen + (tid,)) for d in t["deps"]
              if d in by_id and by_id[d]["wave"] == t["wave"]]
        memo[tid] = 1 + max(ds) if ds else 1
        return memo[tid]

    for t in tasks:
        t["depth"] = depth(t["id"])


# ─────────────────────────── git 상태 ───────────────────────────

def parse_tags(subject):
    """'[F-01][F-02] 요약', '[R-A, R-B] 요약' → (['F-01','F-02'], 접두 길이)."""
    m = TAG_RE.match(subject)
    if not m:
        return [], 0
    tags = []
    for grp in re.findall(r"\[([^\]]+)\]", m.group(1)):
        tags += [x for x in re.split(r"[,\s·]+", grp) if x]
    return list(dict.fromkeys(tags)), m.end()


def collect_git(git):
    info = {"ok": git.ok, "branch": "", "head": {}, "dirty": [], "refs": {}, "commits_all": []}
    if not git.ok:
        return info
    info["branch"] = (git.run("rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    h = git.run("log", "-1", "--format=%H%x1f%h%x1f%s%x1f%cI%x1f%an")
    if h:
        f = h.rstrip("\n").split("\x1f")
        info["head"] = {"sha": f[0], "short": f[1], "subject": f[2], "date": f[3], "author": f[4]}
    st = git.run("status", "--porcelain=v1", "-z", "--untracked-files=all") or ""
    parts, k = st.split("\0"), 0
    while k < len(parts):
        e = parts[k]
        if len(e) > 3:
            info["dirty"].append({"code": e[:2], "path": e[3:]})
            if e[0] in "RC":
                k += 1
        k += 1
    # 브랜치 (git branch -a 와 같은 대상)
    out = git.run("for-each-ref", "--format=%(refname)%1f%(objectname)%1f%(committerdate:iso-strict)%1f%(subject)%1f%(upstream:short)",
                  "refs/heads", "refs/remotes") or ""
    for ln in out.splitlines():
        f = ln.split("\x1f")
        if len(f) < 5:
            continue
        ref = f[0]
        if ref.endswith("/HEAD"):
            continue
        if ref.startswith("refs/heads/"):
            short, logical, kind = ref[11:], ref[11:], "local"
        else:
            short = ref[13:]
            logical, kind = short.split("/", 1)[1] if "/" in short else short, "remote"
        info["refs"][short] = {"ref": ref, "short": short, "logical": logical, "kind": kind,
                               "sha": f[1], "date": f[2], "subject": f[3], "upstream": f[4]}
    log = git.run("log", "--all", "--format=%H%x1f%h%x1f%cI%x1f%an%x1f%s%x1f%P") or ""
    for ln in log.splitlines():
        f = ln.split("\x1f")
        if len(f) < 6:
            continue
        tags = parse_tags(f[4])[0]
        info["commits_all"].append({"sha": f[0], "short": f[1], "date": f[2], "author": f[3],
                                    "subject": f[4], "parents": f[5].split(), "tags": tags})
    return info


def resolve_ref(ginfo, name):
    """논리 브랜치 이름 → 로컬 우선, 없으면 원격(origin/…)."""
    if not name:
        return None
    if name in ginfo["refs"] and ginfo["refs"][name]["kind"] == "local":
        return name
    if name in ginfo["refs"]:  # origin/… 처럼 정확한 원격 이름
        return name
    for short, r in ginfo["refs"].items():
        if r["kind"] == "remote" and r["logical"] == name:
            return short
    return None


def default_base(name, task_bases):
    if name in task_bases:
        return task_bases[name]
    if name.startswith(CFG["integration_prefix"]):
        return MAIN
    if name == MAIN:
        return f"origin/{MAIN}"
    return MAIN


def merged_set(git, base):
    out = git.cached("branch", "-a", "--merged", base, "--format=%(refname)")
    return set(out.split()) if out else set()


def branch_rel(git, ginfo, name, base):
    """브랜치와 base의 관계: ahead/behind, merged(--no-ff 머지 확인)."""
    ref = resolve_ref(ginfo, name)
    bref = resolve_ref(ginfo, base) if base and not base.startswith("origin/") else (
        base if base in ginfo["refs"] else None)
    res = {"name": name, "ref": ref, "exists": ref is not None, "base": base, "base_ref": bref,
           "ahead": None, "behind": None, "merged": False, "state": "없음"}
    if not ref:
        return res
    r = ginfo["refs"][ref]
    res.update(sha=r["sha"], date=r["date"], subject=r["subject"], kind=r["kind"])
    if not bref or bref == ref:
        res["state"] = "기준 없음"
        return res
    cnt = git.cached("rev-list", "--left-right", "--count", f"{bref}...{ref}")
    if cnt:
        b, a = cnt.split()
        res["behind"], res["ahead"] = int(b), int(a)
    in_merged = ginfo["refs"][ref]["ref"] in merged_set(git, bref)
    on_fp = r["sha"] in git.rev_set(bref, first_parent=True)
    if in_merged and not on_fp:
        res["merged"], res["state"] = True, "머지됨"
    elif res["ahead"]:
        res["state"] = "진행"
    else:
        res["state"] = "변경 없음"
    return res


def worker_aliases(tasks, ginfo):
    """Orca 워커 브랜치(예: user/f05-skeleton, user/req-api-007)를 작업 ID에 연결한다.

    브랜치 마지막 구간을 영숫자 토큰으로 나눠, 연속 토큰을 이어 붙인 문자열이
    작업 ID(하이픈 제거, 소문자)와 정확히 같을 때만 연결한다. 표준 브랜치(메인,
    int/*, tasks.yaml의 branch)는 제외한다.
    """
    std = {t["branch"] for t in tasks} | {t["base"] for t in tasks} | {MAIN}
    keys = {}
    for t in tasks:
        k = re.sub(r"[^a-z0-9]", "", t["id"].lower())
        keys[k] = t["id"]
        first = t["id"].split("-", 1)
        if len(first) == 2 and len(first[0]) <= 3:   # 'R-API-001' → 'api001'도 허용
            keys.setdefault(re.sub(r"[^a-z0-9]", "", first[1].lower()), t["id"])
    out = {}
    for r in ginfo["refs"].values():
        name = r["logical"]
        if name in std or name.startswith(CFG["integration_prefix"]) or name in out:
            continue
        toks = [x for x in re.split(r"[^a-z0-9]+", name.split("/")[-1].lower()) if x]
        cands = {"".join(toks[i:j]) for i in range(len(toks)) for j in range(i + 1, len(toks) + 1)}
        hits = [k for k in cands if k in keys]
        if hits:
            out[name] = keys[max(hits, key=len)]
    return out


def alias_base(t, shared):
    if t["branch"] and t["branch"] != MAIN and shared:
        return t["branch"]
    return t["base"] or MAIN


# ─────────────────────────── 상태 판정 ───────────────────────────

def bump(t, status, kind, detail):
    t["sources"].append({"kind": kind, "status": status, "detail": detail})
    if RANK[status] > RANK[t["status"]]:
        t["status"] = status


def merge_target(t):
    if t["layer"] == "L2":
        return t["base"]
    if t["branch"] and t["branch"] != MAIN:
        return MAIN
    return None


def apply_git_status(tasks, git, ginfo):
    """상태 보정 2단계: 브랜치 존재·머지 여부, 워커 브랜치, [작업ID] 커밋."""
    branch_count = {}
    for t in tasks:
        branch_count[t["branch"]] = branch_count.get(t["branch"], 0) + 1
    aliases = worker_aliases(tasks, ginfo)
    tagged = {}
    for c in ginfo["commits_all"]:
        for tg in c["tags"]:
            tagged.setdefault(tg, []).append(c)
    for t in tasks:
        t["commits"] = [c["short"] + " " + c["subject"] for c in tagged.get(t["id"], [])][:20]
        t["branch_info"] = None
        target = merge_target(t)
        tref = resolve_ref(ginfo, target) if target else None
        shared = branch_count.get(t["branch"], 0) > 1
        # 1) 전용 브랜치: 존재·머지 여부
        if t["branch"] and not shared and t["branch"] != MAIN:
            bi = branch_rel(git, ginfo, t["branch"], t["base"] or default_base(t["branch"], {}))
            t["branch_info"] = bi
            if bi["merged"]:
                bump(t, "merged", "git", f"{bi['ref']}가 {bi['base_ref']}에 머지됨")
            elif bi["exists"] and bi["ahead"]:
                bump(t, "running", "git", f"{bi['ref']}가 {bi['base_ref']}보다 {bi['ahead']}커밋 앞섬")
            elif bi["exists"]:
                t["sources"].append({"kind": "git", "status": None,
                                     "detail": f"{bi['ref']} 브랜치 있음 (base 대비 새 커밋 없음)"})
            # 브랜치를 지운 뒤에도 머지 커밋 메시지로 확인
            if t["status"] != "merged" and tref:
                subj = git.cached("log", tref, "--merges", "--format=%s") or ""
                if re.search(r"['\s/]" + re.escape(t["branch"]) + r"(['\s]|$)", subj, re.M):
                    bump(t, "merged", "git", f"{tref}에 '{t['branch']}' 머지 커밋 있음")
        elif shared and t["branch"]:
            bi = branch_rel(git, ginfo, t["branch"], t["base"])
            t["branch_info"] = bi
        # 1-b) Orca 워커 브랜치 (이름 규칙이 req/<ID>가 아닌 경우)
        t["worker_branches"] = []
        for name, tid in aliases.items():
            if tid != t["id"]:
                continue
            wb = branch_rel(git, ginfo, name, alias_base(t, shared))
            t["worker_branches"].append(wb)
            if wb["merged"]:
                st = "merged" if t["layer"] == "L2" else "running"
                bump(t, st, "git", f"워커 브랜치 {wb['ref']}가 {wb['base_ref']}에 머지됨")
            elif wb["ahead"]:
                bump(t, "running", "git", f"워커 브랜치 {wb['ref']}가 {wb['base_ref']}보다 {wb['ahead']}커밋 앞섬")
            else:
                t["sources"].append({"kind": "git", "status": None,
                                     "detail": f"워커 브랜치 {wb['ref']} 있음 ({wb['base_ref']} 대비 새 커밋 없음)"})
        # 2) 커밋 메시지 [작업ID] 접두
        cs = tagged.get(t["id"], [])
        if cs:
            reach = git.rev_set(tref) if tref and tref != resolve_ref(ginfo, t["branch"]) else set()
            if reach and all(c["sha"] in reach for c in cs):
                bump(t, "merged", "git", f"[{t['id']}] 커밋 {len(cs)}개가 모두 {tref}에 포함됨")
            else:
                bump(t, "running", "git", f"[{t['id']}] 커밋 {len(cs)}개 있음")
    # 게이트(G-0 등): 기반 머지 후 모듈 통합 브랜치(int/<모듈>) 생성
    for t in tasks:
        if t["id"] in CFG["gates"]:
            need = sorted({x["base"] for x in tasks if x["layer"] == "L2" and x["base"].startswith(CFG["integration_prefix"])})
            have = [b for b in need if resolve_ref(ginfo, b)]
            by_id = {x["id"]: x for x in tasks}
            open_deps = [d for d in t["deps"] if d in by_id and by_id[d]["status"] != "merged"]
            if need and len(have) == len(need) and not open_deps:
                bump(t, "merged", "git", "모듈 통합 브랜치 생성됨: " + ", ".join(have))
            elif need and len(have) == len(need):
                bump(t, "running", "git", "통합 브랜치는 있으나 선행 미머지: " + ", ".join(open_deps))
            elif have:
                bump(t, "running", "git", "일부 통합 브랜치만 있음: " + ", ".join(have))


def apply_evidence(tasks, repo, git, ginfo):
    """증적 report.md: 작업 트리 → 작업 브랜치 → 병합 대상 순으로 찾는다."""
    found = []
    for t in tasks:
        p = t["evidence_path"]
        t["evidence"] = None
        if (repo / p).is_file():
            t["evidence"] = {"key": p, "where": "작업 트리", "text": (repo / p).read_text(encoding="utf-8", errors="replace")}
        else:
            cands = []
            for name in (t["branch"], merge_target(t), MAIN):
                r = resolve_ref(ginfo, name)
                if r and r not in cands:
                    cands.append(r)
            for r in cands:
                txt = git.file_at(r, p)
                if txt is not None:
                    t["evidence"] = {"key": f"{r}:{p}", "where": f"브랜치 {r}", "text": txt}
                    break
        if t["evidence"]:
            bump(t, "review", "증적", f"{p} ({t['evidence']['where']})")
            found.append(t["evidence"])
    return found


def run_orca(tasks, run_id):
    cli = os.environ.get("ORCA_CLI_COMMAND")
    if not cli:
        return {"enabled": True, "ok": False, "message": "ORCA_CLI_COMMAND 환경변수가 없어 건너뜀", "matched": 0}
    cmd = [cli, "orchestration", "task-list", "--json", "--full"]
    if run_id:
        cmd += ["--run", run_id]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(p.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"enabled": True, "ok": False, "message": f"orca 실행 실패: {e}", "matched": 0}
    if not data.get("ok"):
        err = data.get("error") or {}
        return {"enabled": True, "ok": False,
                "message": f"orca: {err.get('code', '오류')} — {err.get('message', p.stderr.strip())[:200]}",
                "matched": 0}
    res = data.get("result") or {}
    otasks = res.get("tasks") or []
    ids = sorted((t["id"] for t in tasks), key=len, reverse=True)
    pats = {i: re.compile(r"(?<![\w-])" + re.escape(i) + r"(?![\w-])") for i in ids}
    by_id = {t["id"]: t for t in tasks}
    matched = 0
    for ot in otasks:
        best = None
        for field in ("task_title", "display_name", "title", "spec"):
            text = str(ot.get(field) or "")
            hits = [(m.start(), i) for i in ids for m in [pats[i].search(text)] if m]
            if hits:
                best = min(hits)[1]
                break
        if not best:
            continue
        ost = str(ot.get("status", "")).lower()
        mapped = ORCA_STATUS.get(ost)
        t = by_id[best]
        matched += 1
        if mapped:
            bump(t, mapped, "orca", f"{ot.get('id')} status={ost}")
        else:
            t["sources"].append({"kind": "orca", "status": None, "detail": f"{ot.get('id')} status={ost} (반영 안 함)"})
            if ost == "failed":
                t["orca_failed"] = True
    return {"enabled": True, "ok": True, "run": res.get("runId", run_id),
            "message": f"Run {res.get('runId', run_id or '')}: Orca 작업 {len(otasks)}개 중 {matched}개 연결",
            "matched": matched}


def finalize_status(tasks):
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["dependents"] = [x["id"] for x in tasks if t["id"] in x["deps"]]
    for t in tasks:
        open_deps = [d for d in t["deps"] if d in by_id and by_id[d]["status"] != "merged"]
        t["open_deps"] = open_deps
        if t["status"] == "todo" and not open_deps:
            bump(t, "ready", "파생", "선행작업이 모두 머지됨" if t["deps"] else "선행작업 없음")


# ─────────────────────────── RTM / TBD ───────────────────────────

def latest(repo, pattern):
    c = sorted(repo.glob(pattern))
    return c[-1] if c else None


def clean_id(s):
    s = re.sub(r"[*`]", "", s)
    note = "※" in s
    return s.replace("※", "").strip(), note


def parse_rtm(path):
    text = path.read_text(encoding="utf-8")
    sections, derived = [], None
    for tb in parse_tables(text):
        hd = tb["header"]
        if VSTAGES and any(VSTAGES[0] in h for h in hd):
            idx = {h: i for i, h in enumerate(hd)}
            heading = tb["heading"]
            # 섹션 제목의 '(<모듈 약칭>)' 또는 '(<모듈 키>)'로 모듈을 정한다
            prod = "common"
            for p, _, sh in PRODUCTS:
                if re.search(r"\(\s*(" + re.escape(sh) + "|" + re.escape(p) + r")\s*\)", heading, re.I):
                    prod = p
                    break
            ppat = CFG["rtm"].get("parent_pattern")
            parent = re.search(ppat, heading) if ppat else None
            cols = CFG["rtm"]["columns"]
            id_col = idx.get(cols.get("id")) if cols.get("id") else 0
            rows = []
            for r in tb["rows"]:
                rid, note = clean_id(r[id_col or 0])
                stages = []
                for s in VSTAGES:
                    v = r[idx[s]].strip() if s in idx else "-"
                    stages.append(v if v in VMARK_KO else ("-" if not v else v))
                tbd_cell = r[idx[cols["tbd"]]] if cols.get("tbd") in idx else ""
                is_sub = bool(parent) and not re.search(ppat, rid)
                rows.append({
                    "id": rid, "note": note,
                    "summary": r[idx.get(cols.get("summary"), 1)],
                    "verify": r[idx[cols["verify"]]] if cols.get("verify") in idx else "",
                    "alloc": r[idx[cols["alloc"]]] if cols.get("alloc") in idx else "",
                    "tbds": re.findall(r"TB[DC]-\d+", tbd_cell),
                    "stages": stages,
                    "parent": parent.group(0) if is_sub else None,
                    "letter": (rid.rsplit("-", 1)[1] if is_sub else None),
                })
            sections.append({"heading": heading, "product": prod,
                             "parent": parent.group(0) if parent else None, "rows": rows})
        elif "근거" in hd and "상태" in hd:
            derived = {"heading": tb["heading"], "header": hd, "rows": tb["rows"]}
    notes = [ln.strip() for ln in text.split("\n") if ln.strip().startswith("※")]
    return {"path": path, "sections": sections, "derived": derived, "notes": notes}


def expand_req_refs(cell, rtm_ids):
    out, prefix = [], None
    for tok in re.split(r",\s*", cell):
        tok = tok.strip()
        if not tok:
            continue
        if "*" in tok:
            out += [i for i in rtm_ids if fnmatch.fnmatch(i, tok)]
            continue
        m = re.match(CFG["rtm"]["ref_pattern"], tok)
        if m:
            prefix = m.group(1)
            out.append(prefix + m.group(2))
            out += [prefix + x for x in m.group(3).split("·") if x]
            continue
        if re.fullmatch(r"\d+", tok) and prefix:
            out.append(prefix + tok)
            continue
        if tok in rtm_ids:
            out.append(tok)
    return out


def parse_tbd(path, rtm_ids):
    text = path.read_text(encoding="utf-8")
    rows = []
    for tb in parse_tables(text):
        hd = tb["header"]
        tc = CFG["tbd"]["columns"]
        if tc["state"] not in hd:
            continue
        ix = {h: i for i, h in enumerate(hd)}
        for r in tb["rows"]:
            g = lambda k: r[ix[tc[k]]] if tc.get(k) in ix else ""
            rows.append({"id": clean_id(g("id"))[0], "reqs_raw": g("reqs"),
                         "reqs": expand_req_refs(g("reqs"), rtm_ids),
                         "kind": g("kind").strip(), "title": g("title"), "reason": g("reason"),
                         "due": g("due"), "state": g("state").strip()})
    return {"path": path, "rows": rows}


# ─────────────────────────── 문서 ───────────────────────────

def revision_of(text):
    for tb in parse_tables(text):
        if "개정이력" in tb["heading"] or ("판" in tb["header"] and "일자" in tb["header"]):
            if tb["rows"]:
                ix = {h: i for i, h in enumerate(tb["header"])}
                r = tb["rows"][-1]
                return {"ver": r[ix.get("판", 0)], "date": r[ix.get("일자", 1)] if len(r) > 1 else "",
                        "desc": r[ix["내용"]] if "내용" in ix else ""}
    return None


def scan_docs(repo, git, dirty_paths):
    docs_rel = CFG["paths"]["docs"].strip("/")
    docs_dir = repo / docs_rel
    folders, warnings, contents = [], [], {}
    if not docs_dir.is_dir():
        return folders, [{"path": f"{docs_rel}/", "msg": "docs 폴더가 없음"}], contents
    readme = docs_dir / "README.md"
    listed = set()
    if readme.is_file():
        for tb in parse_tables(readme.read_text(encoding="utf-8")):
            for r in tb["rows"]:
                for _, url in LINK_RE.findall(" ".join(r)):
                    listed.add(os.path.normpath(f"{docs_rel}/" + url))

    def git_date(rel):
        if rel in dirty_paths:
            code = dirty_paths[rel]
            if code == "??":
                return "", "미추적"
            last = (git.run("log", "-1", "--format=%cI", "--", rel) or "").strip()
            return last, "수정됨"
        last = (git.run("log", "-1", "--format=%cI", "--", rel) or "").strip()
        return last, "" if last else "기록 없음"

    def entry(f, folder_rel, check_name):
        rel = f.relative_to(repo).as_posix()
        text = f.read_text(encoding="utf-8", errors="replace") if f.suffix == ".md" else ""
        e = {"path": rel, "file": f.name, "seq": "", "name": f.stem, "date": "",
             "rev": revision_of(text) if text else None, "warnings": [], "archive": False}
        e["git_date"], e["git_state"] = git_date(rel)
        m = DOC_NAME_RE.match(f.name)
        am = ARCHIVE_NAME_RE.match(f.name)
        if am:
            # 보관 사본: 원본과 같은 순번을 쓰고 문서 목록에 올리지 않는다
            e["archive"] = True
            e["seq"], e["name"], e["date"] = am.group(1), f"{am.group(2)} (보관 v{am.group(3)})", am.group(4)
        elif check_name:
            if not m:
                e["warnings"].append("파일명 규칙 위반: <순번2자리>_<문서명>_<YYYYMMDD>.md")
            else:
                e["seq"], e["name"], e["date"] = m.group(1), m.group(2), m.group(3)
                if re.search(r"\s", e["name"]):
                    e["warnings"].append("문서명에 공백이 있음")
                try:
                    d = dt.datetime.strptime(e["date"], "%Y%m%d").date()
                    if e["rev"] and e["rev"]["date"] and e["rev"]["date"] != d.isoformat():
                        e["warnings"].append(f"파일명 날짜({d.isoformat()})와 최신 개정일({e['rev']['date']})이 다름")
                except ValueError:
                    e["warnings"].append(f"날짜 형식 오류: {e['date']}")
                if not e["rev"]:
                    e["warnings"].append("개정이력 표 없음")
                if listed and rel not in listed:
                    e["warnings"].append("docs/README.md 문서 목록에 없음")
        elif m:
            e["seq"], e["name"], e["date"] = m.group(1), m.group(2), m.group(3)
        if text:
            contents[rel] = text
        return e

    top = {"name": docs_rel, "num": "", "label": f"{docs_rel} (최상위)", "files": [], "evidence": False}
    for f in sorted(docs_dir.iterdir()):
        if f.is_file() and f.name != ".gitkeep":
            top["files"].append(entry(f, docs_rel, check_name=f.name != "README.md"))
    folders.append(top)
    for d in sorted([x for x in docs_dir.iterdir() if x.is_dir()], key=lambda p: p.name):
        fm = FOLDER_RE.match(d.name)
        fo = {"name": d.name, "num": fm.group(1) if fm else "", "label": d.name, "files": [],
              "evidence": d.name == CFG["paths"]["evidence_dir"], "subdirs": []}
        if not fm:
            warnings.append({"path": f"{docs_rel}/{d.name}/", "msg": "폴더명 규칙 위반: <번호2자리>_<이름>"})
        if fo["evidence"]:
            for f in sorted(d.rglob("*")):
                if f.is_file() and f.name != ".gitkeep":
                    fo["files"].append(entry(f, d.name, check_name=False))
            fo["subdirs"] = sorted(x.name for x in d.iterdir() if x.is_dir())
        else:
            seqs = {}
            for f in sorted(d.rglob("*")):
                if not f.is_file() or f.name == ".gitkeep":
                    continue
                if set(CFG["paths"]["skip_dirs"]) & set(f.relative_to(d).parts[:-1]):
                    continue    # 예: 07_process/snapshots/ 대시보드 스냅샷 보관(문서 아님)
                e = entry(f, d.name, check_name=f.name != "README.md" and f.parent == d)
                if f.parent != d and f.name != "README.md":
                    e["warnings"].append("하위 폴더 안 문서 (번호 규칙 범위 밖)")
                if e["seq"] and not e["archive"]:
                    seqs.setdefault(e["seq"], []).append(e)
                fo["files"].append(e)
            for s, es in seqs.items():
                if len(es) > 1:
                    for e in es:
                        e["warnings"].append(f"순번 {s} 중복")
        folders.append(fo)
    folders.sort(key=lambda x: (x["num"] == "" and x["name"] != docs_rel, x["num"], x["name"]))
    for fo in folders:
        for e in fo["files"]:
            for w in e["warnings"]:
                warnings.append({"path": e["path"], "msg": w})
    return folders, warnings, contents


def parse_review_table(repo):
    readme = repo / CFG["paths"]["docs"] / "README.md"
    out = {}
    codes = [c for c, _, _, _ in REVIEW_STAGES]
    if not readme.is_file() or not codes:
        return out
    for tb in parse_tables(readme.read_text(encoding="utf-8")):
        if "주요 산출물" in tb["header"]:
            ix = {h: i for i, h in enumerate(tb["header"])}
            for r in tb["rows"]:
                m = re.search("(" + "|".join(re.escape(c) for c in codes) + ")", r[0])
                if m:
                    out[m.group(1)] = {"purpose": r[ix.get("목적", 1)], "entry": r[ix.get("진입 조건", 2)],
                                       "outputs": r[ix.get("주요 산출물", 3)]}
    return out


# ─────────────────────────── HTML 조각 ───────────────────────────

def chip(status, extra=""):
    return f'<span class="st st-{status}" title="{status}">{STATUS_KO.get(status, status)}{extra}</span>'


def task_link(tid, by_id, with_status=False):
    if tid not in by_id:
        return f'<span class="mono">{esc(tid)}</span>'
    t = by_id[tid]
    s = f'<a class="mono tl" href="#task/{quote(tid)}" title="{esc(t["title"])}">{esc(tid)}</a>'
    if with_status:
        s += " " + chip(t["status"])
    return s


def doc_href(key):
    return "#doc/" + quote(key, safe="")


def stacked_bar(counts, total, order=STATUSES, cls="st"):
    if not total:
        return '<div class="bar"></div>'
    segs = "".join(
        f'<span class="seg {cls}-{k}" style="width:{counts.get(k, 0) * 100 / total:.2f}%" '
        f'title="{esc(STATUS_KO.get(k, VMARK_KO.get(k, k)))} {counts.get(k, 0)}"></span>'
        for k in order if counts.get(k, 0))
    return f'<div class="bar">{segs}</div>'


def build_dag_svg(tasks):
    NW, NH, CG, RG, LP, LEFT, TOP = 156, 40, 30, 8, 10, 56, 40
    waves = [w for w, _ in WAVES if any(t["wave"] == w for t in tasks)]
    extra = sorted({t["wave"] for t in tasks} - set(waves))
    waves += extra
    wave_label = dict(WAVES)
    cols, col_index = [], {}
    for w in waves:
        md = max(t["depth"] for t in tasks if t["wave"] == w)
        for d in range(1, md + 1):
            col_index[(w, d)] = len(cols)
            cols.append((w, d))
    lanes = [p for p, _, _ in PRODUCTS if any(t["product"] == p for t in tasks)]
    lanes += sorted({t["product"] for t in tasks} - set(lanes))
    # 열 순서대로 배치하면서 선행작업 무게중심으로 정렬 (교차 줄이기)
    cells = {}
    for t in tasks:
        cells.setdefault((t["product"], col_index[(t["wave"], t["depth"])]), []).append(t)
    order_y = {}
    lane_rows = {p: 1 for p in lanes}
    for ci in range(len(cols)):
        for p in lanes:
            cell = cells.get((p, ci), [])

            def key(t):
                ys = [order_y[d] for d in t["deps"] if d in order_y]
                return (sum(ys) / len(ys) if ys else 1e9, t["id"])
            cell.sort(key=key)
            for k, t in enumerate(cell):
                t["_row"] = k
                order_y[t["id"]] = lanes.index(p) * 100 + k
            lane_rows[p] = max(lane_rows[p], len(cell))
    lane_y, y = {}, TOP
    for p in lanes:
        h = LP * 2 + lane_rows[p] * NH + (lane_rows[p] - 1) * RG
        lane_y[p] = (y, h)
        y += h + 4
    W = LEFT + len(cols) * (NW + CG) - CG + 16
    H = y + 4
    pos = {}
    for t in tasks:
        ci = col_index[(t["wave"], t["depth"])]
        pos[t["id"]] = (LEFT + ci * (NW + CG), lane_y[t["product"]][0] + LP + t["_row"] * (NH + RG))
    s = [f'<svg class="dag" id="dag-svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         f'aria-label="작업 의존 그래프" xmlns="http://www.w3.org/2000/svg">',
         '<defs><marker id="ah" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7" markerHeight="7" orient="auto">'
         '<path d="M0,0L8,4L0,8z" class="ahp"/></marker>'
         '<marker id="ah2" viewBox="0 0 8 8" refX="7.5" refY="4" markerWidth="7" markerHeight="7" orient="auto">'
         '<path d="M0,0L8,4L0,8z" class="ahp2"/></marker></defs>']
    for k, p in enumerate(lanes):
        ly, lh = lane_y[p]
        s.append(f'<rect class="lane{k % 2}" x="0" y="{ly}" width="{W}" height="{lh}"/>')
        s.append(f'<text class="lanel" x="8" y="{ly + 18}">{esc(PRODUCT_SHORT.get(p, p))}</text>')
    for w in waves:
        cis = [i for i, (cw, _) in enumerate(cols) if cw == w]
        x0 = LEFT + cis[0] * (NW + CG)
        x1 = LEFT + cis[-1] * (NW + CG) + NW
        n = sum(1 for t in tasks if t["wave"] == w)
        s.append(f'<rect class="wh" x="{x0}" y="8" width="{x1 - x0}" height="22" rx="3"/>')
        s.append(f'<text class="wht" x="{(x0 + x1) / 2}" y="23" text-anchor="middle">'
                 f'{esc(wave_label.get(w, w))} · {n}</text>')
    for t in tasks:
        x2, y2 = pos[t["id"]]
        for d in t["deps"]:
            if d not in pos:
                continue
            x1, y1 = pos[d]
            sx, sy, ex, ey = x1 + NW, y1 + NH / 2, x2 - 1, y2 + NH / 2
            dx = max(18, (ex - sx) / 2)
            s.append(f'<path class="edge" data-from="{esc(d)}" data-to="{esc(t["id"])}" '
                     f'd="M{sx},{sy} C{sx + dx},{sy} {ex - dx},{ey} {ex},{ey}"/>')
    for t in tasks:
        x, y = pos[t["id"]]
        nopen = len(t["open_deps"])
        tip = f'{t["id"]} {t["title"]} — {STATUS_KO[t["status"]]}, 열린 선행 {nopen}/{len(t["deps"])}'
        badge = ""
        if nopen:
            badge = (f'<rect class="bdg" x="{NW - 25}" y="5" width="20" height="14" rx="7"/>'
                     f'<text class="bdgt" x="{NW - 15}" y="15.5" text-anchor="middle">{nopen}</text>')
        perf = f'<text class="perf" x="{NW - 8}" y="{NH - 6}" text-anchor="end">p95</text>' if t["perf"] else ""
        s.append(f'<g class="node st-{t["status"]}" data-id="{esc(t["id"])}" tabindex="0" role="button" '
                 f'transform="translate({x},{y})"><title>{esc(tip)}</title>'
                 f'<rect class="nb" width="{NW}" height="{NH}" rx="4"/>'
                 f'<rect class="ns" width="4" height="{NH}" rx="1"/>'
                 f'<text class="nid" x="10" y="15">{esc(t["id"])}</text>'
                 f'<text class="nt" x="10" y="31">{esc(trunc(t["title"], 23 if t["perf"] else 26))}</text>'
                 f'{badge}{perf}</g>')
    s.append("</svg>")
    return "".join(s)


AGENT_LABEL = {"claude": "Claude", "codex": "Codex", "antigravity": "Antigravity", "grok": "Grok"}


def fmt_tokens(n):
    if not n:
        return ""
    return f"{n / 1e6:.1f}M" if n >= 1e6 else (f"{n / 1e3:.0f}k" if n >= 1e3 else str(n))


def fmt_mins(m):
    if m is None:
        return ""
    return f"{m // 60}시간 {m % 60}분" if m >= 60 else f"{m}분"


def collect_models(repo, tasks):
    """모델 라우팅 정보(orchestration/models.yaml이 있을 때만): 작업별 배정 모델, 구독 상태, 착수 가능 작업의 라우팅 미리보기.
    CLI를 새로 돌리지 않는다(30초마다 재빌드하므로). 모델 목록은 state/models_cache.json에 있는 것만 쓴다."""
    res = {"ok": False, "msg": "", "agents": [], "by_task": {}, "preview": {}, "objective": ""}
    tools = Path(__file__).resolve().parent.parent
    if not (tools / "routing.py").is_file():
        res["msg"] = "routing.py 없음"
        return res
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import routing
        catalog, pol = routing.load(repo)
    except Exception as e:  # 라우팅이 깨져도 대시보드는 나와야 한다
        res["msg"] = f"라우팅 정보를 읽지 못함: {e}"
        return res
    if not pol.get("_present"):
        res["msg"] = "orchestration/models.yaml 없음"
        return res
    raw = {t["id"]: dict(t.get("raw") or {}, id=t["id"], status=t["status"]) for t in tasks}
    rows = routing.read_dispatch(repo, set(raw))
    last = {}
    for r in rows:
        if r["kit_id"] and r["mode"] in ("new", "retry"):
            last[r["kit_id"]] = r
    now = dt.datetime.now()
    for tid, r in last.items():
        try:
            mins = int((now - dt.datetime.strptime(r["ts"], "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
        except ValueError:
            mins = None
        res["by_task"][tid] = {"agent": r["agent"], "model": r["model"], "effort": r["effort"],
                               "mode": r["mode"], "ts": r["ts"], "mins": mins}
    disc = None
    try:
        c = json.loads((routing.state_dir(repo) / "models_cache.json").read_text(encoding="utf-8"))
        if c.get("v") == 2:
            disc = c.get("agents") or {}
    except (OSError, json.JSONDecodeError):
        pass
    running = routing.running_by_agent(rows, raw)
    cools = routing.read_cooldowns(repo)
    caps = routing.rcfg(pol, "max_concurrent") or {}
    allow = routing.rcfg(pol, "agents")
    usage = routing.orca_usage(1)
    for ag in (list(allow) if allow is not None else list(catalog["agents"])):
        info = (disc or {}).get(ag) or {}
        if disc is None:
            state, note = "unknown", "모델 확인 전 (models.py discover)"
        elif not info.get("bin"):
            state, note = "off", "CLI 없음"
        elif info.get("confined") and not (pol.get("agents") or {}).get(ag, {}).get("allow_confined"):
            state, note = "off", "제외 · snap 격리"
        elif ag in cools:
            left = max(0, int(cools[ag]["until"] - time.time()))
            state = "cool"
            note = f"쿨다운 {left // 3600}h" if left >= 3600 else f"쿨다운 {left // 60}분"
            if cools[ag].get("reason"):
                note += f" · {cools[ag]['reason']}"
        else:
            state, note = "ok", ""
        res["agents"].append({"id": ag, "label": AGENT_LABEL.get(ag, ag), "state": state, "note": note,
                              "running": running.get(ag, 0), "cap": caps.get(ag),
                              "tokens": routing.today_tokens(usage, ag)})
    res["enabled"] = routing.enabled(pol)
    res["objective"] = str(routing.rcfg(pol, "objective", "balanced"))
    if res["enabled"] and disc is not None:
        for t in tasks:
            if t["status"] != "ready":
                continue
            try:
                res["preview"][t["id"]] = routing.route(repo, None, t["id"], raw, catalog=catalog, pol=pol, disc=disc, jev_net=False)
            except Exception:
                continue
    res["ok"] = True
    return res


def build_overview_dag(tasks):
    """개요 탭의 웨이브 × 모듈 격자. 노드를 누르면 아래 보드에서 그 카드를 표시한다(선과 상세는 작업 DAG 탭)."""
    waves = [w for w, _ in WAVES if any(t["wave"] == w for t in tasks)]
    waves += sorted({t["wave"] for t in tasks} - set(waves))
    lanes = [p for p, _, _ in PRODUCTS if any(t["product"] == p for t in tasks)]
    lanes += sorted({t["product"] for t in tasks} - set(lanes))
    wl = dict(WAVES)
    cols = f"grid-template-columns:76px repeat({len(waves)},minmax(140px,1fr));min-width:{76 + 148 * len(waves)}px"
    s = [f'<div class="mg" style="{cols}"><div class="mh"></div>']
    s += [f'<div class="mh">{esc(wl.get(w, w))}</div>' for w in waves]
    for i, p in enumerate(lanes):
        alt = " alt" if i % 2 == 0 else ""
        s.append(f'<div class="ml{alt}" data-product="{esc(p)}">{esc(PRODUCT_SHORT.get(p, p))}</div>')
        for w in waves:
            cell = sorted((t for t in tasks if t["product"] == p and t["wave"] == w), key=lambda t: (t["depth"], t["id"]))
            nodes = "".join(
                f'<button type="button" class="mn st-{t["status"]}" data-id="{esc(t["id"])}" aria-pressed="false" '
                f'title="{esc(t["id"])} {esc(t["title"])} — {STATUS_KO[t["status"]]}"><b>{esc(t["id"])}</b>'
                f'<span>{esc(trunc(t["title"], 18))}</span></button>' for t in cell)
            s.append(f'<div class="mc{alt}" data-product="{esc(p)}">{nodes}</div>')
    s.append("</div>")
    return "".join(s)


def board_card(t, models, by_id):
    """개요 보드의 작업 카드 한 장."""
    tid, st = t["id"], t["status"]
    bt = (models.get("by_task") or {}).get(tid)
    dec = (models.get("preview") or {}).get(tid)
    chipm, meta, extra = "", "", ""
    if st in ("running", "review", "merged") and bt and bt.get("agent"):
        chipm = " · ".join(x for x in (bt["agent"], bt["model"], bt["effort"]) if x)
    elif st == "ready" and dec:
        chipm = ("예정 " + " · ".join(x for x in (dec["agent"], dec["model"], dec.get("effort")) if x)) if dec.get("key") else "라우팅 후보 없음"
    if st == "running":
        if bt and bt.get("mins") is not None:
            meta = f'투입 {esc(bt["ts"][5:16])} · {fmt_mins(bt["mins"])} 경과' + (" · 재시도" if bt.get("mode") == "retry" else "")
        else:
            meta = "진행 중"
    elif st == "ready":
        meta = "선행 모두 머지"
        if dec:
            meta = f'난이도 {dec["difficulty"]:.1f}' + (f' · {esc(", ".join(dec["tags"]))}' if dec.get("tags") else "") \
                + (" · 임계 경로" if dec.get("critical") else "")
        extra = (f'<button type="button" class="kcopy" data-cmd="orchestration/tools/launch.sh {esc(tid)}">'
                 f'투입 명령 복사</button>')
    elif st == "todo":
        od = t["open_deps"]
        meta = ("선행 대기 " + ", ".join(od[:3]) + (f" 외 {len(od) - 3}" if len(od) > 3 else "")) if od else "대기"
    elif st == "review":
        meta = "증적 있음 · 검토 대기" if t["evidence"] else "검토 대기"
    else:
        meta = "머지됨" + (" · 증적 있음" if t["evidence"] else "")
    return (f'<article class="kc" data-id="{esc(tid)}" data-product="{esc(t["product"])}">'
            f'<div class="kh">{task_link(tid, by_id)}<span class="kt">{esc(PRODUCT_SHORT.get(t["product"], t["product"]))} · {esc(t["wave"])}</span></div>'
            f'<div class="ktl">{esc(t["title"])}</div>'
            + (f'<span class="km mono">{esc(chipm)}</span>' if chipm else "")
            + f'<div class="muted small">{meta}</div>{extra}</article>')


# ─────────────────────────── 페이지 조립 ───────────────────────────

def build(args):
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()
    git = Git(repo)
    top = (git.run("rev-parse", "--show-toplevel") or "").strip()
    if top:
        repo = Path(top)
    now = dt.datetime.now().astimezone()
    warnings_global = []

    tasks, tasks_src, tw, master_note = load_tasks(repo, git, args.tasks_ref)
    warnings_global += tw
    if master_note:
        warnings_global.append(master_note)
    for t in tasks:
        t["status"] = t["yaml_status"]
        t["sources"] = [{"kind": "tasks.yaml", "status": t["yaml_status"], "detail": f"status: {t['yaml_status']}"}]
    compute_depths(tasks)
    ginfo = collect_git(git)
    if not ginfo["ok"]:
        warnings_global.append("git 저장소를 읽지 못해 브랜치 보정을 건너뜀")
    else:
        apply_git_status(tasks, git, ginfo)
    evid = apply_evidence(tasks, repo, git, ginfo)
    orca = {"enabled": False, "ok": False, "message": "--orca 미사용", "matched": 0}
    if args.orca:
        orca = run_orca(tasks, args.orca_run)
        if not orca["ok"]:
            warnings_global.append(orca["message"])
    finalize_status(tasks)
    by_id = {t["id"]: t for t in tasks}

    # RTM · TBD
    rtm_path = latest(repo, CFG["paths"]["rtm"]) if CFG["paths"].get("rtm") else None
    tbd_path = latest(repo, CFG["paths"]["tbd"]) if CFG["paths"].get("tbd") else None
    rtm = parse_rtm(rtm_path) if rtm_path else {"sections": [], "derived": None, "notes": [], "path": None}
    rtm_ids = [r["id"] for s in rtm["sections"] for r in s["rows"]]
    rtm_parents = {s["parent"] for s in rtm["sections"] if s["parent"]}
    tbd = parse_tbd(tbd_path, rtm_ids) if tbd_path else {"rows": [], "path": None}
    if not rtm_path:
        warnings_global.append(f"요구사항추적표({CFG['paths'].get('rtm')})가 없음")
    if not tbd_path:
        warnings_global.append(f"TBD관리대장({CFG['paths'].get('tbd')})이 없음")

    def tasks_for_row(r):
        if r["parent"]:
            return [t["id"] for t in tasks if r["parent"] in t["reqs"]
                    and (not t["sub_letters"] or r["letter"] in t["sub_letters"])]
        return [t["id"] for t in tasks if r["id"] in t["reqs"]]

    for s in rtm["sections"]:
        for r in s["rows"]:
            r["tasks"] = tasks_for_row(r)
    no_task_rows = [r["id"] for s in rtm["sections"] for r in s["rows"] if not r["tasks"]]
    unknown_reqs = sorted({q for t in tasks for q in t["reqs"] if q not in rtm_ids and q not in rtm_parents})
    tbd_rev = {}
    for s in rtm["sections"]:
        for r in s["rows"]:
            for x in r["tbds"]:
                tbd_rev.setdefault(x, []).append(r["id"])

    # 문서
    dirty_paths = {d["path"]: d["code"] for d in ginfo["dirty"]}
    folders, doc_warnings, doc_texts = scan_docs(repo, git, dirty_paths)
    for e in evid:
        doc_texts.setdefault(e["key"], e["text"])
    doc_count = sum(len(f["files"]) for f in folders)
    numbered_count = sum(1 for f in folders for e in f["files"] if e["seq"] and not f["evidence"])
    known_docs = set(doc_texts)

    def make_linkfn(doc_key):
        base_dir = os.path.dirname(doc_key.split(":", 1)[-1])

        def linkfn(text, url):
            if re.match(r"^https?://", url):
                return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{text}</a>'
            target = os.path.normpath(os.path.join(base_dir, url.split("#")[0])) if url and not url.startswith("#") else ""
            if target in known_docs:
                return f'<a href="{doc_href(target)}">{text}</a>'
            return f'<span class="xref" title="{esc(url)}">{text}</span>'
        return linkfn

    # 검토회의 산출물
    review_info = parse_review_table(repo)
    folder_files = {f["name"]: f["files"] for f in folders}
    stages = []
    for code, ko, folder, arts in REVIEW_STAGES:
        files = folder_files.get(folder, [])
        rows = []
        for name, keys in arts:
            hits = [e for e in files if any(norm_key(k) in norm_key(e["name"]) for k in keys)]
            rows.append((name, hits))
        stages.append({"code": code, "ko": ko, "folder": folder, "exists": folder in folder_files,
                       "files": files, "arts": rows, "info": review_info.get(code, {})})

    # git 활동
    commits = ginfo["commits_all"][:30]
    for c in commits:
        out = git.run("for-each-ref", "--contains", c["sha"], "--format=%(refname:short)",
                      "refs/heads", "refs/remotes") or ""
        c["branches"] = [b for b in out.split() if not b.endswith("/HEAD")]
    task_bases = {t["branch"]: t["base"] for t in tasks if t["branch"] and t["branch"] != t["base"]}
    aliases = worker_aliases(tasks, ginfo) if ginfo["ok"] else {}
    bcount = {}
    for t in tasks:
        bcount[t["branch"]] = bcount.get(t["branch"], 0) + 1
    for name, tid in aliases.items():
        task_bases[name] = alias_base(by_id[tid], bcount.get(by_id[tid]["branch"], 0) > 1)
    branches = []
    for short, r in sorted(ginfo["refs"].items(), key=lambda kv: (kv[1]["kind"], kv[0])):
        base = default_base(r["logical"], task_bases)
        rel = branch_rel(git, ginfo, short, base)
        linked = [t["id"] for t in tasks if t["branch"] == r["logical"]]
        if r["logical"] in aliases:
            linked = [aliases[r["logical"]]]
        rel["kind"], rel["linked"] = r["kind"], linked
        branches.append(rel)

    # ── 집계
    def counts(ts):
        c = {s: 0 for s in STATUSES}
        for t in ts:
            c[t["status"]] += 1
        return c

    total = len(tasks)
    all_counts = counts(tasks)
    rtm_rows_all = [r for s in rtm["sections"] for r in s["rows"]]
    stage_counts = []
    for k, sname in enumerate(VSTAGES):
        c = {m: 0 for m in VMARK_KO}
        for r in rtm_rows_all:
            v = r["stages"][k]
            c[v if v in c else "-"] += 1
        stage_counts.append(c)
    tbd_open = sum(1 for r in tbd["rows"] if r["state"] == CFG["tbd"]["open_state"])

    # ── HTML 작성
    H = []
    a = H.append
    head = ginfo["head"]
    build_id = now.strftime("%Y%m%d%H%M%S%f")

    # 개요: 현재 웨이브 → 구독 → 웨이브 × 모듈 격자(접기) → 상태 열 보드 → 주의 사항 → 집계
    models = collect_models(repo, tasks)
    a('<section class="panel" id="p-overview">')
    cur = next((w for w, _ in WAVES if any(t["wave"] == w and t["status"] != "merged" for t in tasks)), None)
    cur_title = dict(WAVES).get(cur, cur) if cur else ("모든 작업 머지" if tasks else "작업 없음")
    a(f'<div class="ovhead"><h2>{esc(cur_title)}</h2><span class="muted">머지 {all_counts["merged"]}/{total} · 진행 {all_counts["running"]}'
      f' · 검토 {all_counts["review"]} · 착수 가능 {all_counts["ready"]}</span>{stacked_bar(all_counts, total)}</div>')
    if models["ok"] and models["agents"]:
        chips = []
        for g in models["agents"]:
            if g["state"] == "ok":
                cap = g["cap"] if g["cap"] is not None else "∞"
                tok = f' <span class="muted small">오늘 {fmt_tokens(g["tokens"])}</span>' if g["tokens"] else ""
                body = f'{esc(g["label"])} <b>{g["running"]}/{cap}</b>{tok}'
            else:
                body = f'{esc(g["label"])} <span class="small">{esc(trunc(g["note"], 28))}</span>'
            chips.append(f'<span class="sub {g["state"]}" title="{esc(g["label"] + (" · " + g["note"] if g["note"] else " · 진행 중/동시 상한"))}">'
                         f'<i class="dot"></i>{body}</span>')
        flag = "" if models.get("enabled") else " · 자동 라우팅 꺼짐"
        a(f'<div class="subs" aria-label="구독 상태">{"".join(chips)}'
          f'<span class="muted small">목표 {esc(models["objective"])}{flag} · 자세히: <code>models.py plan</code></span></div>')
    a('<details class="card ovdag" id="ov-dag" open><summary><b>작업 DAG · 웨이브 × 모듈</b> '
      '<span class="muted small">노드를 누르면 아래 보드에서 그 카드를 표시한다</span></summary>')
    a(f'<div class="tw">{build_overview_dag(tasks)}</div><div class="ovdag-f"><div class="legend">'
      + "".join(f'<span><i class="dot st-{s}"></i>{STATUS_KO[s]}</span>' for s in STATUSES)
      + '</div><a href="#dag" class="small">의존선·상세는 작업 DAG 탭 →</a></div></details>')
    lanes = [p for p, _, _ in PRODUCTS if any(t["product"] == p for t in tasks)]
    lanes += sorted({t["product"] for t in tasks} - set(lanes))
    a('<div class="filters" id="ov-filter" role="group" aria-label="모듈 거르기"><span class="fl">모듈</span>'
      '<button type="button" data-mod="all" aria-pressed="true">전체</button>'
      + "".join(f'<button type="button" data-mod="{esc(p)}" aria-pressed="false">{esc(PRODUCT_SHORT.get(p, p))}</button>' for p in lanes)
      + '</div>')
    empty_txt = {"todo": "대기 작업 없음", "ready": "띄울 작업 없음", "running": "진행 중인 워커 없음",
                 "review": "검토할 작업 없음", "merged": "아직 없음"}
    worder = {w: i for i, (w, _) in enumerate(WAVES)}
    a('<div class="tw"><div class="kb" id="ov-board">')
    for s in STATUSES:
        cards = sorted((t for t in tasks if t["status"] == s), key=lambda t: (worder.get(t["wave"], 99), t["depth"], t["id"]))
        a(f'<section class="kcol st-{s}" data-status="{s}" aria-label="{STATUS_KO[s]}"><header><i class="dot"></i>'
          f'<h3>{STATUS_KO[s]}</h3><span class="kcnt">{len(cards)}</span></header><div class="kbody">'
          + "".join(board_card(t, models, by_id) for t in cards)
          + f'<p class="kempty muted"{" hidden" if cards else ""}>{empty_txt[s]}</p></div></section>')
    a('</div></div>')

    alerts = list(warnings_global)
    if models.get("msg") and models["msg"] not in ("orchestration/models.yaml 없음", "routing.py 없음"):
        alerts.append(models["msg"])
    if no_task_rows:
        alerts.append(f"작업에 연결되지 않은 RTM 행 {len(no_task_rows)}개: " + ", ".join(no_task_rows))
    # L1은 점진 통합이 정상이고, 같은 통합 브랜치(예: int/common)에서 이어서 하는 선행은 제외한다
    early = [t for t in tasks if t["status"] in ("running", "review", "merged") and t["layer"] != "L1"
             and any(by_id[d]["branch"] != t["branch"] for d in t["open_deps"])]
    if early:
        alerts.append("선행작업이 끝나지 않았는데 진행·완료로 판정된 작업: " + ", ".join(
            f'{t["id"]}({STATUS_KO[t["status"]]}, 열린 선행 {"·".join(t["open_deps"])})' for t in early))
    if unknown_reqs:
        alerts.append("tasks.yaml reqs 중 RTM에 없는 ID: " + ", ".join(unknown_reqs))
    for w in doc_warnings:
        alerts.append(f'{w["path"]}: {w["msg"]}')
    a(f'<div class="card"><h3>주의 사항 <span class="muted">{len(alerts)}</span></h3>')
    a('<ul class="alerts">' + "".join(f'<li>{esc(x)}</li>' for x in alerts) + '</ul>' if alerts else '<p class="muted">없음</p>')
    a('</div>')

    a('<div class="kpis">')
    kp = [
        ("작업", f'{all_counts["merged"]}/{total}', f'머지됨 · 진행 {all_counts["running"]} · 검토 {all_counts["review"]}'),
        ("착수 가능", str(all_counts["ready"]), "선행작업이 모두 끝난 todo 포함"),
        (f"RTM {VSTAGES[0] if VSTAGES else ''} 통과", f'{stage_counts[0]["●"]}/{len(rtm_rows_all)}' if stage_counts else "-",
         f'진행 {stage_counts[0]["◐"]} · 불합격 {stage_counts[0]["✕"]}' if stage_counts else ""),
        ("TBD/TBC 미확정", f'{tbd_open}/{len(tbd["rows"])}', f"확정 여부 = {CFG['tbd']['open_state']}"),
        ("문서", str(numbered_count), f'번호 문서 · 경고 {len(doc_warnings)}건'),
        ("최근 커밋", short_date(commits[0]["date"]) if commits else "-", esc(commits[0]["short"]) if commits else ""),
    ]
    for label, val, sub in kp:
        a(f'<div class="kpi"><div class="kl">{label}</div><div class="kv">{esc(val)}</div><div class="ks">{sub}</div></div>')
    a('</div>')

    a('<div class="grid2">')
    a('<div class="card"><h3>웨이브별 진행률</h3><div class="tw"><table class="compact"><thead><tr>'
      '<th>웨이브</th><th class="num">작업</th><th style="min-width:140px">상태 분포</th><th class="num">머지</th>'
      + "".join(f'<th class="num" title="{s}">{STATUS_KO[s]}</th>' for s in STATUSES) + '</tr></thead><tbody>')
    for w, wl in WAVES:
        ts = [t for t in tasks if t["wave"] == w]
        if not ts:
            continue
        c = counts(ts)
        pct = c["merged"] * 100 / len(ts)
        a(f'<tr><td>{esc(wl)}</td><td class="num">{len(ts)}</td><td>{stacked_bar(c, len(ts))}</td>'
          f'<td class="num">{pct:.0f}%</td>' + "".join(f'<td class="num z{int(c[s] == 0)}">{c[s]}</td>' for s in STATUSES) + '</tr>')
    a(f'<tr class="sum"><td>전체</td><td class="num">{total}</td><td>{stacked_bar(all_counts, total)}</td>'
      f'<td class="num">{all_counts["merged"] * 100 / max(total, 1):.0f}%</td>'
      + "".join(f'<td class="num">{all_counts[s]}</td>' for s in STATUSES) + '</tr>')
    a('</tbody></table></div><div class="legend">' + "".join(
        f'<span><i class="dot st-{s}"></i>{STATUS_KO[s]} <span class="muted mono">{s}</span></span>' for s in STATUSES) + '</div></div>')

    a('<div class="card"><h3>모듈별 작업 상태</h3><div class="tw"><table class="compact"><thead><tr><th>모듈</th><th class="num">작업</th>'
      + "".join(f'<th class="num" title="{s}">{STATUS_KO[s]}</th>' for s in STATUSES)
      + '<th style="min-width:110px">분포</th></tr></thead><tbody>')
    for p, ko, _ in PRODUCTS:
        ts = [t for t in tasks if t["product"] == p]
        if not ts:
            continue
        c = counts(ts)
        a(f'<tr><td title="{esc(ko)}">{esc(PRODUCT_SHORT[p])}</td><td class="num">{len(ts)}</td>'
          + "".join(f'<td class="num z{int(c[s] == 0)}">{c[s]}</td>' for s in STATUSES)
          + f'<td>{stacked_bar(c, len(ts))}</td></tr>')
    a('</tbody></table></div></div>')
    a('</div>')

    if stages:
      a('<div class="grid2">')
      a('<div class="card"><h3>검토 단계별 산출물</h3><div class="tw"><table class="compact"><thead><tr>'
      '<th>단계</th><th>주요 산출물</th><th>문서</th></tr></thead><tbody>')
      for st in stages:
        first = True
        for name, hits in st["arts"]:
            cell = ", ".join(f'<a href="{doc_href(e["path"])}">{esc(e["file"])}</a>' for e in hits) \
                if hits else '<span class="muted">미작성</span>'
            mark = '<span class="ok">●</span>' if hits else '<span class="muted">○</span>'
            lead = ""
            if first:
                n_docs = len([e for e in st["files"] if e["seq"]])
                lead = (f'<td rowspan="{len(st["arts"])}"><b>{st["code"]}</b> {esc(st["ko"])}'
                        f'<div class="muted small mono">{st["folder"]}/ · 문서 {n_docs}</div></td>')
                first = False
            a(f'<tr>{lead}<td>{mark} {esc(name)}</td><td>{cell}</td></tr>')
      a('</tbody></table></div><p class="muted small">산출물 목록은 설정 review_stages 기준. 파일명 키워드로 매칭한다.</p></div>')
      a('</div>')
    a('</section>')

    # 작업 DAG
    a('<section class="panel" id="p-dag" hidden>')
    a('<div class="toolbar"><div class="seg-btns" data-view-group="dag">'
      '<button type="button" data-view="graph" aria-pressed="true">그래프</button>'
      '<button type="button" data-view="table" aria-pressed="false">표</button></div>'
      '<div class="legend">' + "".join(f'<span><i class="dot st-{s}"></i>{STATUS_KO[s]}</span>' for s in STATUSES)
      + '<span><i class="bdgi">n</i>열린 선행</span><span><span class="mono small">p95</span> 지연 측정 필수</span><span>노드를 누르면 상세</span></div></div>')
    a(f'<div class="dag-wrap" data-view-pane="graph">{build_dag_svg(tasks)}</div>')
    a('<div data-view-pane="table" hidden><div class="filters" data-filter-for="dag-table">'
      '<span class="fl">모듈</span>' + '<button type="button" data-key="product" data-val="all" aria-pressed="true">전체</button>'
      + "".join(f'<button type="button" data-key="product" data-val="{p}" aria-pressed="false">{esc(s)}</button>' for p, _, s in PRODUCTS)
      + '<span class="fl">상태</span><button type="button" data-key="status" data-val="all" aria-pressed="true">전체</button>'
      + "".join(f'<button type="button" data-key="status" data-val="{s}" aria-pressed="false">{STATUS_KO[s]}</button>' for s in STATUSES)
      + '</div>')
    a('<div class="tw"><table id="dag-table"><thead><tr><th>ID</th><th>제목</th><th>웨이브</th><th>모듈</th>'
      '<th>상태</th><th class="num">열린 선행</th><th>선행작업</th><th>후행</th></tr></thead><tbody>')
    for t in tasks:
        deps = " ".join(task_link(d, by_id) + ("" if d not in t["open_deps"] else '<sup class="open">●</sup>') for d in t["deps"]) or '<span class="muted">-</span>'
        a(f'<tr data-product="{esc(t["product"])}" data-status="{t["status"]}"><td class="nowrap">{task_link(t["id"], by_id)}</td>'
          f'<td>{esc(t["title"])}</td><td class="nowrap">{t["wave"]}</td><td>{esc(PRODUCT_SHORT.get(t["product"], t["product"]))}</td>'
          f'<td>{chip(t["status"])}</td><td class="num">{len(t["open_deps"])}/{len(t["deps"])}</td>'
          f'<td class="deps">{deps}</td><td class="num">{len(t["dependents"])}</td></tr>')
    a('</tbody></table></div><p class="muted small"><sup class="open">●</sup> 아직 머지되지 않은 선행작업</p></div>')
    a('</section>')

    # 작업 상세 템플릿
    for t in tasks:
        bi = t.get("branch_info") or {}
        if bi.get("exists"):
            binfo = (f'<code>{esc(bi["ref"])}</code> · base <code>{esc(bi.get("base_ref") or bi.get("base") or "-")}</code> 대비 '
                     f'ahead {bi.get("ahead") if bi.get("ahead") is not None else "-"} / behind {bi.get("behind") if bi.get("behind") is not None else "-"} · {esc(bi["state"])}')
        elif t["branch"]:
            binfo = f'<code>{esc(t["branch"])}</code> <span class="muted">(브랜치 없음)</span>'
        else:
            binfo = '<span class="muted">-</span>'
        for wb in t.get("worker_branches", []):
            binfo += (f'<div class="small">워커 <code>{esc(wb["ref"])}</code> · <code>{esc(wb.get("base_ref") or wb["base"])}</code> 대비 '
                      f'ahead {wb.get("ahead") if wb.get("ahead") is not None else "-"} / behind {wb.get("behind") if wb.get("behind") is not None else "-"} · {esc(wb["state"])}</div>')
        if t["evidence"]:
            ev = f'<a href="{doc_href(t["evidence"]["key"])}">{esc(t["evidence_path"])}</a> <span class="muted small">{esc(t["evidence"]["where"])}</span>'
        else:
            ev = f'<span class="muted">없음 · 예정 경로 <code>{esc(t["evidence_path"])}</code></span>'
        reqs = " ".join(f'<a class="mono" href="#req/{quote(q)}">{esc(q)}</a>' for q in t["reqs"]) or '<span class="muted">-</span>'
        if t["sub_letters"]:
            reqs += f' <span class="muted small">세부 {", ".join(t["sub_letters"])}</span>'
        deps = "".join(f'<li>{task_link(d, by_id, True)} <span class="small">{esc(by_id[d]["title"]) if d in by_id else ""}</span></li>' for d in t["deps"])
        dpts = "".join(f'<li>{task_link(d, by_id, True)} <span class="small">{esc(by_id[d]["title"])}</span></li>' for d in t["dependents"])
        src = "".join(
            f'<li><span class="sk">{esc(s["kind"])}</span> {chip(s["status"]) if s["status"] else ""} {esc(s["detail"])}</li>'
            for s in t["sources"])
        cm = "".join(f'<li class="mono small">{esc(c)}</li>' for c in t["commits"]) or '<li class="muted">없음</li>'
        a(f'<template id="task-{esc(t["id"])}"><div class="detail">'
          f'<div class="dh"><span class="mono">{esc(t["id"])}</span> {chip(t["status"])}'
          f'{" <span class=badwarn>Orca 실패</span>" if t.get("orca_failed") else ""}</div>'
          f'<h2>{esc(t["title"])}</h2>'
          f'<dl class="kv">'
          f'<dt>웨이브 · 모듈 · 계층</dt><dd>{esc(dict(WAVES).get(t["wave"], t["wave"]))} · {esc(PRODUCT_KO.get(t["product"], t["product"]))} · {esc(t["layer"])}</dd>'
          f'<dt>요구사항</dt><dd>{reqs}</dd>'
          f'<dt>브랜치</dt><dd>{binfo}</dd>'
          f'<dt>분기 기준</dt><dd><code>{esc(t["base"] or "-")}</code></dd>'
          f'<dt>검증 단계 · 지연 측정</dt><dd>{esc(t["stage"])} · {"p50/p95 측정 필수" if t["perf"] else "해당 없음"}</dd>'
          f'<dt>write_scope</dt><dd>{"".join(f"<code class=blk>{esc(w)}</code>" for w in t["write_scope"]) or "<span class=muted>없음</span>"}</dd>'
          f'<dt>acceptance</dt><dd>{esc(t["acceptance"])}</dd>'
          f'<dt>증적</dt><dd>{ev}</dd>'
          f'</dl>'
          f'<h4>선행작업 <span class="muted">{len(t["open_deps"])}개 열림 / {len(t["deps"])}</span></h4><ul class="tlist">{deps or "<li class=muted>없음</li>"}</ul>'
          f'<h4>후행작업 <span class="muted">{len(t["dependents"])}</span></h4><ul class="tlist">{dpts or "<li class=muted>없음</li>"}</ul>'
          f'<h4>상태 판정 근거</h4><ol class="src">{src}</ol>'
          f'<h4>관련 커밋 <span class="muted small">[{esc(t["id"])}] 접두</span></h4><ul class="plain">{cm}</ul>'
          f'</div></template>')

    # RTM
    a('<section class="panel" id="p-rtm" hidden>')
    a('<div class="card"><h3>검증 단계별 충족률 <span class="muted small">요구사항 ' + str(len(rtm_rows_all)) + '행</span></h3>')
    a('<div class="tw"><table class="compact"><thead><tr><th>단계</th><th style="min-width:160px">분포</th><th class="num">통과율</th>'
      + "".join(f'<th class="num">{VMARK_KO[m]} {m}</th>' for m in ("●", "◐", "✕", "-")) + '</tr></thead><tbody>')
    for k, sname in enumerate(VSTAGES):
        c = stage_counts[k]
        a(f'<tr><td class="nowrap">{VSTAGE_KO[sname]}</td><td>{stacked_bar(c, len(rtm_rows_all), ("●", "◐", "✕", "-"), "vm")}</td>'
          f'<td class="num">{c["●"] * 100 / max(len(rtm_rows_all), 1):.0f}%</td>'
          + "".join(f'<td class="num z{int(c[m] == 0)}">{c[m]}</td>' for m in ("●", "◐", "✕", "-")) + '</tr>')
    a('</tbody></table></div>')
    a('<div class="tw"><table class="compact"><thead><tr><th>모듈</th><th class="num">행</th>'
      + "".join(f'<th>{s}</th>' for s in VSTAGES) + '</tr></thead><tbody>')
    for s in rtm["sections"]:
        rows = s["rows"]
        cells = ""
        for k in range(len(VSTAGES)):
            c = {m: 0 for m in VMARK_KO}
            for r in rows:
                c[r["stages"][k] if r["stages"][k] in c else "-"] += 1
            cells += f'<td style="min-width:70px">{stacked_bar(c, len(rows), ("●", "◐", "✕", "-"), "vm")}<span class="small muted">{c["●"]}/{len(rows)}</span></td>'
        a(f'<tr><td>{esc(PRODUCT_SHORT.get(s["product"], s["product"]))}</td><td class="num">{len(rows)}</td>{cells}</tr>')
    a('</tbody></table></div><div class="legend">' + "".join(
        f'<span><i class="dot vm-{m}"></i>{m} {VMARK_KO[m]}</span>' for m in ("●", "◐", "✕", "-")) + '</div></div>')
    a('<div class="filters" data-filter-for="rtm-tables"><span class="fl">모듈</span>'
      '<button type="button" data-key="product" data-val="all" aria-pressed="true">전체</button>'
      + "".join(f'<button type="button" data-key="product" data-val="{s["product"]}" aria-pressed="false">{esc(PRODUCT_SHORT.get(s["product"], s["product"]))}</button>'
                for s in rtm["sections"]) + '</div>')
    a('<div id="rtm-tables">')
    for s in rtm["sections"]:
        sid = f' id="req-{esc(s["parent"])}"' if s["parent"] else ""
        a(f'<div class="card rtm-sec" data-product="{s["product"]}"><h3{sid}>{md_inline(s["heading"])}</h3><div class="tw"><table class="rtm"><thead><tr>'
          '<th>식별자</th><th>요약</th><th class="wide">예비 설계 할당</th><th>관련 TBD</th>'
          + "".join(f'<th class="vs">{v}</th>' for v in VSTAGES) + '<th>작업</th><th>증적</th></tr></thead><tbody>')
        for r in s["rows"]:
            tb = " ".join(f'<a class="mono" href="#tbdrow/{quote(x)}">{esc(x)}</a>' for x in r["tbds"])
            tl = " ".join(task_link(x, by_id) + f'<i class="dot st-{by_id[x]["status"]}" title="{STATUS_KO[by_id[x]["status"]]}"></i>' for x in r["tasks"]) \
                or '<span class="warn small">작업 없음</span>'
            evs = [by_id[x]["evidence"] for x in r["tasks"] if by_id[x]["evidence"]]
            evl = " ".join(f'<a href="{doc_href(e["key"])}" title="{esc(e["key"])}">보기</a>' for e in evs) or '<span class="muted">-</span>'
            vs = "".join(f'<td class="vs vm-{v if v in VMARK_KO else "x"}" title="{VSTAGE_KO[VSTAGES[k]]}: {esc(VMARK_KO.get(v, v))}">{esc(v)}</td>'
                         for k, v in enumerate(r["stages"]))
            a(f'<tr id="req-{esc(r["id"])}"><td class="nowrap mono">{esc(r["id"])}{" <span class=muted title=비고>※</span>" if r["note"] else ""}</td>'
              f'<td>{md_inline(r["summary"])}</td><td class="wide small">{md_inline(r["alloc"])}</td><td class="nowrap">{tb}</td>{vs}'
              f'<td class="deps">{tl}</td><td>{evl}</td></tr>')
        a('</tbody></table></div></div>')
    if rtm["derived"]:
        d = rtm["derived"]
        a(f'<div class="card rtm-sec" data-product="derived"><h3>{md_inline(d["heading"])}</h3><div class="tw"><table><thead><tr>'
          + "".join(f'<th>{esc(h)}</th>' for h in d["header"]) + '</tr></thead><tbody>')
        for r in d["rows"]:
            a(f'<tr id="req-{esc(clean_id(r[0])[0])}">' + "".join(f'<td>{md_inline(c)}</td>' for c in r) + '</tr>')
        a('</tbody></table></div></div>')
    if rtm["notes"]:
        a('<div class="card">' + "".join(f'<p class="small">{md_inline(n)}</p>' for n in rtm["notes"]) + '</div>')
    a('</div>')
    if rtm["path"]:
        rel = rtm["path"].relative_to(repo).as_posix()
        a(f'<p class="muted small">원본: <a href="{doc_href(rel)}">{esc(rel)}</a></p>')
    a('</section>')

    # TBD
    a('<section class="panel" id="p-tbd" hidden>')
    states = []
    for r in tbd["rows"]:
        if r["state"] not in states:
            states.append(r["state"])
    kinds = sorted({r["kind"] for r in tbd["rows"]})
    a('<div class="filters" data-filter-for="tbd-table"><span class="fl">확정 여부</span>'
      f'<button type="button" data-key="state" data-val="all" aria-pressed="true">전체 {len(tbd["rows"])}</button>'
      + "".join(f'<button type="button" data-key="state" data-val="{esc(s)}" aria-pressed="false">{esc(s)} {sum(1 for r in tbd["rows"] if r["state"] == s)}</button>' for s in states)
      + '<span class="fl">구분</span><button type="button" data-key="kind" data-val="all" aria-pressed="true">전체</button>'
      + "".join(f'<button type="button" data-key="kind" data-val="{esc(k)}" aria-pressed="false">{esc(k)} {sum(1 for r in tbd["rows"] if r["kind"] == k)}</button>' for k in kinds)
      + '</div>')
    a('<div class="tw"><table id="tbd-table"><thead><tr><th>순번</th><th>요구사양 식별자</th><th>구분</th><th>제목</th>'
      '<th>사유 및 계획</th><th>확정 예정</th><th>확정 여부</th><th>RTM 참조 행</th></tr></thead><tbody>')
    rtm_set = set(rtm_ids) | rtm_parents
    for r in tbd["rows"]:
        reqs_html = md_inline(r["reqs_raw"])
        linked = [q for q in r["reqs"] if q in rtm_set]
        if linked:
            reqs_html += '<div class="small">' + " ".join(f'<a class="mono" href="#req/{quote(q)}">{esc(q)}</a>' for q in dict.fromkeys(linked)) + '</div>'
        refs = " ".join(f'<a class="mono small" href="#req/{quote(q)}">{esc(q)}</a>' for q in tbd_rev.get(r["id"], [])) or '<span class="muted">-</span>'
        scls = {CFG["tbd"]["open_state"]: "warn", CFG["tbd"]["closed_state"]: "ok"}.get(r["state"], "")
        a(f'<tr id="tbd-{esc(r["id"])}" data-state="{esc(r["state"])}" data-kind="{esc(r["kind"])}"><td class="nowrap mono">{esc(r["id"])}</td>'
          f'<td>{reqs_html}</td><td>{esc(r["kind"])}</td><td>{md_inline(r["title"])}</td><td class="small">{md_inline(r["reason"])}</td>'
          f'<td class="nowrap">{esc(r["due"])}</td><td class="nowrap {scls}">{esc(r["state"])}</td><td class="deps">{refs}</td></tr>')
    a('</tbody></table></div>')
    if tbd["path"]:
        rel = tbd["path"].relative_to(repo).as_posix()
        a(f'<p class="muted small">원본: <a href="{doc_href(rel)}">{esc(rel)}</a> · "내부 확정"은 고객·이해관계자 확인 전 상태</p>')
    a('</section>')

    # 문서
    a('<section class="panel" id="p-docs" hidden><div class="docs-grid"><div>')
    if doc_warnings:
        a('<div class="card"><h3>파일명·형식 경고 <span class="muted">' + str(len(doc_warnings)) + '</span></h3><ul class="alerts">'
          + "".join(f'<li><a href="{doc_href(w["path"])}" class="mono small">{esc(w["path"])}</a> {esc(w["msg"])}</li>'
                    if w["path"] in doc_texts else f'<li><span class="mono small">{esc(w["path"])}</span> {esc(w["msg"])}</li>'
                    for w in doc_warnings) + '</ul></div>')
    for fo in folders:
        a(f'<div class="card"><h3 class="mono">{esc(fo["label"])}/ <span class="muted">{len(fo["files"])}</span></h3>')
        if not fo["files"]:
            a('<p class="muted small">비어 있음</p></div>')
            continue
        if fo.get("evidence"):
            a('<div class="tw"><table class="compact doc-table"><thead><tr><th>작업 ID</th><th>파일</th><th>최신 판</th><th>git 최종 수정</th></tr></thead><tbody>')
            for e in fo["files"]:
                parts = e["path"].split("/")
                tid = parts[2] if len(parts) > 3 else ""
                tcell = task_link(tid, by_id) if tid in by_id else (f'<span class="warn mono">{esc(tid)}</span> <span class="small warn">작업 없음</span>' if tid else "-")
                rv = f'{esc(e["rev"]["ver"])} <span class="muted small">{esc(e["rev"]["date"])}</span>' if e["rev"] else '<span class="muted">-</span>'
                a(f'<tr data-doc-row="{esc(e["path"])}"><td>{tcell}</td><td><a href="{doc_href(e["path"])}">{esc("/".join(parts[3:]) or e["file"])}</a></td>'
                  f'<td>{rv}</td><td class="nowrap small">{esc(short_date(e["git_date"]))} {esc(e["git_state"])}</td></tr>')
            a('</tbody></table></div></div>')
            continue
        a('<div class="tw"><table class="compact doc-table"><thead><tr><th class="num">순번</th><th>문서명</th><th>날짜</th>'
          '<th>최신 판</th><th>git 최종 수정</th></tr></thead><tbody>')
        for e in fo["files"]:
            rv = (f'{esc(e["rev"]["ver"])} <span class="muted small">{esc(e["rev"]["date"])}</span>' if e["rev"] else '<span class="muted">-</span>')
            dd = f'{e["date"][:4]}-{e["date"][4:6]}-{e["date"][6:]}' if len(e["date"]) == 8 else ""
            wmark = f' <span class="warn" title="{esc("; ".join(e["warnings"]))}">⚠</span>' if e["warnings"] else ""
            label = e["name"] if e["seq"] else e["file"]
            gd = esc(short_date(e["git_date"]))
            gs = f' <span class="{"warn" if e["git_state"] else ""} small">{esc(e["git_state"])}</span>' if e["git_state"] else ""
            a(f'<tr data-doc-row="{esc(e["path"])}"><td class="num mono">{esc(e["seq"])}</td>'
              f'<td><a href="{doc_href(e["path"])}">{esc(label)}</a>{wmark}</td><td class="nowrap">{dd}</td>'
              f'<td class="nowrap">{rv}</td><td class="nowrap small">{gd}{gs}</td></tr>')
        a('</tbody></table></div></div>')
    a('</div><div class="doc-view card" id="doc-view"><p class="muted">문서를 선택하면 본문이 여기 표시됩니다.</p></div></div>')
    for key, text in sorted(doc_texts.items()):
        a(f'<template data-doc="{esc(key)}"><div class="doc-head"><span class="mono small">{esc(key)}</span></div>'
          f'<div class="md">{md_to_html(text, make_linkfn(key))}</div></template>')
    a('</section>')

    # Git
    a('<section class="panel" id="p-git" hidden>')
    a(f'<div class="card"><h3>최근 커밋 <span class="muted">{len(commits)}</span> <span class="muted small">git log --all</span></h3>'
      '<div class="tw"><table class="compact gitc"><thead><tr><th>날짜</th><th>커밋</th><th title="이 커밋을 포함하는 브랜치. 테두리 강조 = 브랜치 끝 커밋">포함 브랜치</th><th>작성자</th><th>메시지</th></tr></thead><tbody>')
    for c in commits:
        subj = c["subject"]
        tags, cut = parse_tags(subj)
        if tags:
            tagh = " ".join(task_link(x, by_id) if x in by_id else f'<span class="tag">{esc(x)}</span>' for x in tags)
            subj_html = f'{tagh} {esc(subj[cut:])}'
        else:
            subj_html = esc(subj)
        logical = list(dict.fromkeys(ginfo["refs"][b]["logical"] if b in ginfo["refs"] else b for b in c["branches"]))
        logical.sort(key=lambda b: (0 if b == MAIN else 1 if b.startswith(CFG["integration_prefix"]) else 2 if b.startswith(CFG["request_prefix"]) else 3, b))
        tips = {ginfo["refs"][b]["logical"] for b in c["branches"] if b in ginfo["refs"] and ginfo["refs"][b]["sha"] == c["sha"]}
        br = " ".join(f'<span class="ref{" tip" if b in tips else ""}">{esc(b)}</span>' for b in logical[:3])
        if len(logical) > 3:
            br += f' <span class="muted small" title="{esc(", ".join(logical))}">+{len(logical) - 3}</span>'
        merge = ' <span class="muted small">머지</span>' if len(c["parents"]) > 1 else ""
        a(f'<tr><td class="nowrap small">{esc(short_date(c["date"]))}</td><td class="mono">{esc(c["short"])}{merge}</td>'
          f'<td class="refs">{br}</td><td class="nowrap">{esc(c["author"])}</td><td>{subj_html}</td></tr>')
    a('</tbody></table></div></div>')
    a(f'<div class="card"><h3>브랜치 <span class="muted">{len(branches)}</span></h3>'
      '<div class="tw"><table class="compact gitc"><thead><tr><th>브랜치</th><th>종류</th><th>base</th><th class="num">ahead</th>'
      '<th class="num">behind</th><th>머지 상태</th><th>최종 커밋</th><th>연결 작업</th></tr></thead><tbody>')
    for b in branches:
        cur = ' <span class="tag">HEAD</span>' if b["name"] == ginfo["branch"] else ""
        a(f'<tr><td class="mono">{esc(b["name"])}{cur}</td><td>{"로컬" if b["kind"] == "local" else "원격"}</td>'
          f'<td class="mono small">{esc(b.get("base_ref") or b["base"] or "-")}</td>'
          f'<td class="num">{"" if b["ahead"] is None else b["ahead"]}</td><td class="num">{"" if b["behind"] is None else b["behind"]}</td>'
          f'<td>{esc(b["state"])}</td><td class="small">{esc(short_date(b.get("date", "")))} {esc(b.get("subject", ""))}</td>'
          f'<td class="deps">{" ".join(task_link(x, by_id) for x in b["linked"]) or "-"}</td></tr>')
    a(f'</tbody></table></div><p class="muted small">base: req/* → int/&lt;모듈&gt;, int/* → {esc(MAIN)}, {esc(MAIN)} → origin/{esc(MAIN)}, 워커 브랜치 → 해당 작업의 통합 브랜치. '
      '머지 판정은 <code>git branch --merged</code>에 포함되고 base의 first-parent 이력에 없는 경우(--no-ff 머지)다.</p></div>')
    a('</section>')

    body = "\n".join(H)

    # 헤더 메타
    dirty_n = len(ginfo["dirty"])
    dirty_title = "\n".join(f'{d["code"]} {d["path"]}' for d in ginfo["dirty"][:40]) + ("\n…" if dirty_n > 40 else "")
    orca_txt = orca["message"] if orca["enabled"] else "Orca 미반영"
    meta_html = (
        f'<span title="생성 시각">생성 {esc(now.strftime("%Y-%m-%d %H:%M:%S"))} {esc(now.strftime("%Z"))}</span>'
        f'<span>브랜치 <b class="mono">{esc(ginfo["branch"] or "-")}</b></span>'
        f'<span title="{esc(head.get("subject", ""))}">HEAD <b class="mono">{esc(head.get("short", "-"))}</b> '
        f'<span class="hs">{esc(trunc(head.get("subject", ""), 48))}</span></span>'
        f'<span title="{esc(dirty_title)}">작업 트리 변경 {dirty_n}건</span>'
        f'<span class="{"" if orca["ok"] or not orca["enabled"] else "warn"}" title="{esc(orca_txt)}">{esc(trunc(orca_txt, 40))}</span>')

    tabs = [("overview", "개요"), ("dag", f"작업 DAG <span class='cnt'>{total}</span>"),
            ("rtm", f"요구사항 추적 <span class='cnt'>{len(rtm_rows_all)}</span>"),
            ("tbd", f"TBD/TBC <span class='cnt'>{len(tbd['rows'])}</span>"),
            ("docs", f"문서 <span class='cnt'>{doc_count}</span>"), ("git", "Git 활동")]
    nav = "".join(f'<a class="tab" href="#{k}" data-tab="{k}" role="tab">{v}</a>' for k, v in tabs)

    page = (PAGE_TEMPLATE
            .replace("%%CSS%%", CSS)
            .replace("%%JS%%", JS)
            .replace("%%BUILD_ID%%", build_id)
            .replace("%%META%%", meta_html)
            .replace("%%NAV%%", nav)
            .replace("%%SRC%%", esc(f"{tasks_src} · {repo}"))
            .replace("%%TITLE%%", esc(CFG["dashboard"].get("title") or f'{CFG["project"]["name"]} 개발 현황'))
            .replace("%%SUBTITLE%%", esc(CFG["dashboard"].get("subtitle") or " · ".join(s for _, _, s in PRODUCTS)))
            .replace("%%STORE%%", esc(CFG["dashboard"].get("storage_key") or "kit-dash"))
            .replace("%%BODY%%", body))

    out = Path(args.out)
    if not out.is_absolute():
        out = repo / out
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".index-", suffix=".html", dir=str(out.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(page)
    os.chmod(tmp, 0o644)
    os.replace(tmp, out)  # 서버가 반쯤 쓴 파일을 내보내지 않도록 원자적 교체

    summary = {
        "out": str(out), "bytes": out.stat().st_size, "tasks": total, "status": all_counts,
        "rtm_rows": len(rtm_rows_all), "derived_rows": len(rtm["derived"]["rows"]) if rtm["derived"] else 0,
        "tbd_rows": len(tbd["rows"]), "docs": doc_count, "doc_warnings": len(doc_warnings),
        "commits": len(commits), "branches": len(branches), "evidence": len(evid), "orca": orca["message"],
    }
    return summary


# ─────────────────────────── 정적 자원 ───────────────────────────

PAGE_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="build-id" content="%%BUILD_ID%%">
<meta name="robots" content="noindex">
<title>%%TITLE%%</title>
<style>%%CSS%%</style>
</head>
<body>
<header class="top">
  <div class="brand"><h1>%%TITLE%%</h1><span class="muted small">%%SUBTITLE%%</span></div>
  <div class="meta">%%META%%</div>
  <div id="newbuild" hidden>새 빌드가 있습니다 <button type="button" id="reload">새로고침</button></div>
</header>
<nav class="tabs" role="tablist">%%NAV%%</nav>
<noscript><p class="card">탭 전환·상세 보기에는 JavaScript가 필요합니다.</p></noscript>
<main>
%%BODY%%
</main>
<aside id="drawer" aria-hidden="true"><div class="dbar"><button type="button" id="dclose" aria-label="닫기">닫기 ✕</button></div><div id="dbody"></div></aside>
<footer class="muted small">데이터: %%SRC%%</footer>
<script>%%JS%%</script>
</body>
</html>
"""

CSS = r"""
:root{
--bg:#f5f6f8;--panel:#fff;--fg:#1c2126;--muted:#5f6a75;--line:#d9dee3;--line2:#eceff2;--accent:#2d5f8f;
--code:#eef1f4;--lane0:#fafbfc;--lane1:#f2f4f6;
--st-todo:#8b949d;--st-ready:#3a72ad;--st-running:#b4761d;--st-review:#7656a6;--st-merged:#2e7d55;
--vm-pass:#2e7d55;--vm-prog:#b4761d;--vm-fail:#b42318;--vm-none:#d5dade;--warn:#a65b06;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;
--bg:#131619;--panel:#1a1e22;--fg:#d8dde2;--muted:#97a1ab;--line:#2c3237;--line2:#23292e;--accent:#79acdd;
--code:#22282d;--lane0:#171b1f;--lane1:#1c2125;
--st-todo:#6f7881;--st-ready:#5a91cc;--st-running:#d39a3a;--st-review:#9d80cc;--st-merged:#4aa877;
--vm-pass:#4aa877;--vm-prog:#d39a3a;--vm-fail:#ec6a5e;--vm-none:#3a4147;--warn:#e0a24a;--bad:#ec6a5e}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 system-ui,-apple-system,"Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif;overflow-x:hidden;word-break:keep-all;overflow-wrap:break-word}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
code,.mono{font-family:ui-monospace,SFMono-Regular,"D2Coding",Menlo,Consolas,monospace;font-size:.92em}
code{background:var(--code);padding:0 3px;border-radius:3px;overflow-wrap:anywhere}
code.blk{display:inline-block;margin:0 4px 3px 0}
.muted{color:var(--muted)}.small{font-size:11.5px}.nowrap{white-space:nowrap}
.warn{color:var(--warn)}.ok{color:var(--st-merged)}.badwarn{color:var(--bad);font-size:11px;border:1px solid;border-radius:3px;padding:0 4px}
header.top{display:flex;flex-wrap:wrap;gap:4px 16px;al\69gn-items:baseline;padding:10px 16px 6px;background:var(--panel);border-bottom:1px solid var(--line)}
.brand{display:flex;flex-wrap:wrap;gap:2px 10px;al\69gn-items:baseline}
h1{font-size:15px;margin:0;font-weight:650}
.meta{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:12px;color:var(--muted);min-width:0}
.meta b{color:var(--fg);font-weight:600}.meta .hs{overflow-wrap:anywhere}
#newbuild{font-size:12px;color:var(--warn)}#newbuild button{font:inherit;margin-left:6px}
nav.tabs{position:sticky;top:0;z-index:5;display:flex;gap:2px;overflow-x:auto;background:var(--panel);border-bottom:1px solid var(--line);padding:0 12px;scrollbar-width:thin}
.tab{flex:0 0 auto;padding:8px 12px;color:var(--muted);border-bottom:2px solid transparent;white-space:nowrap}
.tab:hover{text-decoration:none;color:var(--fg)}
.tab[aria-selected=true]{color:var(--fg);border-bottom-color:var(--accent);font-weight:600}
.cnt{font-size:11px;color:var(--muted);font-weight:400}
main{padding:12px 16px 24px;max-width:1680px;margin:0 auto}
footer{padding:8px 16px 16px;overflow-wrap:anywhere}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:10px 12px;margin:0 0 12px;min-width:0}
.card h3{font-size:13px;margin:0 0 8px;font-weight:650}
h4{font-size:12.5px;margin:14px 0 4px}
.grid2{display:grid;grid-template-columns:minmax(0,1fr);gap:0 12px}
@media(min-width:980px){.grid2{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-bottom:12px}
@media(max-width:400px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px;min-width:0}
.kl{font-size:11.5px;color:var(--muted)}.kv{font-size:19px;font-weight:650;font-variant-numeric:tabular-nums}.ks{font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.tw{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:4px 8px;border-bottom:1px solid var(--line2);text-al\69gn:left;vertical-al\69gn:top}
th{font-weight:600;color:var(--muted);font-size:11.5px;white-space:nowrap;background:var(--panel)}
td{overflow-wrap:break-word}
table.compact td:first-child{white-space:nowrap}
table.rtm{min-width:760px}#tbd-table{min-width:820px}#dag-table{min-width:760px}table.gitc{min-width:640px}table.doc-table{min-width:460px}
table.rtm td:nth-child(2){min-width:200px}#tbd-table td:nth-child(5){min-width:220px}
table.compact td,table.compact th{padding:3px 6px}
td.num,th.num{text-al\69gn:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.z1{color:var(--muted);opacity:.55}
tr.sum td{font-weight:600;border-top:1px solid var(--line)}
tbody tr:hover{background:var(--lane1)}
tr.flash{animation:flash 2s ease-out}
@keyframes flash{from{background:color-mix(in srgb,var(--accent) 22%,transparent)}to{background:transparent}}
.bar{display:flex;height:8px;background:var(--line2);border-radius:2px;overflow:hidden;min-width:60px;margin:5px 0}
.bar .seg{display:block;height:100%}
.st-todo{--c:var(--st-todo)}.st-ready{--c:var(--st-ready)}.st-running{--c:var(--st-running)}.st-review{--c:var(--st-review)}.st-merged{--c:var(--st-merged)}
.vm-●{--c:var(--vm-pass)}.vm-◐{--c:var(--vm-prog)}.vm-✕{--c:var(--vm-fail)}.vm--{--c:var(--vm-none)}
.seg{background:var(--c)}
.st{display:inline-block;font-size:11px;line-height:16px;padding:0 5px;border-radius:3px;border:1px solid var(--c);color:var(--c);white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c);margin:0 4px 1px 2px;vertical-al\69gn:middle}
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:11.5px;color:var(--muted);margin-top:6px;al\69gn-items:center}
.bdgi{display:inline-block;font-style:normal;font-size:10px;line-height:14px;min-width:18px;text-al\69gn:center;border-radius:7px;background:var(--fg);color:var(--panel);margin-right:4px}
td.vs,th.vs{text-al\69gn:center;width:28px;white-space:nowrap}
td.vs{color:var(--c);font-weight:600}td.vm--{color:var(--muted);font-weight:400}
.deps a{margin-right:4px;white-space:nowrap}
sup.open{color:var(--st-running);font-size:8px}
.ref.tip{border-color:var(--accent);color:var(--fg)}
.tag,.ref{display:inline-block;font-size:11px;padding:0 4px;border:1px solid var(--line);border-radius:3px;color:var(--muted);white-space:nowrap;margin:0 3px 2px 0}
td.refs{max-width:280px}
ul.tlist,ul.alerts,ul.plain,ol.src{margin:0;padding-left:18px}
ul.tlist li,ul.alerts li{margin:2px 0}
ul.alerts li{overflow-wrap:anywhere}
.toolbar{display:flex;flex-wrap:wrap;gap:8px 16px;al\69gn-items:center;margin-bottom:8px}
.seg-btns,.filters{display:flex;flex-wrap:wrap;gap:4px;al\69gn-items:center}
.filters{margin-bottom:8px}
.fl{font-size:11.5px;color:var(--muted);margin:0 2px 0 8px}.fl:first-child{margin-left:0}
button{font:inherit;font-size:12px;color:var(--fg);background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:2px 9px;cursor:pointer}
button[aria-pressed=true]{background:var(--fg);color:var(--panel);border-color:var(--fg)}
.dag-wrap{overflow:auto;border:1px solid var(--line);border-radius:6px;background:var(--panel);max-height:calc(100vh - 150px)}
svg.dag{display:block;font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif}
.lane0{fill:var(--lane0)}.lane1{fill:var(--lane1)}
.lanel{font-size:11px;fill:var(--muted);font-weight:600}
.wh{fill:var(--line2)}.wht{font-size:11.5px;fill:var(--fg);font-weight:600}
.edge{fill:none;stroke:var(--muted);stroke-width:1;opacity:.35;marker-end:url(#ah)}
.ahp{fill:var(--muted)}.ahp2{fill:var(--accent)}
.node{cursor:pointer}
.node .nb{fill:var(--panel);stroke:var(--line)}
.node .ns{fill:var(--c)}
.node .nid{font-size:11px;font-weight:650;fill:var(--fg);font-family:ui-monospace,Menlo,Consolas,monospace}
.node .nt{font-size:11px;fill:var(--muted)}
.node .bdg{fill:var(--fg)}.node .bdgt{font-size:10px;fill:var(--panel);font-weight:600}
.node .perf{font-size:9px;fill:var(--muted)}
.node.st-merged .nb{fill:color-mix(in srgb,var(--st-merged) 8%,var(--panel))}
.node.st-running .nb,.node.st-review .nb{stroke:var(--c)}
.node:hover .nb,.node:focus .nb{stroke:var(--accent)}
.node:focus{outline:none}
svg.focus .edge{opacity:.07}
svg.focus .edge.hl{opacity:1;stroke:var(--accent);stroke-width:1.6;marker-end:url(#ah2)}
svg.focus .node{opacity:.35}
svg.focus .node.nbh,svg.focus .node.sel{opacity:1}
.node.sel .nb{stroke:var(--accent);stroke-width:2}
.ovhead{display:flex;flex-wrap:wrap;al\69gn-items:center;gap:4px 16px;margin:2px 0 10px}
.ovhead h2{font-size:17px;margin:0;font-weight:650}
.ovhead .bar{flex:1 1 200px;max-width:520px;margin:0}
.subs{display:flex;flex-wrap:wrap;gap:6px;al\69gn-items:center;margin-bottom:12px}
.sub{display:inline-flex;al\69gn-items:center;gap:6px;padding:5px 10px;border:1px solid var(--line);border-radius:6px;background:var(--panel);font-size:12.5px;--c:var(--st-merged)}
.sub b{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:600}
.sub .dot{margin:0}
.sub.off,.sub.unknown{border-style:dashed;color:var(--muted);background:transparent;--c:var(--st-todo)}
.sub.cool{border-color:var(--warn);color:var(--warn);--c:var(--warn)}
details.ovdag{padding:0}
details.ovdag>summary{cursor:pointer;padding:9px 12px;user-select:none}
details.ovdag>summary b{font-size:13px;margin-right:6px}
details.ovdag[open]>summary{border-bottom:1px solid var(--line2)}
.ovdag-f{display:flex;flex-wrap:wrap;justify-content:space-between;al\69gn-items:center;gap:4px 12px;padding:6px 12px 8px}
.ovdag-f .legend{margin:0}
.mg{display:grid}
.mg>div{padding:5px 6px;border-bottom:1px solid var(--line2);display:flex;flex-direction:column;gap:4px;min-width:0}
.mg .mh{font-size:11.5px;color:var(--muted);font-family:ui-monospace,Menlo,Consolas,monospace;padding:6px}
.mg .ml{font-size:12px;color:var(--muted);font-weight:600;padding:9px 8px}
.mg .alt{background:var(--lane1)}
.mg .dim{opacity:.35}
.mg .mn{display:flex;flex-direction:column;al\69gn-items:flex-start;gap:0;width:100%;min-height:34px;text-al\69gn:left;padding:3px 8px;border:1px solid var(--line);border-left:3px solid var(--c);border-radius:4px;background:color-mix(in srgb,var(--c) 8%,var(--panel));color:var(--fg);font-size:11.5px;line-height:1.35}
.mg .mn b{font-family:ui-monospace,Menlo,Consolas,monospace;font-weight:650;font-size:11px}
.mg .mn span{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}
.mg .mn:hover{border-color:var(--accent);border-left-color:var(--c)}
.mg .mn[aria-pressed=true]{background:color-mix(in srgb,var(--c) 8%,var(--panel));color:var(--fg);border-color:var(--accent);border-left-color:var(--c);outline:2px solid var(--accent);outline-offset:1px}
#ov-filter{margin:4px 0 8px}
.kb{display:grid;grid-template-columns:repeat(5,minmax(200px,1fr));gap:10px;min-width:1040px;al\69gn-items:start;margin-bottom:12px}
.kcol{background:var(--lane1);border:1px solid var(--line2);border-radius:8px;padding:8px;min-width:0}
.kcol>header{display:flex;al\69gn-items:center;gap:6px;padding:2px 4px 8px}
.kcol>header .dot{margin:0;border-radius:3px}
.kcol h3{font-size:12.5px;margin:0;font-weight:650}
.kcnt{margin-left:auto;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:var(--muted)}
.kbody{display:flex;flex-direction:column;gap:6px;max-height:min(70vh,680px);overflow-y:auto}
.kc{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px;display:flex;flex-direction:column;gap:5px;min-width:0}
.kc[hidden]{display:none}
.kc.hl{outline:2px solid var(--accent);outline-offset:1px}
.kh{display:flex;al\69gn-items:center;gap:6px;min-width:0}
.kt{margin-left:auto;font-size:11px;padding:0 6px;border-radius:3px;background:var(--code);color:var(--muted);white-space:nowrap}
.ktl{font-weight:500;line-height:1.4}
.km{al\69gn-self:flex-start;font-size:11px;padding:1px 6px;border-radius:3px;background:color-mix(in srgb,var(--accent) 12%,var(--panel));color:var(--accent);overflow-wrap:anywhere}
.kcopy{min-height:32px;background:var(--accent);color:var(--panel);border-color:var(--accent);font-weight:600}
@media(max-width:700px){.kb{grid-template-columns:minmax(0,1fr);min-width:0}.kbody{max-height:none}}
.kempty{margin:8px 4px;text-al\69gn:center;font-size:12px}
#drawer{position:fixed;top:0;right:0;bottom:0;width:min(580px,100vw);background:var(--panel);border-left:1px solid var(--line);box-shadow:-4px 0 18px rgba(0,0,0,.12);transform:translateX(105%);transition:transform .16s ease-out;z-index:20;overflow-y:auto;overscroll-behavior:contain}
#drawer.open{transform:none}
.dbar{position:sticky;top:0;display:flex;justify-content:flex-end;padding:6px 10px;background:var(--panel);border-bottom:1px solid var(--line2)}
#dbody{padding:4px 16px 24px}
.detail h2{font-size:15px;margin:4px 0 8px}
.dh{display:flex;gap:8px;al\69gn-items:center;margin-top:8px}
dl.kv{display:grid;grid-template-columns:minmax(90px,max-content) minmax(0,1fr);gap:4px 12px;margin:0;font-size:12.5px}
dl.kv dt{color:var(--muted);font-size:11.5px}dl.kv dd{margin:0;overflow-wrap:anywhere}
@media(max-width:480px){dl.kv{grid-template-columns:minmax(0,1fr)}dl.kv dd{margin-bottom:4px}}
.sk{display:inline-block;min-width:64px;font-size:11px;color:var(--muted)}
ol.src li{margin:2px 0;overflow-wrap:anywhere}
.docs-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:0 12px;al\69gn-items:start}
@media(min-width:1180px){.docs-grid{grid-template-columns:minmax(0,600px) minmax(0,1fr)}.doc-view{position:sticky;top:44px;max-height:calc(100vh - 56px);overflow:auto}}
tr.cur td{background:color-mix(in srgb,var(--accent) 10%,transparent)}
.doc-head{border-bottom:1px solid var(--line2);padding-bottom:4px;margin-bottom:8px;overflow-wrap:anywhere}
.md{font-size:13px;overflow-wrap:break-word}
.md h1{font-size:17px;margin:6px 0 10px}.md h2{font-size:15px;margin:18px 0 8px;padding-bottom:3px;border-bottom:1px solid var(--line2)}
.md h3{font-size:13.5px;margin:14px 0 6px}.md h4{font-size:13px}
.md p{margin:6px 0}.md ul,.md ol{margin:4px 0;padding-left:22px}
.md pre{background:var(--code);padding:8px 10px;border-radius:4px;overflow:auto;font-size:12px;line-height:1.45}
.md pre code{background:none;padding:0;overflow-wrap:normal}
.md blockquote{margin:6px 0;padding:2px 10px;border-left:3px solid var(--line);color:var(--muted)}
.md table{margin:6px 0}.md hr{border:0;border-top:1px solid var(--line);margin:12px 0}
.xref{border-bottom:1px dotted var(--muted)}
@media(max-width:600px){main{padding:10px 12px 20px}header.top{padding:8px 12px 6px}nav.tabs{padding:0 6px}th.wide,td.wide{display:none}table.rtm{min-width:600px}.card{padding:8px 10px}}
"""

JS = r"""
(function(){
'use strict';
var $=function(s,r){return (r||document).querySelector(s)};
var $$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};
var TABS=['overview','dag','rtm','tbd','docs','git'];
var drawer=$('#drawer'),dbody=$('#dbody'),svg=$('#dag-svg'),selected=null;
function showTab(t){
  $$('.tab').forEach(function(b){b.setAttribute('aria-selected',String(b.dataset.tab===t))});
  $$('main > section.panel').forEach(function(s){s.hidden=(s.id!=='p-'+t)});
}
function closeDrawer(){drawer.classList.remove('open');drawer.setAttribute('aria-hidden','true');setSel(null)}
function setSel(id){
  selected=id;
  if(!svg)return;
  $$('.node',svg).forEach(function(n){var on=n.dataset.id===id;n.classList.toggle('sel',on);
    if(on&&!$('#p-dag').hidden){try{n.scrollIntoView({block:'nearest',inline:'center'})}catch(_){}}});
  hl(id);
}
function hl(id){
  if(!svg)return;
  if(!id){svg.classList.remove('focus');$$('.edge.hl',svg).forEach(function(e){e.classList.remove('hl')});$$('.node.nbh',svg).forEach(function(n){n.classList.remove('nbh')});return}
  svg.classList.add('focus');
  var nb={};nb[id]=1;
  $$('.edge',svg).forEach(function(e){var on=e.dataset.from===id||e.dataset.to===id;e.classList.toggle('hl',on);if(on){nb[e.dataset.from]=1;nb[e.dataset.to]=1}});
  $$('.node',svg).forEach(function(n){n.classList.toggle('nbh',!!nb[n.dataset.id])});
}
function openTask(id){
  var tpl=document.getElementById('task-'+id);
  if(!tpl){closeDrawer();return}
  dbody.innerHTML='';dbody.appendChild(tpl.content.cloneNode(true));
  drawer.classList.add('open');drawer.setAttribute('aria-hidden','false');drawer.scrollTop=0;
  setSel(id);
}
function openDoc(key){
  var tpl=null;$$('template[data-doc]').forEach(function(t){if(t.dataset.doc===key)tpl=t});
  var view=$('#doc-view');
  $$('tr[data-doc-row]').forEach(function(r){r.classList.toggle('cur',r.dataset.docRow===key)});
  if(!tpl){view.innerHTML='<p class="muted">문서를 찾을 수 없습니다: '+key.replace(/</g,'&lt;')+'</p>';return}
  view.innerHTML='';view.appendChild(tpl.content.cloneNode(true));view.scrollTop=0;
  if(window.matchMedia('(max-width:1179px)').matches)view.scrollIntoView({block:'start'});
}
function focusEl(id){
  var el=document.getElementById(id);if(!el)return;
  var sec=el.closest('.rtm-sec');if(sec&&sec.hidden){resetFilters(sec.parentElement)}
  if(el.closest('tr[hidden]')){resetFilters(el.closest('table'))}
  el.scrollIntoView({block:'center'});
  var row=el.tagName==='TR'?el:el.closest('tr');
  if(row){row.classList.remove('flash');void row.offsetWidth;row.classList.add('flash')}
}
function route(){
  var h=location.hash.slice(1),i=h.indexOf('/'),k=i<0?h:h.slice(0,i),v='';
  try{v=i<0?'':decodeURIComponent(h.slice(i+1))}catch(e){v=h.slice(i+1)}
  if(k==='task'){if(!document.querySelector('.tab[aria-selected=true]'))showTab('dag');openTask(v);return}
  closeDrawer();
  if(k==='doc'){showTab('docs');openDoc(v)}
  else if(k==='req'){showTab('rtm');setTimeout(function(){focusEl('req-'+v)},0)}
  else if(k==='tbdrow'){showTab('tbd');setTimeout(function(){focusEl('tbd-'+v)},0)}
  else if(TABS.indexOf(k)>=0)showTab(k);
  else showTab('overview');
}
window.addEventListener('hashchange',route);
$('#dclose').addEventListener('click',function(){
  closeDrawer();
  var cur=document.querySelector('.tab[aria-selected=true]');
  history.replaceState(null,'','#'+(cur?cur.dataset.tab:'dag'));
});
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&drawer.classList.contains('open'))$('#dclose').click()});
if(svg){
  svg.addEventListener('click',function(e){var n=e.target.closest('.node');if(n)location.hash='task/'+encodeURIComponent(n.dataset.id)});
  svg.addEventListener('keydown',function(e){var n=e.target.closest('.node');if(n&&(e.key==='Enter'||e.key===' ')){e.preventDefault();location.hash='task/'+encodeURIComponent(n.dataset.id)}});
  svg.addEventListener('mouseover',function(e){var n=e.target.closest('.node');if(n)hl(n.dataset.id)});
  svg.addEventListener('mouseleave',function(){hl(selected)});
  svg.addEventListener('mouseout',function(e){var n=e.target.closest('.node');if(n&&!n.contains(e.relatedTarget))hl(selected)});
}
// 그래프/표 전환
$$('[data-view-group]').forEach(function(g){
  g.addEventListener('click',function(e){
    var b=e.target.closest('button[data-view]');if(!b)return;
    $$('button',g).forEach(function(x){x.setAttribute('aria-pressed',String(x===b))});
    var panel=g.closest('section');
    $$('[data-view-pane]',panel).forEach(function(p){p.hidden=p.dataset.viewPane!==b.dataset.view});
    try{localStorage.setItem('%%STORE%%-dagview',b.dataset.view)}catch(_){}
  });
});
try{var dv=localStorage.getItem('%%STORE%%-dagview');if(dv){var bb=$('[data-view-group=dag] button[data-view="'+dv+'"]');if(bb)bb.click()}}catch(_){}
// 필터
function applyFilters(box){
  var target=document.getElementById(box.dataset.filterFor);if(!target)return;
  var act={};$$('button[aria-pressed=true]',box).forEach(function(b){act[b.dataset.key]=b.dataset.val});
  var items=target.tagName==='TABLE'?$$('tbody tr',target):$$(':scope > [data-product]',target);
  items.forEach(function(r){
    var ok=true;for(var k in act){if(act[k]!=='all'&&r.dataset[k]!==act[k])ok=false}
    r.hidden=!ok;
  });
}
function resetFilters(target){
  if(!target)return;
  $$('.filters[data-filter-for="'+target.id+'"]').forEach(function(box){
    $$('button',box).forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.val==='all'))});applyFilters(box);
  });
}
$$('.filters[data-filter-for]').forEach(function(box){
  box.addEventListener('click',function(e){
    var b=e.target.closest('button[data-key]');if(!b)return;
    $$('button[data-key="'+b.dataset.key+'"]',box).forEach(function(x){x.setAttribute('aria-pressed',String(x===b))});
    applyFilters(box);
  });
});
// 개요: 격자 접기(기억) · 모듈 거르기 · 격자 노드 → 보드 카드 표시 · 투입 명령 복사
var ovb=$('#ov-board'),ovd=$('#ov-dag'),ovf=$('#ov-filter');
if(ovd){
  try{if(localStorage.getItem('%%STORE%%-ovdag')==='0')ovd.removeAttribute('open')}catch(_){}
  ovd.addEventListener('toggle',function(){try{localStorage.setItem('%%STORE%%-ovdag',ovd.open?'1':'0')}catch(_){}});
}
function ovFilter(mod){
  if(!ovb)return;
  if(ovf)$$('button[data-mod]',ovf).forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.mod===mod))});
  $$('.kcol',ovb).forEach(function(col){
    var n=0;$$('.kc',col).forEach(function(c){var ok=mod==='all'||c.dataset.product===mod;c.hidden=!ok;if(ok)n++});
    var k=$('.kcnt',col);if(k)k.textContent=String(n);
    var e=$('.kempty',col);if(e)e.hidden=n>0;
  });
  if(ovd)$$('.mg [data-product]',ovd).forEach(function(c){c.classList.toggle('dim',mod!=='all'&&c.dataset.product!==mod)});
}
if(ovf)ovf.addEventListener('click',function(e){var b=e.target.closest('button[data-mod]');if(b)ovFilter(b.dataset.mod)});
function ovPick(id){
  if(ovd)$$('.mn',ovd).forEach(function(n){n.setAttribute('aria-pressed',String(n.dataset.id===id))});
  if(!ovb)return;
  var card=null;$$('.kc',ovb).forEach(function(c){var on=c.dataset.id===id;c.classList.toggle('hl',on);if(on)card=c});
  if(card&&card.hidden)ovFilter('all');
  if(card){try{card.scrollIntoView({block:'nearest',inline:'nearest',behavior:'smooth'})}catch(_){card.scrollIntoView()}}
}
if(ovd)ovd.addEventListener('click',function(e){var n=e.target.closest('.mn');if(n)ovPick(n.getAttribute('aria-pressed')==='true'?null:n.dataset.id)});
function copyText(s,btn){
  var o=btn.textContent;
  function done(){btn.textContent='복사됨';setTimeout(function(){btn.textContent=o},1500)}
  function fallback(){
    var ta=document.createElement('textarea');ta.value=s;ta.setAttribute('readonly','');ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();
    var ok=false;try{ok=document.execCommand('copy')}catch(_){}
    document.body.removeChild(ta);
    if(ok)done();else window.prompt('명령을 복사하세요',s);
  }
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(s).then(done,fallback)}else fallback();
}
if(ovb)ovb.addEventListener('click',function(e){var b=e.target.closest('.kcopy');if(b)copyText(b.dataset.cmd,b)});
// 로컬 서버로 열었을 때 새 빌드 감지
var BID=(document.querySelector('meta[name=build-id]')||{}).content;
if(/^https?:$/.test(location.protocol)&&BID){
  setInterval(function(){
    fetch(location.pathname,{cache:'no-store'}).then(function(r){return r.text()}).then(function(t){
      var m=t.match(/name="build-id" content="([^"]+)"/);if(m&&m[1]!==BID)$('#newbuild').hidden=false;
    }).catch(function(){});
  },15000);
  $('#reload').addEventListener('click',function(){location.reload()});
}
route();
})();
"""


def main():
    ap = argparse.ArgumentParser(description="개발 현황 대시보드(단일 HTML) 생성 — orca-orchestration-kit")
    ap.add_argument("--config", default=None, help="설정 YAML (기본: <저장소>/orchestration/kit.yaml)")
    ap.add_argument("--out", default="dashboard/index.html", help="출력 경로 (저장소 기준 상대경로 허용)")
    ap.add_argument("--orca", action="store_true", help="orca orchestration task-list --json 결과도 반영 ($ORCA_CLI_COMMAND)")
    ap.add_argument("--orca-run", default=None, help="--orca와 함께: 바인딩된 Run 대신 이 Run ID를 조회")
    ap.add_argument("--tasks-ref", default=None, help="tasks.yaml을 작업 트리 대신 git ref에서 읽음 (예: main)")
    ap.add_argument("--repo", default=None, help="저장소 경로 (기본: 현재 디렉터리의 git 최상위)")
    ap.add_argument("-q", "--quiet", action="store_true", help="요약 출력 생략")
    args = ap.parse_args()
    repo = Path(args.repo).resolve() if args.repo else Path.cwd()
    cfg_path = Path(args.config) if args.config else repo / "orchestration" / "kit.yaml"
    if args.config and not cfg_path.is_file():
        raise SystemExit(f"오류: 설정 파일이 없습니다: {cfg_path}")
    load_config(cfg_path)
    if args.orca and not args.orca_run:
        args.orca_run = (CFG.get("orca") or {}).get("work_run") or None
    s = build(args)
    if not args.quiet:
        st = ", ".join(f"{k} {v}" for k, v in s["status"].items())
        print(f"생성: {s['out']} ({s['bytes']:,} bytes)")
        print(f"  작업 {s['tasks']} ({st})")
        print(f"  RTM {s['rtm_rows']}행 + 파생 {s['derived_rows']}행 · TBD/TBC {s['tbd_rows']}건 · 문서 {s['docs']}개 (경고 {s['doc_warnings']})")
        print(f"  커밋 {s['commits']} · 브랜치 {s['branches']} · 증적 {s['evidence']} · {s['orca']}")


if __name__ == "__main__":
    main()
