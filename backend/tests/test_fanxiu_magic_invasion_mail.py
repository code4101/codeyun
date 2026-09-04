from datetime import date

import pytest

import backend.core.fanxiu.data_annotation.tasks.magic_invasion_mail as magic_mail


def _snapshot(*items):
    return {"complete": True, "items": list(items)}


def _mail(**overrides):
    item = {
        "id": "record-id",
        "mail_id": "runtime-id",
        "title": "美杜莎宝库馈赠",
        "create_time_text": "2026年09月04日 11:32",
        "present_in_runtime": True,
        "execution_status": "unclaimed",
        "reward_getted": False,
        "locked": False,
        "action_policy": "claim",
    }
    item.update(overrides)
    return item


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    (
        (_snapshot(), "未发现"),
        (_snapshot(_mail(execution_status="claimed", reward_getted=True, action_policy="")), "已领取"),
        (_snapshot(_mail(create_time_text="2026年09月03日 11:32")), "未发现"),
    ),
)
def test_selector_idempotently_skips_absent_claimed_or_other_day(snapshot, reason):
    decision = magic_mail.select_magic_invasion_gift_mail(
        snapshot, business_day=date(2026, 9, 4)
    )

    assert decision.action == "skip"
    assert reason in decision.reason


def test_selector_returns_exact_runtime_record_identity_for_one_claimable_mail():
    decision = magic_mail.select_magic_invasion_gift_mail(
        _snapshot(_mail()), business_day=date(2026, 9, 4)
    )

    assert decision.action == "claim"
    assert decision.mail_id == "record-id"


@pytest.mark.parametrize(
    "snapshot",
    (
        {"complete": False, "items": []},
        _snapshot(_mail(action_policy="")),
        _snapshot(_mail(locked=True)),
        _snapshot(_mail(), _mail(id="second")),
    ),
)
def test_selector_fails_closed_without_unique_runtime_authorization(snapshot):
    with pytest.raises(RuntimeError):
        magic_mail.select_magic_invasion_gift_mail(
            snapshot, business_day=date(2026, 9, 4)
        )


@pytest.mark.parametrize(
    "runtime_snapshot",
    (
        _snapshot(),
        _snapshot(
            _mail(execution_status="claimed", reward_getted=True, action_policy="")
        ),
    ),
)
def test_absent_or_claimed_checkpoint_never_enters_gui(monkeypatch, runtime_snapshot):
    monkeypatch.setattr(
        magic_mail,
        "current_runtime_mail_sequence_snapshot",
        lambda _engine_getter: runtime_snapshot,
    )

    class Runner:
        def __init__(self):
            self.logs = []

        def _refresh_runtime_mail_snapshot(self, *_args, **_kwargs):
            return True

        def _execute_mail_selective_claim_task(self, *_args, **_kwargs):
            raise AssertionError("已领取邮件不得进入 GUI")

        def _log(self, level, message):
            self.logs.append((level, message))

    runner = Runner()
    result = _drain(
        magic_mail.execute_magic_invasion_mail_checkpoint(
            runner, {}, {}, object(), business_day=date(2026, 9, 4)
        )
    )

    assert result["status"] == "completed"
    assert result["action"] == "skipped"


def test_claimable_checkpoint_reuses_precise_executor_without_cleanup(monkeypatch):
    monkeypatch.setattr(
        magic_mail,
        "current_runtime_mail_sequence_snapshot",
        lambda _engine_getter: _snapshot(_mail()),
    )
    called = {}

    class Runner:
        def _refresh_runtime_mail_snapshot(self, *_args, **_kwargs):
            return True

        def _execute_mail_selective_claim_task(
            self, ctx, stop_event, payload, *, cleanup_after_claim
        ):
            called.update(
                ctx=ctx,
                stop_event=stop_event,
                payload=payload,
                cleanup_after_claim=cleanup_after_claim,
            )
            if False:
                yield None
            return "success"

    stop_event = object()
    result = _drain(
        magic_mail.execute_magic_invasion_mail_checkpoint(
            Runner(), {"asset": "tree"}, {"keep": "value"}, stop_event,
            business_day=date(2026, 9, 4),
        )
    )

    assert result["action"] == "claimed"
    assert called["payload"] == {
        "keep": "value",
        "target_mail_ids": ["record-id"],
    }
    assert called["cleanup_after_claim"] is False
