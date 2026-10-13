"""Pure occurrence/time-format contract; no game simulation."""
from datetime import datetime
from dataclasses import replace
from zoneinfo import ZoneInfo
import pytest
from backend.core.fanxiu.data_annotation.tasks.celebration_trade_auto import next_celebration_trade_checkpoint
from backend.core.fanxiu.data_annotation.tasks.celebration_trade_auto import celebration_trade_batch_fits_window


@pytest.mark.parametrize('hour,minute,expected', [
    (12, 0, '2026-10-13 20:45:00'),
    (20, 45, '2026-10-13 21:45:00'),
    (21, 44, '2026-10-13 21:45:00'),
    (21, 45, None),
    (23, 59, None),
])
def test_checkpoint_stays_in_occurrence_and_uses_scheduler_format(hour, minute, expected):
    assert next_celebration_trade_checkpoint(datetime(2026, 10, 13, hour, minute)) == expected


def test_new_batch_reserves_departure_before_native_close():
    assert celebration_trade_batch_fits_window(now_epoch=1000, end_time_ms=1721000,
                                               timeout_seconds=600)
    assert not celebration_trade_batch_fits_window(now_epoch=1000, end_time_ms=1720000,
                                                   timeout_seconds=600)
    assert not celebration_trade_batch_fits_window(now_epoch=1800, end_time_ms=1720000,
                                                   timeout_seconds=600)
    with pytest.raises(ValueError):
        celebration_trade_batch_fits_window(now_epoch=True, end_time_ms=1720000,
                                           timeout_seconds=600)


def test_lifecycle_binds_native_daily_occurrence_and_never_replays_after_close():
    from backend.core.fanxiu.activity.ranking_lifecycle import (
        RankingOccurrence, checkpoints_for_occurrence, due_ranking_checkpoints,
        next_ranking_lifecycle_time,
    )
    tz = ZoneInfo('Asia/Shanghai')
    def at(day, hour, minute=0):
        return datetime(2026, 10, day, hour, minute, tzinfo=tz)
    occurrence = RankingOccurrence('celebration-trade', 'gameplay_rank',
        '8044501400004', 8044501, at(13, 10), at(13, 22), at(12, 5), at(14, 23), 8)
    checkpoints = checkpoints_for_occurrence(occurrence, business_day=at(13, 0).date())
    assert [c.due_at for c in checkpoints] == [at(13, 20, 45), at(13, 21, 45)]
    assert all(c.runtime_id == occurrence.runtime_id for c in checkpoints)
    assert checkpoints_for_occurrence(occurrence, business_day=at(14, 0).date()) == ()
    assert len(due_ranking_checkpoints([occurrence], now=at(13, 21), production_only=True)) == 1
    assert due_ranking_checkpoints([occurrence], now=at(13, 22, 1), production_only=True) == ()
    assert next_ranking_lifecycle_time([occurrence], now=at(13, 20), production_only=True) == at(13, 20, 45)
    assert checkpoints_for_occurrence(replace(occurrence, cross_count=4), business_day=at(13, 0).date()) == ()
