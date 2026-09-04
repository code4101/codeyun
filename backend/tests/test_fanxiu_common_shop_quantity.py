from __future__ import annotations

import pytest

import backend.core.fanxiu.data_annotation.tasks.common_shop_quantity as module


def _finish(generator):
    with pytest.raises(StopIteration) as stopped:
        while True:
            next(generator)
    return stopped.value.value


def _snapshot(**overrides):
    return {
        "complete": True,
        "showNum": 1,
        "maxNum": 1998,
        "Price": 20,
        "HadPrice": 39962,
        "CanBuy": True,
        "isEnough": True,
        **overrides,
    }


def test_sacred_dialog_uses_same_common_shop_contract_with_scene_634_assets(monkeypatch):
    calls = []

    def set_count(_context, assets, desired, **kwargs):
        calls.append((assets, desired, kwargs))
        if False:
            yield None
        return {"before": 1, "after": desired}

    monkeypatch.setattr(module, "set_verified_integer_slider_count", set_count)
    snapshots = iter((_snapshot(), _snapshot(showNum=100)))

    result = _finish(module.set_verified_common_shop_quantity(
        object(),
        100,
        unit_price=20,
        label="天雷竹兑换天眼符",
        assets=module.SACRED_SHOP_QUANTITY_ASSETS,
        snapshot_reader=lambda: next(snapshots),
    ))

    assert calls[0][0].settings_scene_id == 634
    assert calls[0][0].count_slider_track == "数量滑条"
    assert calls[0][1] == 100
    assert calls[0][2]["maximum"] == 1998
    assert result["expected_total"] == 2000
    assert result["snapshot"]["showNum"] == 100


def test_common_shop_quantity_rejects_target_outside_runtime_range():
    with pytest.raises(RuntimeError, match="超出 1..99"):
        _finish(module.set_verified_common_shop_quantity(
            object(),
            100,
            unit_price=20,
            label="测试购买",
            snapshot_reader=lambda: _snapshot(maxNum=99),
        ))
