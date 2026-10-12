"""Persistent round ownership/idempotence contracts; no game simulation."""
import pytest
from sqlmodel import Session, create_engine

from backend.models import FanxiuExchangeActivity
from backend.core.fanxiu.activity.yunmeng_exchange import (
    begin_yunmeng_challenge_round, settle_yunmeng_challenge_round, read_yunmeng_challenge_round_journal,
)


@pytest.mark.parametrize("before,after,enabled", [(5905, 10, False), (10, 5905, True)])
def test_refill_state_uses_the_observed_transition(before, after, enabled):
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
        _refill_state_after_maximum_change,
    )
    assert _refill_state_after_maximum_change(before, after) is enabled


@pytest.mark.parametrize("before,after", [(10, 10), (0, 0), (-1, 10), (10, -1)])
def test_refill_state_rejects_ambiguous_or_invalid_panel_values(before, after):
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
        _refill_state_after_maximum_change,
    )
    with pytest.raises(ValueError):
        _refill_state_after_maximum_change(before, after)


def test_pending_round_cannot_be_reauthorized_and_partial_cannot_clear_it():
    engine = create_engine("sqlite://")
    FanxiuExchangeActivity.__table__.create(engine)
    identity = {"pid": 42, "process_start_ticks": 7}
    before = {"exchange_currency": 10, "cumulative_currency": 10, "evidence": identity}
    after = {"exchange_currency": 210, "cumulative_currency": 210,
             "captured_at": "2026-10-12T10:20:00+08:00", "evidence": identity}
    with Session(engine) as session:
        row = FanxiuExchangeActivity(id="round", instance_key="instance", activity_type="yunmeng-trial",
                                     start_date="2026-10-12", end_date="2026-10-12")
        session.add(row)
        session.commit()
        intent = begin_yunmeng_challenge_round(session, activity_id="round", instance_key="instance",
                                              requested=100, wallet_before=before,
                                              plan={"phase": "initialization"})
        with pytest.raises(RuntimeError, match="未结整轮"):
            begin_yunmeng_challenge_round(session, activity_id="round", instance_key="instance",
                                          requested=100, wallet_before=before, plan={})
        with pytest.raises(RuntimeError, match="精确完整结算"):
            settle_yunmeng_challenge_round(session, activity_id="round", action_id=intent["action_id"],
                                           wallet_after=after, completion={"ok": True,
                                           "completed_attempts": 99, "evidence": identity})
        assert session.get(FanxiuExchangeActivity, "round").evidence["yunmeng_pending_round"] == intent
        receipt = settle_yunmeng_challenge_round(session, activity_id="round", action_id=intent["action_id"],
                                                 wallet_after=after, completion={"ok": True,
                                                 "completed_attempts": 100, "evidence": identity})
        assert settle_yunmeng_challenge_round(session, activity_id="round", action_id=intent["action_id"],
                                              wallet_after={}, completion={}) == receipt
        session.expire_all()
        evidence = session.get(FanxiuExchangeActivity, "round").evidence
        assert "yunmeng_pending_round" not in evidence
        assert len(evidence["yunmeng_native_auto_batches"]) == 1
        assert read_yunmeng_challenge_round_journal(session, activity_id="round")["settled"] == [receipt]
    engine.dispose()


def test_whole_round_cannot_be_truncated_below_common_minimum():
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import YunmengNativeAutoRequest
    with pytest.raises(ValueError, match="至少100次"):
        YunmengNativeAutoRequest(99, whole_milestone_batch=True)
    with pytest.raises(ValueError, match="禁止裁剪"):
        YunmengNativeAutoRequest(100, whole_milestone_batch=True, maximize_available=True)
    assert YunmengNativeAutoRequest(501, whole_milestone_batch=True).requested_challenges == 501
