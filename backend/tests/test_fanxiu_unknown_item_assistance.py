from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_reward_name_known,
)
from backend.core.fanxiu.client import unknown_item_assistance


def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _evidence():
    return [
        {
            "mail_id": "mail-1",
            "item_id": "999999",
            "reward_type": 0,
            "item_type": "",
            "policy_resolution": "",
        }
    ]


def test_unknown_item_assistance_is_deduplicated_by_evidence(monkeypatch):
    engine = _engine()
    calls = []

    def fake_enqueue(name, func, *args, **kwargs):
        calls.append((name, func, args, kwargs))
        return "task-1", True

    monkeypatch.setattr(
        "backend.core.jobs.executor.background_task_queue.enqueue_once",
        fake_enqueue,
    )

    first = unknown_item_assistance.enqueue_fanxiu_unknown_item_assistance(
        _evidence(), db_bind=engine
    )
    second = unknown_item_assistance.enqueue_fanxiu_unknown_item_assistance(
        _evidence(), db_bind=engine
    )

    assert first["queued"] is True
    assert second["queued"] is False
    assert second["deduplicated"] is True
    assert len(calls) == 1


def test_unknown_item_codex_worker_uses_shared_dispatch_gate(monkeypatch):
    engine = _engine()
    captured = {}

    from types import SimpleNamespace
    def dispatch(request):
        captured['request'] = request
        return SimpleNamespace(model_dump=lambda: {'dispatch_id': 'one'})
    monkeypatch.setattr(unknown_item_assistance, 'request_ai_assistance', dispatch)

    result = unknown_item_assistance.run_fanxiu_unknown_item_assistance(
        _evidence(),
        signature="test-signature",
        db_bind=engine,
    )

    prompt = captured["request"].problem
    assert "禁止点击游戏、领取或删除邮件" in prompt
    assert "不得把未知道具直接视为可领" in prompt
    assert "正式实现并补聚焦测试" in prompt
    assert result["status"] == "dispatched"


def test_runtime_proven_activity_material_is_not_treated_as_unknown():
    reward = {
        "item_id": "400013004",
        "item_name": "",
        "item_type": "材料",
        "policy_resolution": "temporary_activity_material",
        "name_source": "runtime_config",
    }

    assert fanxiu_mail_reward_name_known(reward)
    assert fanxiu_mail_action_policy_for_rewards([reward]) == "claim"
