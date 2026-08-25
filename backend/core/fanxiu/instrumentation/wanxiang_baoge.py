from __future__ import annotations

"""Fail-closed accounting for the 万象宝阁 six-yuan refund offer.

This module deliberately contains no game-side command or GUI click.  Static
configuration proves what the offer is; a fresh read-only Runtime snapshot
proves whether the currently rendered offer is eligible.  The GUI layer may
only consume the explicit action returned here and must read back Runtime
before advancing to the next phase.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root
from backend.core.fanxiu.instrumentation.activity_menu import (
    read_activity_menu_snapshot,
)
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
)
from backend.core.fanxiu.instrumentation.wallet import (
    WALLET_METHODS,
    wallet_currency_data,
)


WANXIANG_ACTIVITY_BASE_ID = 2030001
WANXIANG_REFUND_GOODS_ID = 99001
WANXIANG_REFUND_POOL_ID = 99
WANXIANG_REFUND_PAY_ID = 310001
WANXIANG_REFUND_BOX_ITEM_ID = 1201
WANXIANG_REFUND_VOUCHER_ITEM_ID = 1012
WANXIANG_SPIRIT_STONE_ITEM_ID = 1001
WANXIANG_VOUCHER_CURRENCY_TYPE = 1001
WANXIANG_BOUND_VOUCHER_CURRENCY_TYPE = 1004
WANXIANG_SPIRIT_STONE_CURRENCY_TYPE = 1
_WANXIANG_MANAGER_METHODS = frozenset({"LuaWanxiangshopMgr", "Inst_get"})


class WanxiangRefundContractError(ValueError):
    """The exported client configuration no longer proves the offer contract."""


def _rows(export_root: str | Path | None, table: str) -> list[dict[str, Any]]:
    root = (
        Path(export_root).expanduser().resolve()
        if export_root is not None
        else resolve_fanxiu_export_root()
    )
    path = root / "parsed_configs" / table / "rows.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise WanxiangRefundContractError(f"{table} 静态配置不是对象列表")
    return payload


def _unique_row(
    rows: list[dict[str, Any]], *, table: str, key: str, value: Any
) -> dict[str, Any]:
    matches = [row for row in rows if row.get(key) == value]
    if len(matches) != 1:
        raise WanxiangRefundContractError(
            f"{table}.{key}={value!r} 期望唯一，实际 {len(matches)} 项"
        )
    return matches[0]


def load_wanxiang_refund_offer_contract(
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Load and cross-check the immutable six-yuan refund offer facts.

    The displayed price is insufficient authorization.  The complete join is
    ``WanXiangShopPool -> ChargeGoods -> Item -> OptionalGift``.
    """

    offer = _unique_row(
        _rows(export_root, "WanXiangShopPool"),
        table="WanXiangShopPool",
        key="id",
        value=WANXIANG_REFUND_GOODS_ID,
    )
    pool = _unique_row(
        _rows(export_root, "ShopPoolBase"),
        table="ShopPoolBase",
        key="poolId",
        value=WANXIANG_REFUND_POOL_ID,
    )
    charge = _unique_row(
        _rows(export_root, "ChargeGoods"),
        table="ChargeGoods",
        key="id",
        value=WANXIANG_REFUND_PAY_ID,
    )
    box = _unique_row(
        _rows(export_root, "Item"),
        table="Item",
        key="id",
        value=WANXIANG_REFUND_BOX_ITEM_ID,
    )
    voucher_item = _unique_row(
        _rows(export_root, "Item"),
        table="Item",
        key="id",
        value=WANXIANG_REFUND_VOUCHER_ITEM_ID,
    )

    if (
        int(offer.get("poolId") or 0) != WANXIANG_REFUND_POOL_ID
        or str(offer.get("giftReward") or "") != "Item|1201_1"
        or int(offer.get("payId") or 0) != WANXIANG_REFUND_PAY_ID
        or int(offer.get("isPrize") or 0) != 1
    ):
        raise WanxiangRefundContractError("万象宝阁 6 元商品定义与已证明契约不一致")
    if int(pool.get("activityLimit") or 0) != 1:
        raise WanxiangRefundContractError("万象宝阁 6 元商品活动限购不再是 1")
    if (
        int(charge.get("payId") or 0) != WANXIANG_REFUND_PAY_ID
        or int(charge.get("priceValue") or 0) != 600
        or int(charge.get("replaceValue") or 0) != 6
        or str(charge.get("chargeSource") or "") != "WAN_XIANG_SHOP"
    ):
        raise WanxiangRefundContractError("万象宝阁 6 元支付定义与已证明契约不一致")

    effect_match = re.fullmatch(r"96002435_(\d+)", str(box.get("effectValue") or ""))
    if effect_match is None:
        raise WanxiangRefundContractError("代币宝匣 1201 不再指向已知 OptionalGift 组")
    optional_group = effect_match.group(1)
    rewards = {
        int(row.get("giftID") or 0): int(row.get("number") or 0)
        for row in _rows(export_root, "OptionalGift")
        if str(row.get("groupID") or "") == optional_group
    }
    expected_rewards = {
        WANXIANG_REFUND_VOUCHER_ITEM_ID: 1,
        WANXIANG_SPIRIT_STONE_ITEM_ID: 1140,
    }
    if rewards != expected_rewards:
        raise WanxiangRefundContractError(
            f"代币宝匣 1201 奖励变化：expected={expected_rewards}, actual={rewards}"
        )
    if str(voucher_item.get("effectValue") or "") != "1002_6":
        raise WanxiangRefundContractError("充值代币(6元)不再转换为 6 元代币余额")

    return {
        "complete": True,
        "activity_base_id": WANXIANG_ACTIVITY_BASE_ID,
        "goods_id": WANXIANG_REFUND_GOODS_ID,
        "pool_id": WANXIANG_REFUND_POOL_ID,
        "activity_limit": 1,
        "pay_id": WANXIANG_REFUND_PAY_ID,
        "price_cny_fen": 600,
        "voucher_cost": 6,
        "box_item_id": WANXIANG_REFUND_BOX_ITEM_ID,
        "refund_voucher_item_id": WANXIANG_REFUND_VOUCHER_ITEM_ID,
        "refund_voucher_amount": 6,
        "spirit_stone_item_id": WANXIANG_SPIRIT_STONE_ITEM_ID,
        "spirit_stone_reward": 1140,
        "source_chain": [
            "WanXiangShopPool:99001",
            "ChargeGoods:310001",
            "Item:1201",
            f"OptionalGift:{optional_group}",
            "Item:1012",
        ],
    }


