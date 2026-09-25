from __future__ import annotations

from backend.core.fanxiu.instrumentation import activity_shop
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


def test_activity_shop_manager_prefers_main_lua_global(monkeypatch) -> None:
    memory = object()
    marker_calls: list[object] = []

    monkeypatch.setattr(
        activity_shop,
        "_lua_addresses",
        lambda current: {"state": "0x1234"},
    )

    def resolve_global(current, **kwargs):
        assert current is memory
        assert kwargs["state_address"] == 0x1234
        assert kwargs["global_name"] == "ActivityMgr"
        kwargs["validate"](object(), 456)
        return 456, True, 789

    monkeypatch.setattr(
        activity_shop,
        "resolve_lua_global_manager_root",
        resolve_global,
    )
    monkeypatch.setattr(
        activity_shop,
        "resolve_manager_root",
        lambda *_args, **_kwargs: marker_calls.append(memory),
    )

    assert activity_shop._resolve_activity_manager(memory) == (
        456,
        True,
        "lua_global",
    )
    assert marker_calls == []


def test_activity_shop_manager_falls_back_when_global_is_unavailable(
    monkeypatch,
) -> None:
    memory = object()
    monkeypatch.setattr(
        activity_shop,
        "_lua_addresses",
        lambda current: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        activity_shop,
        "resolve_lua_global_manager_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FanxiuRuntimeMemoryError("主 Lua 全局环境中没有已加载的 ActivityMgr")
        ),
    )
    monkeypatch.setattr(
        activity_shop,
        "resolve_manager_root",
        lambda current, **_kwargs: (654, False),
    )

    assert activity_shop._resolve_activity_manager(memory) == (
        654,
        False,
        "constructor_marker",
    )


def test_free_compact_shop_row_preserves_currency_layout():
    raw = [None,41000023,41000018,4001,2009101,1,None,19002,2,1,None,
           'SpecifyTalismanPinLevel|37_27','CL|999',2,1,None,None,63]
    row, layout = activity_shop._canonical_shop_row(raw,shop_base_id=4001,currency_type=19002)
    assert layout == 'compact'
    assert (row[6],row[7]) == (0,19002)
    assert activity_shop._canonical_shop_row(raw,shop_base_id=4001,currency_type=16001) is None


def test_reward_layout_without_currency_filter_is_not_ambiguous():
    raw=[None,21000033,1,620000,390037007,1,None,100,37130501,2,25,None,'','',1,1,None,None,0,0]
    row,layout=activity_shop._canonical_shop_row(raw,shop_base_id=620000)
    assert layout=='reward'
    assert (row[6],row[7])==(100,37130501)
