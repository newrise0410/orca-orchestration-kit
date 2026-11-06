#!/usr/bin/env python3
"""tasks.yaml에서 작업 하나의 status를 바꾼다. 주석·서식을 그대로 둔다(줄 단위 수정).

사용: python3 orchestration/tools/task_status.py <작업ID> <todo|ready|running|review|merged> [--tasks PATH]
블록형(- id: X ... status: todo)과 흐름형({id: X, ..., status: todo}) 둘 다 지원한다.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402


def set_status(text, tid, status):
    lines = text.split("\n")
    id_re = re.compile(r"^(\s*)-\s*(\{\s*)?id:\s*['\"]?" + re.escape(tid) + r"['\"]?\s*(,|$|\})")
    for i, ln in enumerate(lines):
        m = id_re.match(ln)
        if not m:
            continue
        if m.group(2):  # 흐름형: 같은 줄(또는 닫는 } 까지)
            j = i
            while j < len(lines):
                if re.search(r"status:\s*\w+", lines[j]):
                    lines[j] = re.sub(r"status:\s*\w+", f"status: {status}", lines[j], count=1)
                    return "\n".join(lines)
                if "}" in lines[j]:
                    break
                j += 1
            raise SystemExit(f"{tid}: status 필드를 찾지 못했다(흐름형)")
        indent = len(m.group(1))
        j = i + 1
        while j < len(lines):
            l2 = lines[j]
            if re.match(r"^\s*-\s", l2) and len(l2) - len(l2.lstrip()) <= indent:
                break
            sm = re.match(r"^(\s*)status:\s*\S+(.*)$", l2)
            if sm:
                lines[j] = f"{sm.group(1)}status: {status}{sm.group(2) if sm.group(2).strip().startswith('#') else ''}"
                return "\n".join(lines)
            j += 1
        # status 줄이 없으면 항목 끝에 추가
        lines.insert(j, " " * (indent + 2) + f"status: {status}")
        return "\n".join(lines)
    raise SystemExit(f"작업 ID {tid}를 찾지 못했다")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("status", choices=kitlib.STATUSES)
    ap.add_argument("--tasks"), ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    p = kitlib.tasks_path(repo, kitlib.load_config(repo, a.config), a.tasks)
    p.write_text(set_status(p.read_text(encoding="utf-8"), a.id, a.status), encoding="utf-8")
    print(f"{a.id}: status {a.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
