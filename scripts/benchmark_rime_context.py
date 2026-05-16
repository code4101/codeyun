from __future__ import annotations

import argparse
import csv
import os
import random
import shutil
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


MAX_CONTEXT = 4
MAX_CANDIDATES = 10
CONTEXT_WEIGHTS = [1.0, 1.25, 1.4, 1.5]


def perf_ms() -> float:
    return time.perf_counter() * 1000


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percent))))
    return ordered[index]


def summarize_ms(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values) if values else 0.0,
        "mean": statistics.mean(values) if values else 0.0,
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else 0.0,
    }


def format_ms(stats: dict[str, float]) -> str:
    return (
        f"min={stats['min']:.3f} ms  mean={stats['mean']:.3f} ms  "
        f"p50={stats['p50']:.3f} ms  p95={stats['p95']:.3f} ms  max={stats['max']:.3f} ms"
    )


def resolve_rime_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser()
    configured = os.environ.get("CODEYUN_RIME_USER_DIR")
    if configured:
        return Path(configured).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Rime"
    return Path.home() / "AppData" / "Roaming" / "Rime"


def read_prediction_rows(path: Path) -> list[tuple[str, str, str, float, str]]:
    rows: list[tuple[str, str, str, float, str]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for fields in reader:
            if not fields:
                continue
            first = (fields[0] or "").strip()
            if not first or first.startswith("#") or len(fields) < 4:
                continue
            try:
                weight = float(fields[3] or "1")
            except ValueError:
                weight = 1.0
            rows.append((fields[0], fields[1], fields[2], weight, fields[4] if len(fields) > 4 else ""))
    return rows


def build_index(rows: list[tuple[str, str, str, float, str]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    index: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for context, prefix, text, weight, comment in rows:
        bucket = index[context][prefix]
        existing = next((item for item in bucket if item["text"] == text), None)
        if existing:
            existing["weight"] += weight
            if comment:
                existing["comment"] = comment
        else:
            bucket.append({"text": text, "weight": weight, "comment": comment})
    for by_prefix in index.values():
        for items in by_prefix.values():
            items.sort(key=lambda item: item["weight"], reverse=True)
    return index


def load_index(snapshot_path: Path, seed_path: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    rows = read_prediction_rows(snapshot_path if snapshot_path.exists() else seed_path)
    return build_index(rows)


def load_recent_history(history_path: Path, max_context: int = MAX_CONTEXT) -> list[str]:
    history: list[str] = []
    if not history_path.exists():
        return history
    with history_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            token = parts[1] if len(parts) >= 2 else ""
            if token:
                history.append(token)
                while len(history) > max_context:
                    history.pop(0)
    return history


def context_keys(history: list[str]) -> list[tuple[str, float]]:
    keys: list[tuple[str, float]] = []
    history_size = len(history)
    max_len = min(MAX_CONTEXT, history_size)
    for length in range(1, max_len + 1):
        key = " ".join(history[history_size - length : history_size])
        keys.append((key, CONTEXT_WEIGHTS[length - 1] if length <= len(CONTEXT_WEIGHTS) else 1.0))
    keys.append(("__global", 0.25))
    return keys


def score_candidates(
    index: dict[str, dict[str, list[dict[str, Any]]]],
    history: list[str],
    prefix: str,
) -> list[dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for key, context_weight in context_keys(history):
        for item in index.get(key, {}).get(prefix, []):
            entry = scores.get(item["text"])
            if not entry:
                entry = {"text": item["text"], "score": 0.0}
                scores[item["text"]] = entry
                ordered.append(entry)
            entry["score"] += context_weight * float(item["weight"] or 0)
    ordered.sort(key=lambda item: item["score"], reverse=True)
    return ordered[:MAX_CANDIDATES]


def score_hot_candidates(
    index: dict[str, dict[str, list[dict[str, Any]]]],
    prefix: str,
) -> list[dict[str, Any]]:
    return index.get("__global", {}).get(prefix, [])[:3]


def append_commit_current(pending_path: Path, history_path: Path, history: list[str], prefix: str, token: str) -> None:
    for key, _weight in context_keys(history):
        with pending_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{key}\t{prefix}\t{token}\t1\t自学习\n")
    with history_path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"2026-05-14 00:00:00\t{token}\t{token}\n")


def append_commit_batched(pending_path: Path, history_path: Path, history: list[str], prefix: str, token: str) -> None:
    with pending_path.open("a", encoding="utf-8", newline="\n") as fh:
        for key, _weight in context_keys(history):
            fh.write(f"{key}\t{prefix}\t{token}\t1\t自学习\n")
    with history_path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"2026-05-14 00:00:00\t{token}\t{token}\n")


def timed(callable_obj, repeat: int) -> tuple[Any, list[float]]:
    result = None
    timings: list[float] = []
    for _ in range(repeat):
        start = perf_ms()
        result = callable_obj()
        timings.append(perf_ms() - start)
    return result, timings


def choose_prefixes(rows: list[tuple[str, str, str, float, str]], count: int) -> list[str]:
    counter = Counter(row[1] for row in rows if row[1])
    top = [item for item, _count in counter.most_common(count)]
    return top or ["de", "shi", "wo", "ni", "keyi"]


def benchmark(rime_dir: Path, *, lookup_iterations: int, load_iterations: int, write_iterations: int) -> None:
    snapshot_path = rime_dir / "context_prediction_snapshot.tsv"
    hot_path = rime_dir / "context_prediction_hot.tsv"
    seed_path = rime_dir / "context_prediction.tsv"
    history_path = rime_dir / "context_prediction_history.log"
    pending_path = rime_dir / "context_prediction_pending.tsv"

    rows = read_prediction_rows(snapshot_path if snapshot_path.exists() else seed_path)
    hot_rows = read_prediction_rows(hot_path)
    print(f"Rime dir: {rime_dir}")
    print(f"snapshot rows: {len(rows):,}")
    print(f"hot rows: {len(hot_rows):,}")
    print(f"hot size: {(hot_path.stat().st_size if hot_path.exists() else 0):,} bytes")
    print(f"snapshot size: {(snapshot_path.stat().st_size if snapshot_path.exists() else 0):,} bytes")
    print(f"history size: {(history_path.stat().st_size if history_path.exists() else 0):,} bytes")
    print(f"pending size: {(pending_path.stat().st_size if pending_path.exists() else 0):,} bytes")
    print()

    index, load_timings = timed(lambda: load_index(snapshot_path, seed_path), load_iterations)
    print(f"load_index x{load_iterations}: {format_ms(summarize_ms(load_timings))}")
    hot_index, hot_load_timings = timed(lambda: load_index(hot_path, seed_path), load_iterations)
    print(f"load_hot_index x{load_iterations}: {format_ms(summarize_ms(hot_load_timings))}")

    history, history_timings = timed(lambda: load_recent_history(history_path), max(5, min(50, load_iterations * 5)))
    print(f"load_recent_history: {format_ms(summarize_ms(history_timings))}")

    prefixes = choose_prefixes(rows, 200)
    random.seed(13)
    lookup_timings: list[float] = []
    non_empty = 0
    for _ in range(lookup_iterations):
        prefix = random.choice(prefixes)
        start = perf_ms()
        candidates = score_candidates(index, history, prefix)
        lookup_timings.append(perf_ms() - start)
        if candidates:
            non_empty += 1
    print(f"lookup x{lookup_iterations}: {format_ms(summarize_ms(lookup_timings))}  non_empty={non_empty:,}")

    hot_prefixes = choose_prefixes(hot_rows, 200)
    hot_lookup_timings: list[float] = []
    hot_non_empty = 0
    for _ in range(lookup_iterations):
        prefix = random.choice(hot_prefixes)
        start = perf_ms()
        candidates = score_hot_candidates(hot_index, prefix)
        hot_lookup_timings.append(perf_ms() - start)
        if candidates:
            hot_non_empty += 1
    print(
        f"hot_lookup x{lookup_iterations}: {format_ms(summarize_ms(hot_lookup_timings))}  "
        f"non_empty={hot_non_empty:,}"
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="rime-context-bench-"))
    try:
        current_pending = temp_dir / "current_pending.tsv"
        current_history = temp_dir / "current_history.log"
        batched_pending = temp_dir / "batched_pending.tsv"
        batched_history = temp_dir / "batched_history.log"
        append_history = history[-MAX_CONTEXT:] or ["测试"]
        current_timings: list[float] = []
        batched_timings: list[float] = []
        for index_no in range(write_iterations):
            token = f"测试{index_no}"
            start = perf_ms()
            append_commit_current(current_pending, current_history, append_history, "ceshi", token)
            current_timings.append(perf_ms() - start)
            start = perf_ms()
            append_commit_batched(batched_pending, batched_history, append_history, "ceshi", token)
            batched_timings.append(perf_ms() - start)
        print(f"append current x{write_iterations}: {format_ms(summarize_ms(current_timings))}")
        print(f"append batched x{write_iterations}: {format_ms(summarize_ms(batched_timings))}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Rime context prediction runtime costs.")
    parser.add_argument("--rime-dir", default="", help="Rime user directory. Defaults to APPDATA/Rime.")
    parser.add_argument("--lookup-iterations", type=int, default=5000)
    parser.add_argument("--load-iterations", type=int, default=10)
    parser.add_argument("--write-iterations", type=int, default=500)
    args = parser.parse_args()

    rime_dir = resolve_rime_dir(args.rime_dir or None)
    if not rime_dir.exists():
        raise SystemExit(f"Rime dir does not exist: {rime_dir}")
    benchmark(
        rime_dir,
        lookup_iterations=max(1, args.lookup_iterations),
        load_iterations=max(1, args.load_iterations),
        write_iterations=max(1, args.write_iterations),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
