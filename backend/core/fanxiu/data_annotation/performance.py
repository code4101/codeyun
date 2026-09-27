"""Per-Cell counters. Nested phases overlap; never sum them as wall time.

Only observes existing work: no captures, OCR, persistence or game actions.
The Kernel resets the context between Cells; snapshots own their values.
"""
from math import sqrt
from threading import Lock


class PerformanceCounters:
    def __init__(self):
        self._lock = Lock()
        self._values = {}

    def record(self, phase: str, seconds: float = 0.0, *, units: int = 0):
        with self._lock:
            row = self._values.setdefault(phase, [0, 0.0, 0.0, 0])
            row[0] += 1
            row[1] += seconds
            row[2] += seconds * seconds
            row[3] += units

    def snapshot(self):
        with self._lock:
            return {name: tuple(row) for name, row in self._values.items()}


def counters(ctx):
    return ctx.setdefault('_performance_counters', PerformanceCounters())


def performance_summary(ctx, *, since=None):
    """Read count/total/mean/population stddev and work units; no I/O.

    ``since`` is a previous snapshot from counters(ctx).snapshot(). Layer
    time includes OCR and worker waits; scene_wait includes ticks/loading.
    """
    result = {}
    for name, row in counters(ctx).snapshot().items():
        old = (since or {}).get(name, (0, 0.0, 0.0, 0))
        n, total, squares, units = (a - b for a, b in zip(row, old))
        if n:
            mean = total / n
            result[name] = dict(count=n, seconds=round(total, 4),
                                mean=round(mean, 4),
                                std=round(sqrt(max(0, squares / n - mean * mean)), 4),
                                units=units)
    return result
