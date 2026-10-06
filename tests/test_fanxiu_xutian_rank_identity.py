import pytest

from backend.core.fanxiu.activity.xutian_palace_instrumentation import (
    resolve_xutian_rank_activity_ids,
)


def test_runtime_ids_follow_parent_not_reward_group_or_cross_arithmetic():
    rows = [
        {"id": 4080001, "baseId": 80000, "crossGroup": 4, "follow": [80452, 80472]},
        {"id": 80452, "baseId": 80500, "crossGroup": 4, "rewardGroup": 80451},
        {"id": 80472, "baseId": 80700, "crossGroup": 4, "rewardGroup": 80471},
        {"id": 80491, "baseId": 80500, "crossGroup": 4, "rewardGroup": 80491},
    ]
    assert resolve_xutian_rank_activity_ids(
        4, parent_activity_id=4080001, activity_rows=rows
    ) == {"personal": 80452, "plane": 80472}


def test_missing_scope_fails_closed():
    rows = [
        {"id": 4080001, "baseId": 80000, "crossGroup": 4, "follow": [80452]},
        {"id": 80452, "baseId": 80500, "crossGroup": 4},
    ]
    with pytest.raises(ValueError, match="plane"):
        resolve_xutian_rank_activity_ids(4, parent_activity_id=4080001, activity_rows=rows)
