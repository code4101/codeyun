from __future__ import annotations

import inspect

import pytest

from backend.core.fanxiu.data_annotation.tasks import magic_invasion_tail as tail
from backend.core.fanxiu.data_annotation.tasks import common_shop_quantity as quantity
from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju_tail
from backend.core.fanxiu.data_annotation.tasks import xianyuan_duokui_tail
from backend.core.fanxiu.data_annotation.tasks import yunmeng_tail


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _dialog(*, show_num: int, maximum: int = 50, price: int = 2500):
    return {
        "complete": True,
        "showNum": show_num,
        "maxNum": maximum,
        "Price": price,
        "HadPrice": 500_000,
        "goodsNum": 1,
        "CanBuy": True,
        "isEnough": True,
    }


def test_common_shop_quantity_uses_shared_slider_and_runtime_proof(monkeypatch):
    snapshots = iter(
        (
            _dialog(show_num=1),
            _dialog(show_num=17),
            _dialog(show_num=17),
        )
    )
    snapshot_reader = lambda: next(snapshots)
    observed = {}

    def set_count(context, assets, desired, **kwargs):
        observed.update(
            context=context,
            assets=assets,
            desired=desired,
            maximum=kwargs["maximum"],
            runtime=kwargs["runtime_count_reader"](),
        )
        if False:
            yield None
        return {"after": desired, "phase": "verified"}

    monkeypatch.setattr(quantity, "set_verified_integer_slider_count", set_count)
    context = object()
    result = _drain(
        quantity.set_verified_common_shop_quantity(
            context,
            17,
            unit_price=2500,
            label="魔道_兑换收尾/测试商品",
            snapshot_reader=snapshot_reader,
        )
    )

    assert observed == {
        "context": context,
        "assets": quantity.COMMON_SHOP_QUANTITY_ASSETS,
        "desired": 17,
        "maximum": 50,
        "runtime": {"current": 17, "maximum": 50},
    }
    assert result["quantity"] == 17
    assert result["owned_currency"] == 500_000
    assert result["adjustment"]["after"] == 17


def test_common_shop_quantity_refuses_purchase_when_final_runtime_count_differs(
    monkeypatch,
):
    snapshots = iter((_dialog(show_num=1), _dialog(show_num=16)))
    snapshot_reader = lambda: next(snapshots)

    def set_count(_context, _assets, desired, **_kwargs):
        if False:
            yield None
        return {"after": desired}

    monkeypatch.setattr(quantity, "set_verified_integer_slider_count", set_count)
    with pytest.raises(RuntimeError, match="购买前数量 16 != 17"):
        _drain(
            quantity.set_verified_common_shop_quantity(
                object(),
                17,
                unit_price=2500,
                label="魔道_兑换收尾/测试商品",
                snapshot_reader=snapshot_reader,
            )
        )


def test_common_shop_quantity_rejects_target_outside_runtime_maximum(monkeypatch):
    snapshot_reader = lambda: _dialog(show_num=1, maximum=10)

    def unexpected_controller(*_args, **_kwargs):
        raise AssertionError("越界目标不得进入数量控制器")
        yield

    monkeypatch.setattr(
        quantity,
        "set_verified_integer_slider_count",
        unexpected_controller,
    )
    with pytest.raises(RuntimeError, match=r"购买数量 11 超出 1\.\.10"):
        _drain(
            quantity.set_verified_common_shop_quantity(
                object(),
                11,
                unit_price=2500,
                label="魔道_兑换收尾/测试商品",
                snapshot_reader=snapshot_reader,
            )
        )


def test_magic_tail_execution_no_longer_uses_direct_quantity_click_plan():
    source = inspect.getsource(tail.execute_magic_invasion_tail_checkpoint)
    assert "yield from set_verified_common_shop_quantity" in source
    assert "exchange_quantity_clicks" not in source
    assert "click_shape_center_fast" not in source


@pytest.mark.parametrize(
    "executor",
    (
        tiandi_yiju_tail.execute_tiandi_yiju_exchange_tail,
        xianyuan_duokui_tail.execute_xianyuan_duokui_tail_checkpoint,
        yunmeng_tail.execute_yunmeng_tail_job,
    ),
)
def test_other_exchange_tails_use_common_shop_quantity_control(executor):
    source = inspect.getsource(executor)
    assert "yield from set_verified_common_shop_quantity" in source
    assert "exchange_quantity_clicks" not in source
    assert "click_shape_center_fast" not in source
    quantity_at = source.index("yield from set_verified_common_shop_quantity")
    total_price_at = source.index("ocr_numbers_in_shapes", quantity_at)
    purchase_at = source.index('"购买"', total_price_at)
    assert quantity_at < total_price_at < purchase_at
