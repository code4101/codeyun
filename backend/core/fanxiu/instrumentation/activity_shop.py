from __future__ import annotations

"""Reusable dynamic collector for condition-filtered activity shops."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
import logging
import re
import struct
import time
from typing import Any

from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    lua_jit_intern_state,
    _nearby_lua_heap_regions,
    _valid_lua_string_addresses,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
    table_ref,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses


_EQUAL_CROSS_GROUP_RE = re.compile(r"(?:^|[,;])EqualCrossGroup\|(\d+)_(\d+)(?=$|[,;])")
_CONFIG_MARKER = b"V_ShopCfg"
_ACTIVITY_TEMPLATE_MAIN_VIEW_ID = 114202
_CONFIG_VALUE_COUNT = 17
_ROW_PRICE = 6
_ROW_CURRENCY = 7
_ROW_LIMIT_TIMES = 9
_ROW_SHOW_LIMIT = 11
_ROW_DISAPPEAR_LIMIT = 12
_ROW_POSITION = 13
_ROW_DISCOUNT = 18
_ROW_ORIGINAL_PRICE = 19
_ACTIVITY_MANAGER_METHODS = frozenset(
    {"Inst_get", "LuaActivityMgr", "GetOpenServerTime"}
)
_LOGGER = logging.getLogger(__name__)


def _canonical_shop_row(
    values: Sequence[Any],
    *,
    shop_base_id: int,
    currency_type: int | None = None,
) -> tuple[list[Any], str] | None:
    """Normalize compact and reward-bearing ActivityShop row layouts.

    Current generated config reserves ``reward`` at 6 and places
    price/currency at 7/8.  Older compact snapshots omit that field and use
    6/7.  Internally keep the compact layout so existing Xutian/Yunmeng field
    semantics remain unchanged.
    """

    raw = list(values)
    if len(raw) < _CONFIG_VALUE_COUNT or as_int(raw[3]) != int(shop_base_id):
        return None
    augmented = (
        raw[6] is None
        and isinstance(raw[7], (int, float))
        and isinstance(raw[8], (int, float))
        and (currency_type is None or as_int(raw[8]) == int(currency_type))
    )
    legacy = (
        isinstance(raw[6], (int, float))
        and isinstance(raw[7], (int, float))
        and (currency_type is None or as_int(raw[7]) == int(currency_type))
    )
    if augmented == legacy:
        return None
    if augmented:
        raw.pop(6)
        return raw, "reward"
    return raw, "compact"


class FanxiuActivityShopCollectionError(RuntimeError):
    pass


class FanxiuActivityShopNotLoadedError(FanxiuActivityShopCollectionError):
    """Raised when the current process has not loaded the shop config yet."""


def _activity_shop_data(
    reader: LuaJitReader,
    manager_root: int,
) -> tuple[dict[Any, Any], int]:
    manager_fields = manager_index_fields(
        reader, manager_root, _ACTIVITY_MANAGER_METHODS
    )
    instance = table_ref(manager_fields.get("inst"))
    if instance is None:
        raise FanxiuActivityShopNotLoadedError("ActivityMgr 实例尚未加载")
    model = table_ref(reader.table(instance.address)["fields"].get("Model"))
    if model is None:
        raise FanxiuActivityShopNotLoadedError("ActivityMgr.Model 尚未加载")
    shop_data = table_ref(
        reader.table(model.address)["fields"].get("ActivityShopData")
    )
    if shop_data is None:
        raise FanxiuActivityShopNotLoadedError("ActivityShopData 尚未加载")
    fields = reader.table(shop_data.address)["fields"]
    if table_ref(fields.get("V_ShopCfg")) is None:
        raise FanxiuActivityShopNotLoadedError("游戏尚未加载 V_ShopCfg")
    return fields, shop_data.address


def _validate_activity_manager(reader: LuaJitReader, manager_root: int) -> None:
    _activity_shop_data(reader, manager_root)


def _resolve_activity_manager(
    memory: MumuProcessMemory,
) -> tuple[int, bool, str]:
    """Prefer the loaded main-Lua global; marker scan is compatibility only."""

    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="activity-manager-for-shop",
            state_address=int(_lua_addresses(memory)["state"], 16),
            global_name="ActivityMgr",
            required_methods=_ACTIVITY_MANAGER_METHODS,
            # Manager identity is the fast-path contract. Whether the
            # concrete shop projection is loaded is checked by the caller;
            # an unloaded V_ShopCfg must not trigger a full marker scan.
            validate=lambda _reader, _address: None,
        )
        return root, cache_hit, "lua_global"
    except FanxiuRuntimeMemoryError:
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="activity-manager-for-shop",
            marker=b"LuaActivityMgr",
            required_methods=_ACTIVITY_MANAGER_METHODS,
            validate=_validate_activity_manager,
        )
        return root, cache_hit, "constructor_marker"


def _read_activity_shop_purchase_counts(
    reader: LuaJitReader,
    shop_data_fields: Mapping[Any, Any],
    groups: Sequence[Sequence[Sequence[Any]]],
) -> tuple[dict[int, int], dict[str, Any]]:
    """Read the complete server purchase ledger for the active shop rows.

    ``ActivityShopData.V_ShopInfo`` is the server projection keyed by concrete
    shop config id. It contains entries only after a row has been purchased;
    for finite-limit rows, absence is therefore zero after the complete
    dictionary has been decoded. A displayed goods group may aggregate several
    config rows, so its purchased count is the sum of those concrete rows.
    Unlimited config ids are reused across occurrences and are deliberately
    excluded from occurrence purchase progress.
    """

    shop_info = table_ref(shop_data_fields.get("V_ShopInfo"))
    if shop_info is None:
        raise FanxiuActivityShopNotLoadedError("游戏尚未加载 V_ShopInfo")
    server_rows = reader.dictionary_fields(shop_info)
    decoded_by_config_id: dict[int, int] = {}
    for raw_key, raw_value in server_rows.items():
        config_id = as_int(raw_key)
        if config_id is None:
            raise FanxiuActivityShopCollectionError(
                "V_ShopInfo 含非数字商店配置 id"
            )
        info_ref = table_ref(raw_value)
        if info_ref is None:
            raise FanxiuActivityShopCollectionError(
                f"V_ShopInfo[{config_id}] 不是有效服务端购买记录"
            )
        info_fields = reader.table(info_ref.address)["fields"]
        config_ref = table_ref(info_fields.get("cfg"))
        server_ref = table_ref(info_fields.get("svr"))
        if config_ref is None or server_ref is None:
            raise FanxiuActivityShopCollectionError(
                f"V_ShopInfo[{config_id}] 缺少 cfg/svr"
            )
        config_values = reader.table(config_ref.address)["array"]
        server_fields = reader.table(server_ref.address)["fields"]
        config_row_id = as_int(_row_value(config_values, 1))
        server_row_id = as_int(server_fields.get("shopItemId"))
        purchased_count = as_int(server_fields.get("num"))
        if (
            config_row_id != config_id
            or server_row_id != config_id
            or purchased_count is None
            or purchased_count < 0
        ):
            raise FanxiuActivityShopCollectionError(
                f"V_ShopInfo[{config_id}] 身份或购买数量校验失败"
            )
        decoded_by_config_id[config_id] = purchased_count

    purchase_counts: dict[int, int] = {}
    active_config_ids: list[int] = []
    active_limited_config_ids: list[int] = []
    ignored_unlimited_records: dict[str, int] = {}
    for group in groups:
        goods_id = int(group[0][1])
        config_ids = [int(row[1]) for row in group]
        active_config_ids.extend(config_ids)
        limits = [
            int(row[_ROW_LIMIT_TIMES])
            if row[_ROW_LIMIT_TIMES] is not None
            else -1
            for row in group
        ]
        if any(limit < 0 for limit in limits):
            # Unlimited config ids are reused across activity occurrences. Their
            # server ``num`` is a cross-period counter, so it must never reduce
            # this occurrence's target budget.
            purchase_counts[goods_id] = 0
            ignored_unlimited_records.update({
                str(config_id): decoded_by_config_id[config_id]
                for config_id in config_ids
                if decoded_by_config_id.get(config_id, 0) > 0
            })
            continue
        active_limited_config_ids.extend(config_ids)
        purchased_count = sum(
            decoded_by_config_id.get(config_id, 0) for config_id in config_ids
        )
        purchase_limit = sum(limits)
        if purchased_count > purchase_limit:
            raise FanxiuActivityShopCollectionError(
                f"限量商品 {goods_id} 的购买量 {purchased_count} 超过限购 {purchase_limit}"
            )
        purchase_counts[goods_id] = purchased_count
    return purchase_counts, {
        "source": "ActivityShopData.V_ShopInfo.cfg_and_svr",
        "declared_server_record_count": len(server_rows),
        "decoded_server_record_count": len(decoded_by_config_id),
        "active_config_ids": active_config_ids,
        "active_limited_config_ids": active_limited_config_ids,
        "active_nonzero_limited_records": {
            str(config_id): decoded_by_config_id[config_id]
            for config_id in active_limited_config_ids
            if decoded_by_config_id.get(config_id, 0) > 0
        },
        "ignored_cross_period_unlimited_records": ignored_unlimited_records,
    }


def _decode_config_rows_from_table(
    reader: LuaJitReader,
    config_ref: LuaRef,
    *,
    shop_base_id: int,
) -> list[list[Any]]:
    config_table = reader.table(config_ref.address)
    storage_ref = table_ref(config_table["fields"].get("_dt_"))
    if storage_ref is not None:
        config_table = reader.table(storage_ref.address)
    raw_values = list(config_table["fields"].values()) + list(config_table["array"])
    rows: dict[tuple[str, ...], list[Any]] = {}
    schemas: set[str] = set()
    for raw_value in raw_values:
        row_ref = table_ref(raw_value)
        if row_ref is None:
            continue
        decoded = _canonical_shop_row(
            reader.table(row_ref.address)["array"],
            shop_base_id=int(shop_base_id),
        )
        if decoded is None:
            continue
        values, schema = decoded
        if (
            isinstance(values[1], int)
            and isinstance(values[2], int)
            and isinstance(values[4], int)
            and isinstance(values[5], (int, float))
            and isinstance(values[6], (int, float))
            and isinstance(values[7], (int, float))
            and isinstance(values[9], (int, float))
            and isinstance(values[13], (int, float))
            and values[6] >= 0
            and values[7] >= 0
            and values[13] >= 0
            and (values[11] is None or isinstance(values[11], str))
            and (values[12] is None or isinstance(values[12], str))
        ):
            rows[tuple(str(value) for value in values[:17])] = values
            schemas.add(schema)
    if len(schemas) > 1:
        raise FanxiuActivityShopCollectionError("V_ShopCfg 混入两种配置行布局")
    return list(rows.values())


def _decode_config_rows_from_shop_dictionary(
    reader: LuaJitReader,
    shop_dictionary: LuaRef,
    *,
    shop_base_id: int,
    currency_type: int,
) -> list[list[Any]]:
    base_mapping = reader.dictionary_fields(shop_dictionary)
    currency_dictionary = table_ref(base_mapping.get(int(shop_base_id)))
    if currency_dictionary is None:
        return []
    currency_mapping = reader.dictionary_fields(currency_dictionary)
    shop_list = table_ref(currency_mapping.get(int(currency_type)))
    if shop_list is None:
        return []
    raw_rows, row_count = reader.list_items(shop_list)
    if row_count is None or row_count <= 0 or len(raw_rows) != row_count:
        raise FanxiuActivityShopCollectionError(
            f"V_ShopDic[{int(shop_base_id)}][{int(currency_type)}] 列表不完整"
        )
    rows: list[list[Any]] = []
    schemas: set[str] = set()
    for raw_row in raw_rows:
        row_ref = table_ref(raw_row)
        if row_ref is None:
            raise FanxiuActivityShopCollectionError(
                f"V_ShopDic[{int(shop_base_id)}][{int(currency_type)}] 含无效配置"
            )
        decoded = _canonical_shop_row(
            reader.table(row_ref.address)["array"],
            shop_base_id=int(shop_base_id),
            currency_type=int(currency_type),
        )
        if decoded is None:
            raise FanxiuActivityShopCollectionError(
                f"V_ShopDic[{int(shop_base_id)}][{int(currency_type)}] 配置身份不一致"
            )
        values, schema = decoded
        schemas.add(schema)
        rows.append(values)
    if len(schemas) != 1:
        raise FanxiuActivityShopCollectionError(
            f"V_ShopDic[{int(shop_base_id)}][{int(currency_type)}] 混入两种配置行布局"
        )
    return rows


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _row_value(row: Sequence[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _decode_config_rows(
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    *,
    shop_base_id: int,
    anchors: Sequence[int],
) -> list[list[Any]]:
    needle = struct.pack(
        "<Q", reader.tagged_pointer(0xFFFFFFF2, int(shop_base_id))
    )
    rows: dict[tuple[str, ...], list[Any]] = {}
    regions = _nearby_lua_heap_regions(
        memory,
        anchors,
        radius=256 * 1024 * 1024,
        max_total_bytes=256 * 1024 * 1024,
    )
    for region in regions:
        try:
            raw = memory.read_region(region)
        except FanxiuRuntimeMemoryError:
            continue
        offset = 0
        while True:
            offset = raw.find(needle, offset)
            if offset < 0:
                break
            array_start = offset - 3 * 8
            if array_start >= 0 and array_start + _CONFIG_VALUE_COUNT * 8 <= len(raw):
                try:
                    values = [
                        reader.value(
                            struct.unpack_from("<Q", raw, array_start + index * 8)[0]
                        )
                        for index in range(_CONFIG_VALUE_COUNT)
                    ]
                    if (
                        values[3] == int(shop_base_id)
                        and isinstance(values[1], int)
                        and isinstance(values[2], int)
                        and isinstance(values[4], int)
                        and isinstance(values[5], (int, float))
                        and isinstance(values[6], (int, float))
                        and isinstance(values[7], (int, float))
                        and isinstance(values[9], (int, float))
                        and isinstance(values[13], (int, float))
                        and values[6] >= 0
                        and values[7] >= 0
                        and values[13] >= 0
                        and (values[11] is None or isinstance(values[11], str))
                        and (values[12] is None or isinstance(values[12], str))
                    ):
                        rows[tuple(str(value) for value in values[:17])] = values
                except (FanxiuRuntimeMemoryError, struct.error, TypeError, ValueError):
                    pass
            offset += 1
    return list(rows.values())


def _candidate_index_nodes(
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    *,
    candidate_ids: set[int],
    anchors: Sequence[int],
) -> list[tuple[int, int, int]]:
    hits: list[tuple[int, int, int]] = []
    regions = _nearby_lua_heap_regions(
        memory,
        anchors,
        radius=256 * 1024 * 1024,
        max_total_bytes=256 * 1024 * 1024,
    )
    needles = {
        goods_id: struct.pack("<d", float(goods_id)) for goods_id in candidate_ids
    }
    for region in regions:
        try:
            raw = memory.read_region(region)
        except FanxiuRuntimeMemoryError:
            continue
        for goods_id, needle in needles.items():
            offset = 8
            while True:
                offset = raw.find(needle, offset)
                if offset < 0:
                    break
                try:
                    position = reader.value(struct.unpack_from("<Q", raw, offset - 8)[0])
                except (FanxiuRuntimeMemoryError, struct.error):
                    position = None
                if isinstance(position, int) and 0 <= position < 4096:
                    hits.append((region.start + offset - 8, goods_id, position))
                offset += 1
    return hits


def _discover_active_config_rows(
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    *,
    rows: Sequence[Sequence[Any]],
    anchors: Sequence[int],
) -> tuple[list[Sequence[Any]], dict[int, int]] | None:
    """Read the manager's config-id -> active-config references.

    Unlike the full ``V_ShopCfg`` store, this runtime map is populated only
    after the game has applied its complete condition engine.
    """

    config_ids = {int(row[1]) for row in rows}
    expected_by_signature = {
        tuple(str(value) for value in row[:17]): row for row in rows
    }
    references: dict[int, tuple[int, Sequence[Any]]] = {}
    regions = _nearby_lua_heap_regions(
        memory,
        anchors,
        radius=256 * 1024 * 1024,
        max_total_bytes=256 * 1024 * 1024,
    )
    needles = {
        config_id: struct.pack("<d", float(config_id)) for config_id in config_ids
    }
    for region in regions:
        try:
            raw = memory.read_region(region)
        except FanxiuRuntimeMemoryError:
            continue
        for config_id, needle in needles.items():
            offset = 8
            while True:
                offset = raw.find(needle, offset)
                if offset < 0:
                    break
                try:
                    value = reader.value(struct.unpack_from("<Q", raw, offset - 8)[0])
                    if isinstance(value, LuaRef) and value.kind == "table":
                        config_row = reader.table(value.address)["array"]
                        signature = tuple(str(item) for item in config_row[:17])
                        canonical = expected_by_signature.get(signature)
                        if canonical is not None and int(canonical[1]) == config_id:
                            references[value.address] = (
                                region.start + offset - 8,
                                canonical,
                            )
                except (FanxiuRuntimeMemoryError, KeyError, struct.error, TypeError, ValueError):
                    pass
                offset += 1
    if not references:
        return None
    ordered = [value for value in sorted(references.values(), key=lambda item: item[0])]
    active_rows = [row for _, row in ordered]
    goods_order: list[int] = []
    for row in active_rows:
        goods_id = int(row[1])
        if goods_id not in goods_order:
            goods_order.append(goods_id)
    if len(goods_order) < 3:
        return None
    return active_rows, {
        goods_id: position for position, goods_id in enumerate(goods_order)
    }


def _read_show_list_groups(
    reader: LuaJitReader,
    show_list: LuaRef,
    *,
    rows: Sequence[Sequence[Any]],
    diagnostics: dict[str, Any] | None = None,
) -> list[list[Sequence[Any]]]:
    """Decode the already-filtered grouped CList rendered by the shop view."""

    if not rows:
        raise FanxiuActivityShopCollectionError("兑换页缺少可校验的商店配置")
    expected_base_id = as_int(rows[0][3])
    expected_currency = as_int(rows[0][_ROW_CURRENCY])
    if expected_base_id is None or expected_currency is None:
        raise FanxiuActivityShopCollectionError("兑换页商店配置身份无效")
    expected_by_signature = {
        tuple(str(value) for value in row[:17]): row for row in rows
    }
    indexed_reader = getattr(reader, "indexed_list_items", None)
    if callable(indexed_reader):
        indexed_groups, group_count = indexed_reader(show_list)
    else:
        raw_groups, group_count = reader.list_items(show_list)
        indexed_groups = list(enumerate(raw_groups, start=1))
    raw_groups = [value for _key, value in indexed_groups]
    if group_count is None or group_count <= 0 or len(raw_groups) != group_count:
        raise FanxiuActivityShopCollectionError("兑换页 V_ShowList 分组列表不完整")

    groups: list[list[Sequence[Any]]] = []
    group_evidence: list[dict[str, Any]] = []
    for group_index, (outer_key, raw_group) in enumerate(indexed_groups):
        group_ref = table_ref(raw_group)
        if group_ref is None:
            raise FanxiuActivityShopCollectionError(
                f"兑换页 V_ShowList 第 {group_index + 1} 组不是 CList"
            )
        if callable(indexed_reader):
            indexed_rows, row_count = indexed_reader(group_ref)
        else:
            raw_rows, row_count = reader.list_items(group_ref)
            indexed_rows = list(enumerate(raw_rows, start=1))
        raw_rows = [value for _key, value in indexed_rows]
        if row_count is None or row_count <= 0 or len(raw_rows) != row_count:
            raise FanxiuActivityShopCollectionError(
                f"兑换页 V_ShowList 第 {group_index + 1} 组不完整"
            )
        group: list[Sequence[Any]] = []
        inner_keys: list[int] = []
        config_ids: list[int] = []
        config_groups: list[int] = []
        for inner_key, raw_row in indexed_rows:
            row_ref = table_ref(raw_row)
            if row_ref is None:
                raise FanxiuActivityShopCollectionError(
                    f"兑换页 V_ShowList 第 {group_index + 1} 组含无效配置"
                )
            decoded = _canonical_shop_row(
                reader.table(row_ref.address)["array"],
                shop_base_id=expected_base_id,
                currency_type=expected_currency,
            )
            if decoded is None:
                raise FanxiuActivityShopCollectionError(
                    f"兑换页 V_ShowList 第 {group_index + 1} 组配置身份不一致"
                )
            config_row, _schema = decoded
            signature = tuple(str(value) for value in config_row[:17])
            canonical = expected_by_signature.get(signature)
            if canonical is None:
                raise FanxiuActivityShopCollectionError(
                    f"兑换页 V_ShowList 第 {group_index + 1} 组配置不属于 V_ShopCfg"
                )
            group.append(canonical)
            inner_keys.append(int(inner_key))
            config_ids.append(int(canonical[1]))
            config_groups.append(int(canonical[2]))
        groups.append(group)
        group_evidence.append({
            "outer_key": int(outer_key),
            "inner_keys": inner_keys,
            "config_ids": config_ids,
            "config_groups": config_groups,
        })
    if diagnostics is not None:
        diagnostics.update({
            "outer_keys": [int(key) for key, _value in indexed_groups],
            "declared_outer_count": int(group_count),
            "groups": group_evidence,
        })
    return groups


def _discover_page_show_list_groups(
    memory: MumuProcessMemory,
    reader: LuaJitReader,
    *,
    shop_base_id: int,
    expected_currency_type: int | None,
    rows: Sequence[Sequence[Any]],
) -> tuple[list[list[Sequence[Any]]], int, dict[str, Any]] | None:
    """Read the active redemption panel through UIShowMgr's fixed object chain."""

    try:
        state_address = int(_lua_addresses(memory)["state"], 16)
        environment_address = struct.unpack(
            "<Q", memory.read(state_address + 72, 8)
        )[0]
        _global, string_table, string_mask, string_seed = lua_jit_intern_state(
            memory, state_address
        )
        exact_kwargs = {
            "string_table_address": string_table,
            "string_mask": string_mask,
            "string_seed": string_seed,
        }

        def field(address: int, name: str) -> Any:
            return reader.interned_string_field(address, name, **exact_kwargs)

        package = table_ref(field(environment_address, "package"))
        loaded = table_ref(field(package.address, "loaded")) if package else None
        module = (
            table_ref(field(loaded.address, "Core.UIManager.Manager.UIShowMgr"))
            if loaded
            else None
        )
        instance = (
            table_ref(
                reader.metatable_index_string_field(
                    module.address, "inst", **exact_kwargs
                )
            )
            if module
            else None
        )
        component_dictionary = (
            table_ref(field(instance.address, "V_M_compDic")) if instance else None
        )
        dictionary_storage = (
            table_ref(field(component_dictionary.address, "_dt_"))
            if component_dictionary
            else None
        )
        window_list = (
            table_ref(
                reader.table(dictionary_storage.address)["fields"].get(
                    _ACTIVITY_TEMPLATE_MAIN_VIEW_ID
                )
            )
            if dictionary_storage
            else None
        )
        windows, window_count = reader.list_items(window_list)
        if window_count is None or window_count <= 0 or len(windows) != window_count:
            return None
        window_component = table_ref(windows[-1])
        main_panel = (
            table_ref(field(window_component.address, "m_panel"))
            if window_component
            else None
        )
        tab_group = (
            table_ref(field(main_panel.address, "tabPanelGroup")) if main_panel else None
        )
        current_index = as_int(field(tab_group.address, "curTabIndex")) if tab_group else None
        panel_components = (
            table_ref(field(tab_group.address, "panelShowComps")) if tab_group else None
        )
        panel_count = (
            as_int(field(panel_components.address, "count"))
            if panel_components
            else None
        )
        panel_storage = (
            table_ref(field(panel_components.address, "_dt_"))
            if panel_components
            else None
        )
        if (
            current_index is None
            or panel_count is None
            or panel_count <= 0
            or not 0 <= current_index < panel_count
            or panel_storage is None
        ):
            return None
        # panelShowComps is sparse: only materialized tabs occupy their Lua
        # numeric slot.  CList's zero-based index maps to _dt_[index + 1], and
        # LuaJitReader's array list preserves that numeric key as its index.
        panel_storage_table = reader.table(panel_storage.address)
        panel_array = panel_storage_table["array"]
        active_slot = current_index + 1
        active_value = (
            panel_array[active_slot]
            if active_slot < len(panel_array)
            else panel_storage_table["fields"].get(active_slot)
        )
        active_component = table_ref(active_value)
        panel = (
            table_ref(field(active_component.address, "m_panel"))
            if active_component
            else None
        )
        if panel is None:
            return None
        if as_int(field(panel.address, "V_BaseActivityId")) != int(shop_base_id):
            return None
        if (
            expected_currency_type is not None
            and as_int(field(panel.address, "V_WalletType"))
            != int(expected_currency_type)
        ):
            return None
        show_list = table_ref(field(panel.address, "V_ShowList"))
        if show_list is None:
            return None
        show_list_evidence: dict[str, Any] = {}
        groups = _read_show_list_groups(
            reader,
            show_list,
            rows=rows,
            diagnostics=show_list_evidence,
        )
        if any(
            as_int(row[3]) != int(shop_base_id)
            or (
                expected_currency_type is not None
                and as_int(row[_ROW_CURRENCY]) != int(expected_currency_type)
            )
            for group in groups
            for row in group
        ):
            raise FanxiuActivityShopCollectionError(
                "兑换页 V_ShowList 与目标活动或代币不一致"
            )
        return groups, panel.address, show_list_evidence
    except FanxiuActivityShopCollectionError:
        raise
    except (FanxiuRuntimeMemoryError, KeyError, TypeError, ValueError, struct.error):
        return None


