from backend.core.fanxiu.instrumentation.xianyuan_banquet import (
    build_xianyuan_banquet_runtime_snapshot,
    classify_banquet_parties,
    classify_quest_rows,
    classify_wish_tree_draw,
    select_spirit_stone_goods,
    select_spirit_stone_store_offers,
)


_OCCURRENCES = [
    {"activity_id": 304, "base_id": 118000, "state": 2},
    {"activity_id": 30403, "base_id": 118020, "state": 2},
]


def test_preopen_constructor_state_is_not_loaded_empty() -> None:
    result = build_xianyuan_banquet_runtime_snapshot(
        occurrences=_OCCURRENCES,
        party_rows=None,
        party_declared_count=None,
    )
    assert result["status"] == "data_not_loaded"
    assert result["complete"] is False
    assert result["main_activity_id"] == 304


def test_materialized_empty_party_list_is_a_valid_server_fact() -> None:
    result = build_xianyuan_banquet_runtime_snapshot(
        occurrences=_OCCURRENCES,
        party_rows=[],
        party_declared_count=0,
    )
    assert result["status"] == "loaded_empty"
    assert result["complete"] is True


def test_party_list_count_mismatch_fails_closed() -> None:
    result = build_xianyuan_banquet_runtime_snapshot(
        occurrences=_OCCURRENCES,
        party_rows=[{"party_id": 91, "is_completed": False}],
        party_declared_count=0,
    )
    assert result["status"] == "snapshot_incomplete"
    assert result["parties"] == []


def test_unloaded_party_state_is_not_treated_as_empty() -> None:
    result = classify_banquet_parties(None, chest_inventory={})
    assert result["state"] == "data_not_loaded"


def test_completed_party_is_claimed_before_launching_another() -> None:
    result = classify_banquet_parties(
        [{"partyId": "p1", "isCompleted": True}],
        chest_inventory={100: 2},
    )
    assert result["authorized_action"] == "claim_party_reward"


def test_turn_greater_than_reward_time_is_claimable() -> None:
    result = classify_quest_rows([{"id": 7, "serverData": {"turn": 2, "rewardTime": 1}}])
    assert result["claimable_task_ids"] == [7]


def test_spirit_stone_shop_requires_explicit_budget() -> None:
    result = select_spirit_stone_goods(
        [{"goodsId": 9, "currencyType": 1, "buyPrice": 50}],
        purchase_counts={},
        wallet_balance=1000,
        spend_budget=None,
    )
    assert result["state"] == "spend_budget_required"
    assert result["selected"] == []


def test_spirit_stone_selector_ignores_other_currencies_and_purchase_limits() -> None:
    result = select_spirit_stone_goods(
        [
            {"goodsId": 1, "currencyType": 1, "buyPrice": 20, "limitBuy": 1, "limitTimes": 3},
            {"goodsId": 2, "currencyType": 12, "buyPrice": 1, "limitBuy": 1, "limitTimes": 99},
        ],
        purchase_counts={1: 1},
        wallet_balance=100,
        spend_budget=50,
    )
    assert result["selected"] == [{"goods_id": 1, "quantity": 2, "unit_price": 20}]
    assert result["total_cost"] == 40


def test_wish_tree_prefers_ten_draw_only_when_ten_are_affordable() -> None:
    one = classify_wish_tree_draw({"costType": 8010, "costValue": 1}, wallet_balance=7)
    ten = classify_wish_tree_draw({"costType": 8010, "costValue": 1}, wallet_balance=10)
    assert one["authorized_draw_count"] == 1
    assert ten["authorized_draw_count"] == 10


def test_activity_store_accepts_spirit_stones_but_rejects_pay_id() -> None:
    result = select_spirit_stone_store_offers(
        [
            {"id": 11, "costs": "Item|1_188", "times": 2},
            {"id": 12, "payId": 310001, "times": 1},
            {"id": 13, "costs": "Item|12_5", "times": 1},
        ],
        purchase_counts={11: 1},
        wallet_balance=500,
        spend_budget=200,
    )
    assert result["selected"] == [{"offer_id": 11, "quantity": 1, "unit_cost": 188}]
    assert result["rejected_paid_ids"] == [12]
    assert result["unknown_ids"] == [13]
