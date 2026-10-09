from __future__ import annotations


import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_direct_use import (
    StorageBagDirectUseBlocked,
    StorageBagDirectUseRequest,
    parse_exact_positive_quantity,
    verify_spirit_stone_direct_use_delta,
)


REQUEST = StorageBagDirectUseRequest(1001, "stone", "灵石", 57_810)


def _bag(
    items: list[tuple[str, int, int]],
    fingerprint: str,
    *,
    pid: int = 123,
) -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": fingerprint,
        "evidence": {"pid": pid, "process_start_ticks": 456},
        "items": [
            {
                "ui_index": index,
                "instance_id": instance_id,
                "base_id": base_id,
                "num": quantity,
                "is_padding": False,
            }
            for index, (instance_id, base_id, quantity) in enumerate(items)
        ],
    }


def _wallet(amount: int, captured_at: str, *, pid: int = 123) -> dict:
    return {
        "source": "runtime_memory",
        "currency_type": 1,
        "exchange_currency": amount,
        "captured_at": captured_at,
        "evidence": {"pid": pid, "process_start_ticks": 456},
    }


BEFORE = _bag([("stone", 1001, 57_810), ("other", 200, 3)], "before")
AFTER = _bag([("other", 200, 3)], "after")
WALLET_BEFORE = _wallet(1_000, "2026-08-20T00:30:00+08:00")
WALLET_AFTER = _wallet(58_810, "2026-08-20T00:30:02+08:00")








def test_exact_transaction_accepts_only_bag_minus_n_and_wallet_plus_n() -> None:
    result = verify_spirit_stone_direct_use_delta(
        BEFORE,
        AFTER,
        request=REQUEST,
        wallet_before=WALLET_BEFORE,
        wallet_after=WALLET_AFTER,
    )

    assert result.consumed_quantity == 57_810
    assert result.wallet_delta == 57_810
    assert result.before_fingerprint == "before"
    assert result.after_fingerprint == "after"


@pytest.mark.parametrize(
    ("after", "wallet_after", "message"),
    [
        (
            _bag([("stone", 1001, 1), ("other", 200, 3)], "partial"),
            WALLET_AFTER,
            "未精确减少",
        ),
        (
            _bag([("other", 200, 4)], "other-changed"),
            WALLET_AFTER,
            "其它背包实例",
        ),
        (
            AFTER,
            _wallet(58_809, "2026-08-20T00:30:02+08:00"),
            "未精确增加",
        ),
        (
            _bag([("other", 200, 3)], "new-process", pid=999),
            WALLET_AFTER,
            "不是同一游戏进程",
        ),
        (
            AFTER,
            _wallet(58_810, "2026-08-20T00:29:59+08:00"),
            "时间发生倒退",
        ),
    ],
)
def test_transaction_fails_closed_on_any_non_exact_delta(
    after: dict, wallet_after: dict, message: str
) -> None:
    with pytest.raises(StorageBagDirectUseBlocked, match=message):
        verify_spirit_stone_direct_use_delta(
            BEFORE,
            after,
            request=REQUEST,
            wallet_before=WALLET_BEFORE,
            wallet_after=wallet_after,
        )


def test_quantity_parser_requires_label_when_contract_has_one() -> None:
    assert parse_exact_positive_quantity(
        [{"text": "持有数量", "x": 1, "y": 1}, {"text": "57810", "x": 2, "y": 1}],
        label="持有数量",
    ) == 57_810
    with pytest.raises(StorageBagDirectUseBlocked, match="缺少标签"):
        parse_exact_positive_quantity(
            [{"text": "57810", "x": 1, "y": 1}], label="持有数量"
        )










def test_adapter_is_not_added_to_the_production_whitelist() -> None:
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_execution import (
        SUPPORTED_PRODUCTION_TEMPLATES,
    )

    assert "direct_use" not in SUPPORTED_PRODUCTION_TEMPLATES
