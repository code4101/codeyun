"""领奖时间与榜单族隔离的纯业务契约；不模拟游戏交互。"""
from datetime import datetime, date
from zoneinfo import ZoneInfo
import pytest

from backend.core.fanxiu.activity.ranking_lifecycle import (
    RankingOccurrence, RANKING_TASK_REWARDS_KIND, checkpoints_for_occurrence,
    due_ranking_checkpoints, ranking_checkpoint_is_production,
)

TZ = ZoneInfo("Asia/Shanghai")


def occurrence(kind="beast-abyss", family="gameplay_rank"):
    return RankingOccurrence(activity_type=kind, family=family, runtime_id="current", activity_id=4150001, cross_count=8,
        prepare_at=datetime(2026, 10, 7, tzinfo=TZ), start_at=datetime(2026, 10, 7, 10, tzinfo=TZ),
        end_at=datetime(2026, 10, 7, 21, tzinfo=TZ), close_at=datetime(2026, 10, 8, 23, tzinfo=TZ))


@pytest.mark.parametrize("kind", ["beast-abyss", "magic-invasion", "xutian-palace", "tiandi-yiju", "yunmeng-trial"])
def test_one_evening_reward_check_and_durable_completion(kind):
    item = occurrence(kind)
    # Tiandi uses the playable ID contract, unrelated to reward timing.
    rows = checkpoints_for_occurrence(item, business_day=date(2026, 10, 7))
    rewards = [r for r in rows if r.checkpoint_kind == RANKING_TASK_REWARDS_KIND]
    assert len(rewards) == 1
    reward = rewards[0]
    assert reward.due_at.hour == 21 and reward.due_at.minute == 30
    assert ranking_checkpoint_is_production(reward)
    before = due_ranking_checkpoints([item], now=datetime(2026, 10, 7, 21, 29, tzinfo=TZ))
    assert reward.key not in {r.key for r in before}
    after = due_ranking_checkpoints([item], now=datetime(2026, 10, 7, 21, 30, tzinfo=TZ))
    assert reward.key in {r.key for r in after}
    replay = due_ranking_checkpoints([item], now=datetime(2026, 10, 7, 22, tzinfo=TZ), completed_keys=[reward.key])
    assert reward.key not in {r.key for r in replay}


def test_resource_family_gets_no_gameplay_reward_checkpoint():
    rows = checkpoints_for_occurrence(occurrence("dandao-wending", "resource_rank"), business_day=date(2026, 10, 7))
    assert all(r.checkpoint_kind != RANKING_TASK_REWARDS_KIND for r in rows)
    assert any(r.checkpoint_kind == "dandao_rewards_1810" and r.due_at.hour == 18 for r in rows)


@pytest.mark.parametrize("failed_first", [False, True])
def test_initial_reward_receipt_skips_success_but_retries_failure(monkeypatch, failed_first):
    from sqlmodel import create_engine
    from backend import db
    from backend.core.fanxiu.activity.ranking_lifecycle_store import ensure_ranking_lifecycle_checkpoint_table
    from backend.core.fanxiu.data_annotation.tasks.gameplay_rank_reward_cadence import claim_initial_gameplay_rewards_once
    engine = create_engine("sqlite:///:memory:")
    ensure_ranking_lifecycle_checkpoint_table(engine)
    monkeypatch.setattr(db, "engine", engine)
    calls = []

    def claim():
        calls.append(True)
        if False:
            yield
        return {"status": "pending" if failed_first and len(calls) == 1 else "completed"}

    def finish():
        flow = claim_initial_gameplay_rewards_once(occurrence(), claim)
        with pytest.raises(StopIteration) as stopped:
            next(flow)
        return stopped.value.value

    if failed_first:
        with pytest.raises(RuntimeError, match="未完成"):
            finish()
    assert finish()["status"] == "completed"
    assert finish()["skipped"] is True
    assert len(calls) == (2 if failed_first else 1)
