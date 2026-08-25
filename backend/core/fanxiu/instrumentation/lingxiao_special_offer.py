from __future__ import annotations

"""Strict read-only state for Lingxiao's SpecialOffer tab (#577).

``免费`` is a presentation string in this activity, not an entitlement.  The
only authority for a no-recharge action is the first *currently reachable*
SpecialOffer package whose loaded configuration has ``payId <= 0``.  This
adapter deliberately requires both the active #577 panel identity and the
already-loaded SpecialOffer model; it does not invoke Lua methods or load
configurations.
"""

from datetime import datetime
import time
from typing import Any, Iterable

from backend.core.fanxiu.instrumentation.activity_menu import (
    active_ui_component_objects,
    read_ui_object_field,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.resource_auto_use import (
    _environment_config_indexes,
    _packed_config_value,
    _runtime_config_null_defaults,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)
from backend.core.fanxiu.instrumentation.ui_runtime_context import (
    UiRuntimeContext,
    read_ui_runtime_snapshot,
)


_SPECIAL_OFFER_METHODS = frozenset({"Inst_get", "GetActivityID", "GetActivityVO"})
_SPECIAL_OFFER_CONFIG_GROUP = "SpecialOffer"
_SPECIAL_OFFER_CONFIG_TABLE = "SpecialOfferPackage"
_PACKAGE_FIELDS = (
    "id", "activityid", "sort", "payId", "personlimit", "purchaseEligibility", "optPacCount",
)
_SPECIAL_OFFER_UI_KEYS = frozenset({
    "m_panel", "V_ActivityId", "V_CurActivityId", "AllCount", "tabPanelGroup",
    "panelShowComps", "_dt_", "count", "ScrollView", "ItemInfoList",
    "id", "activityid", "sort", "payId", "personlimit", "purchaseEligibility",
    "optPacCount",
})


def _fields(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    return reader.fields(value) if isinstance(value, LuaRef) and value.kind == "table" else {}


def _integer(reader: LuaJitReader, value: Any) -> int | None:
    return reader.long(value) if isinstance(value, LuaRef) else as_int(value)


def _dictionary_items(reader: LuaJitReader, value: Any) -> dict[Any, Any]:
    data = _fields(reader, value).get("_dt_")
    if not isinstance(data, LuaRef) or data.kind != "table":
        return {}
    table = reader.table(data.address)
    result = dict(table.get("fields") or {})
    for index, item in enumerate(table.get("array") or []):
        if item is not None:
            result.setdefault(index, item)
    return result


def _list_values(reader: LuaJitReader, value: Any) -> list[Any]:
    values, declared = reader.list_items(value)
    if declared is not None and int(declared) != len(values):
        raise FanxiuRuntimeMemoryError("特惠连充包列表长度不完整")
    return list(values)


def build_lingxiao_special_offer_snapshot(
    *,
    special_activity_id: int,
    current_page_id: int,
    packages: Iterable[dict[str, Any]],
    bought_by_offer_id: dict[int, int],
) -> dict[str, Any]:
    """Find the first reachable package from live package definitions.

    This is intentionally a data projection instead of a generic "round"
    model.  The game's own implementation sorts packages by ``sort`` then
    accepts a package only after its prerequisite is sold out.
    """

    if int(special_activity_id) <= 0 or int(current_page_id) <= 0:
        raise FanxiuRuntimeMemoryError("特惠连充活动身份不完整")
    normalized: list[dict[str, int]] = []
    for raw in packages:
        row = {key: as_int(raw.get(key)) for key in (
            "id", "activityid", "sort", "payId", "personlimit", "purchaseEligibility", "optPacCount"
        )}
        missing = [key for key in ("id", "activityid", "sort", "payId", "personlimit", "purchaseEligibility", "optPacCount") if row[key] is None]
        if missing:
            observed = raw.get("_field_names") if isinstance(raw, dict) else None
            raw_kind = raw.get("_raw_kind") if isinstance(raw, dict) else None
            raise FanxiuRuntimeMemoryError(
                f"特惠连充包配置字段不完整：missing={missing}，kind={raw_kind}，observed={observed}"
            )
        if row["id"] <= 0 or row["personlimit"] <= 0 or row["activityid"] != int(current_page_id):
            raise FanxiuRuntimeMemoryError("特惠连充包配置身份或限购无效")
        normalized.append({key: int(value) for key, value in row.items()})
    if not normalized:
        raise FanxiuRuntimeMemoryError("NotLoaded: 特惠连充包配置尚未自然加载")
    if len({row["id"] for row in normalized}) != len(normalized):
        raise FanxiuRuntimeMemoryError("特惠连充包 ID 重复")
    normalized.sort(key=lambda row: (row["sort"], row["id"]))
    bought = {int(key): int(value) for key, value in bought_by_offer_id.items()}
    by_id = {row["id"]: row for row in normalized}
    missing_prerequisites = sorted(
        row["purchaseEligibility"]
        for row in normalized
        if row["purchaseEligibility"] > 0 and row["purchaseEligibility"] not in by_id
    )
    if missing_prerequisites:
        raise FanxiuRuntimeMemoryError(
            f"特惠连充包前置不在当前完整列表：{missing_prerequisites}"
        )
    for row in normalized:
        buy_num = bought.get(row["id"], 0)
        if buy_num < 0:
            raise FanxiuRuntimeMemoryError("特惠连充购买次数无效")
        row["buy_num"] = buy_num
        row["sold_out"] = buy_num >= row["personlimit"]
    reachable: dict[str, int] | None = None
    for row in normalized:
        predecessor = row["purchaseEligibility"]
        prerequisite_done = predecessor <= 0 or (
            predecessor in by_id and bool(by_id[predecessor]["sold_out"])
        )
        if prerequisite_done and not row["sold_out"]:
            reachable = row
            break
    if reachable is None:
        state = "all_sold_out"
        free_claimable = False
    elif reachable["payId"] <= 0 and reachable["optPacCount"] == 0:
        state = "free_claimable"
        free_claimable = True
    elif reachable["payId"] <= 0:
        state = "free_choice_unmapped"
        free_claimable = None
    else:
        state = "paid_gate"
        free_claimable = False
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "SpecialOfferMgr.SpecialOfferData",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "special_activity_id": int(special_activity_id),
        "current_page_id": int(current_page_id),
        "packages": normalized,
        "first_reachable": reachable,
        "state": state,
        "free_claimable": free_claimable,
    }


def _special_offer_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _SPECIAL_OFFER_METHODS)
    instance = _fields(reader, manager.get("inst"))
    data = _fields(reader, _fields(reader, instance.get("Model")).get("SpecialOfferData"))
    if "_SpecailOfferSynDic" not in data:
        raise FanxiuRuntimeMemoryError("NotLoaded: SpecialOffer 当前同步数据未自然加载")
    return data


