"""Read-only Runtime facts for Lilian events."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.core.fanxiu.catalog.lilian_event import (
    build_lilian_event_catalog,
    load_lilian_event_catalog,
)
from backend.core.fanxiu.catalog.lua_config import (
    _find_default_lang_path,
    load_fanxiu_lang_map,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import (
    _lua_addresses,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)
from backend.core.temp_paths import codeyun_temp_root


LILIAN_SUCCESS_ITEM_ID = 17003


def _lilian_partner_snapshot_cache_path() -> Path:
    return codeyun_temp_root("fanxiu-runtime-memory") / "lilian-partner-snapshot.json"


def _read_lilian_partner_snapshot_cache(
    memory: MumuProcessMemory,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(
            _lilian_partner_snapshot_cache_path().read_text(encoding="utf-8")
        )
        if (
            int(payload.get("pid") or 0) != memory.pid
            or int(payload.get("process_start_ticks") or 0)
            != memory.process_start_ticks
        ):
            return None
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("complete") is not True:
            return None
        return {
            **snapshot,
            "source": "runtime_memory_snapshot",
            "snapshot_cache_hit": True,
            "snapshot_age_seconds": max(
                0.0,
                time.time() - float(payload.get("updated_at") or 0),
            ),
        }
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_lilian_partner_snapshot_cache(
    memory: MumuProcessMemory,
    snapshot: dict[str, Any],
) -> None:
    path = _lilian_partner_snapshot_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "updated_at": time.time(),
                "snapshot": snapshot,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
def lilian_reward_is_success(
    rewards: Iterable[dict[str, Any]],
) -> bool:
    return any(
        int(item.get("code") or 0) == LILIAN_SUCCESS_ITEM_ID
        and int(item.get("amount") or 0) > 0
        for item in rewards
    )


_DB_METHODS = frozenset(
    {"DBMgr", "GetConfigTable", "GetConfigTableByIdWithLog", "Inst_get"}
)
_LILIAN_CONFIG_TABLES = {
    "event": "XianLvTravel.PartnerTrainEvent",
    "plot": "XianLvTravel.PartnerTrainEventPlot",
    "reward": "XianLvTravel.PartnerTrainReward",
    "check": "XianLvTravel.PartnerTrainCheck",
    "item": "Item.Item",
}
_LILIAN_CONFIG_FIELD_INDEXES = {
    "event": {
        "id": 1, "eventName": 2, "eventType": 3, "eventGroupId": 4,
        "areaIds": 6, "condition": 7, "spEventCondition": 8,
        "spEventConditionDes": 9, "spReward": 10,
    },
    "plot": {
        "id": 1, "eventGroupId": 2, "eventPlotType": 3,
        "checkGroupId": 4, "eventDes": 5, "winDes": 6,
        "winReward": 7, "loseDes": 8, "loseReward": 9,
    },
    "reward": {
        "id": 1, "rewardGroupId": 2, "condition": 3, "reward": 4,
    },
    "check": {
        "id": 1, "checkGroupId": 2, "checkCondition": 3,
        "battleScoreShow": 4,
    },
    "item": {"id": 1, "name": 2, "quality": 13},
}

_PARTNER_EXPLORE_METHODS = frozenset(
    {"LuaPartnerExploreMgr", "OpenExploreDispatchView"}
)
_PARTNER_CONFIG_FIELD_INDEXES = {
    "id": 1,
    "name": 2,
    "npcId": 3,
    "xianLvCareer": 27,
}
_NPC_CONFIG_FIELD_INDEXES = {"id": 1, "name": 2, "sex": 4}
_CAPTAIN_CAREER_TO_PARTNER_CAREER = {
    1: 4,  # 剑修
    2: 3,  # 法修
    3: 2,  # 魔修
    4: 1,  # 体修
}
_CAPTAIN_CAREER_LABELS = {1: "剑修", 2: "法修", 3: "魔修", 4: "体修"}
_SEX_LABELS = {1: "男性", 2: "女性"}


def _runtime_config_table(
    reader: LuaJitReader,
    db_root: int,
    name: str,
) -> dict[Any, Any]:
    manager = manager_index_fields(reader, db_root, _DB_METHODS)
    instance = reader.fields(manager.get("inst"))
    configs = reader.dictionary_fields(instance.get("ConfigDic"))
    table = reader.fields(configs.get(name))
    if not table:
        raise FanxiuRuntimeMemoryError(f"历练事件配置尚未加载：{name}")
    return table


def _runtime_array(reader: LuaJitReader, value: Any) -> list[Any]:
    if isinstance(value, LuaRef) and value.kind == "table":
        table = reader.table(value.address)
        array = [item for item in table.get("array", ()) if item is not None]
        if array:
            return array
        numeric = sorted(
            (
                (int(key), item)
                for key, item in table.get("fields", {}).items()
                if as_int(key) is not None
            ),
            key=lambda pair: pair[0],
        )
        return [item for _key, item in numeric]
    return []


def _runtime_config_rows(
    reader: LuaJitReader,
    db_root: int,
    name: str,
    fields: Iterable[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    table_key = next(
        (key for key, table_name in _LILIAN_CONFIG_TABLES.items() if table_name == name),
        "",
    )
    indexes = _LILIAN_CONFIG_FIELD_INDEXES.get(table_key, {})
    for row_key, value in _runtime_config_table(reader, db_root, name).items():
        raw = reader.fields(value)
        array = (
            list(reader.table(value.address).get("array", ()))
            if isinstance(value, LuaRef) and value.kind == "table"
            else []
        )
        row: dict[str, Any] = {"_row_key": as_int(row_key) or row_key}
        for field in fields:
            current = raw.get(field)
            index = indexes.get(field)
            if current is None and index is not None and index < len(array):
                current = array[index]
            if isinstance(current, LuaRef):
                row[field] = _runtime_array(reader, current)
            else:
                row[field] = current
        rows.append(row)
    return rows


def read_lilian_event_catalog_snapshot() -> dict[str, Any]:
    """Read every loaded choice event and reward from the live client DBMgr.

    This is strict external process-memory reading. It does not initialize a
    manager, execute Lua, attach Frida, or send a game/network command.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover()
        state_address = int(_lua_addresses(memory)["state"], 16)
        db_root, cache_hit, environment_address = resolve_lua_global_manager_root(
            memory,
            manager_key="lilian-event-db",
            state_address=state_address,
            global_name="DBMgr",
            required_methods=_DB_METHODS,
            validate=lambda reader, root: _runtime_config_table(
                reader, root, _LILIAN_CONFIG_TABLES["event"]
            ),
        )
        reader = LuaJitReader(memory)
        event_rows = _runtime_config_rows(
            reader,
            db_root,
            _LILIAN_CONFIG_TABLES["event"],
            (
                "id", "eventName", "eventType", "eventGroupId", "areaIds",
                "condition", "spEventCondition", "spEventConditionDes", "spReward",
            ),
        )
        plot_rows = _runtime_config_rows(
            reader,
            db_root,
            _LILIAN_CONFIG_TABLES["plot"],
            (
                "id", "eventGroupId", "eventPlotType", "eventDes", "winDes",
                "loseDes", "winReward", "loseReward", "checkGroupId",
            ),
        )
        reward_rows = _runtime_config_rows(
            reader,
            db_root,
            _LILIAN_CONFIG_TABLES["reward"],
            ("id", "rewardGroupId", "condition", "reward"),
        )
        unloaded_tables: list[str] = []
        try:
            check_rows = _runtime_config_rows(
                reader,
                db_root,
                _LILIAN_CONFIG_TABLES["check"],
                ("id", "checkGroupId", "checkCondition", "battleScoreShow"),
            )
        except FanxiuRuntimeMemoryError:
            check_rows = []
            unloaded_tables.append(_LILIAN_CONFIG_TABLES["check"])
        preliminary = build_lilian_event_catalog(
            event_rows, plot_rows, reward_rows, check_rows
        )
        item_ids = {
            int(item["item_id"])
            for event in preliminary.get("events") or []
            for choice in event.get("choices") or []
            for reward_key in ("win_rewards", "lose_rewards")
            for item in choice.get(reward_key) or []
        }


        item_rows: list[dict[str, Any]] = []
        try:
            item_table = _runtime_config_table(
                reader, db_root, _LILIAN_CONFIG_TABLES["item"]
            )
            for item_id in sorted(item_ids):
                value = item_table.get(item_id)
                raw = reader.fields(value)
                array = (
                    list(reader.table(value.address).get("array", ()))
                    if isinstance(value, LuaRef) and value.kind == "table"
                    else []
                )
                item_rows.append(
                    {
                        "id": item_id,
                        "name": raw.get("name") or (array[2] if len(array) > 2 else ""),
                        "quality": raw.get("quality") or (array[13] if len(array) > 13 else None),
                    }
                )
        except FanxiuRuntimeMemoryError:
            unloaded_tables.append(_LILIAN_CONFIG_TABLES["item"])
        runtime_result = build_lilian_event_catalog(
            event_rows, plot_rows, reward_rows, check_rows, item_rows
        )
        result = runtime_result
        static_signature_match: bool | None = None
        if unloaded_tables:
            static_result = load_lilian_event_catalog()

            def signature(catalog: dict[str, Any]) -> list[tuple[Any, ...]]:
                return sorted(
                    (
                        event.get("id"),
                        choice.get("id"),
                        choice.get("win_reward_group_id"),
                        choice.get("lose_reward_group_id"),
                        tuple(
                            (item.get("item_id"), item.get("amount"))
                            for item in choice.get("win_rewards") or []
                        ),
                        tuple(
                            (item.get("item_id"), item.get("amount"))
                            for item in choice.get("lose_rewards") or []
                        ),
                    )
                    for event in catalog.get("events") or []
                    for choice in event.get("choices") or []
                )

            static_signature_match = signature(runtime_result) == signature(static_result)
            if not static_signature_match:
                raise FanxiuRuntimeMemoryError(
                    "当前 Runtime 历练事件/选项/奖励与已导出判定配置版本不一致"
                )
            result = static_result
        result.update(
            {
                "available": True,
                "source": (
                    "runtime_memory"
                    if not unloaded_tables
                    else "runtime_memory+version_matched_parsed_config"
                ),
                "protocol": "DBMgr.ConfigDic[XianLvTravel.PartnerTrain*]",
                "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "captured_at_epoch": time.time(),
                "loaded_table_counts": {
                    "PartnerTrainEvent": len(event_rows),
                    "PartnerTrainEventPlot": len(plot_rows),
                    "PartnerTrainReward": len(reward_rows),
                    "PartnerTrainCheck": len(check_rows),
                },
                "runtime_loaded_complete": not unloaded_tables,
                "unloaded_runtime_tables": unloaded_tables,
                "parsed_config_signature_match": static_signature_match,
                "evidence": {
                    "pid": memory.pid,
                    "process_start_ticks": memory.process_start_ticks,
                    "state_address": f"0x{state_address:x}",
                    "environment_address": f"0x{environment_address:x}",
                    "db_root_address": f"0x{db_root:x}",
                    "db_root_cache_hit": cache_hit,
                },
                "elapsed_seconds": time.perf_counter() - started_at,
            }
        )
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": (
                str(exc)
                if isinstance(exc, FanxiuRuntimeMemoryError)
                else f"{type(exc).__name__}: {exc}"
            ),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