def _infer_show_list_cross_count(
    groups: Sequence[Sequence[Sequence[Any]]],
    *,
    shop_base_id: int,
) -> int | None:
    cross_counts = {
        int(match.group(2))
        for group in groups
        for row in group
        for match in _EQUAL_CROSS_GROUP_RE.finditer(str(row[_ROW_SHOW_LIMIT] or ""))
        if int(match.group(1)) == int(shop_base_id)
    }
    if len(cross_counts) > 1:
        raise FanxiuActivityShopCollectionError(
            f"兑换页 V_ShowList 混入多个跨服配置：{sorted(cross_counts)}"
        )
    return next(iter(cross_counts)) if cross_counts else None


def _discover_active_index(
    nodes: Sequence[tuple[int, int, int]],
) -> dict[int, int]:
    """Find Lua hash nodes whose values are a complete zero-based index."""

    candidates: list[tuple[int, int, dict[int, int]]] = []
    for modulo in range(24):
        points = sorted(node for node in nodes if node[0] % 24 == modulo)
        for start_index, start in enumerate(points):
            for node_count in (4, 8, 16, 32, 64, 128, 256, 512, 1024):
                end_address = start[0] + (node_count - 1) * 24
                window: dict[int, int] = {}
                for address, goods_id, position in points[start_index:]:
                    if address > end_address:
                        break
                    previous = window.get(goods_id)
                    if previous is not None and previous != position:
                        window = {}
                        break
                    window[goods_id] = position
                positions = set(window.values())
                if (
                    len(window) >= 3
                    and len(positions) == len(window)
                    and positions == set(range(len(window)))
                ):
                    candidates.append((len(window), -node_count, window))
    if not candidates:
        raise FanxiuActivityShopCollectionError("没有找到活动商店的已过滤商品索引")
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_size = candidates[0][0]
    best = {
        tuple(sorted(item[2].items())): item[2]
        for item in candidates
        if item[0] == best_size
    }
    if len(best) != 1:
        raise FanxiuActivityShopCollectionError(
            f"活动商店商品索引不唯一：{len(best)} 个候选均为 {best_size} 项"
        )
    return next(iter(best.values()))


