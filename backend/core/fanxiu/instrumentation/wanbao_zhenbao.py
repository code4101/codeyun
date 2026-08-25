from __future__ import annotations

"""Strict read-only Runtime projection for the Revenue/Mining 万宝臻宝 family.

The activity is not a ``BothdrawMgr`` lottery.  Its authoritative state is
split between ``RevenueMgr`` (configuration, cumulative rewards and shop),
``MiningMgr``/the embedded ``revenuePlayVO`` (draw and 飨珍 state), and
``QuestMgr`` (task claim state).  This module only reads already-loaded Lua
objects; it never initializes a manager or sends a game message.
"""

from datetime import datetime
import time
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.instrumentation.bothdraw import (
    _QUEST_METHODS,
    _REVENUE_METHODS,
    _dictionary_item,
    _dictionary_items,
    _fields,
    _list_values,
    _quest_data_fields,
    _revenue_data_fields,
    build_bothdraw_revenue_task_snapshot,
)
from backend.core.fanxiu.instrumentation.redbag_runtime_loader import _lua_addresses
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_lua_global_manager_root,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.wallet import WALLET_METHODS, wallet_currency_data


WANBAO_TEMPLATE_ID = 909
WANBAO_PERSISTENT_DRAW_CURRENCY_TYPE = 40017
WANBAO_EXCHANGE_CURRENCY_TYPE = 40012

_MINING_METHODS = frozenset({"Inst_get", "LuaMiningMgr", "GetMiningDataInfo"})


def _long_or_int(reader: LuaJitReader, value: Any) -> int | None:
    if isinstance(value, LuaRef):
        return reader.long(value)
    return as_int(value)


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise FanxiuRuntimeMemoryError(f"{field} 不是有效正整数")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise FanxiuRuntimeMemoryError(f"{field} 不是有效正整数") from exc
    if result <= 0:
        raise FanxiuRuntimeMemoryError(f"{field} 不是有效正整数")
    return result


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise FanxiuRuntimeMemoryError(f"{field} 不是有效非负整数")
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise FanxiuRuntimeMemoryError(f"{field} 不是有效非负整数") from exc
    if result < 0:
        raise FanxiuRuntimeMemoryError(f"{field} 不是有效非负整数")
    return result


def build_wanbao_cumulative_rewards(
    *,
    progress: int,
    milestones: Iterable[Mapping[str, Any]],
    claimed_ids: Iterable[int],
) -> dict[str, Any]:
    """Build the exact cumulative-draw ladder without inventing thresholds."""

    current = _nonnegative_int(progress, field="累抽进度")
    claimed = {int(value) for value in claimed_ids if int(value) > 0}
    rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    previous_target = -1
    for source in milestones:
        reward_id = _positive_int(source.get("id"), field="累抽奖励 id")
        target = _positive_int(source.get("progress"), field=f"累抽奖励 {reward_id} 目标")
        if reward_id in seen_ids:
            raise FanxiuRuntimeMemoryError(f"累抽奖励 id 重复：{reward_id}")
        if target <= previous_target:
            raise FanxiuRuntimeMemoryError("累抽奖励目标不是严格递增序列")
        seen_ids.add(reward_id)
        previous_target = target
        is_claimed = reward_id in claimed
        rows.append(
            {
                "id": reward_id,
                "target": target,
                "reward": str(source.get("reward") or ""),
                "claimed": is_claimed,
                "claimable": current >= target and not is_claimed,
            }
        )
    if not rows:
        raise FanxiuRuntimeMemoryError("万宝臻宝 Runtime 未提供累抽奖励档位")
    unknown_claimed = claimed - seen_ids
    if unknown_claimed:
        raise FanxiuRuntimeMemoryError(
            f"累抽已领取 id 不属于当前档位：{sorted(unknown_claimed)}"
        )
    return {
        "progress": current,
        "milestones": rows,
        "milestone_count": len(rows),
        "claimed_reward_ids": sorted(claimed),
        "claimable_reward_ids": [row["id"] for row in rows if row["claimable"]],
    }


