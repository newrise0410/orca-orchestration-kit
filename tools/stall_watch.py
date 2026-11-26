#!/usr/bin/env python3
"""stall_watch.py = quota_watch.py (예약 점검 하나로 한도와 멈춤을 함께 본다. Orca 예약 실행을 둘 두지 않으려고 합쳤다).
  python3 orchestration/tools/stall_watch.py install|status|remove|precheck
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quota_watch  # noqa: E402

if __name__ == "__main__":
    sys.exit(quota_watch.main())