def _model_snapshot(memory: MumuProcessMemory, force_refresh: bool) -> dict[str, Any]:
    state_address = int(_lua_addresses(memory)["state"], 16)
    root, cache_hit, environment = resolve_lua_global_manager_root(
        memory,
        manager_key="lingxiao-special-offer",
        state_address=state_address,
        global_name="SpecialOfferMgr",
        required_methods=_SPECIAL_OFFER_METHODS,
        validate=_special_offer_data_fields,
        force_refresh=force_refresh,
    )
    reader = LuaJitReader(memory)
    data = _special_offer_data_fields(reader, root)
    return {
        "reader": reader,
        "data": data,
        "environment_address": environment,
        "cache_hit": cache_hit,
    }


def _panel_identity(context: UiRuntimeContext, *, expected_parent_activity_id: int) -> tuple[int, int, list[LuaRef]]:
    """Join treasure parent #575 to the nested SpecialOffer child.

    #577 is not itself the 3001003 treasure panel.  Its child owns
    ``V_ActivityId=3001009`` (and may use it again as ``V_CurActivityId``),
    whereas its parent container remains 3001003.  Inspecting only registered
    components misses tab-panel children, so this follows the same bounded,
    observed ``panelShowComps`` edge as the Fuling reader.
    """
    candidates: list[tuple[int, int, list[LuaRef], int]] = []
    seen_child_addresses: set[int] = set()
    for component in active_ui_component_objects(context):
        host = table_ref(read_ui_object_field(context, component.address, "m_panel")) or component
        parent_id = as_int(read_ui_object_field(context, host.address, "V_ActivityId"))
        if parent_id != int(expected_parent_activity_id):
            continue
        tab_group = table_ref(read_ui_object_field(context, host.address, "tabPanelGroup"))
        panels = table_ref(read_ui_object_field(context, tab_group.address, "panelShowComps")) if tab_group is not None else None
        storage = table_ref(read_ui_object_field(context, panels.address, "_dt_")) if panels is not None else None
        count = as_int(read_ui_object_field(context, panels.address, "count")) if panels is not None else None
        if storage is None or count is None or not 1 <= count <= 16:
            continue
        values = context.reader.table(storage.address)["array"]
        if len(values) > count + 2:
            raise FanxiuRuntimeMemoryError("特惠连充 Tab 子面板数组超出有界范围")
        for raw in values:
            child_component = table_ref(raw)
            child = table_ref(read_ui_object_field(context, child_component.address, "m_panel")) if child_component is not None else None
            if child is None:
                continue
            # The sparse tab-array may expose the same pooled panel through
            # both an array and a numeric alias.  That is one UI object, not
            # two candidates; distinct addresses remain an ambiguity.
            if child.address in seen_child_addresses:
                continue
            seen_child_addresses.add(child.address)
            special_id = as_int(read_ui_object_field(context, child.address, "V_ActivityId"))
            current_id = as_int(read_ui_object_field(context, child.address, "V_CurActivityId"))
            all_count = as_int(read_ui_object_field(context, child.address, "AllCount"))
            if special_id and current_id and all_count and all_count > 0 and special_id == current_id:
                scroll = table_ref(read_ui_object_field(context, child.address, "ScrollView"))
                item_list = table_ref(read_ui_object_field(context, scroll.address, "ItemInfoList")) if scroll else None
                packages: list[LuaRef] = []
                for row in _list_values(context.reader, item_list) if item_list else []:
                    if not isinstance(row, LuaRef) or row.kind != "table":
                        raise FanxiuRuntimeMemoryError("特惠连充包列表含非表配置行")
                    packages.append(row)
                if len(packages) != all_count:
                    raise FanxiuRuntimeMemoryError(
                        f"特惠连充面板 AllCount={all_count} 与 ItemInfoList={len(packages)} 不一致"
                    )
                candidates.append((special_id, current_id, packages, child.address))
    if len(candidates) != 1:
        identities = sorted((special_id, page_id, f"0x{address:x}") for special_id, page_id, _packages, address in candidates)
        raise FanxiuRuntimeMemoryError(f"Ambiguous: 特惠连充活跃面板身份不唯一：{identities}")
    special_id, page_id, packages, _address = candidates[0]
    if not packages:
        raise FanxiuRuntimeMemoryError("NotLoaded: 特惠连充页面包列表尚未自然加载")
    return special_id, page_id, packages


