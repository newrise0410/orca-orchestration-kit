#!/usr/bin/env python3
"""merged 작업을 tasks.yaml에서 보관 파일(orchestration/tasks.archive.yaml)로 옮긴다.

tasks.yaml이 커지면 매니저가 읽을 때마다 문맥을 쓴다(실사용 예: 작업 75개 103KB 중 96%가 merged).
옮긴 작업은 validate_dag·render_spec·launch·status·대시보드가 그대로 'merged 선행 작업'으로 읽는다.
항목 텍스트(주석 포함)를 줄 단위로 옮기고, 옮기기 전후 작업 집합·내용이 같은지 검산한 뒤에만 쓴다.

사용: python3 orchestration/tools/archive_tasks.py [--dry-run] [--keep ID ...]
  --keep: merged여도 남길 작업 (예: 아직 자주 참조하는 계약 작업)
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402
import yaml  # noqa: E402

HEADER = ("# 보관 작업 — archive_tasks.py가 tasks.yaml에서 옮긴 merged 작업.\n"
          "# validate_dag·render_spec·launch·status·대시보드가 이 파일도 읽는다. 고치지 않는다(되살리려면 항목을 tasks.yaml로 옮긴다).\n"
          "tasks:\n")


def split_items(text):
    """tasks: 아래 목록 항목을 [(시작줄, 끝줄(제외), 들여쓰기)]로. 섹션 주석·빈 줄은 항목에 넣지 않는다."""
    lines = text.split("\n")
    try:
        k = next(i for i, ln in enumerate(lines) if re.match(r"^tasks:\s*(#.*)?$", ln))
    except StopIteration:
        raise SystemExit("최상위 'tasks:'를 찾지 못했다")
    items, cur, ind = [], None, None
    for i in range(k + 1, len(lines)):
        ln = lines[i]
        m = re.match(r"^(\s*)- ", ln)
        if m and (ind is None or len(m.group(1)) == ind):
            ind = len(m.group(1))
            if cur is not None:
                items.append((cur, last + 1, ind))
            cur, last = i, i
            continue
        if cur is None:
            continue
        if ln.strip() == "":
            continue
        lead = len(ln) - len(ln.lstrip())
        if lead > ind:
            last = i                   # 항목 안의 줄(주석 포함)
        else:
            items.append((cur, last + 1, ind))
            cur = None
            if not ln.lstrip().startswith("#"):
                break                  # tasks 목록 끝(다음 최상위 키)
    if cur is not None:
        items.append((cur, last + 1, ind))
    return lines, items


def main(argv=None):
    ap = argparse.ArgumentParser(description="merged 작업을 보관 파일로 옮긴다")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", nargs="*", default=[])
    ap.add_argument("--repo"), ap.add_argument("--config")
    a = ap.parse_args(argv)
    repo = kitlib.repo_root(a.repo)
    cfg = kitlib.load_config(repo, a.config)
    tpath, apath = kitlib.tasks_path(repo, cfg), kitlib.archive_path(repo, cfg)
    text = tpath.read_text(encoding="utf-8")
    before = {str(t["id"]): t for t in (yaml.safe_load(text) or {}).get("tasks") or []}
    lines, items = split_items(text)
    move, keep_set = [], set(a.keep)
    for s, e, ind in items:
        one = yaml.safe_load("\n".join(x[ind:] if x[:ind].strip() == "" else x for x in lines[s:e]))
        t = one[0] if isinstance(one, list) and one else None
        if isinstance(t, dict) and str(t.get("status", "")).strip().lower() == "merged" and str(t.get("id")) not in keep_set:
            move.append((s, e, ind, str(t["id"])))
    if not move:
        print("옮길 merged 작업이 없다")
        return 0
    drop = set()
    chunk = []
    for s, e, ind, _ in move:
        drop |= set(range(s, e))
        chunk += ["  " + x[ind:] if x.strip() else "" for x in lines[s:e]]
    new_text = "\n".join(x for i, x in enumerate(lines) if i not in drop)
    old_arch = apath.read_text(encoding="utf-8") if apath.is_file() else HEADER
    new_arch = old_arch.rstrip("\n") + "\n" + "\n".join(chunk) + "\n"
    # 검산: 옮긴 뒤 tasks + 보관 = 옮기기 전 tasks (+ 이전 보관), 내용 동일
    after = {str(t["id"]): t for t in (yaml.safe_load(new_text) or {}).get("tasks") or []}
    arch = {str(t["id"]): t for t in (yaml.safe_load(new_arch) or {}).get("tasks") or []}
    prev = {str(t["id"]): t for t in (yaml.safe_load(old_arch) or {}).get("tasks") or []}
    ids = [m[3] for m in move]
    ok = (set(after) == set(before) - set(ids) and set(arch) == set(prev) | set(ids)
          and all(arch[i] == before[i] for i in ids) and all(after[i] == before[i] for i in after))
    if not ok:
        raise SystemExit("검산 실패: 항목 경계를 잘못 읽었다. 아무것도 쓰지 않았다(tasks.yaml 서식을 확인하라)")
    print(f"옮김 {len(ids)}: {', '.join(ids)}")
    print(f"tasks.yaml {len(text.encode())}B → {len(new_text.encode())}B, 남은 작업 {len(after)}")
    if a.dry_run:
        print("(dry-run: 쓰지 않았다)")
        return 0
    apath.write_text(new_arch, encoding="utf-8")
    tpath.write_text(new_text, encoding="utf-8")
    print(f"썼다: {tpath.relative_to(repo)}, {apath.relative_to(repo)} — validate_dag 후 커밋한다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
