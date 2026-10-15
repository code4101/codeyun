"""Bounded, low-overhead memory diagnostics for the WeChat agent worker.

Samples only process counters, Python object *type* counts and (opt-in)
tracemalloc code locations.  It never reads message content, credentials or
provider payloads.  Results are published through the worker heartbeat meta
``memory_diagnostics`` and ``wechat_agent_status()``.

The optional private-memory ceiling is governance, not a root-cause fix: it
only asks the existing busy-gated ``request_wechat_agent_reload`` path once per
worker process; that path drains replies and never kills active work.
"""
from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
import gc
import os
import threading
import time
import tracemalloc
from typing import Any

TRACEMALLOC_ENV = "CODEYUN_WECHAT_TRACEMALLOC_FRAMES"
_MAX_SAMPLES = 240
_TOP = 15


def growth_mb_per_hour(samples: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of (epoch seconds, MB) samples, in MB/h."""

    if len(samples) < 2:
        return None
    n = len(samples)
    mean_t = sum(t for t, _ in samples) / n
    mean_v = sum(v for _, v in samples) / n
    var = sum((t - mean_t) ** 2 for t, _ in samples)
    if var <= 0:
        return None
    cov = sum((t - mean_t) * (v - mean_v) for t, v in samples)
    return cov / var * 3600.0


def top_counts(counter: Counter, limit: int = _TOP) -> list[list[Any]]:
    """Bounded, deterministic top-N list of ``[name, count]``."""

    return [[name, count] for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def heap_type_delta(baseline: dict[str, int], current: dict[str, int], limit: int = _TOP) -> list[list[Any]]:
    """Types whose live count grew most since the worker's first heap sample."""

    growth = Counter({name: count - baseline.get(name, 0) for name, count in current.items()
                      if count - baseline.get(name, 0) > 0})
    return top_counts(growth, limit)


def should_request_reload(private_mb: float | None, ceiling_mb: Any, already_requested: bool) -> bool:
    """Ceiling is opt-in (positive number) and fires at most once per process."""

    if already_requested or private_mb is None or isinstance(ceiling_mb, bool):
        return False
    if not isinstance(ceiling_mb, (int, float)) or ceiling_mb <= 0:
        return False
    return private_mb >= ceiling_mb


WORKER_STAGES = frozenset({"turn.run", "content.enrich"})
_STAGE_LOCK = threading.Lock()
_WORKER_STAGES: dict[str, dict[str, float]] = {}


def _private_mb() -> float | None:
    try:
        import psutil
        info = psutil.Process().memory_info()
        return getattr(info, "private", info.rss) / 2 ** 20
    except Exception:
        return None


@contextmanager
def worker_stage(name: str):
    """Count one worker turn/content boundary: calls, seconds, units, private deltas.

    Deltas are process-wide and include concurrent threads; nested stages
    overlap.  Only fixed names and numbers are kept — never message content.
    """
    if name not in WORKER_STAGES:
        raise ValueError(f"unknown worker stage {name!r}")
    started, before = time.perf_counter(), _private_mb()
    units = {"value": 0}
    try:
        yield units
    finally:
        after = _private_mb()
        delta = after - before if after is not None and before is not None else None
        with _STAGE_LOCK:
            stat = _WORKER_STAGES.setdefault(name, {"count": 0, "seconds": 0.0, "units": 0, "private_delta_mb": 0.0,
                                                    "positive_delta_mb": 0.0, "max_delta_mb": 0.0})
            stat["count"] += 1
            stat["seconds"] += time.perf_counter() - started
            stat["units"] += int(units["value"] or 0)
            if delta is not None:
                stat["private_delta_mb"] += delta
                stat["positive_delta_mb"] += max(0.0, delta)
                stat["max_delta_mb"] = max(stat["max_delta_mb"], delta)


def worker_stage_diagnostics() -> dict[str, dict[str, float]]:
    with _STAGE_LOCK:
        return {name: {k: round(v, 3) if isinstance(v, float) else v for k, v in stat.items()}
                for name, stat in sorted(_WORKER_STAGES.items())}


def aggregate_client_event_stats(clients) -> dict[str, int]:
    """Sum bounded notification-queue counters over live model clients."""
    total = {"clients": 0, "enqueued": 0, "dropped_unwatched": 0, "max_queue_depth": 0, "queued_now": 0}
    for client in list(clients):
        stats = getattr(client, "event_stats", None)
        if not isinstance(stats, dict):
            continue
        total["clients"] += 1
        total["enqueued"] += int(stats.get("enqueued", 0))
        total["dropped_unwatched"] += int(stats.get("dropped_unwatched", 0))
        total["max_queue_depth"] = max(total["max_queue_depth"], int(stats.get("max_queue_depth", 0)))
        events = getattr(client, "events", None)
        total["queued_now"] += events.qsize() if hasattr(events, "qsize") else 0
    return total


def _provider_stages() -> dict | None:
    """Bounded stage totals published by the WeChat storage provider, if available."""
    try:
        from pyxllib.autogui.wechat_diagnostics import sync_stage_diagnostics
    except ImportError:
        return None
    return sync_stage_diagnostics()


class WechatAgentMemoryDiagnostics:
    def __init__(self, settings: dict | None = None):
        settings = dict(settings or {})
        self.sample_seconds = max(10.0, float(settings.get("sample_seconds", 60)))
        self.heap_every = max(1, int(settings.get("heap_every", 10)))
        self.reload_private_mb = settings.get("reload_private_mb")
        self.samples: deque[tuple[float, float]] = deque(maxlen=_MAX_SAMPLES)
        self.sample_count = 0
        self.heap_baseline: dict[str, int] | None = None
        self.trace_baseline = None
        self.reload_requested = False
        self.last: dict[str, Any] = {}
        frames = os.environ.get(TRACEMALLOC_ENV, "").strip()
        if frames.isdigit() and 1 <= int(frames) <= 5 and not tracemalloc.is_tracing():
            tracemalloc.start(int(frames))

    def _process(self) -> dict[str, Any]:
        import psutil
        process = psutil.Process()
        info = process.memory_info()
        private = getattr(info, "private", None)
        return {
            "pid": process.pid,
            "process_create_time": process.create_time(),
            "private_mb": round((private if private is not None else info.vms) / 2 ** 20, 1),
            "rss_mb": round(info.rss / 2 ** 20, 1),
            "handles": getattr(process, "num_handles", lambda: None)(),
            "threads": process.num_threads(),
        }

    def _heap(self) -> dict[str, Any]:
        counts = Counter(type(obj).__name__ for obj in gc.get_objects())
        current = dict(counts)
        if self.heap_baseline is None:
            self.heap_baseline = current
        return {"gc_tracked_objects": sum(counts.values()), "top_types": top_counts(counts),
                "growth_since_first_sample": heap_type_delta(self.heap_baseline, current),
                "gc_counts": list(gc.get_count())}

    def _tracemalloc(self) -> dict[str, Any] | None:
        if not tracemalloc.is_tracing():
            return None
        snapshot = tracemalloc.take_snapshot().filter_traces(
            (tracemalloc.Filter(False, tracemalloc.__file__),))
        current, peak = tracemalloc.get_traced_memory()
        result = {"traced_mb": round(current / 2 ** 20, 1), "peak_mb": round(peak / 2 ** 20, 1)}
        if self.trace_baseline is None:
            self.trace_baseline = snapshot
            result["top_growth"] = []
        else:
            stats = snapshot.compare_to(self.trace_baseline, "lineno")[:10]
            result["top_growth"] = [[str(stat.traceback[0]), round(stat.size_diff / 2 ** 20, 2), stat.count_diff]
                                    for stat in stats]
        return result

    def sample(self) -> dict[str, Any]:
        process = self._process()
        now = time.time()
        self.samples.append((now, process["private_mb"]))
        self.sample_count += 1
        values = [v for _, v in self.samples]
        summary: dict[str, Any] = {
            "at": now, **process, "sample_count": len(self.samples), "sample_seconds": self.sample_seconds,
            "private_mb_mean": round(sum(values) / len(values), 1),
            "private_growth_mb_per_hour": (
                round(slope, 1) if (slope := growth_mb_per_hour(list(self.samples))) is not None else None),
            "tracemalloc_frames": tracemalloc.get_traceback_limit() if tracemalloc.is_tracing() else 0,
            "reload_private_mb": self.reload_private_mb,
            "reload_requested": self.reload_requested,
        }
        summary["provider_stages"] = _provider_stages()
        summary["worker_stages"] = worker_stage_diagnostics()
        if self.sample_count == 1 or self.sample_count % self.heap_every == 0:
            summary["python_heap"] = self._heap()
            summary["tracemalloc"] = self._tracemalloc()
        elif self.last:
            summary["python_heap"] = self.last.get("python_heap")
            summary["tracemalloc"] = self.last.get("tracemalloc")
        self.last = summary
        return summary

    def ceiling_reached(self) -> bool:
        return should_request_reload(self.last.get("private_mb"), self.reload_private_mb, self.reload_requested)