def _wanxiang_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _WANXIANG_MANAGER_METHODS)
    instance = reader.fields(manager.get("inst"))
    model = reader.fields(instance.get("Model"))
    data = reader.fields(model.get("WanxiangshopData"))
    required = {
        "goodsIds",
        "goodsId2BuyTimesMap",
        "buyTimes",
        "refreshTimes",
    }
    missing = sorted(required - {str(key) for key in data})
    if missing:
        raise FanxiuRuntimeMemoryError(
            f"WanxiangshopMgr 当前活动数据尚未加载：missing={missing}",
            code="data_not_loaded",
        )
    return data


def _int_list(reader: LuaJitReader, value: Any, *, field: str) -> list[int]:
    # Empty Lua model lists are sometimes represented by an absent field after
    # the first server refresh.  The count/map ledgers remain the authoritative
    # proof that no item was bought.
    if value is None:
        return []
    values, declared_count = reader.list_items(value)
    if declared_count is None or declared_count != len(values):
        raise FanxiuRuntimeMemoryError(f"{field} 列表声明数量不一致")
    result = [as_int(item) for item in values]
    if any(item is None or int(item) <= 0 for item in result):
        raise FanxiuRuntimeMemoryError(f"{field} 含无效商品 ID")
    return [int(item) for item in result if item is not None]