def build_wanbao_draw_state(
    *,
    progress: int,
    cost_type: int,
    cost_per_draw: int,
    available_currency: int | None,
    smalls: Iterable[Mapping[str, Any]],
    hit_counts: Mapping[int, int],
) -> dict[str, Any]:
    """Project the authoritative first-big-reward state used by MiningMainPanel6."""

    current_progress = _nonnegative_int(progress, field="万宝累计抽数")
    normalized_cost_type = _positive_int(cost_type, field="万宝抽奖货币类型")
    normalized_cost = _positive_int(cost_per_draw, field="万宝单抽消耗")
    balance = (
        None
        if available_currency is None
        else _nonnegative_int(available_currency, field="万宝抽奖货币余额")
    )
    rows: list[dict[str, Any]] = []
    big_rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for source in smalls:
        item_id = _positive_int(source.get("id"), field="万宝奖池条目 id")
        if item_id in seen_ids:
            raise FanxiuRuntimeMemoryError(f"万宝奖池条目 id 重复：{item_id}")
        seen_ids.add(item_id)
        # Mining configs use ``0`` for an unlimited row.  A positive limit is
        # a pool cap; neither value changes the separate first-hit objective.
        limit = _nonnegative_int(
            source.get("limit"), field=f"万宝奖池条目 {item_id} 上限"
        )
        hit_count = _nonnegative_int(
            hit_counts.get(item_id, 0), field=f"万宝奖池条目 {item_id} 命中数"
        )
        if limit > 0 and hit_count > limit:
            raise FanxiuRuntimeMemoryError(
                f"万宝奖池条目 {item_id} 命中 {hit_count} 次，超过上限 {limit}"
            )
        row = {
            "id": item_id,
            "limit": limit,
            "hit_count": hit_count,
            "big_reward": bool(source.get("bigReward")),
            "reward": str(source.get("reward") or ""),
        }
        rows.append(row)
        if row["big_reward"]:
            big_rows.append(row)
    if not rows:
        raise FanxiuRuntimeMemoryError("万宝臻宝 Runtime 未提供当前奖池")
    if len(big_rows) != 1:
        raise FanxiuRuntimeMemoryError(
            f"万宝臻宝当前大奖条目不唯一：{[row['id'] for row in big_rows]}"
        )
    unknown_hit_ids = {int(key) for key in hit_counts} - seen_ids
    if unknown_hit_ids:
        raise FanxiuRuntimeMemoryError(
            f"万宝命中记录不属于当前奖池：{sorted(unknown_hit_ids)}"
        )
    grand_prize = big_rows[0]
    return {
        "strategy": "first_hit",
        "enabled": True,
        "cost_type": normalized_cost_type,
        "cost_per_draw": normalized_cost,
        "available_currency": balance,
        "available_draws": None if balance is None else balance // normalized_cost,
        "wallet_complete": balance is not None,
        "progress": current_progress,
        "x": current_progress,
        "y": grand_prize["hit_count"],
        "target_hit_count": 1,
        "target_complete": grand_prize["hit_count"] >= 1,
        "grand_prize": grand_prize,
        "pool_items": rows,
    }


def project_wanbao_exchange_shop(
    configs: Iterable[Mapping[str, Any]],
    *,
    purchased_counts: Mapping[int, int],
) -> dict[str, Any]:
    """Normalize the activity-owned exchange shop and its server ledger."""

    items: list[dict[str, Any]] = []
    seen_goods: set[int] = set()
    currencies: set[int] = set()
    for source in configs:
        goods_id = _positive_int(source.get("goodsId"), field="兑换商品 goodsId")
        if goods_id in seen_goods:
            raise FanxiuRuntimeMemoryError(f"兑换商品 goodsId 重复：{goods_id}")
        seen_goods.add(goods_id)
        currency_type = _positive_int(source.get("currencyType"), field="兑换货币类型")
        currencies.add(currency_type)
        limit = int(source.get("limitTimes") or 0)
        bought = _nonnegative_int(purchased_counts.get(goods_id, 0), field="已购买次数")
        if limit >= 0 and bought > limit:
            raise FanxiuRuntimeMemoryError(
                f"兑换商品 {goods_id} 已购买 {bought} 次，超过限购 {limit}"
            )
        items.append(
            {
                "goods_id": goods_id,
                "item_id": _positive_int(source.get("itemId"), field="兑换物品 id"),
                "goods_num": _positive_int(source.get("goodsNum"), field="兑换物品数量"),
                "currency_type": currency_type,
                "unit_price": _positive_int(source.get("buyPrice"), field="兑换价格"),
                "limit_times": limit,
                "purchased_count": bought,
                "remaining": None if limit < 0 else limit - bought,
                "position": int(source.get("position") or 0),
                "show_limit": str(source.get("showLimit") or ""),
                "disappear_limit": str(source.get("disappearLimit") or ""),
                "reward": str(source.get("reward") or ""),
            }
        )
    items.sort(key=lambda item: (item["position"], item["goods_id"]))
    if not items:
        raise FanxiuRuntimeMemoryError("万宝臻宝 Runtime 未提供兑换商店配置")
    return {
        "items": items,
        "item_count": len(items),
        "currency_types": sorted(currencies),
    }


