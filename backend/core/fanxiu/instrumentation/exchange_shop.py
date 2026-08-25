from __future__ import annotations

"""Strictly read the already-loaded common exchange shop from Lua memory."""

from collections import defaultdict
import re
import time
from typing import Any, Iterable, Sequence

from backend.core.fanxiu.catalog.item import load_fanxiu_item_catalog
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    manager_index_fields,
    resolve_lua_global_manager_root,
    table_ref,
)


_EXCHANGE_MANAGER_METHODS = frozenset({"ShowShopBuyTipsView"})
_ITEM_COST_RE = re.compile(r"^Item\|(\d+)_(\d+)$")


class FanxiuExchangeShopCollectionError(RuntimeError):
    pass


def _int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FanxiuExchangeShopCollectionError(f"兑换商店字段 {field} 不是数字")
    return int(value)


def _optional_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _int(value, field=field)


def _parse_cost(value: Any) -> tuple[int, int]:
    if not isinstance(value, str):
        raise FanxiuExchangeShopCollectionError("兑换商店 cost 不是物品消耗字符串")
    match = _ITEM_COST_RE.fullmatch(value)
    if match is None:
        raise FanxiuExchangeShopCollectionError(f"不支持的兑换商店 cost：{value}")
    return int(match.group(1)), int(match.group(2))


def decode_exchange_shop_config(
    values: Sequence[Any],
    *,
    cost_values: Sequence[Any],
) -> dict[str, Any]:
    """Decode the proven CommonShop_ExchangeShop generated-row layout."""

    if len(values) <= 17:
        raise FanxiuExchangeShopCollectionError("兑换商店配置行长度不足")
    cost_text = next((item for item in cost_values[1:] if isinstance(item, str)), None)
    cost_item_id, cost_num = _parse_cost(cost_text)
    decoded = {
        "goods_id": _int(values[1], field="goodsId"),
        "item_id": _int(values[2], field="itemId"),
        "goods_num": _int(values[8], field="goodsNum"),
        "limit_buy": _int(values[4], field="limitBuy"),
        "second_tag": _int(values[5], field="secondTag"),
        "third_tag": _int(values[6], field="thirdTag"),
        "group": _int(values[7], field="group"),
        "scope_type": _optional_int(values[10], field="scopeType"),
        "limit_times": _int(values[11], field="limitTimes"),
        "position": _int(values[16], field="position"),
        "cost_item_id": cost_item_id,
        "cost_num": cost_num,
    }
    if decoded["goods_id"] <= 0 or decoded["item_id"] <= 0:
        raise FanxiuExchangeShopCollectionError("兑换商店商品 id 无效")
    if decoded["goods_num"] <= 0 or decoded["cost_num"] <= 0:
        raise FanxiuExchangeShopCollectionError("兑换商店商品或消耗数量无效")
    return decoded


def _manager_data(reader: LuaJitReader, root: int) -> tuple[dict[str, Any], int]:
    manager_fields = manager_index_fields(reader, root, _EXCHANGE_MANAGER_METHODS)
    instance = table_ref(manager_fields.get("inst"))
    if instance is None:
        raise FanxiuExchangeShopCollectionError("ExchangeshopMgr 实例尚未加载")
    instance_fields = reader.table(instance.address)["fields"]
    model = table_ref(instance_fields.get("Model"))
    if model is None:
        raise FanxiuExchangeShopCollectionError("ExchangeshopMgr.Model 尚未加载")
    model_fields = reader.table(model.address)["fields"]
    data = table_ref(model_fields.get("ExchangeshopData"))
    if data is None:
        raise FanxiuExchangeShopCollectionError("ExchangeshopData 尚未加载")
    fields = reader.table(data.address)["fields"]
    if table_ref(fields.get("AllExchangeShopItemVoList")) is None:
        raise FanxiuExchangeShopCollectionError("兑换商店商品列表尚未加载")
    return fields, data.address


def _validate_manager(reader: LuaJitReader, root: int) -> None:
    _manager_data(reader, root)


def _catalog_by_id() -> dict[int, dict[str, Any]]:
    catalog = load_fanxiu_item_catalog(rebuild_missing=False)
    return {
        int(card.get("id") or 0): card
        for card in catalog.get("cards") or []
        if int(card.get("id") or 0) > 0
    }