def _purchase_counts(reader: LuaJitReader, value: Any) -> dict[int, int]:
    result: dict[int, int] = {}
    for raw_id, raw_count in reader.dictionary_fields(value).items():
        goods_id = as_int(raw_id)
        count = as_int(raw_count)
        if goods_id is None or goods_id <= 0 or count is None or count < 0:
            raise FanxiuRuntimeMemoryError("万象宝阁商品购买次数字典含无效项")
        if goods_id in result:
            raise FanxiuRuntimeMemoryError(f"万象宝阁商品购买次数重复：{goods_id}")
        result[int(goods_id)] = int(count)
    return result


def read_wanxiang_baoge_runtime() -> dict[str, Any]:
    """Read one coherent activity/shop/wallet/backpack snapshot.

    The function never calls a Lua method.  If the activity page has not
    naturally loaded ``WanxiangshopData`` it returns ``data_not_loaded``
    instead of scanning or initializing the model.
    """

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        activity = read_activity_menu_snapshot("world_left")
        if activity.complete is not True:
            raise FanxiuRuntimeMemoryError(
                f"万象宝阁活动身份不可读：{activity.reason or 'unknown'}"
            )
        candidates = [
            item
            for item in activity.items
            if int(item.base_id or 0) == WANXIANG_ACTIVITY_BASE_ID
        ]
        if len(candidates) != 1:
            raise FanxiuRuntimeMemoryError(
                f"当前开放万象宝阁实例不唯一：{[item.activity_id for item in candidates]}"
            )
        occurrence = candidates[0]
        memory = MumuProcessMemory.discover_cached()
        if (
            int(activity.pid or 0) != memory.pid
            or int(activity.process_start_ticks or 0) != memory.process_start_ticks
        ):
            raise FanxiuRuntimeMemoryError("活动身份与万象宝阁模型不属于同一游戏进程")
        state_address = int(_lua_addresses(memory)["state"], 16)
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="wanxiang-baoge",
            state_address=state_address,
            global_name="WanxiangshopMgr",
            required_methods=_WANXIANG_MANAGER_METHODS,
            validate=_wanxiang_data_fields,
        )
        reader = LuaJitReader(memory)
        data = _wanxiang_data_fields(reader, root)
        goods_ids = _int_list(reader, data.get("goodsIds"), field="goodsIds")
        bought_ids = _int_list(
            reader, data.get("buyGoodsIds"), field="buyGoodsIds"
        )
        purchase_counts = _purchase_counts(
            reader, data.get("goodsId2BuyTimesMap")
        )
        if any(purchase_counts.get(goods_id, 0) <= 0 for goods_id in bought_ids):
            raise FanxiuRuntimeMemoryError("已购商品列表与购买次数字典不一致")

        def validate_wallet(current_reader: LuaJitReader, address: int) -> None:
            for currency_type in (
                WANXIANG_VOUCHER_CURRENCY_TYPE,
                WANXIANG_BOUND_VOUCHER_CURRENCY_TYPE,
                WANXIANG_SPIRIT_STONE_CURRENCY_TYPE,
            ):
                wallet_currency_data(
                    current_reader,
                    address,
                    currency_type,
                    missing_as_zero=True,
                )

        wallet_root, wallet_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="wanxiang-baoge-wallet",
            state_address=state_address,
            global_name="WalletMgr",
            required_methods=WALLET_METHODS,
            validate=validate_wallet,
        )
        reader = LuaJitReader(memory)
        balances = {
            currency_type: wallet_currency_data(
                reader,
                wallet_root,
                currency_type,
                missing_as_zero=True,
            )["exchange_currency"]
            for currency_type in (
                WANXIANG_VOUCHER_CURRENCY_TYPE,
                WANXIANG_BOUND_VOUCHER_CURRENCY_TYPE,
                WANXIANG_SPIRIT_STONE_CURRENCY_TYPE,
            )
        }
        backpack, backpack_evidence = read_backpack_item_counts(
            [WANXIANG_REFUND_BOX_ITEM_ID], manager_key="wanxiang-refund-box"
        )
        if (
            int(backpack_evidence.get("pid") or 0) != memory.pid
            or int(backpack_evidence.get("process_start_ticks") or 0)
            != memory.process_start_ticks
        ):
            raise FanxiuRuntimeMemoryError("万象宝阁背包快照来自另一游戏进程")
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory.activity+wanxiangshop+wallet+backpack",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "activity_id": int(occurrence.activity_id or 0),
            "activity_config_id": int(occurrence.activity_id or 0),
            "activity_base_id": WANXIANG_ACTIVITY_BASE_ID,
            "activity_open": True,
            "goods_ids": goods_ids,
            "bought_goods_ids": bought_ids,
            "purchase_counts": purchase_counts,
            "buy_times": int(as_int(data.get("buyTimes")) or 0),
            "refresh_times": int(as_int(data.get("refreshTimes")) or 0),
            "voucher": int(balances[WANXIANG_VOUCHER_CURRENCY_TYPE]),
            "bound_voucher": int(
                balances[WANXIANG_BOUND_VOUCHER_CURRENCY_TYPE]
            ),
            "spirit_stone": int(balances[WANXIANG_SPIRIT_STONE_CURRENCY_TYPE]),
            "refund_box_count": int(backpack[WANXIANG_REFUND_BOX_ITEM_ID]),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "wanxiang_root": f"0x{root:x}",
                "wanxiang_root_cache_hit": cache_hit,
                "wallet_root": f"0x{wallet_root:x}",
                "wallet_root_cache_hit": wallet_cache_hit,
                "backpack": backpack_evidence,
                "read_only": True,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory.activity+wanxiangshop+wallet+backpack",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "read_only": True,
            },
        }