def _scalar_fields(reader: LuaJitReader, value: Any) -> dict[str, Any]:
    return {
        str(key): item
        for key, item in _fields(reader, value).items()
        if item is None or isinstance(item, (str, int, float, bool))
    }


def _revenue_activity(
    reader: LuaJitReader,
    revenue_data: Mapping[Any, Any],
    expected_activity_id: int | None,
) -> tuple[int, dict[Any, Any]]:
    activities = _dictionary_items(reader, revenue_data.get("V_ActivityDic"))
    candidates: list[tuple[int, dict[Any, Any]]] = []
    for raw_id, value in activities.items():
        activity_id = int(raw_id)
        fields = _fields(reader, value)
        base = _fields(reader, fields.get("revenueBaseVO"))
        if int(base.get("templateId") or 0) == WANBAO_TEMPLATE_ID:
            candidates.append((activity_id, fields))
    if expected_activity_id is not None:
        candidates = [item for item in candidates if item[0] == int(expected_activity_id)]
    if len(candidates) != 1:
        raise FanxiuRuntimeMemoryError(
            "RevenueMgr 当前万宝臻宝实例不唯一："
            f"expected={expected_activity_id}, candidates={[item[0] for item in candidates]}"
        )
    return candidates[0]


def _task_snapshot(
    reader: LuaJitReader,
    quest_data: Mapping[Any, Any],
    *,
    activity_id: int,
) -> dict[str, Any]:
    activity_model = _fields(
        reader,
        _dictionary_item(reader, quest_data.get("V_AllTaskInfoDic"), activity_id),
    )
    storage = activity_model.get("_dt_")
    if not isinstance(storage, LuaRef) or storage.kind != "table":
        raise FanxiuRuntimeMemoryError(f"活动 {activity_id} 的任务 UI 模型尚未加载")
    fields = reader.table(storage.address).get("fields") or {}
    all_rows = _dictionary_items(reader, fields.get("vodic"))
    if not all_rows:
        raise FanxiuRuntimeMemoryError(f"活动 {activity_id} 的任务行尚未加载")
    groups: dict[int, list[dict[str, Any]]] = {}
    grouped_ids: set[int] = set()
    for raw_group_id, group_value in fields.items():
        if not isinstance(raw_group_id, (int, float)) or int(raw_group_id) != raw_group_id:
            continue
        rows: list[dict[str, Any]] = []
        for value in _list_values(reader, group_value):
            row = _fields(reader, value)
            task_id = _positive_int(row.get("id"), field="活动任务 id")
            if task_id in grouped_ids:
                raise FanxiuRuntimeMemoryError(f"活动任务跨分组重复：{task_id}")
            grouped_ids.add(task_id)
            rows.append(
                {
                    "id": task_id,
                    "isFinished": row.get("isFinished"),
                    "serverData": _fields(reader, row.get("serverData")),
                }
            )
        if rows:
            groups[int(raw_group_id)] = rows
    if grouped_ids != {int(key) for key in all_rows}:
        raise FanxiuRuntimeMemoryError("活动任务分组与全量行不一致")
    return build_bothdraw_revenue_task_snapshot(
        activity_id=activity_id,
        task_groups=groups,
    )


def _purchase_counts(reader: LuaJitReader, revenue_model: Mapping[Any, Any]) -> dict[int, int]:
    exchange = _fields(reader, revenue_model.get("V_RevenueExchange"))
    counts: dict[int, int] = {}
    for raw_goods_id, value in _dictionary_items(reader, exchange.get("V_ShopInfo")).items():
        goods_id = int(raw_goods_id)
        info = _fields(reader, value)
        server = _fields(reader, info.get("svr"))
        server_goods_id = int(server.get("shopItemId") or 0)
        if server_goods_id != goods_id:
            raise FanxiuRuntimeMemoryError(f"兑换购买记录身份不一致：{goods_id}")
        counts[goods_id] = _nonnegative_int(server.get("num"), field="兑换购买次数")
    return counts