def _read_rows(reader: LuaJitReader, data_fields: dict[str, Any]) -> list[dict[str, Any]]:
    item_list = table_ref(data_fields.get("AllExchangeShopItemVoList"))
    if item_list is None:
        raise FanxiuExchangeShopCollectionError("兑换商店商品列表尚未加载")
    list_table = reader.table(item_list.address)
    count = _int(list_table["fields"].get("count"), field="itemCount")
    storage = table_ref(list_table["fields"].get("_dt_"))
    if storage is None:
        raise FanxiuExchangeShopCollectionError("兑换商店商品列表存储尚未加载")
    array = reader.table(storage.address)["array"]
    if count <= 0 or len(array) <= count:
        raise FanxiuExchangeShopCollectionError("兑换商店商品列表不完整")

    rows: list[dict[str, Any]] = []
    for raw_vo in array[1 : count + 1]:
        vo = table_ref(raw_vo)
        if vo is None:
            raise FanxiuExchangeShopCollectionError("兑换商店商品 VO 缺失")
        vo_fields = reader.table(vo.address)["fields"]
        config_ref = table_ref(vo_fields.get("configData"))
        if config_ref is None:
            raise FanxiuExchangeShopCollectionError("兑换商店 configData 缺失")
        config = reader.table(config_ref.address)["array"]
        if len(config) <= 9 or not isinstance(config[9], LuaRef):
            # Free claims use the same shop container but are not currency
            # exchange candidates for either GongFa job.
            continue
        cost = reader.table(config[9].address)["array"]
        try:
            row = decode_exchange_shop_config(config, cost_values=cost)
        except FanxiuExchangeShopCollectionError as exc:
            raise FanxiuExchangeShopCollectionError(
                f"兑换商店 goodsId={vo_fields.get('goodsId')} 配置无效：{exc}; "
                f"row={config[:18]!r}"
            ) from exc
        if row["goods_id"] != _int(vo_fields.get("goodsId"), field="vo.goodsId"):
            raise FanxiuExchangeShopCollectionError("兑换商店 VO 与配置 goodsId 不一致")
        row["has_buy_time"] = _int(vo_fields.get("hasBuyTime") or 0, field="hasBuyTime")
        rows.append(row)
    return rows


def project_exchange_shop_items(
    rows: Iterable[dict[str, Any]],
    *,
    catalog_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate the config rows exactly as ExchangeShopItem does for one card."""

    grouped: dict[tuple[int | None, int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        key = (
            None if row["scope_type"] is None else int(row["scope_type"]),
            int(row["second_tag"]),
            int(row["third_tag"]),
            int(row["group"]),
            int(row["item_id"]),
        )
        grouped[key].append(row)

    items: list[dict[str, Any]] = []
    for key, group_rows in grouped.items():
        signatures = {
            (int(row["goods_num"]), int(row["cost_item_id"]), int(row["cost_num"]), int(row["position"]))
            for row in group_rows
        }
        if len(signatures) != 1:
            raise FanxiuExchangeShopCollectionError(
                f"兑换商店分组 {key[3]} 的商品、价格或位置不一致"
            )
        item_id = key[4]
        card = catalog_by_id.get(item_id)
        if card is None:
            # Some system pseudo-items are deliberately absent from the
            # inventory catalog. They cannot map to a GongFa and are outside
            # the two exchange jobs.
            continue
        unlimited = all(int(row["limit_times"]) < 0 for row in group_rows)
        if not unlimited and any(int(row["limit_times"]) < 0 for row in group_rows):
            raise FanxiuExchangeShopCollectionError(f"兑换商店分组 {key[3]} 混合有限与无限库存")
        limit_total = None if unlimited else sum(max(0, int(row["limit_times"])) for row in group_rows)
        bought_total = sum(max(0, int(row["has_buy_time"])) for row in group_rows)
        remaining = None if limit_total is None else max(0, limit_total - bought_total)
        first = group_rows[0]
        items.append({
            "item_id": item_id,
            "name": str(card.get("name") or f"物品 {item_id}"),
            "item_type": int(card.get("type") or 0),
            "item_sub_type": int(card.get("sub_type") or 0),
            "linked_gongfa_id": int(card.get("linked_gongfa_id") or 0),
            "scope_type": key[0],
            "second_tag": key[1],
            "third_tag": key[2],
            "group": key[3],
            "goods_num": int(first["goods_num"]),
            "cost_item_id": int(first["cost_item_id"]),
            "cost_num": int(first["cost_num"]),
            "position": int(first["position"]),
            "limit_total": limit_total,
            "bought_total": bought_total,
            "remaining": remaining,
            "unlimited": unlimited,
            "goods_ids": [int(row["goods_id"]) for row in group_rows],
        })
    items.sort(key=lambda item: (-1 if item["scope_type"] is None else item["scope_type"], item["second_tag"], item["third_tag"], item["position"], item["item_id"]))
    return items


def read_exchange_shop_runtime() -> dict[str, Any]:
    """Return a coherent read-only snapshot; never initializes or mutates Lua."""

    started = time.perf_counter()
    memory = MumuProcessMemory.discover_cached(max_age_seconds=None)
    reader = LuaJitReader(memory)
    root, cache_hit, environment = resolve_lua_global_manager_root(
        memory,
        manager_key="exchange-shop-global",
        state_address=int(_lua_addresses(memory)["state"], 16),
        global_name="ExchangeshopMgr",
        required_methods=_EXCHANGE_MANAGER_METHODS,
        validate=_validate_manager,
    )
    data_fields, data_address = _manager_data(reader, root)
    rows = _read_rows(reader, data_fields)
    items = project_exchange_shop_items(rows, catalog_by_id=_catalog_by_id())
    if not items:
        raise FanxiuExchangeShopCollectionError("兑换商店运行态没有商品")
    return {
        "runtime_complete": True,
        "runtime_error": "",
        "runtime_updated_at": time.time(),
        "items": items,
        "runtime_item_count": len(items),
        "runtime_debug": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "manager_root": f"0x{root:x}",
            "manager_root_cache_hit": cache_hit,
            "environment": f"0x{environment:x}",
            "data": f"0x{data_address:x}",
            "config_row_count": len(rows),
            "elapsed_seconds": time.perf_counter() - started,
            "protocol": "ExchangeshopMgr.Model.ExchangeshopData.AllExchangeShopItemVoList",
            "read_only": True,
        },
    }
