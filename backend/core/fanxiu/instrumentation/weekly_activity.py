from __future__ import annotations

"""Strict read-only facts for the already-loaded weekly activity rewards."""

import time
from datetime import datetime
from typing import Any, Iterable

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
)


_ACTIVETASK_MARKER = b"LuaActivetaskMgr"
_ACTIVETASK_METHODS = frozenset({"LuaActivetaskMgr", "Inst_get"})
_WEEKLY_TYPE = 2

# Generate.Cfg.ActiveTasks.ActiveProgress, type=WeekendTask.  Runtime rows are
# checked against this exact contract before their state may authorize a claim.
WEEKLY_ACTIVITY_TIERS = (
    {"config_id": 13, "threshold": 400, "reward": ("Item|1010_50",)},
    {"config_id": 14, "threshold": 600, "reward": ("Item|1010_50",)},
    {"config_id": 15, "threshold": 800, "reward": ("Item|1010_50",)},
    {"config_id": 16, "threshold": 1200, "reward": ("Item|1010_100",)},
    {"config_id": 17, "threshold": 1600, "reward": ("Item|1010_100",)},
    {"config_id": 18, "threshold": 2000, "reward": ("Item|1010_150",)},
    {"config_id": 19, "threshold": 2400, "reward": ("Item|1010_200",)},
)


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value) if isinstance(value, LuaRef) and value.kind == "table" else {}


def _numeric_table_item(reader: LuaJitReader, value: Any, key: int) -> Any:
    if not isinstance(value, LuaRef) or value.kind != "table":
        return None
    table = reader.table(value.address)
    array = table.get("array") or []
    if 0 <= key < len(array) and array[key] is not None:
        return array[key]
    fields = table.get("fields") or {}
    return fields.get(key) or fields.get(float(key))


def _config_row(reader: LuaJitReader, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, LuaRef) or value.kind != "table":
        return None
    array = reader.table(value.address).get("array") or []
    if len(array) <= 3:
        return None
    config_id = as_int(array[1])
    task_type = as_int(array[2])
    threshold = as_int(array[3])
    if config_id is None or task_type is None or threshold is None:
        return None
    return {"config_id": config_id, "type": task_type, "threshold": threshold}


def _loaded_fields(reader: LuaJitReader, root_address: int) -> dict[str, Any]:
    manager = manager_index_fields(reader, root_address, _ACTIVETASK_METHODS)
    instance = _fields(reader, manager.get("inst"))
    if not instance:
        raise FanxiuRuntimeMemoryError("ActivetaskMgr 实例尚未加载", code="data_not_loaded")
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("ActivetaskData"))
    required = {"typeActiveProgressCfg", "allActiveBoxDic"}
    if not required.issubset(data):
        raise FanxiuRuntimeMemoryError("ActivetaskMgr 周常数据尚未加载", code="data_not_loaded")
    return data


