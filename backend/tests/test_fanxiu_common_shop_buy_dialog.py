from backend.core.fanxiu.instrumentation import common_shop_buy_dialog
from backend.core.fanxiu.instrumentation.common_shop_buy_dialog import (
    plan_common_shop_quantity,
    read_common_shop_buy_dialog_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


def test_quantity_plan_reaches_tianyan_inventory_floor_with_minimum_cost() -> None:
    plan = plan_common_shop_quantity(
        inventory=193,
        target_inventory=3000,
        goods_num=1,
        unit_price=20,
        max_num=5845,
        currency=116906,
    )

    assert plan == {
        "quantity": 2807,
        "cost": 56140,
        "result_inventory": 3000,
        "within_dialog_max": True,
        "currency_sufficient": True,
        "ready": True,
    }


def _dialog_snapshot(show_num: int, *, address: str) -> dict:
    return {
        "ok": True,
        "complete": True,
        "source": "active_common_shop_buy_tips",
        "pid": 11,
        "process_start_ticks": 22,
        "showNum": show_num,
        "panel_address": address,
        "read_only": True,
    }


def test_dialog_reader_rebinds_replaced_panel_without_returning_stale_value(
    monkeypatch,
) -> None:
    stale = FanxiuRuntimeMemoryError("Runtime 内存地址越界：0xdead")
    reads = iter((
        _dialog_snapshot(9, address="0x100"),
        stale,
        _dialog_snapshot(100, address="0x200"),
        _dialog_snapshot(100, address="0x200"),
    ))
    clears: list[bool] = []

    def read(*_args, **_kwargs):
        value = next(reads)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(
        common_shop_buy_dialog,
        "read_ui_runtime_snapshot",
        read,
    )
    monkeypatch.setattr(
        common_shop_buy_dialog,
        "clear_ui_runtime_context_cache",
        lambda: clears.append(True),
    )

    first = read_common_shop_buy_dialog_snapshot()
    rebound = read_common_shop_buy_dialog_snapshot()
    confirmed = read_common_shop_buy_dialog_snapshot()

    assert first["showNum"] == 9
    assert rebound["showNum"] == confirmed["showNum"] == 100
    assert rebound["panel_address"] == "0x200"
    assert rebound["panel_rebinds"] == 1
    assert rebound["read_attempts"] == 2
    assert rebound["panel_rebind_reasons"] == (str(stale),)
    assert clears == [True]


def test_dialog_reader_fails_after_bounded_rebind_when_panel_stays_stale(
    monkeypatch,
) -> None:
    errors = iter((
        FanxiuRuntimeMemoryError("Runtime 内存地址越界：0xdead"),
        FanxiuRuntimeMemoryError("Runtime 内存地址越界：0xbeef"),
    ))
    clears: list[bool] = []
    monkeypatch.setattr(
        common_shop_buy_dialog,
        "read_ui_runtime_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(next(errors)),
    )
    monkeypatch.setattr(
        common_shop_buy_dialog,
        "clear_ui_runtime_context_cache",
        lambda: clears.append(True),
    )

    result = read_common_shop_buy_dialog_snapshot()

    assert result["complete"] is False
    assert result["panel_rebinds"] == 1
    assert result["read_attempts"] == 2
    assert result["panel_rebind_reasons"] == (
        "Runtime 内存地址越界：0xdead",
        "Runtime 内存地址越界：0xbeef",
    )
    assert result["reason"] == "Runtime 内存地址越界：0xbeef"
    assert clears == [True]
