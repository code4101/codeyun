"""Ownership decisions only; no simulated game or navigation."""
import pytest

from backend.core.fanxiu.data_annotation.tasks.lingmai_execution import (
    owns_pending_lingmai_navigation_tail,
)


def status(**changes):
    return {"available": True, "complete": True,
            "protocol": "UnionVenisMgr.Model.data",
            "battle_replay": {"available": True, "pending": True}, **changes}


@pytest.mark.parametrize("scene", [318, 303, 374, 382, 375, 588])
def test_live_pending_receipt_owns_only_supported_tail(scene):
    assert owns_pending_lingmai_navigation_tail(scene, status(), "想当年我一把西瓜刀")


@pytest.mark.parametrize("change", [
    {"complete": False}, {"available": False}, {"protocol": "LundaoMgr"},
    {"battle_replay": {}}, {"battle_replay": {"available": True, "pending": False}},
    {"battle_replay": {"available": False, "pending": True}},
])
def test_seat_or_scene_cannot_replace_pending_receipt(change):
    assert not owns_pending_lingmai_navigation_tail(382, status(**change))


@pytest.mark.parametrize("text", ["", "继续论道", "听 道 奖 励", "闻道结束"])
def test_shared_dialogue_rejects_missing_or_foreign_text(text):
    assert not owns_pending_lingmai_navigation_tail(318, status(), text)


@pytest.mark.parametrize("scene", [34, 85, 661, 951])
def test_foreign_or_stable_scene_never_triggers_tail(scene):
    assert not owns_pending_lingmai_navigation_tail(scene, status(), "对白")


@pytest.mark.parametrize("win", [True, False])
def test_shared_report_requires_native_pending_outcome(win):
    assert owns_pending_lingmai_navigation_tail(295, status(
        battle_replay={"available": True, "pending": True, "win": win}))
    assert not owns_pending_lingmai_navigation_tail(295, status())
    assert not owns_pending_lingmai_navigation_tail(295, status(
        battle_replay={"available": True, "pending": False, "win": win}))


def test_settled_loss_cooldown_is_bound_and_does_not_slide():
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.tasks.lingmai_execution import settled_lingmai_defeat_retry_at
    now = datetime(2026, 10, 25, 18, 0)
    receipt = {"outcome": "settled_loss", "task_id": "seat", "role_id": 7,
        "pid": 2, "process_start_ticks": 3, "settled_at": "2026-10-25T17:55:00",
        "next_time": "2026-10-25 18:25:00"}
    facts = status(battle_replay={"available": True, "pending": False},
        self_seat_facts={"available": True, "seated": False},
        evidence={"pid": 2, "process_start_ticks": 3}, self_profile={"role_id": 7})
    def check(r=receipt, s=facts, task="seat", at=now):
        return settled_lingmai_defeat_retry_at(r, s, task_id=task, now=at)
    assert check() == receipt["next_time"]
    assert check(at=datetime(2026,10,25,18,1)) == receipt["next_time"]
    assert check(task="other") is None
    assert check(at=datetime(2026,10,25,18,25)) is None
    assert check(at=datetime(2026,10,26,18)) is None
    assert check(s={**facts,"evidence":{"pid":2,"process_start_ticks":4}}) is None
    assert check(s={**facts,"self_profile":{"role_id":8}}) is None
    assert check(s={**facts,"self_seat_facts":{"available":True,"seated":True}}) is None
    assert check(s={**facts,"battle_replay":{"available":True,"pending":True}}) is None
    assert check(s={**facts,"complete":False}) is None
    assert check(r={**receipt,"next_time":"invalid"}) is None