def build_weekly_activity_snapshot(
    *,
    active_num: Any,
    all_active_num: Any,
    claimed_config_ids: Iterable[Any],
    runtime_tiers: Iterable[dict[str, Any]],
    claimed_declared_count: int | None,
) -> dict[str, Any]:
    """Validate and normalize one atomic weekly reward observation."""

    reasons: list[str] = []
    expected_identity = [
        (tier["config_id"], tier["threshold"]) for tier in WEEKLY_ACTIVITY_TIERS
    ]
    observed_identity = sorted(
        (
            (as_int(row.get("config_id")), as_int(row.get("threshold")))
            for row in runtime_tiers
            if as_int(row.get("type")) == _WEEKLY_TYPE
        ),
        key=lambda item: ((item[1] or -1), (item[0] or -1)),
    )
    # ``typeActiveProgressCfg`` is a derived client cache.  It may legitimately
    # be empty while the generated ActiveProgress config and the server-backed
    # ``allActiveBoxDic`` are already usable.  A non-empty cache is useful
    # cross-evidence, but a partial/different cache must still fail closed.
    tier_authority = "runtime_cross_checked" if observed_identity else "static_generated_config"
    if observed_identity and observed_identity != expected_identity:
        reasons.append("weekly_tier_contract_mismatch")

    active = as_int(active_num)
    all_active = as_int(all_active_num)
    if active is None or active < 0:
        reasons.append("invalid_active_num")
    if all_active is None or all_active < 0:
        reasons.append("invalid_all_active_num")

    claimed_raw = list(claimed_config_ids)
    claimed = [as_int(value) for value in claimed_raw]
    if any(value is None for value in claimed):
        reasons.append("invalid_claimed_config_id")
    claimed_ids = [value for value in claimed if value is not None]
    if claimed_declared_count is None or claimed_declared_count != len(claimed_raw):
        reasons.append("claimed_count_mismatch")
    if len(set(claimed_ids)) != len(claimed_ids):
        reasons.append("duplicate_claimed_config_id")
    expected_ids = {tier["config_id"] for tier in WEEKLY_ACTIVITY_TIERS}
    if not set(claimed_ids).issubset(expected_ids):
        reasons.append("unknown_claimed_config_id")

    complete = not reasons
    claimed_set = set(claimed_ids) if complete else set()
    claimed_thresholds = [
        tier["threshold"] for tier in WEEKLY_ACTIVITY_TIERS if tier["config_id"] in claimed_set
    ]
    claimable = [
        {"config_id": tier["config_id"], "threshold": tier["threshold"]}
        for tier in WEEKLY_ACTIVITY_TIERS
        if complete and tier["threshold"] <= active and tier["config_id"] not in claimed_set
    ]
    if not complete:
        status = "ambiguous"
    elif claimable:
        status = "claimable"
    elif len(claimed_set) == len(WEEKLY_ACTIVITY_TIERS):
        status = "already_claimed"
    else:
        status = "pending_threshold"

    return {
        "complete": complete,
        "status": status,
        "reason": ",".join(reasons),
        "tier_authority": tier_authority,
        "active_num": active,
        "all_active_num": all_active,
        "tiers": [dict(tier) for tier in WEEKLY_ACTIVITY_TIERS],
        "thresholds": [tier["threshold"] for tier in WEEKLY_ACTIVITY_TIERS],
        "claimed_config_ids": sorted(claimed_ids),
        "claimed_thresholds": claimed_thresholds,
        "claimable": claimable,
        "claimable_config_ids": [item["config_id"] for item in claimable],
        "claimable_thresholds": [item["threshold"] for item in claimable],
    }


def read_weekly_activity_snapshot() -> dict[str, Any]:
    """Read loaded weekly reward facts without calling ``Inst_get`` or sending packets."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="weekly-activity",
            marker=_ACTIVETASK_MARKER,
            required_methods=_ACTIVETASK_METHODS,
            validate=lambda reader, address: _loaded_fields(reader, address),
        )
        reader = LuaJitReader(memory)
        data = _loaded_fields(reader, root)

        weekly_cfg = _numeric_table_item(reader, data["typeActiveProgressCfg"], _WEEKLY_TYPE)
        cfg_values, cfg_declared_count = reader.list_items(weekly_cfg)
        runtime_tiers = [row for value in cfg_values if (row := _config_row(reader, value))]
        if cfg_declared_count is not None and cfg_declared_count != len(cfg_values):
            raise FanxiuRuntimeMemoryError("周常奖励配置列表计数不一致", code="snapshot_incomplete")

        weekly_info = reader.dictionary_fields(data["allActiveBoxDic"]).get(_WEEKLY_TYPE)
        if weekly_info is None:
            weekly_info = reader.dictionary_fields(data["allActiveBoxDic"]).get(float(_WEEKLY_TYPE))
        info = _fields(reader, weekly_info)
        if not {"type", "activeNum", "receiveBoxList", "allActiveNum"}.issubset(info):
            raise FanxiuRuntimeMemoryError("周常奖励服务器状态尚未加载", code="data_not_loaded")
        claimed_values, claimed_count = reader.list_items(info["receiveBoxList"])
        result = build_weekly_activity_snapshot(
            active_num=info["activeNum"],
            all_active_num=info["allActiveNum"],
            claimed_config_ids=claimed_values,
            runtime_tiers=runtime_tiers,
            claimed_declared_count=claimed_count,
        )
        return {
            "ok": result["complete"],
            "available": True,
            "source": "runtime_memory",
            "protocol": "ActivetaskMgr.Model.ActivetaskData.allActiveBoxDic[2]",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **result,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_address": f"0x{root:x}",
                "root_cache_hit": cache_hit,
                "runtime_tier_count": len(runtime_tiers),
                "runtime_tier_declared_count": cfg_declared_count,
                "claimed_declared_count": claimed_count,
            },
        }
    except Exception as exc:
        reason = str(exc)
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "status": "unavailable",
            "reason": reason,
            "tier_authority": "static_generated_config",
            "tiers": [dict(tier) for tier in WEEKLY_ACTIVITY_TIERS],
            "thresholds": [tier["threshold"] for tier in WEEKLY_ACTIVITY_TIERS],
            "claimed_config_ids": [],
            "claimed_thresholds": [],
            "claimable": [],
            "claimable_config_ids": [],
            "claimable_thresholds": [],
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
            },
        }
