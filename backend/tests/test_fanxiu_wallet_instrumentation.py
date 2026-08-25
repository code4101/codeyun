from __future__ import annotations

from backend.core.fanxiu.instrumentation import redbag_runtime_loader, wallet
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


class _Memory:
    pid = 9348
    process_start_ticks = 123


def _patch_common(monkeypatch) -> None:
    monkeypatch.setattr(
        wallet.MumuProcessMemory,
        "discover_cached",
        lambda **_kwargs: _Memory(),
    )
    monkeypatch.setattr(wallet, "LuaJitReader", lambda memory: object())
    monkeypatch.setattr(
        redbag_runtime_loader,
        "_lua_addresses",
        lambda memory: {"state": "0x1234"},
    )
    monkeypatch.setattr(
        wallet,
        "wallet_currency_data",
        lambda reader, root, currency_type, **_kwargs: {
            "exchange_currency": 36474,
            "currency_amount": 36474,
            "currency_borrow": 0,
            "cumulative_currency": 36474,
        },
    )


def test_wallet_snapshot_resolves_loaded_wallet_global_first(monkeypatch) -> None:
    _patch_common(monkeypatch)
    marker_calls: list[object] = []
    monkeypatch.setattr(
        wallet,
        "resolve_lua_global_manager_root",
        lambda *args, **kwargs: (0xABCD, True, 0x9999),
    )
    monkeypatch.setattr(
        wallet,
        "resolve_manager_root",
        lambda *args, **kwargs: marker_calls.append(kwargs),
    )

    snapshot = wallet.read_wallet_currency_snapshot(14)

    assert snapshot["exchange_currency"] == 36474
    assert snapshot["evidence"]["wallet_root_address"] == "0xabcd"
    assert snapshot["evidence"]["wallet_root_resolver"] == "lua_global"
    assert marker_calls == []


def test_wallet_snapshot_uses_marker_only_as_compatibility_fallback(monkeypatch) -> None:
    _patch_common(monkeypatch)

    def fail_global(*args, **kwargs):
        raise FanxiuRuntimeMemoryError("global unavailable")

    monkeypatch.setattr(wallet, "resolve_lua_global_manager_root", fail_global)
    monkeypatch.setattr(
        wallet,
        "resolve_manager_root",
        lambda *args, **kwargs: (0xBCDE, False),
    )

    snapshot = wallet.read_wallet_currency_snapshot(14, allow_discovery=True)

    assert snapshot["evidence"]["wallet_root_address"] == "0xbcde"
    assert snapshot["evidence"]["wallet_root_resolver"] == "constructor_marker"


def test_wallet_snapshot_does_not_scan_marker_when_currency_is_not_loaded(
    monkeypatch,
) -> None:
    _patch_common(monkeypatch)
    marker_calls: list[object] = []

    def fail_currency(*args, **kwargs):
        raise FanxiuRuntimeMemoryError("兑币类型 14 尚未同步到 Runtime")

    monkeypatch.setattr(wallet, "resolve_lua_global_manager_root", fail_currency)
    monkeypatch.setattr(
        wallet,
        "resolve_manager_root",
        lambda *args, **kwargs: marker_calls.append(kwargs),
    )

    try:
        wallet.read_wallet_currency_snapshot(14, allow_discovery=True)
    except FanxiuRuntimeMemoryError as exc:
        assert "尚未同步" in str(exc)
    else:  # pragma: no cover - the assertion above is the contract
        raise AssertionError("missing currency must fail closed")
    assert marker_calls == []


def test_wallet_missing_currency_reports_loaded_type_evidence(monkeypatch) -> None:
    class Reader:
        def fields(self, value):
            return value

        def dictionary_fields(self, value):
            return {
                1: {"type": 1},
                14.5: {"type": 14.5},
                40020: {"type": 40020},
            }

    manager = {
        "inst": {
            "Model": {
                "WalletData": {
                    "_WalletInfo": object(),
                }
            }
        }
    }
    monkeypatch.setattr(wallet, "manager_index_fields", lambda *_args: manager)

    try:
        wallet.wallet_currency_data(Reader(), 123, 14)
    except FanxiuRuntimeMemoryError as exc:
        message = str(exc)
    else:  # pragma: no cover - the assertion below is the contract
        raise AssertionError("missing currency must fail closed")

    assert "兑币类型 14 尚未同步到 Runtime" in message
    assert "已加载币种 2 项：1,40020" in message
    assert "键与 WalletVO.type 样本：1->1,40020->40020" in message


def test_wallet_missing_currency_can_use_the_client_zero_semantics(monkeypatch) -> None:
    class Reader:
        def fields(self, value):
            return value

        def dictionary_fields(self, value):
            return {1: {"type": 1}}

    manager = {"inst": {"Model": {"WalletData": {"_WalletInfo": object()}}}}
    monkeypatch.setattr(wallet, "manager_index_fields", lambda *_args: manager)

    assert wallet.wallet_currency_data(Reader(), 123, 14, missing_as_zero=True) == {
        "exchange_currency": 0,
        "currency_amount": 0,
        "currency_borrow": 0,
        "cumulative_currency": 0,
    }
