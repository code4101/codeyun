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


def test_codex_client_queues_only_notifications_for_threads_with_active_run():
    from backend.core.codex.wechat_agent import CodexWechatClient

    client = CodexWechatClient()
    autonomous = {"method": "item/agentMessage/delta", "params": {"threadId": "goal-thread", "turnId": "t9"}}
    assert client._wants_event(autonomous) is False
    assert client._wants_event({"method": "account/updated", "params": {}}) is False
    client.run_threads["goal-thread"] = 1
    assert client._wants_event(autonomous) is True


def test_codex_run_registration_is_released_even_when_turn_fails():
    from backend.core.codex.wechat_agent import CodexWechatClient

    client = CodexWechatClient()
    seen = []

    def failing_run(thread_id, *_args, **_kwargs):
        seen.append(dict(client.run_threads))
        raise RuntimeError("turn failed")

    client._run = failing_run
    with pytest.raises(RuntimeError):
        client.run("t1", "p", lambda: False, None, lambda _: None)
    assert seen == [{"t1": 1}] and client.run_threads == {}


def test_worker_stage_is_bounded_and_aggregates_client_counters():
    import queue

    from backend.core.messaging.wechat_agent_memory import (
        aggregate_client_event_stats, worker_stage, worker_stage_diagnostics,
    )

    with worker_stage("content.enrich") as units:
        units["value"] = 2
    stats = worker_stage_diagnostics()["content.enrich"]
    assert stats["count"] >= 1 and stats["units"] >= 2
    with pytest.raises(ValueError):
        with worker_stage("turn.private-chat-text"):
            pass

    class Client:
        def __init__(self, depth, dropped):
            self.events = queue.Queue()
            for _ in range(depth):
                self.events.put({})
            self.event_stats = {"enqueued": depth, "dropped_unwatched": dropped, "max_queue_depth": depth}

    total = aggregate_client_event_stats([Client(2, 5), Client(1, 7), object()])
    assert total == {"clients": 2, "enqueued": 3, "dropped_unwatched": 12, "max_queue_depth": 2, "queued_now": 3}