def _runtime_config_array(
    reader: LuaJitReader,
    table: dict[Any, Any],
    row_id: int,
    *,
    table_name: str,
) -> list[Any]:
    value = table.get(int(row_id))
    if not isinstance(value, LuaRef) or value.kind != "table":
        raise FanxiuRuntimeMemoryError(
            f"{table_name} 缺少 Runtime 行：{row_id}"
        )
    return list(reader.table(value.address).get("array", ()))


def _array_field(
    array: list[Any],
    indexes: dict[str, int],
    field: str,
    *,
    table_name: str,
) -> Any:
    index = indexes[field]
    if index >= len(array):
        raise FanxiuRuntimeMemoryError(
            f"{table_name}.{field} Runtime 数组越界：{len(array)}"
        )
    return array[index]


def read_lilian_partner_snapshot(*, force_refresh: bool = False) -> dict[str, Any]:
    """Read owned partners, sex and career from the live client.

    Only the already-loaded LuaJIT heap and generated-config tables are read.
    Names are resolved through the version-pinned exported localization table;
    no OCR, Lua execution, manager initialization or process injection is used.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        if not force_refresh:
            cached = _read_lilian_partner_snapshot_cache(memory)
            if cached is not None:
                cached["elapsed_seconds"] = time.perf_counter() - started_at
                return cached
        state_address = int(_lua_addresses(memory)["state"], 16)

        def validate_partner_manager(reader: LuaJitReader, root: int) -> None:
            manager = manager_index_fields(
                reader, root, _PARTNER_EXPLORE_METHODS
            )
            instance = reader.fields(manager.get("inst"))
            model = reader.fields(instance.get("Model"))
            data = reader.fields(model.get("Data"))
            if data.get("ActivePartnerList") is None:
                raise FanxiuRuntimeMemoryError(
                    "PartnerExploreMgr.ActivePartnerList 尚未加载"
                )

        partner_root, partner_cache_hit, _partner_environment = (
            resolve_lua_global_manager_root(
                memory,
                manager_key="probe-partner-explore",
                state_address=state_address,
                global_name="PartnerExploreMgr",
                required_methods=_PARTNER_EXPLORE_METHODS,
                validate=validate_partner_manager,
            )
        )
        reader = LuaJitReader(memory)
        partner_manager = manager_index_fields(
            reader, partner_root, _PARTNER_EXPLORE_METHODS
        )
        partner_instance = reader.fields(partner_manager.get("inst"))
        partner_model = reader.fields(partner_instance.get("Model"))
        partner_data = reader.fields(partner_model.get("Data"))
        active_items, active_count = reader.list_items(
            partner_data.get("ActivePartnerList")
        )

        db_root, db_cache_hit, _db_environment = resolve_lua_global_manager_root(
            memory,
            manager_key="lilian-event-db",
            state_address=state_address,
            global_name="DBMgr",
            required_methods=_DB_METHODS,
            validate=lambda current_reader, root: _runtime_config_table(
                current_reader, root, "Partner.Partner"
            ),
        )
        partner_configs = _runtime_config_table(
            reader, db_root, "Partner.Partner"
        )
        npc_configs = _runtime_config_table(reader, db_root, "Npc.Npc")

        lang_path = _find_default_lang_path()
        if lang_path is None:
            raise FanxiuRuntimeMemoryError("未找到当前导出版本的语言表")
        lang_map = load_fanxiu_lang_map(lang_path)

        partners: list[dict[str, Any]] = []
        for active_item in active_items:
            active_fields = reader.fields(active_item)
            partner_id = as_int(active_fields.get("id"))
            if partner_id is None:
                raise FanxiuRuntimeMemoryError("仙侣 Runtime 条目缺少 id")
            partner_array = _runtime_config_array(
                reader,
                partner_configs,
                partner_id,
                table_name="Partner.Partner",
            )
            runtime_id = as_int(
                _array_field(
                    partner_array,
                    _PARTNER_CONFIG_FIELD_INDEXES,
                    "id",
                    table_name="Partner.Partner",
                )
            )
            if runtime_id != partner_id:
                raise FanxiuRuntimeMemoryError(
                    f"Partner.Partner Runtime 行错位：{partner_id} != {runtime_id}"
                )
            name_key = as_int(
                _array_field(
                    partner_array,
                    _PARTNER_CONFIG_FIELD_INDEXES,
                    "name",
                    table_name="Partner.Partner",
                )
            )
            npc_id = as_int(
                _array_field(
                    partner_array,
                    _PARTNER_CONFIG_FIELD_INDEXES,
                    "npcId",
                    table_name="Partner.Partner",
                )
            )
            career = as_int(
                _array_field(
                    partner_array,
                    _PARTNER_CONFIG_FIELD_INDEXES,
                    "xianLvCareer",
                    table_name="Partner.Partner",
                )
            )
            if name_key is None or npc_id is None or career is None:
                raise FanxiuRuntimeMemoryError(
                    f"仙侣 {partner_id} 的名称/NPC/流派字段不完整"
                )
            npc_array = _runtime_config_array(
                reader, npc_configs, npc_id, table_name="Npc.Npc"
            )
            npc_runtime_id = as_int(
                _array_field(
                    npc_array,
                    _NPC_CONFIG_FIELD_INDEXES,
                    "id",
                    table_name="Npc.Npc",
                )
            )
            sex = as_int(
                _array_field(
                    npc_array,
                    _NPC_CONFIG_FIELD_INDEXES,
                    "sex",
                    table_name="Npc.Npc",
                )
            )
            name = str(lang_map.get(name_key) or "").strip()
            if npc_runtime_id != npc_id or not name or sex not in _SEX_LABELS:
                raise FanxiuRuntimeMemoryError(
                    f"仙侣 {partner_id} 的 Runtime 身份/名称/性别无法完整解析"
                )
            partners.append(
                {
                    "id": partner_id,
                    "name": name,
                    "name_key": name_key,
                    "npc_id": npc_id,
                    "sex": sex,
                    "sex_label": _SEX_LABELS[sex],
                    "career": career,
                }
            )

        complete = bool(partners) and (
            active_count is None or int(active_count) == len(partners)
        )
        if not complete:
            raise FanxiuRuntimeMemoryError(
                f"仙侣 Runtime 清单不完整：count={active_count}, rows={len(partners)}"
            )
        result = {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory+version_pinned_localization",
            "partners": partners,
            "partner_count": len(partners),
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "partner_root_cache_hit": partner_cache_hit,
                "db_root_cache_hit": db_cache_hit,
                "lang_path": str(lang_path),
            },
            "elapsed_seconds": time.perf_counter() - started_at,
        }
        _write_lilian_partner_snapshot_cache(memory, result)
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "reason": (
                str(exc)
                if isinstance(exc, FanxiuRuntimeMemoryError)
                else f"{type(exc).__name__}: {exc}"
            ),
            "partners": [],
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
            },
        }


def select_lilian_condition_partner(
    snapshot: dict[str, Any],
    special_condition: str,
) -> dict[str, Any] | None:
    """Choose any live owned partner satisfying a name/captain condition."""

    if snapshot.get("complete") is not True:
        raise FanxiuRuntimeMemoryError(
            f"历练仙侣清单不完整：{snapshot.get('reason') or 'unknown'}"
        )
    kind, separator, raw_value = str(special_condition or "").partition("|")
    if not separator:
        return None
    first_value = raw_value.split("_", 1)[0]
    try:
        condition_value = int(first_value)
    except ValueError as exc:
        raise FanxiuRuntimeMemoryError(
            f"无法解析历练特殊条件：{special_condition}"
        ) from exc
    partners = [
        dict(item)
        for item in snapshot.get("partners") or []
        if isinstance(item, dict)
    ]
    selected: dict[str, Any] | None = None
    role = "member"
    if kind == "IncludeXianLv":
        selected = next(
            (item for item in partners if as_int(item.get("id")) == condition_value),
            None,
        )
    elif kind == "CaptainSex":
        role = "captain"
        selected = next(
            (item for item in partners if as_int(item.get("sex")) == condition_value),
            None,
        )
    elif kind == "CaptainCareer":
        role = "captain"
        partner_career = _CAPTAIN_CAREER_TO_PARTNER_CAREER.get(condition_value)
        selected = next(
            (
                item
                for item in partners
                if as_int(item.get("career")) == partner_career
            ),
            None,
        )
    else:
        return None
    if selected is None:
        label = (
            _SEX_LABELS.get(condition_value)
            if kind == "CaptainSex"
            else _CAPTAIN_CAREER_LABELS.get(condition_value)
            if kind == "CaptainCareer"
            else str(condition_value)
        )
        raise FanxiuRuntimeMemoryError(
            f"当前已拥有仙侣中没有满足 {kind}={label} 的人选"
        )
    selected["selection_role"] = role
    selected["special_condition"] = special_condition
    return selected

__all__ = [
    "LILIAN_SUCCESS_ITEM_ID",
    "lilian_reward_is_success",
    "read_lilian_event_catalog_snapshot",
    "read_lilian_partner_snapshot",
    "select_lilian_condition_partner",
]
