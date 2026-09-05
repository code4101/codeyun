import threading
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.behavior_tree_executor import (
    BehaviorTreeExecutor,
)
import backend.core.fanxiu.data_annotation.tasks.mail as mail_module
from backend.core.fanxiu.data_annotation.tasks.mail import MailTaskMixin


def _mail(mail_id: str, *, execution_status: str, action_policy: str) -> dict:
    return {
        "id": mail_id,
        "execution_status": execution_status,
        "present_in_runtime": True,
        "locked": False,
        "action_policy": action_policy,
    }


def test_absent_claim_action_is_complete_only_after_exact_runtime_id_resolves() -> None:
    refreshed = {
        "items": [
            _mail("already-claimed", execution_status="claimed", action_policy=""),
            _mail("still-unclaimed", execution_status="unclaimed", action_policy="claim"),
        ]
    }

    assert (
        BehaviorTreeExecutor._runtime_mail_target_still_requires_claim(
            refreshed, "already-claimed"
        )
        is False
    )
    assert (
        BehaviorTreeExecutor._runtime_mail_target_still_requires_claim(
            refreshed, "still-unclaimed"
        )
        is True
    )


def test_absent_claim_action_does_not_resolve_by_same_title_or_neighbor_state() -> None:
    refreshed = {
        "items": [
            {
                **_mail("claimed-neighbor", execution_status="claimed", action_policy=""),
                "title": "香车馈赠",
            },
            {
                **_mail("intended", execution_status="unclaimed", action_policy="claim"),
                "title": "香车馈赠",
            },
        ]
    }

    assert (
        BehaviorTreeExecutor._runtime_mail_target_still_requires_claim(
            refreshed, "intended"
        )
        is True
    )


def test_complete_runtime_snapshot_retries_one_transient_empty_decode(monkeypatch) -> None:
    refresh_results = iter((False, True))
    snapshot = {"complete": True, "decoded_count": 1, "items": [_mail("1", execution_status="claimed", action_policy="")]}
    runner = SimpleNamespace(
        _MAIL_RUNTIME_READ_ATTEMPTS=3,
        _raise_if_stopped=lambda _event: None,
        _refresh_runtime_mail_snapshot=lambda *_args, **_kwargs: next(refresh_results),
        _log=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(mail_module, "current_runtime_mail_sequence_snapshot", lambda _engine: snapshot)
    monkeypatch.setattr(mail_module, "_db_engine", lambda: object())
    monkeypatch.setattr(mail_module.time, "sleep", lambda _seconds: None)

    result = MailTaskMixin._read_complete_precise_mail_snapshot(
        runner,
        threading.Event(),
        reason="#11 领取后只读复验",
    )

    assert result is snapshot
