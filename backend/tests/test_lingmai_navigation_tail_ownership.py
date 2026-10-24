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
