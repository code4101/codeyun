from __future__ import annotations

"""Strict read-only Runtime projection for the shared activity-rank gift page.

The native ``ActivityRankGiftView`` merges ``ACTIVITY_GIFT`` and
``ACTIVITY_FREE_GIFT`` rows from ``ChargeMgr``.  A row is free only when the
server configuration has neither a cash ``payId`` nor an item ``costs``
value.  Purchase counts are read from the matching user VO and are the
authoritative idempotency fact.
"""

from datetime import datetime
import time
from typing import Any, Iterable

from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)


CHARGE_METHODS = frozenset({"Inst_get", "LuaChargeMgr"})
ACTIVITY_GIFT_KEY = "ACTIVITY_GIFT"
ACTIVITY_FREE_GIFT_KEY = "ACTIVITY_FREE_GIFT"


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    if value is None:
        return {}
    try:
        return reader.fields(value)
    except Exception:
        return {}


def _list_values(reader: LuaJitReader, value: Any) -> list[Any]:
    if value is None:
        return []
    values, declared = reader.list_items(value)
    if declared is None or declared < 0 or len(values) != declared:
        raise FanxiuRuntimeMemoryError("活动礼包 Runtime 列表长度不完整")
    return list(values)


def _charge_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = _fields(
        reader,
        manager_index_fields(reader, root_address, CHARGE_METHODS).get("inst"),
    )
    model = _fields(reader, manager.get("Model"))
    data = _fields(reader, model.get("ChargeData"))
    if "_CycleGiftDicFunc" not in data:
        raise FanxiuRuntimeMemoryError("ChargeMgr 尚未初始化活动礼包数据")
    return data


def _dictionary_value(
    reader: LuaJitReader,
    dictionary: Any,
    wanted_key: str,
) -> Any:
    for key, value in reader.dictionary_fields(dictionary).items():
        if str(key) == wanted_key:
            return value
    return None


def _string_value(reader: LuaJitReader, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, LuaRef):
        try:
            return str(reader.string(value.address) or "")
        except Exception:
            return ""
    return str(value)


def _activity_gift_rows(
    reader: LuaJitReader,
    data: dict[Any, Any],
    *,
    expected_activity_ids: set[int] | None,
) -> list[dict[str, Any]]:
    cycle_dictionary = data["_CycleGiftDicFunc"]
    rows: list[dict[str, Any]] = []
    for source_key in (ACTIVITY_GIFT_KEY, ACTIVITY_FREE_GIFT_KEY):
        group = _fields(
            reader,
            _dictionary_value(reader, cycle_dictionary, source_key),
        )
        if not group:
            continue
        bought_times: dict[int, int] = {}
        for raw_user in _list_values(reader, group.get("giftUserVOs")):
            user = _fields(reader, raw_user)
            gift_id = as_int(user.get("id"))
            times = as_int(user.get("times"))
            if gift_id is not None and gift_id > 0:
                bought_times[gift_id] = max(0, int(times or 0))
        for raw_config in _list_values(reader, group.get("giftConfVOs")):
            config = _fields(reader, raw_config)
            gift_id = as_int(config.get("id"))
            activity_id = as_int(config.get("activityId"))
            if gift_id is None or gift_id <= 0 or activity_id is None or activity_id <= 0:
                continue
            if expected_activity_ids is not None and activity_id not in expected_activity_ids:
                continue
            pay_id = as_int(config.get("payId")) or 0
            costs = _string_value(reader, config.get("costs")).strip()
            limit_times = as_int(config.get("times"))
            if limit_times is None or limit_times < 0:
                raise FanxiuRuntimeMemoryError(
                    f"活动礼包 {gift_id} 的限购次数无效：{limit_times}"
                )
            purchased = bought_times.get(gift_id, 0)
            if purchased > limit_times:
                raise FanxiuRuntimeMemoryError(
                    f"活动礼包 {gift_id} 已购次数超过配置：{purchased}/{limit_times}"
                )
            is_free = pay_id == 0 and not costs
            remaining = max(0, limit_times - purchased)
            rows.append(
                {
                    "id": gift_id,
                    "activity_id": activity_id,
                    "source_kind": source_key,
                    "title": _string_value(reader, config.get("title")),
                    "reward": _string_value(reader, config.get("reward")),
                    "pay_id": pay_id,
                    "costs": costs,
                    "gift_type": _string_value(reader, config.get("giftType")),
                    "show_condition": _string_value(
                        reader, config.get("showCondition")
                    ),
                    "hide_condition": _string_value(
                        reader, config.get("hideCondition")
                    ),
                    "buy_condition": _string_value(
                        reader, config.get("buyCondition")
                    ),
                    "sort": as_int(config.get("sort")) or 0,
                    "limit_times": limit_times,
                    "purchased_times": purchased,
                    "remaining_times": remaining,
                    "is_free": is_free,
                    "claimable": is_free and remaining > 0,
                }
            )
    rows.sort(key=lambda row: (int(row["activity_id"]), int(row["id"])))
    return rows


def read_activity_gift_runtime_snapshot(
    activity_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Read already loaded activity-gift rows without invoking game methods."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        expected = (
            {int(value) for value in activity_ids if int(value) > 0}
            if activity_ids is not None
            else None
        )
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, environment = resolve_lua_global_manager_root(
            memory,
            manager_key="activity-gift-charge",
            state_address=state_address,
            global_name="ChargeMgr",
            required_methods=CHARGE_METHODS,
            validate=_charge_data_fields,
            force_refresh=False,
        )
        reader = LuaJitReader(memory)
        rows = _activity_gift_rows(
            reader,
            _charge_data_fields(reader, root),
            expected_activity_ids=expected,
        )
        if expected is not None:
            loaded = {int(row["activity_id"]) for row in rows}
            missing = sorted(expected - loaded)
            if missing:
                raise FanxiuRuntimeMemoryError(
                    f"目标活动礼包尚未加载：{','.join(map(str, missing))}"
                )
        free_rows = [row for row in rows if row["is_free"]]
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory.charge.activity_gift",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "items": rows,
            "item_count": len(rows),
            "free_items": free_rows,
            "free_item_count": len(free_rows),
            # ChargeMgr keeps multiple condition-gated configurations for the
            # same activity.  These are balance candidates, not proof that a
            # row is currently rendered by GameUtil.CheckCondition.
            "active_filter_applied": False,
            "remaining_free_candidate_ids": [
                int(row["id"]) for row in free_rows if row["claimable"]
            ],
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_cache_hit": cache_hit,
                "lua_environment": environment,
                "read_only": True,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory.charge.activity_gift",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "read_only": True,
            },
        }


__all__ = [
    "ACTIVITY_FREE_GIFT_KEY",
    "ACTIVITY_GIFT_KEY",
    "CHARGE_METHODS",
    "read_activity_gift_runtime_snapshot",
]