def _infer_cross_count(
    rows: Sequence[Sequence[Any]],
    *,
    shop_base_id: int,
    active_ids: set[int],
) -> int | None:
    goods_ids_by_cross: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        for match in _EQUAL_CROSS_GROUP_RE.finditer(str(row[_ROW_SHOW_LIMIT] or "")):
            if int(match.group(1)) == int(shop_base_id):
                goods_ids_by_cross[int(match.group(2))].add(int(row[1]))
    matches = [
        cross_count
        for cross_count, goods_ids in goods_ids_by_cross.items()
        if active_ids <= goods_ids
    ]
    if len(matches) > 1:
        raise FanxiuActivityShopCollectionError(
            f"活动商店跨服配置不唯一：{sorted(matches)}"
        )
    return matches[0] if matches else None


def lua51_sort_goods_ids(
    goods_ids: Sequence[int],
    sort_order_by_id: Mapping[int, int],
) -> list[int]:
    """Reproduce Lua 5.1 ``table.sort`` including its unstable tie ordering."""

    values = [int(value) for value in goods_ids]

    def less(left: int, right: int) -> bool:
        return int(sort_order_by_id[left]) < int(sort_order_by_id[right])

    def auxsort(lower: int, upper: int) -> None:
        while lower < upper:
            if less(values[upper], values[lower]):
                values[lower], values[upper] = values[upper], values[lower]
            if upper - lower == 1:
                break
            middle = (lower + upper) // 2
            if less(values[middle], values[lower]):
                values[middle], values[lower] = values[lower], values[middle]
            elif less(values[upper], values[middle]):
                values[middle], values[upper] = values[upper], values[middle]
            if upper - lower == 2:
                break
            values[middle], values[upper - 1] = values[upper - 1], values[middle]
            pivot = values[upper - 1]
            left = lower
            right = upper - 1
            while True:
                left += 1
                while less(values[left], pivot):
                    left += 1
                right -= 1
                while less(pivot, values[right]):
                    right -= 1
                if right < left:
                    break
                values[left], values[right] = values[right], values[left]
            values[left], values[upper - 1] = values[upper - 1], values[left]
            if left - lower < upper - left:
                auxsort(lower, left - 1)
                lower = left + 1
            else:
                auxsort(left + 1, upper)
                upper = left - 1

    if values:
        auxsort(0, len(values) - 1)
    return values


