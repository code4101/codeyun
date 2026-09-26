from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.ranking_lifecycle import (
    BEAST_ABYSS_REGISTRATION_KIND, RankingOccurrence,
    due_ranking_checkpoints, next_ranking_lifecycle_time,
)

TZ = ZoneInfo("Asia/Shanghai")


def test_registration_only_on_eve_after_five_and_completion_suppresses_replay():
    occurrence = RankingOccurrence(
        activity_type="beast-abyss", family="gameplay_rank", runtime_id="4150001400004",
        activity_id=4150001, cross_count=4,
        prepare_at=datetime(2026, 9, 26, 5, tzinfo=TZ),
        start_at=datetime(2026, 9, 27, 10, tzinfo=TZ),
        end_at=datetime(2026, 9, 28, 22, tzinfo=TZ),
        close_at=datetime(2026, 9, 29, 23, 59, tzinfo=TZ),
    )
    before = datetime(2026, 9, 26, 4, 59, tzinfo=TZ)
    due = datetime(2026, 9, 26, 5, tzinfo=TZ)
    assert not due_ranking_checkpoints([occurrence], now=before, production_only=True)
    assert next_ranking_lifecycle_time([occurrence], now=before, production_only=True) == due
    checkpoints = due_ranking_checkpoints([occurrence], now=due, production_only=True)
    assert [c.checkpoint_kind for c in checkpoints] == [BEAST_ABYSS_REGISTRATION_KIND]
    assert not due_ranking_checkpoints([occurrence], now=due,
                                      completed_keys=[checkpoints[0].key], production_only=True)
    for day in (27, 28):
        assert BEAST_ABYSS_REGISTRATION_KIND not in {
            c.checkpoint_kind for c in due_ranking_checkpoints(
                [occurrence], now=datetime(2026, 9, day, 6, tzinfo=TZ), production_only=True)
        }
    late = replace(occurrence, prepare_at=datetime(2026, 9, 26, 7, tzinfo=TZ))
    assert not due_ranking_checkpoints([late], now=due, production_only=True)
