from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_default_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.tasks.magic_invasion_initialization_rewards import (
    select_unique_open_magic_occurrence,
)


TZ = ZoneInfo("Asia/Shanghai")


def _occurrence(
    *,
    runtime_id: str,
    activity_id: int,
    cross_count: int,
    start_at: datetime,
    end_at: datetime,
) -> RankingOccurrence:
    return RankingOccurrence(
        activity_type="magic-invasion",
        family="gameplay_rank",
        runtime_id=runtime_id,
        activity_id=activity_id,
        start_at=start_at,
        end_at=end_at,
        prepare_at=start_at,
        close_at=end_at + timedelta(days=1),
        cross_count=cross_count,
    )


def test_magic_initialization_rewards_is_public_manual_rnd_task() -> None:
    register_fanxiu_default_jobs()

    definition = get_fanxiu_data_annotation_task_cell_definition(
        "magic_invasion_initialization_rewards_rnd"
    )

    assert definition is not None
    assert definition.label == "魔道_初始化与任务奖励研发"
    assert definition.scheduler_supported is False
    assert definition.standard_job is False


def test_magic_server_and_cross_instances_are_selected_only_in_their_own_windows() -> None:
    server_start = datetime(2026, 9, 5, 10, 0, tzinfo=TZ)
    handoff = datetime(2026, 9, 5, 15, 0, tzinfo=TZ)
    cross_end = datetime(2026, 9, 5, 22, 0, tzinfo=TZ)
    server = _occurrence(
        runtime_id="1070001400004",
        activity_id=1070001,
        cross_count=1,
        start_at=server_start,
        end_at=handoff,
    )
    cross = _occurrence(
        runtime_id="4070001400004",
        activity_id=4070001,
        cross_count=4,
        start_at=handoff,
        end_at=cross_end,
    )

    assert select_unique_open_magic_occurrence(
        (server, cross), now=server_start + timedelta(hours=1)
    ) is server
    assert select_unique_open_magic_occurrence(
        (server, cross), now=handoff + timedelta(seconds=1)
    ) is cross
    with pytest.raises(RuntimeError, match="matches=2"):
        select_unique_open_magic_occurrence((server, cross), now=handoff)


def test_magic_open_window_includes_exact_start_and_end_only() -> None:
    start_at = datetime(2026, 9, 5, 10, 0, tzinfo=TZ)
    end_at = datetime(2026, 9, 5, 22, 0, tzinfo=TZ)
    occurrence = _occurrence(
        runtime_id="4070001400004",
        activity_id=4070001,
        cross_count=4,
        start_at=start_at,
        end_at=end_at,
    )

    assert select_unique_open_magic_occurrence((occurrence,), now=start_at) is occurrence
    assert select_unique_open_magic_occurrence((occurrence,), now=end_at) is occurrence
    with pytest.raises(RuntimeError, match="matches=0"):
        select_unique_open_magic_occurrence(
            (occurrence,), now=start_at - timedelta(microseconds=1)
        )
    with pytest.raises(RuntimeError, match="matches=0"):
        select_unique_open_magic_occurrence(
            (occurrence,), now=end_at + timedelta(microseconds=1)
        )
