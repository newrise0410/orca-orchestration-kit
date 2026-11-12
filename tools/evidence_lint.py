#!/usr/bin/env python3
"""증적 위생 검사: 증적·로그에 환경 변수 덤프, 홈 절대경로, 개인 이메일, 비밀값 모양이 남았는지 본다.

값은 출력하지 않는다(파일:줄 + 종류). worker_done 전에 돌려 0건이어야 한다.
사용: python3 orchestration/tools/evidence_lint.py [경로 ...]
  경로를 안 주면 작업 트리의 변경·추가 파일 중 증적 폴더(docs/06_evidence/) 아래만 본다. 폴더를 주면 그 아래 전부.
종료 코드: 0 깨끗함, 1 걸린 것 있음.
프로젝트 규칙 추가: kit.yaml evidence_lint.patterns: [{name: <이름>, regex: <정규식>}],
                    evidence_lint.allow_emails: [<허용 도메인>]  (기본: .invalid, example.com/org, noreply)
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402

ENV_LINE = re.compile(r"^\s*(?:export\s+)?[A-Z_][A-Z0-9_]{1,40}=\S")
BASE_RULES = [
    ("환경변수_덤프", re.compile(r"^\s*(?:export\s+)?(PATH|HOME|USER|LOGNAME|SHELL|PWD|HOSTNAME|SSH_[A-Z_]+)=\S")),
    ("홈_절대경로", re.compile(r"(?<![\w<])/(?:home|Users)/(?!<)[A-Za-z0-9._-]+/")),
    ("비밀값_모양", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{12,}")),
    ("비밀값_모양", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})\b")),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
TEXT_EXT = {".md", ".txt", ".log", ".json", ".yaml", ".yml", ".csv", ".tsv", ".out", ".xml", ".html", ""}


def targets(repo, cfg, args):
    if args:
        out = []
        for a in args:
            p = Path(a)
            out += [x for x in p.rglob("*") if x.is_file()] if p.is_dir() else [p]
        return out
    ev = str(kitlib.cfg_get(cfg, "paths.evidence", "docs/06_evidence/{id}/report.md")).split("{id}")[0]
    p = subprocess.run(["git", "-C", str(repo), "-c", "core.quotePath=false", "status", "--porcelain", "--untracked-files=all"],
                       capture_output=True, text=True)
    files = []
    for ln in p.stdout.splitlines():
        path = ln[3:].split(" -> ")[-1].strip('"')
        if path.startswith(ev) and (repo / path).is_file():
            files.append(repo / path)
    return files


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    repo = kitlib.repo_root()
    cfg = kitlib.load_config(repo)
    lint = cfg.get("evidence_lint") or {}
    rules = list(BASE_RULES) + [(str(r["name"]), re.compile(str(r["regex"]))) for r in lint.get("patterns") or [] if r.get("regex")]
    allow = [d.lower() for d in lint.get("allow_emails") or []] + ["invalid", "example.com", "example.org", "noreply.github.com"]
    commit_email = str(kitlib.cfg_get(cfg, "project.commit_email", "")).lower()
    hits = 0
    files = targets(repo, cfg, argv)
    for f in files:
        if f.suffix.lower() not in TEXT_EXT:
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        run = 0
        for n, ln in enumerate(lines, 1):
            kinds = [name for name, rx in rules if rx.search(ln)]
            for m in EMAIL.finditer(ln):
                dom = m.group(1).lower()
                if m.group(0).lower() != commit_email and not any(dom == d or dom.endswith("." + d) or dom.endswith(d) for d in allow):
                    kinds.append("이메일")
            run = run + 1 if ENV_LINE.match(ln) else 0
            if run == 5:
                kinds.append("환경변수_덤프(연속 5줄)")
            for k in dict.fromkeys(kinds):
                try:
                    rel = f.resolve().relative_to(repo)
                except ValueError:
                    rel = f
                print(f"{rel}:{n}: {k}")
                hits += 1
    print(f"evidence_lint: 파일 {len(files)} · 걸림 {hits}", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
