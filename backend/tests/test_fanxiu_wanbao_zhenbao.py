from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.wanbao_zhenbao import (
    build_wanbao_cumulative_rewards,
    build_wanbao_draw_state,
    project_wanbao_exchange_shop,
)


def test_cumulative_rewards_use_runtime_targets_and_claim_ledger() -> None:
    snapshot = build_wanbao_cumulative_rewards(
        progress=25,
        milestones=[
            {"id": 71200001, "progress": 10, "reward": "Item|9070095_1"},
            {"id": 71200002, "progress": 20, "reward": "Item|40017_4"},
            {"id": 71200003, "progress": 30, "reward": "Item|9070095_1"},
        ],
        claimed_ids=[71200001],
    )

    assert snapshot["progress"] == 25
    assert snapshot["claimed_reward_ids"] == [71200001]
    assert snapshot["claimable_reward_ids"] == [71200002]
    assert snapshot["milestones"][2]["claimable"] is False


@pytest.mark.parametrize(
    ("milestones", "claimed_ids"),
    [
        (
            [
                {"id": 1, "progress": 20, "reward": "Item|1_1"},
                {"id": 2, "progress": 10, "reward": "Item|1_1"},
            ],
            [],
        ),
        ([{"id": 1, "progress": 10, "reward": "Item|1_1"}], [2]),
    ],
)
def test_cumulative_rewards_fail_closed_on_incoherent_runtime(
    milestones: list[dict], claimed_ids: list[int]
) -> None:
    with pytest.raises(FanxiuRuntimeMemoryError):
        build_wanbao_cumulative_rewards(
            progress=20,
            milestones=milestones,
            claimed_ids=claimed_ids,
        )


def test_exchange_shop_projects_finite_and_unlimited_stock() -> None:
    snapshot = project_wanbao_exchange_shop(
        [
            {
                "goodsId": 71200002,
                "itemId": 3020184,
                "goodsNum": 20,
                "currencyType": 40012,
                "buyPrice": 400,
                "limitTimes": 3,
                "position": 2,
            },
            {
                "goodsId": 71200017,
                "itemId": 9020042,
                "goodsNum": 1,
                "currencyType": 40012,
                "buyPrice": 1,
                "limitTimes": -1,
                "position": 17,
            },
        ],
        purchased_counts={71200002: 1, 71200017: 9},
    )

    assert snapshot["currency_types"] == [40012]
    assert snapshot["items"][0]["remaining"] == 2
    assert snapshot["items"][1]["remaining"] is None
    assert snapshot["items"][1]["purchased_count"] == 9


def test_exchange_shop_rejects_purchase_count_past_limit() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError):
        project_wanbao_exchange_shop(
            [
                {
                    "goodsId": 1,
                    "itemId": 2,
                    "goodsNum": 1,
                    "currencyType": 40012,
                    "buyPrice": 10,
                    "limitTimes": 1,
                    "position": 1,
                }
            ],
            purchased_counts={1: 2},
        )


def test_draw_state_projects_authoritative_first_hit_scatter_point() -> None:
    snapshot = build_wanbao_draw_state(
        progress=17,
        cost_type=40017,
        cost_per_draw=1,
        available_currency=23,
        smalls=[
            {"id": 1, "limit": 0, "bigReward": False, "reward": "Item|1_1"},
            {"id": 2, "limit": 0, "bigReward": True, "reward": "Item|9_1"},
        ],
        hit_counts={1: 3, 2: 1},
    )

    assert (snapshot["x"], snapshot["y"]) == (17, 1)
    assert snapshot["target_complete"] is True
    assert snapshot["grand_prize"]["id"] == 2
    assert snapshot["available_draws"] == 23


@pytest.mark.parametrize(
    ("smalls", "hit_counts"),
    [
        ([{"id": 1, "limit": 1, "bigReward": False}], {}),
        (
            [
                {"id": 1, "limit": 1, "bigReward": True},
                {"id": 2, "limit": 1, "bigReward": True},
            ],
            {},
        ),
        ([{"id": 1, "limit": 1, "bigReward": True}], {2: 1}),
        ([{"id": 1, "limit": 1, "bigReward": True}], {1: 2}),
    ],
)
def test_draw_state_fails_closed_on_incoherent_pool(
    smalls: list[dict], hit_counts: dict[int, int]
) -> None:
    with pytest.raises(FanxiuRuntimeMemoryError):
        build_wanbao_draw_state(
            progress=0,
            cost_type=40017,
            cost_per_draw=1,
            available_currency=10,
            smalls=smalls,
            hit_counts=hit_counts,
        )
