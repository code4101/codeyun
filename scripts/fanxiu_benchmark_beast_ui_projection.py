from __future__ import annotations

"""Benchmark the strictly read-only beast-bag UI projection.

The one initial full snapshot supplies the authoritative expected inventory
set.  Timed iterations exercise only the UI projection; they never invoke Lua,
scroll, click, refresh, or send a network command.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.fanxiu.instrumentation.beast_spirit import (
    clear_beast_spirit_order_cache,
    read_active_beast_bag_projection,
    read_beast_spirit_snapshot,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    clear_ui_runtime_context_cache,
)


def _stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "mean": statistics.fmean(values),
        "std": statistics.pstdev(values),
        "p50": statistics.median(ordered),
        "max": max(values),
    }


def _run(expected_ids: set[str], hot_iterations: int) -> dict[str, Any]:
    clear_ui_runtime_context_cache()
    clear_beast_spirit_order_cache()
    samples: list[dict[str, Any]] = []
    for index in range(hot_iterations + 1):
        started = time.perf_counter()
        result = read_active_beast_bag_projection(
            expected_ids,
            include_materialized=False,
        )
        wall_seconds = time.perf_counter() - started
        if not result.get("complete"):
            raise RuntimeError(result.get("reason") or "beast UI projection incomplete")
        samples.append({
            "iteration": index,
            "kind": "cold" if index == 0 else "hot",
            "wall_seconds": wall_seconds,
            "cache_mode": (result.get("performance") or {}).get("cache_mode"),
            "stages": (result.get("performance") or {}).get("stages") or {},
        })
    hot = [sample["wall_seconds"] for sample in samples[1:]]
    stage_names = sorted({
        name
        for sample in samples[1:]
        for name, value in sample["stages"].items()
        if isinstance(value, (int, float)) and not name.endswith("candidates")
    })
    return {
        "expected_item_count": len(expected_ids),
        "cold": samples[0],
        "hot_iterations": hot_iterations,
        "hot_wall_seconds": _stats(hot),
        "hot_stage_seconds": {
            name: _stats([float(sample["stages"].get(name, 0.0)) for sample in samples[1:]])
            for name in stage_names
        },
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hot-iterations", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.hot_iterations < 8:
        parser.error("--hot-iterations must be at least 8")

    baseline_started = time.perf_counter()
    baseline = read_beast_spirit_snapshot()
    baseline_seconds = time.perf_counter() - baseline_started
    if not baseline.get("complete"):
        raise RuntimeError(baseline.get("reason") or "full beast snapshot incomplete")
    expected_ids = {
        str(item["item_id"])
        for item in baseline.get("items") or []
        if not item.get("equipped")
    }
    report = {
        "read_only": True,
        "full_snapshot_setup_seconds": baseline_seconds,
        "benchmark": _run(expected_ids, args.hot_iterations),
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