def collect_activity_shop_runtime(
    *,
    shop_base_id: int,
    item_names: Mapping[int, str] | None = None,
    expected_currency_type: int | None = None,
    expected_cross_count: int | None = None,
) -> dict[str, Any]:
    """Collect the game's exact active shop projection from LuaJIT memory.

    The caller supplies only the stable shop-base identity. Product IDs,
    condition cohort, cross-server count, source order, prices and limits are
    discovered from runtime state on every collection.
    """

    started_at = time.perf_counter()
    phase_started_at = started_at
    phase_seconds: dict[str, float] = {}
    memory = MumuProcessMemory.discover()
    phase_seconds["process_discovery"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "activity-shop phase=process_discovery elapsed=%.3fs pid=%s",
        phase_seconds["process_discovery"],
        memory.pid,
    )
    reader = LuaJitReader(memory)
    phase_started_at = time.perf_counter()
    manager_root, manager_cache_hit, manager_resolver = (
        _resolve_activity_manager(memory)
    )
    phase_seconds["activity_manager"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "activity-shop phase=activity_manager resolver=%s elapsed=%.3fs",
        manager_resolver,
        phase_seconds["activity_manager"],
    )
    phase_started_at = time.perf_counter()
    shop_data_fields, shop_data_address = _activity_shop_data(reader, manager_root)
    config_ref = table_ref(shop_data_fields.get("V_ShopCfg"))
    if config_ref is None:
        raise FanxiuActivityShopNotLoadedError("游戏尚未加载 V_ShopCfg")
    shop_dictionary = table_ref(shop_data_fields.get("V_ShopDic"))
    rows = (
        _decode_config_rows_from_shop_dictionary(
            reader,
            shop_dictionary,
            shop_base_id=int(shop_base_id),
            currency_type=int(expected_currency_type),
        )
        if shop_dictionary is not None and expected_currency_type is not None
        else _decode_config_rows_from_table(
            reader, config_ref, shop_base_id=int(shop_base_id)
        )
    )
    if not rows:
        raise FanxiuActivityShopCollectionError(
            f"V_ShopCfg 中没有商店 {int(shop_base_id)} 的运行态配置"
        )
    phase_seconds["shop_config"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "activity-shop phase=shop_config rows=%s elapsed=%.3fs",
        len(rows),
        phase_seconds["shop_config"],
    )
    candidate_ids = {int(row[1]) for row in rows}

    phase_started_at = time.perf_counter()
    page_projection = _discover_page_show_list_groups(
        memory,
        reader,
        shop_base_id=int(shop_base_id),
        expected_currency_type=expected_currency_type,
        rows=rows,
    )
    if page_projection is None:
        raise FanxiuActivityShopNotLoadedError(
            "目标活动兑换页当前未打开，无法读取 V_ShowList"
        )
    phase_seconds["page_projection"] = time.perf_counter() - phase_started_at
    _LOGGER.info(
        "activity-shop phase=page_projection elapsed=%.3fs",
        phase_seconds["page_projection"],
    )
    page_groups, page_root, show_list_evidence = page_projection
    active_config_rows = [row for group in page_groups for row in group]
    active_index = {
        int(group[0][1]): position for position, group in enumerate(page_groups)
    }
    if len(active_index) != len(page_groups):
        raise FanxiuActivityShopCollectionError(
            "兑换页 V_ShowList 存在重复的首商品 id"
        )
    cross_count = _infer_show_list_cross_count(
        page_groups, shop_base_id=int(shop_base_id)
    )
    if expected_cross_count is not None and cross_count != int(expected_cross_count):
        raise FanxiuActivityShopCollectionError(
            "活动商店运行态投影与目标跨服不一致："
            f"期望 {int(expected_cross_count)} 跨，实际 {cross_count}；"
            "请先在游戏中打开目标活动的兑换页"
        )

    selected = {int(group[0][1]): list(group) for group in page_groups}
    missing = [goods_id for goods_id, values in selected.items() if not values]
    if missing:
        raise FanxiuActivityShopCollectionError(
            f"活动商店动态快照缺少商品配置：{missing[0]}"
        )
    purchase_counts, purchase_count_evidence = (
        _read_activity_shop_purchase_counts(
            reader,
            shop_data_fields,
            page_groups,
        )
    )

    names = item_names or {}
    items_by_id: dict[int, dict[str, Any]] = {}
    sort_order_by_id: dict[int, int] = {}
    currencies: set[int] = set()
    for goods_id, config_rows in selected.items():
        signatures = {
            (
                int(row[4]),
                int(row[5] or 1),
                int(row[_ROW_PRICE] or 0),
                int(row[_ROW_CURRENCY] or 0),
            )
            for row in config_rows
        }
        if len(signatures) != 1:
            raise FanxiuActivityShopCollectionError(
                f"活动商品 {goods_id} 的当前配置内容不一致"
            )
        primary = config_rows[0]
        limits = [
            int(row[_ROW_LIMIT_TIMES] if row[_ROW_LIMIT_TIMES] is not None else -1)
            for row in config_rows
        ]
        purchase_limit = -1 if any(limit < 0 for limit in limits) else sum(limits)
        item_id = int(primary[4])
        currency_type = int(primary[_ROW_CURRENCY] or 0)
        currencies.add(currency_type)
        sort_order_by_id[goods_id] = int(primary[_ROW_POSITION] or 0)
        items_by_id[goods_id] = {
            "goods_id": goods_id,
            "item_id": item_id,
            "name": str(names.get(item_id) or item_id),
            "goods_num": int(primary[5] or 1),
            "token_cost": int(primary[_ROW_PRICE] or 0),
            "currency_type": currency_type,
            "purchase_limit": purchase_limit,
            "purchased_count": int(purchase_counts[goods_id]),
            "discount": _optional_int(_row_value(primary, _ROW_DISCOUNT)),
            "original_price": _optional_int(
                _row_value(primary, _ROW_ORIGINAL_PRICE)
            ),
            "show_limit": str(primary[_ROW_SHOW_LIMIT] or ""),
            "disappear_limit": str(primary[_ROW_DISAPPEAR_LIMIT] or ""),
            "raw_data": {
                "config_ids": sorted(int(row[1]) for row in config_rows),
                "goods_ids": [int(row[1]) for row in config_rows],
                "aggregated_config_count": len(config_rows),
                "runtime_active_position": active_index[goods_id],
                "runtime_sort_order": int(primary[_ROW_POSITION] or 0),
                "cross_count": cross_count,
            },
        }
    if expected_currency_type is not None and currencies != {int(expected_currency_type)}:
        raise FanxiuActivityShopCollectionError(
            f"活动商店代币类型不一致：期望 {int(expected_currency_type)}，实际 {sorted(currencies)}"
        )
    initial_order = [
        goods_id for goods_id, _ in sorted(active_index.items(), key=lambda item: item[1])
    ]
    display_order = initial_order
    items = [
        {**items_by_id[goods_id], "source_order": source_order}
        for source_order, goods_id in enumerate(display_order, start=1)
    ]
    return {
        "complete": len(items) == len(active_index) and bool(items),
        "shop_base_id": int(shop_base_id),
        "cross_count": cross_count,
        "currency_types": sorted(currencies),
        "active_shop_item_count": len(active_index),
        "resolved_shop_item_count": len(items),
        "items": items,
        "evidence": {
            "source": "luajit_activity_view_v_show_list_and_v_shopcfg",
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "activity_manager_root": manager_root,
            "activity_manager_cache_hit": manager_cache_hit,
            "activity_manager_resolver": manager_resolver,
            "activity_shop_data_address": shop_data_address,
            "activity_view_root": page_root,
            "candidate_config_row_count": len(rows),
            "candidate_shop_item_count": len(candidate_ids),
            "active_config_reference_count": len(active_config_rows),
            "v_show_list": show_list_evidence,
            "purchase_counts": purchase_count_evidence,
            "elapsed_seconds": time.perf_counter() - started_at,
            "phase_seconds": phase_seconds,
        },
    }
