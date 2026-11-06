#!/usr/bin/env python3
"""셸 스크립트용 kit.yaml 값 읽기.  사용: kitcfg.py <점 경로> [기본값]   예) kitcfg.py orca.work_run
목록 값은 공백으로 이어 출력한다. 모듈 키 목록은 'modules.keys'."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kitlib  # noqa: E402

repo = kitlib.repo_root()
cfg = kitlib.load_config(repo)
key = sys.argv[1]
default = sys.argv[2] if len(sys.argv) > 2 else ""
if key == "modules.keys":
    print(" ".join(str(m["key"]) for m in cfg.get("modules") or [] if str(m.get("key")) not in ("common", "system")))
    sys.exit(0)
v = kitlib.cfg_get(cfg, key, default)
if isinstance(v, list):
    print(" ".join(str(x) for x in v))
elif isinstance(v, bool):
    print("true" if v else "false")
else:
    print("" if v is None else v)
