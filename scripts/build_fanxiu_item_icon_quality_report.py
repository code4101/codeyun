from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu_item_icon_quality import build_item_icon_quality_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a triage report for heavily reused Fanxiu item icons.")
    parser.add_argument("--export-root", default="")
    parser.add_argument("--threshold", type=int, default=50)
    args = parser.parse_args()
    summary = build_item_icon_quality_report(export_root=args.export_root or None, threshold=max(1, args.threshold))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
