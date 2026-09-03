import pytest

from backend.core.fanxiu.data_annotation.tasks.mail_claim_law import (
    MailClaimLawTaskMixin,
    active_law_end_time,
    law_next_time,
    select_oldest_claimable_law_mail,
)


def _mail(mail_id, created, reward):
    return {"id": mail_id, "create_time_ms": created, "execution_status": "unclaimed", "present_in_runtime": True, "locked": False, "action_policy": "claim", "payload": {"mail_rewards": [reward]}}


def test_selects_oldest_unlocked_law_mail_from_runtime_reward_structure():
    selected = select_oldest_claimable_law_mail({"items": [
        _mail("late", 20, {"item_id": "10080014", "item_name": "仙弈法则", "item_type": "法则"}),
        _mail("old", 10, {"item_id": "10080012", "item_name": "魔道法则", "extra_mark": 7}),
    ]})
    assert selected == {"mail_id": "old", "mail_key": "", "title": "", "create_time_ms": 10, "base_id": 10080012, "name": "魔道法则"}


def test_future_runtime_end_time_is_the_only_schedule_fact():
    active = active_law_end_time({"items": [{"instance_id": "x", "base_id": 10080014, "end_time": 1788078515666}]}, now_ms=1786790400000)
    assert active == {"instance_id": "x", "base_id": 10080014, "end_time_ms": 1788078515666}
    assert law_next_time(active["end_time_ms"]) == "2026-08-30 16:28:35"


def test_multiple_live_end_times_fail_closed():
    assert active_law_end_time({"items": [{"end_time": 200}, {"end_time": 300}]}, now_ms=100) is None


def test_claim_law_refreshes_the_current_runtime_mail_snapshot(monkeypatch):
    refresh_calls = []

    class Runtime:
        def go_scene(self, _view_id):
            if False:
                yield None

    class Runner(MailClaimLawTaskMixin):
        def _behavior_tree_context(self, *_args, **_kwargs):
            return Runtime()

        def _open_storage_bag(self, _runtime):
            if False:
                yield None

        def _remembered_law(self):
            return None

        def _refresh_runtime_mail_snapshot(self, label, *, force_refresh):
            refresh_calls.append((label, force_refresh))
            return False

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.mail_claim_law.read_backpack_ui_snapshot",
        lambda: {},
    )
    task = Runner()._execute_mail_claim_law_task({}, object(), {})

    with pytest.raises(RuntimeError, match="动态邮件模型不可用"):
        list(task)

    assert refresh_calls == [("法则邮件选择", True)]
