"""周日祈愿前置与零点兑换的独立日期/完成键契约；不模拟游戏。"""
from dataclasses import replace
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pytest

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingOccurrence, EXCHANGE_TAIL_KIND, PRAYER_EXCHANGE_PRECLOSE_KIND,
    checkpoints_for_occurrence, due_ranking_checkpoints, next_ranking_lifecycle_time,
    occurrence_prayer_exchange_window_contains, pending_prayer_exchange_preclose_checkpoints,
)

TZ = ZoneInfo('Asia/Shanghai')


def moment(day, hour=0, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def occurrence(kind='xutian-palace'):
    return RankingOccurrence(activity_type=kind, family='gameplay_rank',
        runtime_id='current-instance', activity_id=8090001 if kind == 'tiandi-yiju' else 8070001, cross_count=1,
        start_at=moment(11), prepare_at=moment(11),
        end_at=moment(11, 23, 59), close_at=moment(12, 23, 59))


def preclose(o):
    return next(c for c in checkpoints_for_occurrence(o, business_day=date(2026, 10, 11))
                if c.checkpoint_kind == PRAYER_EXCHANGE_PRECLOSE_KIND)


@pytest.mark.parametrize('kind', ['xutian-palace', 'beast-abyss', 'magic-invasion', 'yunmeng-trial', 'xianyuan-duokui', 'tiandi-yiju'])
def test_sunday_exchange_keeps_midnight_tail_as_a_separate_obligation(kind):
    o = occurrence(kind)
    early = preclose(o)
    assert early.due_at == moment(11, 22, 30)
    late = next(c for c in checkpoints_for_occurrence(o, business_day=date(2026, 10, 12))
                if c.checkpoint_kind == EXCHANGE_TAIL_KIND)
    due = due_ranking_checkpoints([o], now=moment(12, 0, 15), completed_keys=[early.key], production_only=True)
    assert late.key in {c.key for c in due}


def test_preclose_wakes_before_prayer_and_late_exchange_still_precedes_topup():
    o = occurrence()
    assert next_ranking_lifecycle_time([o], now=moment(11, 22)) == moment(11, 22, 30)
    assert not occurrence_prayer_exchange_window_contains(o, moment(11, 22, 29))
    assert occurrence_prayer_exchange_window_contains(o, moment(11, 22, 30))
    assert occurrence_prayer_exchange_window_contains(o, moment(11, 23))
    assert not occurrence_prayer_exchange_window_contains(o, moment(12))
    assert any(c.checkpoint_kind == PRAYER_EXCHANGE_PRECLOSE_KIND for c in
               due_ranking_checkpoints([o], now=moment(11, 23)))
    assert pending_prayer_exchange_preclose_checkpoints([o], now=moment(11, 23)) == (preclose(o),)
    assert not pending_prayer_exchange_preclose_checkpoints([o], now=moment(11, 23), completed_keys=[preclose(o).key])


def test_other_days_resource_boards_and_unopened_instances_are_not_advanced():
    for o in (replace(occurrence(), end_at=moment(10, 23, 59)),
              replace(occurrence(), family='resource_rank'),
              replace(occurrence(), start_at=moment(11, 23)),
              replace(occurrence(), close_at=moment(11, 23))):
        assert not any(c.checkpoint_kind == PRAYER_EXCHANGE_PRECLOSE_KIND for c in
                       checkpoints_for_occurrence(o, business_day=date(2026, 10, 11)))


def test_another_instance_completion_cannot_satisfy_this_weeks_prerequisite():
    o = occurrence()
    old = replace(o, runtime_id='old-instance', start_at=moment(10), prepare_at=moment(10))
    assert pending_prayer_exchange_preclose_checkpoints([o], now=moment(11, 23), completed_keys=[preclose(old).key])
