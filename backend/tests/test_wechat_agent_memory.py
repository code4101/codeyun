from collections import Counter

import pytest

from backend.core.messaging.wechat_agent_memory import (
    growth_mb_per_hour,
    heap_type_delta,
    should_request_reload,
    top_counts,
)


def test_growth_slope_is_mb_per_hour_and_needs_two_distinct_times():
    assert growth_mb_per_hour([]) is None
    assert growth_mb_per_hour([(0.0, 100.0)]) is None
    assert growth_mb_per_hour([(5.0, 1.0), (5.0, 2.0)]) is None
    # 0.5 MB/s linear growth -> 1800 MB/h.
    assert growth_mb_per_hour([(t, 1000 + 0.5 * t) for t in range(0, 600, 60)]) == pytest.approx(1800.0)


def test_heap_summaries_are_bounded_and_only_report_growth():
    counts = Counter({f"T{i}": i for i in range(100)})
    top = top_counts(counts, 15)
    assert len(top) == 15 and top[0] == ["T99", 99]
    delta = heap_type_delta({"dict": 10, "list": 5}, {"dict": 25, "list": 5, "bytes": 3})
    assert delta == [["dict", 15], ["bytes", 3]]


@pytest.mark.parametrize("private_mb, ceiling, requested, expected", [
    (5000, None, False, False),      # opt-in only
    (5000, 0, False, False),
    (5000, True, False, False),
    (4000, 4096, False, False),
    (4096, 4096, False, True),
    (9000, 4096, True, False),       # at most once per worker process
    (None, 4096, False, False),
])
def test_reload_ceiling_is_opt_in_and_fires_once(private_mb, ceiling, requested, expected):
    assert should_request_reload(private_mb, ceiling, requested) is expected