def _decode_panel_packages(
    reader: LuaJitReader,
    rows: list[LuaRef],
    *,
    environment_address: int,
) -> list[dict[str, Any]]:
    """Decode the exact #577 rows without retaining them beyond this snapshot.

    ``ItemInfoList`` contains generated packed config rows.  The metatable's
    ``__index`` closure owns both the column map and omitted-value defaults;
    reading direct fields is therefore not an alternate representation.  The
    helper validates that closure against the live main-environment config map
    before projecting just the seven package fields required by the policy.
    """

    indexes = _environment_config_indexes(
        reader,
        environment_address,
        group_name=_SPECIAL_OFFER_CONFIG_GROUP,
        table_name=_SPECIAL_OFFER_CONFIG_TABLE,
    )
    missing_indexes = set(_PACKAGE_FIELDS) - set(indexes)
    if missing_indexes:
        raise FanxiuRuntimeMemoryError(
            f"特惠连充配置字段索引不完整：missing={sorted(missing_indexes)}"
        )
    defaults = _runtime_config_null_defaults(
        reader, {index: row for index, row in enumerate(rows)}, indexes
    )
    packages: list[dict[str, Any]] = []
    for row in rows:
        package: dict[str, Any] = {}
        for field in _PACKAGE_FIELDS:
            raw = _packed_config_value(reader, row, indexes, field)
            if raw is None:
                raw = defaults.get(field)
            package[field] = _integer(reader, raw)
        packages.append(package)
    return packages


def read_lingxiao_special_offer_runtime(*, expected_parent_activity_id: int) -> dict[str, Any]:
    """Read #577's reachability; this function never clicks or purchases."""

    started = time.perf_counter()
    try:
        def _read(context: UiRuntimeContext) -> dict[str, Any]:
            special_activity_id, page_id, rows = _panel_identity(
                context, expected_parent_activity_id=expected_parent_activity_id
            )
            # One coherent memory context is essential: pooled UI rows may be
            # replaced on a page transition, so never decode their addresses in
            # a separately acquired Runtime snapshot.
            model = _model_snapshot(context.memory, force_refresh=True)
            reader: LuaJitReader = model["reader"]
            packages = _decode_panel_packages(
                reader, rows, environment_address=int(model["environment_address"])
            )
            data = model["data"]
            page_groups = _dictionary_items(reader, data.get("_SpecailOfferSynDic"))
            page_group = page_groups.get(special_activity_id) or page_groups.get(float(special_activity_id))
            pages = _dictionary_items(reader, page_group)
            if page_id not in pages and float(page_id) not in pages:
                raise FanxiuRuntimeMemoryError("SpecialOffer Runtime 未同步当前 #577 页面")
            bought: dict[int, int] = {}
            for key, value in _dictionary_items(
                reader,
                _fields(reader, pages.get(page_id) or pages.get(float(page_id))).get(
                    "offerIdToBuyTime"
                ),
            ).items():
                offer_id = _integer(reader, key)
                buy_num = _integer(reader, value)
                if offer_id is None or buy_num is None:
                    raise FanxiuRuntimeMemoryError("SpecialOffer 已购次数含未解码字段")
                bought[int(offer_id)] = int(buy_num)
            return {
                **build_lingxiao_special_offer_snapshot(
                    special_activity_id=special_activity_id,
                    current_page_id=page_id,
                    packages=packages,
                    bought_by_offer_id=bought,
                ),
                "evidence": {"read_only": True, "root_cache_hit": model["cache_hit"]},
            }

        result = read_ui_runtime_snapshot(_SPECIAL_OFFER_UI_KEYS, _read)
        return {**result, "elapsed_seconds": time.perf_counter() - started}
    except Exception as exc:
        return {"ok": False, "available": False, "complete": False, "source": "SpecialOfferMgr.SpecialOfferData", "reason": str(exc), "elapsed_seconds": time.perf_counter() - started, "evidence": {"read_only": True}}


__all__ = ["build_lingxiao_special_offer_snapshot", "read_lingxiao_special_offer_runtime"]
