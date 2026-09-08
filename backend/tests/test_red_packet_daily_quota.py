"""Pure HasReward quota rules; no simulated GUI or Runtime memory."""

from backend.core.fanxiu.instrumentation.red_packet import evaluate_red_packet_daily_quota


def quota(bag_id=523, event=1, kind=2, limit=3, ids=None, events=None):
    return evaluate_red_packet_daily_quota(
        {"id": bag_id, "eventType": event, "dailyNumType": kind, "dailyNum": limit},
        id_counts=ids, event_counts=events,
    )


def test_shared_quota_excludes_new_packets_but_not_other_event_groups():
    assert quota(events={1: 3})["status"] == "exhausted"
    assert quota(bag_id=524, events={1: 3})["status"] == "exhausted"
    assert quota(event=2, events={1: 3})["status"] == "remaining"


def test_independent_quota_and_unlimited_do_not_inherit_shared_exhaustion():
    assert quota(kind=1, ids={523: 3}, events={1: 0})["status"] == "exhausted"
    assert quota(bag_id=524, kind=1, ids={523: 3}, events={1: 3})["count"] == 0
    assert quota(limit=-1)["status"] == "unlimited"


def test_fresh_counter_reset_restores_eligibility():
    assert quota(events={1: 3})["status"] == "exhausted"
    assert quota(events={1: 0})["remaining"] == 3


def test_missing_and_invalid_counters_are_unknown_not_zero():
    assert quota()["status"] == "unknown"
    for value in (None, -1, True, "invalid"):
        assert quota(events={1: value})["status"] == "unknown"
    assert quota(kind=9, events={1: 3})["status"] == "unknown"
    assert quota(limit=None, events={1: 3})["status"] == "unknown"


def test_string_keys_and_exact_limit_boundary():
    assert quota(events={"1": 2})["remaining"] == 1
    assert quota(events={"1": 3})["status"] == "exhausted"
    assert quota(events={"1": 4})["remaining"] == 0