@dataclass(frozen=True)
class WanxiangRefundLedger:
    """Minimal Runtime accounting point around an irreversible action."""

    activity_id: int
    process_start_ticks: int
    target_purchase_count: int
    target_visible: bool
    voucher: int
    bound_voucher: int
    spirit_stone: int
    refund_box_count: int

    @property
    def voucher_total(self) -> int:
        return self.voucher + self.bound_voucher


def ledger_from_snapshot(snapshot: Mapping[str, Any]) -> WanxiangRefundLedger:
    if snapshot.get("complete") is not True:
        raise WanxiangRefundContractError("万象宝阁 Runtime 快照不完整")
    if int(snapshot.get("activity_base_id") or 0) != WANXIANG_ACTIVITY_BASE_ID:
        raise WanxiangRefundContractError("当前 Runtime 不是万象宝阁活动")
    if snapshot.get("activity_open") is not True:
        raise WanxiangRefundContractError("万象宝阁当前不在可购买窗口")
    goods_ids = snapshot.get("goods_ids")
    purchase_counts = snapshot.get("purchase_counts")
    if not isinstance(goods_ids, list) or not isinstance(purchase_counts, Mapping):
        raise WanxiangRefundContractError("万象宝阁商品或购买账本未完整加载")
    evidence = snapshot.get("evidence")
    if not isinstance(evidence, Mapping):
        raise WanxiangRefundContractError("万象宝阁 Runtime 缺少进程证据")
    return WanxiangRefundLedger(
        activity_id=int(snapshot.get("activity_id") or 0),
        process_start_ticks=int(evidence.get("process_start_ticks") or 0),
        target_purchase_count=int(
            purchase_counts.get(WANXIANG_REFUND_GOODS_ID)
            or purchase_counts.get(str(WANXIANG_REFUND_GOODS_ID))
            or 0
        ),
        target_visible=WANXIANG_REFUND_GOODS_ID
        in {int(value) for value in goods_ids},
        voucher=int(snapshot.get("voucher") or 0),
        bound_voucher=int(snapshot.get("bound_voucher") or 0),
        spirit_stone=int(snapshot.get("spirit_stone") or 0),
        refund_box_count=int(snapshot.get("refund_box_count") or 0),
    )


