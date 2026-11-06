"""orca-orchestration-kit 공용 도우미: 저장소·설정·tasks.yaml 읽기.

표준 라이브러리 + PyYAML만 쓴다. 설치 위치는 <저장소>/orchestration/tools/ 이다.
"""
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("오류: PyYAML이 필요하다 (sudo apt install python3-yaml 또는 pip install pyyaml)\n")
    sys.exit(2)

CONFIG_REL = "orchestration/kit.yaml"
STATUSES = ["todo", "ready", "running", "review", "merged"]
LAYERS = ["L0", "L1", "L2"]


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


def task_module(t):
    """module 필드(구 이름 product 허용)."""
    return str(t.get("module", t.get("product", "common")))


def as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]
