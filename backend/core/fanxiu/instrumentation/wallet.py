from __future__ import annotations

from datetime import datetime
import logging
import time
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)


WALLET_MARKER = b"LuaWalletMgr"
WALLET_METHODS = frozenset({"LuaWalletMgr", "Inst_get"})
_LOGGER = logging.getLogger(__name__)


def _required_fields(
    reader: LuaJitReader,
    value: Any,
    names: tuple[str, ...],
    context: str,
) -> dict[Any, Any]:
    fields = reader.fields(value)
    missing = [name for name in names if name not in fields]
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"{context} 尚未初始化，缺少字段：{','.join(missing)}"
        )
    return fields


def wallet_currency_data(
    reader: LuaJitReader,
    root_address: int,
    currency_type: int,
    *,
    missing_as_zero: bool = False,
) -> dict[str, int]:
    """Read one currency from the game's already initialized wallet model."""

    manager = manager_index_fields(reader, root_address, WALLET_METHODS)
    instance = _required_fields(reader, manager.get("inst"), ("Model",), "钱包管理器")
    model = _required_fields(reader, instance["Model"], ("WalletData",), "钱包模型")
    data = _required_fields(reader, model["WalletData"], ("_WalletInfo",), "钱包数据")
    wallet_dictionary = reader.dictionary_fields(data["_WalletInfo"])
    loaded_currency_types = sorted(
        {
            int(currency_key)
            for key in wallet_dictionary
            if (currency_key := as_int(key)) is not None
        }
    )
    loaded_type_pairs: list[str] = []
    for key, value in wallet_dictionary.items():
        key_type = as_int(key)
        if key_type is None:
            continue
        value_type = as_int(reader.fields(value).get("type"))
        loaded_type_pairs.append(f"{int(key_type)}->{value_type}")
        if len(loaded_type_pairs) >= 16:
            break
    wallet_value = next(
        (
            value
            for key, value in wallet_dictionary.items()
            if as_int(key) == int(currency_type)
        ),
        None,
    )
    if wallet_value is None:
        # WalletData.GetCurrencyByType returns zero when its already-loaded
        # dictionary has no entry.  Some event currencies are omitted at an
        # exact zero balance, so consumers that explicitly need the client's
        # zero semantics may opt in without pretending an absent WalletData
        # model is initialized.
        if missing_as_zero:
            return {
                "exchange_currency": 0,
                "currency_amount": 0,
                "currency_borrow": 0,
                "cumulative_currency": 0,
            }
        preview = ",".join(str(value) for value in loaded_currency_types[:64])
        suffix = (
            f"；已加载币种 {len(loaded_currency_types)} 项：{preview}"
            f"；键与 WalletVO.type 样本：{','.join(loaded_type_pairs)}"
            if loaded_currency_types
            else "；钱包字典当前为空"
        )
        raise FanxiuRuntimeMemoryError(
            f"兑币类型 {int(currency_type)} 尚未同步到 Runtime{suffix}"
        )
    wallet = _required_fields(
        reader,
        wallet_value,
        ("amount", "borrow", "history", "type"),
        f"兑币类型 {int(currency_type)}",
    )
    actual_type = as_int(wallet.get("type"))
    amount = reader.long(wallet.get("amount"))
    borrow = reader.long(wallet.get("borrow"))
    history = reader.long(wallet.get("history"))
    if actual_type != int(currency_type) or None in (amount, borrow, history):
        raise FanxiuRuntimeMemoryError(f"兑币类型 {int(currency_type)} 数据校验失败")
    return {
        "exchange_currency": int(amount) - int(borrow),
        "currency_amount": int(amount),
        "currency_borrow": int(borrow),
        "cumulative_currency": int(history),
    }




def read_wallet_currency_snapshot(
    currency_type: int,
    *,
    allow_discovery: bool = False,
    missing_as_zero: bool = False,
) -> dict[str, Any]:
    """Read a fresh currency snapshot without invoking Lua or network commands."""

    started_at = time.perf_counter()
    phase_started_at = started_at
    phase_seconds: dict[str, float] = {}
    memory = (
        MumuProcessMemory.discover()
        if allow_discovery
        else MumuProcessMemory.discover_cached(fallback_to_discovery=False)
    )
    phase_seconds["process_discovery"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "wallet-runtime phase=process_discovery elapsed=%.3fs pid=%s",
        phase_seconds["process_discovery"],
        memory.pid,
    )
    reader = LuaJitReader(memory)
    # ``WalletMgr`` is the actual loaded global name.  The marker belongs to
    # its constructor method (``LuaWalletMgr``), so marker discovery is only a
    # compatibility fallback and must not be the primary resolution path.
    from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
        _lua_addresses,
    )

    phase_started_at = time.perf_counter()
    lua_state_address = int(_lua_addresses(memory)["state"], 16)
    phase_seconds["lua_state"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "wallet-runtime phase=lua_state elapsed=%.3fs",
        phase_seconds["lua_state"],
    )
    resolver = "lua_global"
    phase_started_at = time.perf_counter()
    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key=f"wallet-currency-{int(currency_type)}",
            state_address=lua_state_address,
            global_name="WalletMgr",
            required_methods=WALLET_METHODS,
            validate=lambda current_reader, address: wallet_currency_data(
                current_reader, address, int(currency_type), missing_as_zero=missing_as_zero
            ),
        )
    except FanxiuRuntimeMemoryError as exc:
        # A resolved WalletMgr without this concrete WalletVO is an
        # authoritative not-loaded result. Re-scanning the same process for a
        # constructor marker cannot make the missing entry appear and turns a
        # cheap failure into a long probe.
        if f"兑币类型 {int(currency_type)}" in str(exc):
            phase_seconds["wallet_manager"] = (
                time.perf_counter() - phase_started_at
            )
            _LOGGER.info(
                "wallet-runtime phase=wallet_manager resolver=%s "
                "elapsed=%.3fs result=currency_not_loaded",
                resolver,
                phase_seconds["wallet_manager"],
            )
            raise
        resolver = "constructor_marker"
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key=f"wallet-currency-{int(currency_type)}-marker",
            marker=WALLET_MARKER,
            required_methods=WALLET_METHODS,
            validate=lambda current_reader, address: wallet_currency_data(
                current_reader, address, int(currency_type), missing_as_zero=missing_as_zero
            ),
            allow_discovery=allow_discovery,
        )
    phase_seconds["wallet_manager"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "wallet-runtime phase=wallet_manager resolver=%s elapsed=%.3fs",
        resolver,
        phase_seconds["wallet_manager"],
    )
    phase_started_at = time.perf_counter()
    currency_data = wallet_currency_data(
        reader, root, int(currency_type), missing_as_zero=missing_as_zero
    )
    phase_seconds["wallet_vo"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "wallet-runtime phase=wallet_vo elapsed=%.3fs",
        phase_seconds["wallet_vo"],
    )
    return {
        **currency_data,
        "currency_type": int(currency_type),
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "runtime_memory",
        "currency_derivation": "wallet_amount_minus_borrow",
        "elapsed_seconds": time.perf_counter() - started_at,
        "phase_seconds": phase_seconds,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "wallet_root_address": f"0x{root:x}",
            "wallet_root_cache_hit": cache_hit,
            "wallet_root_resolver": resolver,
        },
    }
