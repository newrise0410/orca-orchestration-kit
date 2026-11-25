"""orca-orchestration-kit 공용 도우미: 저장소·설정·tasks.yaml 읽기.

표준 라이브러리 + PyYAML만 쓴다. 설치 위치는 <저장소>/orchestration/tools/ 이다.
"""
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("오류: PyYAML이 필요하다 (sudo apt install python3-yaml 또는 pip install pyyaml)\n")
    sys.exit(2)

CONFIG_REL = "orchestration/kit.yaml"
ARCHIVE_REL = "orchestration/tasks.archive.yaml"
STATUSES = ["todo", "ready", "running", "review", "merged"]
LAYERS = ["L0", "L1", "L2"]
# Orca worker-list의 state 중 '끝난' 것. 나머지(running 등)는 진행 중으로 본다.
def workspace_path(v):
    """Orca 워커의 workspace 값 → 파일 시스템 경로. Orca는 '<워크스페이스 ID>::<경로>'로 준다(경로만 오기도 한다).
    Windows 경로의 'C:'는 '::'가 아니라서 그대로 남는다."""
    if isinstance(v, dict):
        v = v.get("path") or v.get("worktreePath") or ""
    v = str(v or "")
    return v.split("::", 1)[1] if "::" in v else v


def msg_payload(m):
    """Orca 메일의 payload → dict. --full로 읽으면 JSON 문자열로 온다(현장 확인)."""
    pl = (m or {}).get("payload")
    if isinstance(pl, str):
        try:
            pl = json.loads(pl)
        except ValueError:
            return {}
    return pl if isinstance(pl, dict) else {}


SETTLED_WORKER_STATES = {"succeeded", "failed", "abandoned", "stopped", "cancelled", "canceled"}


def repo_root(start=None):
    """start(기본 현재 디렉터리)가 속한 git 저장소 최상위. git이 아니면 start 그대로."""
    start = Path(start or Path.cwd()).resolve()
    try:
        out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except OSError:
        pass
    return start


def load_config(repo, path=None):
    """kit.yaml을 읽는다. 없으면 빈 dict."""
    p = Path(path) if path else Path(repo) / CONFIG_REL
    if not p.is_file():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def cfg_get(cfg, dotted, default=None):
    cur = cfg
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


class DupKeyLoader(yaml.SafeLoader):
    """같은 매핑 안의 중복 키를 기록하는 로더 (PyYAML 기본은 조용히 덮어쓴다)."""

    duplicates = []


def _construct_mapping(loader, node, deep=False):
    seen = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError:
            continue
        if key in seen:
            DupKeyLoader.duplicates.append((key, key_node.start_mark.line + 1, seen[key]))
        else:
            seen[key] = key_node.start_mark.line + 1
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


DupKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_tasks_text(text):
    """tasks.yaml 문자열 → (data, 중복 키 목록[(키, 줄, 처음 줄)])."""
    DupKeyLoader.duplicates = []
    data = yaml.load(text, Loader=DupKeyLoader) or {}
    return data, list(DupKeyLoader.duplicates)


def tasks_path(repo, cfg, override=None):
    if override:
        return Path(override)
    return Path(repo) / cfg_get(cfg, "paths.tasks", "orchestration/tasks.yaml")


def archive_path(repo, cfg):
    return Path(repo) / cfg_get(cfg, "paths.tasks_archive", ARCHIVE_REL)


def load_archive(repo, cfg):
    """보관 파일(merged 작업을 옮겨 둔 곳)의 작업 목록. 없으면 []."""
    p = archive_path(repo, cfg)
    if not p.is_file():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return [t for t in (data.get("tasks") or []) if isinstance(t, dict) and t.get("id")]


def all_tasks(repo, cfg, override=None):
    """(tasks.yaml data, {id: 작업}) — 보관 파일 작업도 포함한다(같은 ID면 tasks.yaml이 이긴다)."""
    data, _ = load_tasks_text(tasks_path(repo, cfg, override).read_text(encoding="utf-8"))
    out = {}
    for t in load_archive(repo, cfg) + [t for t in (data.get("tasks") or []) if isinstance(t, dict)]:
        if t.get("id") is not None:
            out[str(t["id"])] = t
    return data, out


def git_remote(cfg):
    """브랜치를 올릴 git 원격 이름. 빈 값이면 로컬 전용 운영(원격 push 없음)."""
    v = cfg_get(cfg, "project.git_remote", "origin")
    return "" if v in (None, False) else str(v).strip()


def is_gate(t, cfg):
    tid = str(t.get("id", ""))
    return tid.startswith("G-") or tid in [str(g) for g in (cfg.get("gates") or [])]


def open_deps(t, tasks):
    """아직 merged가 아닌 선행 작업. L1 통합 작업은 상시 세션이라 L2 선행은 기다리지 않는다(먼저 띄워 두고 req마다 머지)."""
    out = []
    for d in as_list(t.get("deps")):
        dt = tasks.get(d)
        if dt is None:
            out.append(d)
            continue
        if str(t.get("layer")) == "L1" and str(dt.get("layer")) == "L2":
            continue
        if str(dt.get("status", "todo")).strip().lower() != "merged":
            out.append(d)
    return out


def is_ready(t, tasks, cfg):
    """투입 가능: status todo/ready, hold(보류 사유) 없음, 게이트 아님, 선행 작업 충족."""
    if str(t.get("status", "todo")).strip().lower() not in ("todo", "ready") or t.get("hold"):
        return False
    return not is_gate(t, cfg) and not open_deps(t, tasks)


def task_module(t):
    """module 필드(구 이름 product 허용)."""
    return str(t.get("module", t.get("product", "common")))


def as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]
