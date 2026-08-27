from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation import tiandi_yiju
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


class _Reader:
    def fields(self, value):
        return value if isinstance(value, dict) else {}

    def dictionary_fields(self, value):
        return value if isinstance(value, dict) else {}

    def long(self, value):
        return value if isinstance(value, int) else None


def _state():
    instance = {
        "enterPanelStrength": 29,
        "playChessInfo": {
            "personalRank": 7,
            "personalScore": 19595,
            "allianceRank": 1,
            "allianceScore": 65289380,
        },
    }
    data = {
        "strength": 29,
        "_MaxEnergyValue": 100,
        "_ConsumeNum": 1,
        "_EnergyRecoverTime": 1_800_000,
        "_StrengthItem": 30050000,
        "_MaxMulNum": 5,
        "_IsCross": 0,
        "_ChessInfoDic": {
            1: {"id": 1, "belongAlliance": 1003, "refreshBelongTime": 0},
            2: {"id": 2, "belongAlliance": 1004, "refreshBelongTime": 9},
        },
        "rankDic": {
            1: {
                1: {"allianceId": 1003, "score": 65289380, "name": "万妖谷"},
                2: {"allianceId": 1004, "score": 924143, "name": "天魔宗"},
            },
            2: {1: {"roleId": 2001, "score": 999, "name": "甲"}},
        },
        "chooseStateDic": {2: False, 3: True, 4: False},
    }
    return instance, {}, data


def test_snapshot_derives_natural_budget_and_owned_pieces() -> None:
    instance, model, data = _state()

    result = tiandi_yiju._decode_snapshot(_Reader(), instance, model, data)

    assert result["strength"] == 29
    assert result["natural_play_budget"] == 29
    assert result["own_alliance_id"] == 1003
    assert result["owned_piece_ids"] == [1]
    assert result["personal_score"] == 19595
    assert result["alliance_score"] == 65289380
    assert result["resource_spending_choices"] == {
        "multiple_score_item": False,
        "double_reward_item": True,
        "auto_use_strength_item": False,
    }


def test_snapshot_fails_closed_when_own_alliance_is_ambiguous() -> None:
    instance, model, data = _state()
    data["rankDic"][1][3] = {
        "allianceId": 1005,
        "rank": 1,
        "score": 65289380,
        "name": "同分宗门",
    }

    with pytest.raises(FanxiuRuntimeMemoryError, match="无法从宗门榜唯一反推"):
        tiandi_yiju._decode_snapshot(_Reader(), instance, model, data)


def test_snapshot_rejects_invalid_consume_cost() -> None:
    instance, model, data = _state()
    data["_ConsumeNum"] = 0

    with pytest.raises(FanxiuRuntimeMemoryError, match="单次体力消耗"):
        tiandi_yiju._decode_snapshot(_Reader(), instance, model, data)


def test_group_selection_is_never_a_playable_board() -> None:
    assert tiandi_yiju.GROUP_SELECTION_ACTIVITY_ID == 8090002
    assert tiandi_yiju.GROUP_SELECTION_ACTIVITY_ID not in tiandi_yiju.PLAYABLE_ACTIVITY_IDS


def _transition_snapshot(*, strength: int, personal: int, alliance: int) -> dict:
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "strength": strength,
        "max_strength": 100,
        "consume_per_play": 1,
        "personal_score": personal,
        "alliance_score": alliance,
        "resource_spending_choices": {
            "multiple_score_item": False,
            "double_reward_item": False,
            "auto_use_strength_item": False,
        },
    }


def test_natural_play_transition_accepts_bounded_recovery() -> None:
    result = tiandi_yiju.validate_tiandi_yiju_natural_play_transition(
        _transition_snapshot(strength=32, personal=19595, alliance=92427000),
        _transition_snapshot(strength=30, personal=19625, alliance=92427010),
        expected_plays=3,
    )
    assert result == {
        "plays": 3,
        "strength_spent": 3,
        "natural_strength_recovered": 1,
        "personal_score_gained": 30,
        "alliance_score_gained": 10,
    }


def test_natural_play_transition_rejects_resource_switch() -> None:
    before = _transition_snapshot(strength=32, personal=19595, alliance=92427000)
    before["resource_spending_choices"]["auto_use_strength_item"] = True
    with pytest.raises(RuntimeError, match="资源型开关"):
        tiandi_yiju.validate_tiandi_yiju_natural_play_transition(
            before,
            _transition_snapshot(strength=31, personal=19605, alliance=92427010),
            expected_plays=1,
        )


def test_natural_play_transition_requires_score_gain() -> None:
    with pytest.raises(RuntimeError, match="个人棋符未增加"):
        tiandi_yiju.validate_tiandi_yiju_natural_play_transition(
            _transition_snapshot(strength=32, personal=19595, alliance=92427000),
            _transition_snapshot(strength=31, personal=19595, alliance=92427000),
            expected_plays=1,
        )
