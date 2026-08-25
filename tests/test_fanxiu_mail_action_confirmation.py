from datetime import datetime, timedelta
from types import SimpleNamespace

from backend.api import fanxiu as _fanxiu_api  # noqa: F401 - initializes the composed runtime class
from backend.core.fanxiu.data_annotation import default_jobs as _default_jobs  # noqa: F401
from backend.core.fanxiu.data_annotation import behavior_tree_runtime as _behavior_tree_runtime  # noqa: F401
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks


def _record(*, status: str, evidence: dict | None = None):
    return SimpleNamespace(
        title="宗门镇邪活动奖励",
        normalized_title="宗门镇邪活动奖励",
        create_time_text="2026年07月31日 21:05",
        status=status,
        action_policy="claim",
        evidence=evidence or {},
        payload={"rewards": [{"itemId": 1, "count": 1}]},
        last_seen_at=1.0,
        updated_at=1.0,
    )


def test_mail_action_confirmation_requires_server_success_fact(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    row = {"title": "宗门镇邪活动奖励", "time_text": "2026年07月31日 21:05"}

    monkeypatch.setattr(runner, "_find_runtime_mail_record", lambda *_args, **_kwargs: _record(status="claim_requested"))
    assert runner._runtime_mail_action_confirmed_for_row(row, "claim") is False

    server_confirmed = _record(
        status="claim_requested",
        evidence={"mail_actions": [{"protocol": "SM_GetMailReward"}]},
    )
    monkeypatch.setattr(runner, "_find_runtime_mail_record", lambda *_args, **_kwargs: server_confirmed)
    assert runner._runtime_mail_action_confirmed_for_row(row, "claim") is True


def test_stale_unconfirmed_mail_request_returns_to_pending_count(monkeypatch):
    runner = create_behavior_tree_runtime_runner()
    requested_at = (datetime.now() - timedelta(seconds=61)).strftime("%Y-%m-%d %H:%M:%S")
    record = _record(
        status="claim_requested",
        evidence={
            "runtime_requested_action": "claim",
            "runtime_action_requested_at": requested_at,
        },
    )
    monkeypatch.setattr(mail_tasks, "pending_runtime_mail_records", lambda _engine: [record])

    assert runner._pending_runtime_mail_action_count() == 1
