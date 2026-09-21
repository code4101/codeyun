from __future__ import annotations

import pytest

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
        "discover",
        lambda **_kwargs: _Memory(),
    )
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


def test_wallet_snapshot_unmapped_retries_fresh_maps_without_marker(monkeypatch) -> None:
    """A stale mapping must refresh /proc/maps instead of masking the cause."""
    cached_calls: list[int] = []
    discover_calls: list[int] = []
    marker_calls: list[object] = []

    monkeypatch.setattr(
        wallet.MumuProcessMemory,
        "discover_cached",
        lambda **_kwargs: cached_calls.append(1) or _Memory(),
    )
    monkeypatch.setattr(
        wallet.MumuProcessMemory,
        "discover",
        lambda **_kwargs: discover_calls.append(1) or _Memory(),
    )
    monkeypatch.setattr(wallet, "LuaJitReader", lambda memory: object())
    monkeypatch.setattr(
        redbag_runtime_loader,
        "_lua_addresses",
        lambda memory: {"state": "0x1234"},
    )

    def fail_global(*args, **kwargs):
        raise FanxiuRuntimeMemoryError(
            "Runtime 内存地址越界：0x1+8", code="memory_address_unmapped"
        )

    monkeypatch.setattr(wallet, "resolve_lua_global_manager_root", fail_global)
    monkeypatch.setattr(
        wallet,
        "resolve_manager_root",
        lambda *args, **kwargs: marker_calls.append(kwargs) or (0xBEEF, False),
    )

    with pytest.raises(FanxiuRuntimeMemoryError) as excinfo:
        wallet.read_wallet_currency_snapshot(14)

    assert excinfo.value.code == "memory_address_unmapped"
    assert cached_calls == [1]
    assert discover_calls == [1]
    assert marker_calls == []


@pytest.mark.parametrize("allow_discovery", [False, True])
def test_wallet_snapshot_retry_keeps_allow_discovery(monkeypatch, allow_discovery) -> None:
    calls: list[dict[str, object]] = []

    def fake_helper(currency_type, **kwargs):
        calls.append({"currency_type": currency_type, **kwargs})
        if len(calls) == 1:
            raise FanxiuRuntimeMemoryError(
                "Runtime 内存地址越界：0x1+8", code="memory_address_unmapped"
            )
        return {"source": "runtime_memory"}

    monkeypatch.setattr(wallet, "_read_wallet_currency_snapshot", fake_helper)

    result = wallet.read_wallet_currency_snapshot(
        14, allow_discovery=allow_discovery, missing_as_zero=True
    )

    assert result == {"source": "runtime_memory"}
    assert len(calls) == 2
    assert "refresh_process" not in calls[0]
    assert calls[1] == {
        "currency_type": 14,
        "allow_discovery": allow_discovery,
        "missing_as_zero": True,
        "refresh_process": True,
    }


@pytest.mark.parametrize(
    "code", ["root_cache_miss", "process_cache_miss", "memory_read_failed"]
)
def test_wallet_snapshot_does_not_retry_other_error_codes(monkeypatch, code) -> None:
    calls: list[dict[str, object]] = []

    def fake_helper(currency_type, **kwargs):
        calls.append({"currency_type": currency_type, **kwargs})
        raise FanxiuRuntimeMemoryError("wallet read failed", code=code)

    monkeypatch.setattr(wallet, "_read_wallet_currency_snapshot", fake_helper)

    with pytest.raises(FanxiuRuntimeMemoryError) as excinfo:
        wallet.read_wallet_currency_snapshot(14, allow_discovery=True)

    assert excinfo.value.code == code
    assert len(calls) == 1


def test_wallet_snapshot_second_unmapped_failure_propagates_directly(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_helper(currency_type, **kwargs):
        calls.append({"currency_type": currency_type, **kwargs})
        raise FanxiuRuntimeMemoryError(
            "Runtime 内存地址越界：0x2+8", code="memory_address_unmapped"
        )

    monkeypatch.setattr(wallet, "_read_wallet_currency_snapshot", fake_helper)

    with pytest.raises(FanxiuRuntimeMemoryError) as excinfo:
        wallet.read_wallet_currency_snapshot(14, allow_discovery=True)

    assert excinfo.value.code == "memory_address_unmapped"
    assert len(calls) == 2
    assert calls[1]["refresh_process"] is True
    assert calls[1]["allow_discovery"] is True


@pytest.mark.parametrize("target_present", [True, False])
def test_wallet_success_does_not_decode_unrelated_currency(monkeypatch, target_present):
    """A targeted observation cannot fail on an unrelated VO's unreadable fields."""
    unrelated = object()
    entries = {1: unrelated}
    if target_present:
        entries[14] = {"type": 14, "amount": 90, "borrow": 7, "history": 123}

    class Reader:
        def fields(self, value):
            if value is unrelated:
                pytest.fail("successful target read decoded unrelated diagnostic VO")
            return value

        def dictionary_fields(self, value):
            return entries

        def long(self, value):
            return value

    manager = {"inst": {"Model": {"WalletData": {"_WalletInfo": object()}}}}
    monkeypatch.setattr(wallet, "manager_index_fields", lambda *_args: manager)
    result = wallet.wallet_currency_data(Reader(), 123, 14, missing_as_zero=not target_present)
    assert result == {
        "exchange_currency": 83 if target_present else 0,
        "currency_amount": 90 if target_present else 0,
        "currency_borrow": 7 if target_present else 0,
        "cumulative_currency": 123 if target_present else 0,
    }