def _wallet_balance(
    memory: MumuProcessMemory,
    *,
    state_address: int,
    currency_type: int,
) -> tuple[int | None, bool, str]:
    try:
        root, cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key=f"wanbao-wallet-{currency_type}",
            state_address=state_address,
            global_name="WalletMgr",
            required_methods=WALLET_METHODS,
            validate=lambda reader, address: wallet_currency_data(reader, address, currency_type),
        )
        wallet = wallet_currency_data(LuaJitReader(memory), root, currency_type)
        return int(wallet["exchange_currency"]), cache_hit, ""
    except Exception as exc:
        return None, False, str(exc)


def _mining_data_fields(reader: LuaJitReader, root_address: int) -> dict[Any, Any]:
    manager = manager_index_fields(reader, root_address, _MINING_METHODS)
    instance = _fields(reader, manager.get("inst"))
    model = _fields(reader, instance.get("Model"))
    data = _fields(reader, model.get("MiningData"))
    if "_MiningInfoMap" not in data:
        raise FanxiuRuntimeMemoryError("MiningMgr 活动数据尚未加载")
    return data


def _mining_box_snapshot(
    reader: LuaJitReader,
    mining_data: Mapping[Any, Any],
    *,
    activity_id: int,
) -> dict[str, Any]:
    """Project the same unopened-box predicate used by ``MiningData``.

    This is a read-only translation of ``IsHasBox``/``IsOpenedBox``.  It does
    not call either Lua method and therefore cannot initialize or mutate the
    game model.
    """

    play = _fields(
        reader,
        _dictionary_item(reader, mining_data.get("_MiningInfoMap"), activity_id),
    )
    if not play:
        raise FanxiuRuntimeMemoryError(f"MiningMgr 未加载活动 {activity_id}")
    item_limits: dict[int, int] = {}
    for value in _list_values(reader, play.get("items")):
        row = _fields(reader, value)
        storey = _positive_int(row.get("storey"), field="飨珍层数")
        box_limit = _nonnegative_int(row.get("boxLimit"), field="飨珍层开启上限")
        if storey in item_limits:
            raise FanxiuRuntimeMemoryError(f"飨珍层配置重复：{storey}")
        item_limits[storey] = box_limit

    opened_positions: set[tuple[int, int]] = set()
    opened_count_by_storey: dict[int, int] = {}
    open_rows: list[dict[str, int]] = []
    for value in _list_values(reader, play.get("openBoxes")):
        row = _fields(reader, value)
        storey = _positive_int(row.get("storey"), field="已开飨珍层数")
        position = _nonnegative_int(row.get("position"), field="已开飨珍位置")
        key = (storey, position)
        if key in opened_positions:
            raise FanxiuRuntimeMemoryError(f"已开飨珍位置重复：{key}")
        opened_positions.add(key)
        opened_count_by_storey[storey] = opened_count_by_storey.get(storey, 0) + 1
        open_rows.append({"storey": storey, "position": position})

    club_mining = bool(play.get("clubMining"))
    join_club_at = _long_or_int(reader, play.get("joinClubAt"))
    boxes: list[dict[str, Any]] = []
    for value in _list_values(reader, play.get("boxes")):
        row = _fields(reader, value)
        box_id = _positive_int(row.get("id"), field="飨珍 id")
        storey = _positive_int(row.get("storey"), field="飨珍层数")
        position = _nonnegative_int(row.get("position"), field="飨珍位置")
        grid_num = _positive_int(row.get("gridNum"), field="飨珍格数")
        opened_grids = len(_list_values(reader, row.get("grids")))
        if opened_grids > grid_num:
            raise FanxiuRuntimeMemoryError(f"飨珍 {box_id} 已开格数超过总格数")
        limit = item_limits.get(storey)
        if limit is None:
            raise FanxiuRuntimeMemoryError(f"飨珍 {box_id} 缺少第 {storey} 层配置")
        create_at = _long_or_int(reader, row.get("createAt"))
        expiration_known = not club_mining or (
            create_at is not None and join_club_at is not None
        )
        expired = bool(
            club_mining
            and create_at is not None
            and join_club_at is not None
            and create_at < join_club_at
        )
        opened = (
            (storey, position) in opened_positions
            or opened_count_by_storey.get(storey, 0) >= limit
            or expired
        )
        full = opened_grids >= grid_num
        boxes.append(
            {
                "id": box_id,
                "storey": storey,
                "position": position,
                "grid_num": grid_num,
                "opened_grid_count": opened_grids,
                "creator": str(row.get("creator") or ""),
                "box_item_id": int(row.get("boxItemId") or 0),
                "opened": opened,
                "full": full,
                "expired": expired,
                "expiration_known": expiration_known,
                "claimable": expiration_known and not opened and not full,
            }
        )
    if len({row["id"] for row in boxes}) != len(boxes):
        raise FanxiuRuntimeMemoryError("MiningMgr 飨珍 id 重复")
    candidates = [row for row in boxes if row["claimable"]]
    all_expiration_known = all(row["expiration_known"] for row in boxes)
    return {
        "box_count": len(boxes),
        "open_box_record_count": len(open_rows),
        "claimable_box_count": len(candidates),
        "claimable_box_ids": [row["id"] for row in candidates],
        "boxes": boxes,
        "claim_action_ready": all_expiration_known and bool(candidates),
        "claim_action_reason": (
            f"MiningData.IsHasBox 等价投影确认 {len(candidates)} 个可开飨珍"
            if all_expiration_known and candidates
            else "MiningData 等价投影未发现可开飨珍"
            if all_expiration_known
            else "宗门飨珍时间边界不完整，拒绝点击"
        ),
    }


