from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.instrumentation.backpack_quick_settings import (
    read_backpack_quick_settings_snapshot,
)


def main() -> int:
    result = read_backpack_quick_settings_snapshot()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
