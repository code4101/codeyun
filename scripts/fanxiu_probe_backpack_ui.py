from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.instrumentation.backpack_ui import (
    locate_backpack_ui_items,
    read_backpack_ui_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strictly read the already-loaded ordinary backpack UI projection."
    )
    parser.add_argument("--base-id", type=int)
    parser.add_argument("--instance-id")
    args = parser.parse_args()

    snapshot = read_backpack_ui_snapshot()
    result = {"snapshot": snapshot, "matches": None}
    if args.base_id is not None or args.instance_id is not None:
        result["matches"] = (
            locate_backpack_ui_items(
                snapshot,
                instance_id=args.instance_id,
                base_id=args.base_id,
            )
            if snapshot.get("complete")
            else []
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if snapshot.get("complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