def read_wanbao_zhenbao_runtime(
    *,
    expected_activity_id: int | None = None,
) -> dict[str, Any]:
    """Read a coherent, fail-closed snapshot of the current 万宝臻宝 activity."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        state_address = int(_lua_addresses(memory)["state"], 16)
        revenue_root, revenue_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="wanbao-revenue",
            state_address=state_address,
            global_name="RevenueMgr",
            required_methods=_REVENUE_METHODS,
            validate=_revenue_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        revenue_manager = _fields(
            reader,
            manager_index_fields(reader, revenue_root, _REVENUE_METHODS).get("inst"),
        )
        revenue_model = _fields(reader, revenue_manager.get("Model"))
        revenue_data = _revenue_data_fields(reader, revenue_root)
        activity_id, activity = _revenue_activity(
            reader, revenue_data, expected_activity_id
        )
        base = _fields(reader, activity.get("revenueBaseVO"))
        play = _fields(reader, activity.get("revenuePlayVO"))
        if int(base.get("costType") or 0) != WANBAO_PERSISTENT_DRAW_CURRENCY_TYPE:
            raise FanxiuRuntimeMemoryError(
                f"万宝臻宝抽奖货币异常：{base.get('costType')}"
            )
        role_progress = _fields(reader, play.get("roleProgress"))
        cumulative_milestones = [
            _scalar_fields(reader, value)
            for value in _list_values(
                reader, _fields(reader, activity.get("rateConfigs")).get("itemList")
            )
        ]
        shop = project_wanbao_exchange_shop(
            [
                _scalar_fields(reader, value)
                for value in _list_values(
                    reader, _fields(reader, activity.get("shopConfigs")).get("itemList")
                )
            ],
            purchased_counts=_purchase_counts(reader, revenue_model),
        )
        if shop["currency_types"] != [WANBAO_EXCHANGE_CURRENCY_TYPE]:
            raise FanxiuRuntimeMemoryError(
                f"万宝臻宝兑换货币异常：{shop['currency_types']}"
            )

        quest_root, quest_cache_hit = resolve_manager_root(
            memory,
            manager_key="wanbao-quest-tasks",
            marker=b"LuaQuestMgr",
            required_methods=_QUEST_METHODS,
            validate=_quest_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        tasks = _task_snapshot(
            reader,
            _quest_data_fields(reader, quest_root),
            activity_id=activity_id,
        )
        draw_balance, draw_wallet_cache_hit, draw_wallet_reason = _wallet_balance(
            memory,
            state_address=state_address,
            currency_type=WANBAO_PERSISTENT_DRAW_CURRENCY_TYPE,
        )
        exchange_balance, exchange_wallet_cache_hit, exchange_wallet_reason = _wallet_balance(
            memory,
            state_address=state_address,
            currency_type=WANBAO_EXCHANGE_CURRENCY_TYPE,
        )
        mining_root, mining_cache_hit, _environment = resolve_lua_global_manager_root(
            memory,
            manager_key="wanbao-mining",
            state_address=state_address,
            global_name="MiningMgr",
            required_methods=_MINING_METHODS,
            validate=_mining_data_fields,
            force_refresh=True,
        )
        reader = LuaJitReader(memory)
        mining_data = _mining_data_fields(reader, mining_root)
        mining_play = _fields(
            reader,
            _dictionary_item(reader, mining_data.get("_MiningInfoMap"), activity_id),
        )
        if not mining_play:
            raise FanxiuRuntimeMemoryError(f"MiningMgr 未加载活动 {activity_id}")
        mining_role_progress = _fields(reader, mining_play.get("roleProgress"))
        mining_progress = int(mining_role_progress.get("progress") or 0)
        # MiningData.UpdateMiningDraw owns the live draw transaction.  The
        # embedded Revenue play can remain at its opening value for the whole
        # panel lifetime, so it is provenance only and must not veto the
        # authoritative Mining roleProgress after a draw.
        cumulative = build_wanbao_cumulative_rewards(
            progress=mining_progress,
            milestones=cumulative_milestones,
            claimed_ids=[
                int(value)
                for value in _list_values(reader, role_progress.get("draws"))
                if int(value or 0) > 0
            ],
        )
        hit_counts = {
            int(raw_id): _nonnegative_int(value, field=f"万宝命中记录 {raw_id}")
            for raw_id, value in _dictionary_items(reader, mining_play.get("hitCount")).items()
        }
        draw = build_wanbao_draw_state(
            progress=mining_progress,
            cost_type=int(base.get("costType") or 0),
            cost_per_draw=int(base.get("costValue") or 0),
            available_currency=draw_balance,
            smalls=[
                _scalar_fields(reader, value)
                for value in _list_values(reader, mining_play.get("smalls"))
            ],
            hit_counts=hit_counts,
        )
        draw.update(
            {
                "wallet_reason": draw_wallet_reason,
                "times": int(mining_play.get("times") or play.get("times") or 0),
            }
        )
        xiangzhen = _mining_box_snapshot(
            reader,
            mining_data,
            activity_id=activity_id,
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory.revenue+mining+quest+wallet",
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "activity_id": activity_id,
            "template_id": int(base.get("templateId") or 0),
            "name": str(base.get("name") or ""),
            "activity_base": _scalar_fields(reader, base),
            "draw": draw,
            "cumulative_rewards": cumulative,
            "tasks": tasks,
            "exchange_shop": {
                **shop,
                "available_currency": exchange_balance,
                "wallet_complete": exchange_balance is not None,
                "wallet_reason": exchange_wallet_reason,
            },
            "xiangzhen": xiangzhen,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "revenue_root_cache_hit": revenue_cache_hit,
                "quest_root_cache_hit": quest_cache_hit,
                "draw_wallet_cache_hit": draw_wallet_cache_hit,
                "exchange_wallet_cache_hit": exchange_wallet_cache_hit,
                "mining_root_cache_hit": mining_cache_hit,
                "read_only": True,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory.revenue+mining+quest+wallet",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
                "read_only": True,
            },
        }


def read_wanbao_task_runtime(*, expected_activity_id: int) -> dict[str, Any]:
    """Read only the already-loaded RevenueTask model for one 万宝 instance."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        memory = MumuProcessMemory.discover_cached()
        root, cache_hit = resolve_manager_root(
            memory,
            manager_key="wanbao-quest-tasks",
            marker=b"LuaQuestMgr",
            required_methods=_QUEST_METHODS,
            validate=_quest_data_fields,
            force_refresh=False,
        )
        reader = LuaJitReader(memory)
        snapshot = _task_snapshot(
            reader,
            _quest_data_fields(reader, root),
            activity_id=int(expected_activity_id),
        )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory.quest.revenue_task",
            **snapshot,
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "quest_root_cache_hit": cache_hit,
                "read_only": True,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory.quest.revenue_task",
            "reason": str(exc),
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": memory.process_start_ticks if memory is not None else None,
                "read_only": True,
            },
        }


__all__ = [
    "WANBAO_EXCHANGE_CURRENCY_TYPE",
    "WANBAO_PERSISTENT_DRAW_CURRENCY_TYPE",
    "WANBAO_TEMPLATE_ID",
    "build_wanbao_cumulative_rewards",
    "project_wanbao_exchange_shop",
    "read_wanbao_task_runtime",
    "read_wanbao_zhenbao_runtime",
]
