import pytest

from backend.core.fanxiu.data_annotation.tasks.mail_claim_law import (
    MailClaimLawTaskMixin,
    active_law_end_time,
    law_detail_title_texts,
    law_next_time,
    select_claimed_law_from_backpack,
    select_oldest_claimable_law_mail,
)


def test_law_detail_title_texts_reads_only_dynamic_title_roi() -> None:
    tokens = [
        {"text": "仙", "x": 303, "y": 281, "w": 43, "h": 39, "parent_line_id": "title", "order": 0},
        {"text": "弈", "x": 341, "y": 281, "w": 44, "h": 39, "parent_line_id": "title", "order": 1},
        {"text": "法", "x": 387, "y": 281, "w": 43, "h": 39, "parent_line_id": "title", "order": 2},
        {"text": "则", "x": 429, "y": 281, "w": 43, "h": 39, "parent_line_id": "title", "order": 3},
        {"text": "仙弈法则", "x": 100, "y": 700, "w": 120, "h": 30, "parent_line_id": "body", "order": 0},
    ]

    assert law_detail_title_texts(tokens) == ("仙弈法则",)


def _mail(mail_id, created, reward):
    return {"id": mail_id, "create_time_ms": created, "execution_status": "unclaimed", "present_in_runtime": True, "locked": False, "action_policy": "claim", "payload": {"mail_rewards": [reward]}}


def test_selects_oldest_unlocked_law_mail_from_runtime_reward_structure():
    selected = select_oldest_claimable_law_mail({"items": [
        _mail("late", 20, {"item_id": "10080014", "item_name": "仙弈法则", "item_type": "法则"}),
        _mail("old", 10, {"item_id": "10080012", "item_name": "魔道法则", "extra_mark": 7}),
    ]})
    assert selected == {"mail_id": "old", "mail_key": "", "title": "", "create_time_ms": 10, "base_id": 10080012, "name": "魔道法则"}


def test_selects_a_policy_protected_law_for_the_dedicated_law_task():
    item = _mail("law", 10, {"item_id": "10080014", "item_name": "仙弈法则", "item_type": "法则"})
    item.update({"desired_status": "锁定", "action_policy": ""})

    assert select_oldest_claimable_law_mail({"items": [item]}) is not None


def test_extra_mark_alone_does_not_turn_a_rare_reward_into_a_law():
    rare_reward = _mail(
        "rare",
        5,
        {"item_id": "4400007", "item_name": "神炼元炁·红线", "item_type": "特殊道具", "extra_mark": 7},
    )
    law = _mail(
        "law",
        10,
        {"item_id": "10080014", "item_name": "仙弈法则", "item_type": "法则", "extra_mark": 7},
    )

    assert select_oldest_claimable_law_mail({"items": [rare_reward, law]})["mail_id"] == "law"


def test_future_runtime_end_time_is_the_only_schedule_fact():
    active = active_law_end_time(
        {"items": [{"instance_id": "x", "base_id": 10080014, "end_time": 1788078515666}]},
        active_faze_id=10020,
        active_item_base_id=10080014,
        now_ms=1786790400000,
    )
    assert active == {"instance_id": "x", "base_id": 10080014, "end_time_ms": 1788078515666}
    assert law_next_time(active["end_time_ms"]) == "2026-08-30 16:28:35"


def test_multiple_live_end_times_fail_closed():
    assert active_law_end_time(
        {"items": [{"base_id": 1, "end_time": 200}, {"base_id": 1, "end_time": 300}]},
        active_faze_id=10020,
        active_item_base_id=1,
        now_ms=100,
    ) is None


def test_unused_future_law_item_does_not_prove_role_activation():
    snapshot = {"items": [{"instance_id": "x", "base_id": 10080014, "end_time": 300}]}

    assert active_law_end_time(snapshot, active_faze_id=0, active_item_base_id=10080014, now_ms=100) is None


def test_role_faze_id_is_not_assumed_to_be_an_item_base_id():
    snapshot = {"items": [{"instance_id": "x", "base_id": 10080014, "end_time": 300}]}

    assert active_law_end_time(snapshot, active_faze_id=10020, now_ms=100) is None


def test_recovers_claimed_law_item_after_a_later_step_failed():
    mail = _mail("law", 10, {"item_id": "10080014", "item_name": "仙弈法则", "item_type": "法则"})
    mail["execution_status"] = "claimed"
    selected = select_claimed_law_from_backpack(
        {"items": [mail]},
        {"items": [{"instance_id": "bag-law", "base_id": 10080014, "end_time": 300, "ui_index": 0}]},
        now_ms=100,
    )

    assert selected == {
        "instance_id": "bag-law",
        "base_id": 10080014,
        "name": "仙弈法则",
        "end_time_ms": 300,
        "ui_index": 0,
    }


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
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.tasks.mail_claim_law.current_role_faze_id",
        lambda: 0,
    )
    task = Runner()._execute_mail_claim_law_task({}, object(), {})

    with pytest.raises(RuntimeError, match="动态邮件模型不可用"):
        list(task)

    assert refresh_calls == [("法则邮件选择", True)]
