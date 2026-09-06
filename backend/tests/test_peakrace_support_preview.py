"""纯预览契约；不模拟游戏执行或 Runtime 接口。"""

from datetime import datetime, timedelta

import pytest

from backend.core.fanxiu.activity.peakrace_stage import preview_peakrace_support


def snapshot(now):
    rows = [{"rank": rank, "role_id": 100 + rank, "support_value": 0} for rank in range(1, 5)]
    return {
        "ok": True, "activity_id": 12, "group": 3, "top_four": rows,
        "peakrace": {
            "complete": True, "captured_at": now.isoformat(),
            "activity_groups": [{"activity_id": 12, "group": 3}],
            "guesses": [{"activity_id": 12, "group": 3, "role_ids": [101]},
                        {"activity_id": 12, "group": 4, "role_ids": [102]}],
        },
        "ranking": {"complete": True, "rank_activity_id": 12,
                    "captured_at": now.isoformat(), "rankings": rows},
    }


def test_trigger_is_only_preview_and_supported_roles_are_group_scoped():
    now = datetime.fromisoformat("2026-09-06T20:30:00+08:00")
    result = preview_peakrace_support(snapshot(now), now=now)
    assert result["status"] == "preview"
    assert result["after_daily_trigger"]
    assert [r["role_id"] for r in result["candidates"]] == [102, 103, 104]
    assert result["authorized_action_count"] == 0
    assert not result["execution_implemented"]
    assert not result["server_sync_freshness_known"]
    before = now - timedelta(seconds=1)
    assert not preview_peakrace_support(snapshot(before), now=before)["after_daily_trigger"]


@pytest.mark.parametrize("problem", ["missing_support", "duplicate_role", "wrong_group", "stale", "future"])
def test_incomplete_or_old_observations_never_produce_candidates(problem):
    now = datetime.fromisoformat("2026-09-06T20:30:00+08:00")
    data = snapshot(now)
    if problem == "missing_support":
        data["top_four"][0]["support_value"] = None
    elif problem == "duplicate_role":
        data["top_four"][1]["role_id"] = 101
    elif problem == "wrong_group":
        data["group"] = 4
    else:
        delta = timedelta(seconds=-121 if problem == "stale" else 1)
        data["ranking"]["captured_at"] = (now + delta).isoformat()
    result = preview_peakrace_support(data, now=now)
    assert result["status"] == "data_incomplete"
    assert result["blockers"]
    assert result["candidates"] == []
    assert result["authorized_action_count"] == 0