def decide_wanxiang_refund_action(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Authorize at most one next action; unknown payment state is a stop."""

    ledger = ledger_from_snapshot(snapshot)
    if ledger.activity_id <= 0 or ledger.process_start_ticks <= 0:
        raise WanxiangRefundContractError("万象宝阁活动或进程身份不完整")
    if ledger.target_purchase_count >= 1:
        if ledger.refund_box_count > 0:
            return {
                "action": "open_refund_box",
                "item_id": WANXIANG_REFUND_BOX_ITEM_ID,
                "max_count": 1,
                "reason": "6元商品已购买但代币宝匣尚未打开",
            }
        return {
            "action": "stop",
            "outcome": "already_completed",
            "reason": "6元商品已在购买账本且没有待开启宝匣",
        }
    if not ledger.target_visible:
        return {
            "action": "stop",
            "outcome": "target_not_visible",
            "reason": "当前五个商品不含 goodsId=99001；付费刷新未获本接口授权",
        }
    if ledger.voucher_total < 6:
        return {
            "action": "stop",
            "outcome": "real_money_risk",
            "reason": "充值代币余额不足6；点击会越过代币确认并落入真实支付",
        }
    return {
        "action": "purchase_with_voucher",
        "goods_id": WANXIANG_REFUND_GOODS_ID,
        "pay_id": WANXIANG_REFUND_PAY_ID,
        "voucher_cost": 6,
        "expected_confirmation": "VoucherUseTipsView",
        "reason": "Runtime证明目标可见、未购买且代币总额不少于6",
    }


def verify_wanxiang_purchase_transition(
    before: WanxiangRefundLedger, after: WanxiangRefundLedger
) -> dict[str, Any]:
    """Verify the purchase only; do not assume the refund box auto-opened."""

    _require_same_occurrence(before, after)
    if after.target_purchase_count - before.target_purchase_count != 1:
        raise WanxiangRefundContractError("6元商品购买次数没有精确增加1")
    if after.voucher_total != before.voucher_total - 6:
        raise WanxiangRefundContractError("6元商品购买后代币总额未精确减少6")
    if after.refund_box_count != before.refund_box_count + 1:
        raise WanxiangRefundContractError("6元商品购买后代币宝匣未精确增加1")
    return {"complete": True, "outcome": "purchased_box_pending"}


def verify_wanxiang_refund_box_transition(
    before: WanxiangRefundLedger, after: WanxiangRefundLedger
) -> dict[str, Any]:
    """Verify one box use: refund 6 voucher and award exactly 1140 stones."""

    _require_same_occurrence(before, after)
    if before.refund_box_count <= 0:
        raise WanxiangRefundContractError("打开前没有代币宝匣")
    if after.refund_box_count != before.refund_box_count - 1:
        raise WanxiangRefundContractError("代币宝匣数量未精确减少1")
    if after.voucher_total != before.voucher_total + 6:
        raise WanxiangRefundContractError("代币宝匣未精确返还6元代币")
    if after.spirit_stone != before.spirit_stone + 1140:
        raise WanxiangRefundContractError("代币宝匣未精确增加1140灵石")
    return {"complete": True, "outcome": "refund_complete", "net_value": 1140}


def _require_same_occurrence(
    before: WanxiangRefundLedger, after: WanxiangRefundLedger
) -> None:
    if (
        before.activity_id <= 0
        or before.activity_id != after.activity_id
        or before.process_start_ticks <= 0
        or before.process_start_ticks != after.process_start_ticks
    ):
        raise WanxiangRefundContractError("万象宝阁前后快照不属于同一活动/进程")


__all__ = [
    "WANXIANG_ACTIVITY_BASE_ID",
    "WANXIANG_REFUND_BOX_ITEM_ID",
    "WANXIANG_REFUND_GOODS_ID",
    "WANXIANG_REFUND_PAY_ID",
    "WanxiangRefundContractError",
    "WanxiangRefundLedger",
    "decide_wanxiang_refund_action",
    "ledger_from_snapshot",
    "load_wanxiang_refund_offer_contract",
    "read_wanxiang_baoge_runtime",
    "verify_wanxiang_purchase_transition",
    "verify_wanxiang_refund_box_transition",
]
