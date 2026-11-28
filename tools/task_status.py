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
    return set_field(text, tid, "status", status)


def set_field(text, tid, key, value):
    """작업 tid의 key 값을 바꾸거나 넣는다(주석·서식 보존). status 말고 hold 같은 칸에도 쓴다(값은 YAML 그대로 들어간다)."""
    lines = text.split("\n")
    id_re = re.compile(r"^(\s*)-\s*(\{\s*)?id:\s*['\"]?" + re.escape(tid) + r"['\"]?\s*(,|$|\})")
    kv = re.compile(r"(?<![\w-])" + re.escape(key) + r":\s*(\"[^\"]*\"|'[^']*'|[^,}#\s][^,}#]*?)?(?=\s*(,|\}|#|$))")
    for i, ln in enumerate(lines):
        m = id_re.match(ln)
        if not m:
            continue
        if m.group(2):  # 흐름형: 같은 줄(또는 닫는 } 까지)
            j = i
            while j < len(lines):
                if kv.search(lines[j]):
                    lines[j] = kv.sub(lambda _m: f"{key}: {value}", lines[j], count=1)
                    return "\n".join(lines)
                if "}" in lines[j]:
                    k = lines[j].rindex("}")
                    lines[j] = lines[j][:k].rstrip() + f", {key}: {value}" + lines[j][k:]
                    return "\n".join(lines)
                j += 1
            raise SystemExit(f"{tid}: 흐름형 항목의 끝(}})을 찾지 못했다")
        indent = len(m.group(1))
        j = i + 1
        while j < len(lines):
            l2 = lines[j]
            if (re.match(r"^\s*-\s", l2) and len(l2) - len(l2.lstrip()) <= indent) or (l2.strip() and len(l2) - len(l2.lstrip()) <= indent):
                break
            sm = re.match(r"^(\s*)" + re.escape(key) + r":\s*\S*(.*)$", l2)
            if sm:
                tail = sm.group(2) if sm.group(2).strip().startswith("#") else ""
                lines[j] = f"{sm.group(1)}{key}: {value}{tail}"
                return "\n".join(lines)
            j += 1
        # 그 칸이 없으면 항목 끝에 추가(뒤 빈 줄 앞)
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        lines.insert(j, " " * (indent + 2) + f"{key}: {value}")
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
    with kitlib.locked(repo):
        p.write_text(set_status(p.read_text(encoding="utf-8"), a.id, a.status), encoding="utf-8")
    kitlib.log_event(repo, "status", a.id, status=a.status)
    print(f"{a.id}: status {a.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
