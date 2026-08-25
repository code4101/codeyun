from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.backpack_quick_settings import (
    _decode_quick_view,
    _select_unique_quick_view,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaRef,
)


def _field(values):
    identity = {
        "configBtn": LuaRef("table", 1),
        "ItemContent": LuaRef("table", 2),
        "BackPackQuickItem": LuaRef("table", 3),
    }
    return lambda _address, name: {**identity, **values}.get(name)


def test_active_quick_view_decodes_exact_quick_types_one_through_four():
    values = _decode_quick_view(
        100,
        field=_field({
            "isSelectedOpenBox": False,
            "isSelectedFenJie": True,
            "isSelectedMerge": True,
            "isSelectedUse": True,
        }),
    )
    assert values == {1: 0, 2: 1, 3: 1, 4: 1}


@pytest.mark.parametrize("bad_value", [None, 0, 1, "true"])
def test_quick_view_rejects_missing_or_non_boolean_setting(bad_value):
    with pytest.raises(FanxiuRuntimeMemoryError, match="明确 Lua boolean"):
        _decode_quick_view(
            100,
            field=_field({
                "isSelectedOpenBox": bad_value,
                "isSelectedFenJie": True,
                "isSelectedMerge": True,
                "isSelectedUse": True,
            }),
        )


def test_quick_view_rejects_missing_identity_and_ambiguous_instances():
    with pytest.raises(FanxiuRuntimeMemoryError, match="身份字段"):
        _decode_quick_view(
            100,
            field=lambda _address, name: True if name == "isShow" else None,
        )
    with pytest.raises(FanxiuRuntimeMemoryError, match="NotLoaded"):
        _select_unique_quick_view({})
    with pytest.raises(FanxiuRuntimeMemoryError, match="同时发现 2 个"):
        _select_unique_quick_view({1: {"values": {}}, 2: {"values": {}}})


def test_closed_window_removed_from_open_set_is_not_a_candidate():
    values = {
        "isSelectedOpenBox": False,
        "isSelectedFenJie": True,
        "isSelectedMerge": True,
        "isSelectedUse": True,
    }

    active = {"values": _decode_quick_view(100, field=_field(values))}
    # UIShowMgr.P_CloseOneWin removes the closed component before candidate
    # selection, so a closed view is represented by absence, not isShow=false.
    assert _select_unique_quick_view({100: active}) is active
    with pytest.raises(FanxiuRuntimeMemoryError, match="NotLoaded"):
        _select_unique_quick_view({})
