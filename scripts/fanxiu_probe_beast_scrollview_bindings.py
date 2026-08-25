from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.instrumentation.beast_spirit import (
    diagnose_active_beast_scrollview_bindings,
    diagnose_beast_scrollview_root_positions,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scrollview-address",
        type=lambda value: int(value, 0),
        help="Known active BeastSpiritSlotGridPanel.scrollview address",
    )
    parser.add_argument(
        "--panel-address",
        type=lambda value: int(value, 0),
        help="Known active panel address (evidence only)",
    )
    args = parser.parse_args()
    if args.scrollview_address is not None:
        result = diagnose_beast_scrollview_root_positions(
            scrollview_address=args.scrollview_address,
            panel_address=args.panel_address,
        )
    else:
        result = diagnose_active_beast_scrollview_bindings()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
