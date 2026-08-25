from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.instrumentation.backpack_quick_settings import (
    clear_backpack_quick_view_cache,
    read_backpack_quick_settings_snapshot,
)
from backend.core.fanxiu.instrumentation.backpack_ui import (
    clear_backpack_ui_view_cache,
    locate_backpack_ui_items,
    read_backpack_ui_snapshot,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    clear_ui_runtime_context_cache,
)


def _summary(values: list[float]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "mean_ms": statistics.fmean(values) * 1000,
        "std_ms": statistics.pstdev(values) * 1000 if len(values) > 1 else 0.0,
        "p50_ms": statistics.median(values) * 1000,
        "max_ms": max(values) * 1000,
    }


def _run(
    reader: Callable[[], dict[str, Any]],
    *,
    hot_rounds: int,
    base_id: int | None,
    instance_id: str | None,
) -> dict[str, Any]:
    clear_ui_runtime_context_cache()
    clear_backpack_quick_view_cache()
    clear_backpack_ui_view_cache()
    snapshots = [reader()]
    for _ in range(hot_rounds):
        snapshots.append(reader())
    for snapshot in snapshots:
        if snapshot.get("complete") is not True:
            raise RuntimeError(snapshot.get("reason") or "instrumentation snapshot incomplete")

    query_results: list[list[dict[str, Any]]] = []
    if base_id is not None or instance_id is not None:
        for snapshot in snapshots:
            # Querying is deliberately a pure filter over the one captured
            # snapshot.  It must never trigger another process scan.
            query_results.append(
                locate_backpack_ui_items(
                    snapshot,
                    base_id=base_id,
                    instance_id=instance_id,
                )
            )

    hot = snapshots[1:]
    stage_names = sorted(
        {
            name
            for snapshot in snapshots
            for name in snapshot.get("performance", {}).get("stages_seconds", {})
        }
    )
    return {
        "cold": {
            "elapsed_ms": snapshots[0]["elapsed_seconds"] * 1000,
            "stages_ms": {
                name: value * 1000
                for name, value in snapshots[0]
                .get("performance", {})
                .get("stages_seconds", {})
                .items()
            },
        },
        "hot": _summary([snapshot["elapsed_seconds"] for snapshot in hot]),
        "hot_stages": {
            name: _summary(
                [
                    snapshot.get("performance", {})
                    .get("stages_seconds", {})
                    .get(name, 0.0)
                    for snapshot in hot
                ]
            )
            for name in stage_names
        },
        "identity": snapshots[-1].get("evidence"),
        "query_match_counts": [len(matches) for matches in query_results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark strictly read-only backpack instrumentation."
    )
    parser.add_argument("target", choices=("quick", "backpack"))
    parser.add_argument("--hot-rounds", type=int, default=8)
    parser.add_argument("--base-id", type=int)
    parser.add_argument("--instance-id")
    args = parser.parse_args()
    if args.hot_rounds < 8:
        parser.error("--hot-rounds must be at least 8")
    if args.target == "quick" and (
        args.base_id is not None or args.instance_id is not None
    ):
        parser.error("item locate filters are only valid for target=backpack")
    reader = (
        read_backpack_quick_settings_snapshot
        if args.target == "quick"
        else read_backpack_ui_snapshot
    )
    result = _run(
        reader,
        hot_rounds=args.hot_rounds,
        base_id=args.base_id,
        instance_id=args.instance_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
