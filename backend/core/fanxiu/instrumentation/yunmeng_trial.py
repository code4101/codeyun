from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    read_activity_rank_snapshot,
    resolve_activity_rank_root,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    LuaRef,
    MumuProcessMemory,
    as_int,
    manager_index_fields,
    resolve_manager_root,
)
from backend.core.fanxiu.instrumentation.wallet import (
    WALLET_MARKER as _WALLET_MARKER,
    WALLET_METHODS as _WALLET_METHODS,
    wallet_currency_data as _wallet_data,
)


_YUNMENG_MARKER = b"LuaYunmengpkMgr"
_YUNMENG_METHODS = frozenset({"LuaYunmengpkMgr", "Inst_get", "GetActivityVO"})


def reward_guard_tiers(
    reward_tiers: list[dict[str, Any]],
    rank_list_size: int,
) -> list[dict[str, Any]]:
    """Return reward intervals whose last-place guard exists in the rank list."""

    return [
        row
        for row in reward_tiers
        if int(row["rank_end"]) <= int(rank_list_size)
    ]


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


def _rank_data(
    reader: LuaJitReader,
    root_address: int,
    rank_activity_id: int,
    *,
    include_rankings: bool = True,
    reward_tiers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = read_activity_rank_snapshot(reader, root_address, rank_activity_id)
    self_rank = snapshot.self_ranking
    score = int(self_rank["score"])
    rank = int(self_rank["rank"])
    self_role_key = str(self_rank["role_key"])
    self_result = {
        "score": score,
        "rank": rank,
        "role_key": self_role_key,
        "name": str(self_rank.get("name") or ""),
        "server_id": self_rank.get("server_id"),
        "server_name": str(self_rank.get("server_name") or ""),
        "club_name": str(self_rank.get("club_name") or ""),
    }
    if not include_rankings:
        return self_result
    rank_list_size = snapshot.rank_list_size
    rows_by_rank = {
        int(row["rank"]): {
            **dict(row),
            "is_self": False,
            "is_reward_guard": False,
        }
        for row in snapshot.rankings
    }
    if not reward_tiers:
        raise FanxiuRuntimeMemoryError("云梦排名奖励档位配置为空")
    active_tiers = reward_guard_tiers(reward_tiers, rank_list_size)
    guard_ranks = [int(row["rank_end"]) for row in active_tiers]
    missing_guard_ranks = [value for value in guard_ranks if value not in rows_by_rank]
    if missing_guard_ranks:
        raise FanxiuRuntimeMemoryError(
            "云梦活动榜奖励档位尚未完整同步："
            + ",".join(str(value) for value in missing_guard_ranks)
        )
    rankings: list[dict[str, Any]] = []
    for guard_rank in guard_ranks:
        row = dict(rows_by_rank[guard_rank])
        row["is_reward_guard"] = True
        tier = next(item for item in active_tiers if int(item["rank_end"]) == guard_rank)
        row["reward_rank_start"] = int(tier["rank_start"])
        row["reward_rank_end"] = int(tier["rank_end"])
        rankings.append(row)
    self_row = {
        "rank": rank,
        "score": score,
        "role_key": self_role_key,
        "name": str(self_rank.get("name") or ""),
        "server_id": self_rank.get("server_id"),
        "server_name": str(self_rank.get("server_name") or ""),
        "club_name": str(self_rank.get("club_name") or ""),
        "is_self": True,
        "is_reward_guard": rank in guard_ranks,
        "reward_rank_start": None,
        "reward_rank_end": None,
    }
    matching_self = next(
        (
            row
            for row in rankings
            if row["rank"] == rank and row["role_key"] == self_role_key
        ),
        None,
    )
    if matching_self is None:
        rankings.append(self_row)
    else:
        matching_self["is_self"] = True
    if rank_list_size > 0 and not any(row["rank"] == rank_list_size for row in rankings):
        last_row = rows_by_rank.get(rank_list_size)
        if last_row is not None:
            last_row = dict(last_row)
            last_row.update(
                is_last_player=True,
                is_reward_guard=False,
                reward_rank_start=None,
                reward_rank_end=None,
            )
            rankings.append(last_row)
    return {
        **self_result,
        "rank_list_size": rank_list_size,
        "loaded_rank_count": snapshot.loaded_rank_count,
        "declared_rank_count": snapshot.declared_rank_count,
        "reward_guard_ranks": guard_ranks,
        "rankings": sorted(rankings, key=lambda row: int(row["rank"])),
    }


def _quick_auto_currency_delta(
    reader: LuaJitReader,
    root_address: int,
    currency_type: int,
) -> dict[str, Any]:
    manager = manager_index_fields(reader, root_address, _YUNMENG_METHODS)
    instance = _required_fields(reader, manager.get("inst"), ("Model",), "云梦管理器")
    model = _required_fields(reader, instance["Model"], ("YunmengpkData",), "云梦模型")
    data = _required_fields(
        reader,
        model["YunmengpkData"],
        ("_QuickAutoInfo", "_AutoFightCount"),
        "云梦自动挑战数据",
    )
    quick = _required_fields(
        reader,
        data["_QuickAutoInfo"],
        ("rewardList", "winCount", "failCount", "totalScore"),
        "云梦自动挑战结果",
    )
    quick_ref = data["_QuickAutoInfo"]
    reward_list_ref = quick["rewardList"]
    rewards, declared_count = reader.list_items(quick["rewardList"])
    matching_amounts: list[int] = []
    for reward_value in rewards:
        reward = reader.fields(reward_value)
        if as_int(reward.get("type")) != 1 or as_int(reward.get("code")) != int(
            currency_type
        ):
            continue
        amount = reader.long(reward.get("amount"))
        if amount is not None:
            matching_amounts.append(int(amount))
    if len(matching_amounts) != 1:
        raise FanxiuRuntimeMemoryError(
            f"云梦自动挑战奖励中兑币类型 {int(currency_type)} 不唯一"
        )
    challenge_count = as_int(data.get("_AutoFightCount"))
    if challenge_count is None:
        raise FanxiuRuntimeMemoryError("云梦自动挑战次数无效")
    return {
        "exchange_currency_delta": matching_amounts[0],
        "auto_fight_count": challenge_count,
        "auto_win_count": as_int(quick.get("winCount")) or 0,
        "auto_fail_count": as_int(quick.get("failCount")) or 0,
        "auto_total_score": as_int(quick.get("totalScore")) or 0,
        "auto_reward_count": declared_count or len(rewards),
        "quick_auto_result_address": (
            f"0x{quick_ref.address:x}" if isinstance(quick_ref, LuaRef) else None
        ),
        "quick_auto_reward_list_address": (
            f"0x{reward_list_ref.address:x}"
            if isinstance(reward_list_ref, LuaRef)
            else None
        ),
    }


def read_yunmeng_trial_status_snapshot(
    *,
    rank_activity_id: int,
    currency_type: int,
    previous_exchange_currency: int | None = None,
    previous_cumulative_currency: int | None = None,
    expected_challenge_count: int | None = None,
    event_date: str,
) -> dict[str, Any]:
    """Read Yunmeng state through already validated Runtime caches only.

    This is a user-facing refresh path.  A missing cache must fail fast instead
    of discovering Lua roots synchronously: full process/root scans are an
    explicit diagnostic operation and can stall the emulator.
    """

    started_at = time.perf_counter()
    memory = MumuProcessMemory.discover_cached(fallback_to_discovery=False)
    reader = LuaJitReader(memory)
    from backend.core.fanxiu.activity.yunmeng_rank_reward import (
        load_yunmeng_rank_reward_tiers,
    )

    reward_tiers = load_yunmeng_rank_reward_tiers(
        rank_activity_id=int(rank_activity_id),
        event_date=event_date,
    )
    wallet_root: int | None = None
    wallet_cache_hit = False
    yunmeng_root: int | None = None
    yunmeng_cache_hit = False
    currency_derivation = "wallet_amount_minus_borrow"
    quick_auto: dict[str, Any] = {}
    try:
        wallet_root, wallet_cache_hit = resolve_manager_root(
            memory,
            manager_key="wallet",
            marker=_WALLET_MARKER,
            required_methods=_WALLET_METHODS,
            validate=lambda current_reader, address: _wallet_data(
                current_reader, address, int(currency_type)
            ),
            allow_discovery=False,
        )
        wallet_data = _wallet_data(reader, wallet_root, int(currency_type))
    except FanxiuRuntimeMemoryError:
        if expected_challenge_count is None:
            raise FanxiuRuntimeMemoryError(
                "云梦兑币精确缓存尚未就绪；本次未扫描游戏内存，也未写入数据库"
            )
        if previous_exchange_currency is None:
            raise
        yunmeng_root, yunmeng_cache_hit = resolve_manager_root(
            memory,
            manager_key="yunmeng-trial-quick-auto",
            marker=_YUNMENG_MARKER,
            required_methods=_YUNMENG_METHODS,
            validate=lambda current_reader, address: _quick_auto_currency_delta(
                current_reader, address, int(currency_type)
            ),
            allow_discovery=False,
        )
        quick_auto = _quick_auto_currency_delta(
            reader, yunmeng_root, int(currency_type)
        )
        if (
            expected_challenge_count is not None
            and quick_auto["auto_fight_count"] != int(expected_challenge_count)
        ):
            raise FanxiuRuntimeMemoryError(
                "云梦自动挑战运行态次数与请求不一致："
                f"运行态 {quick_auto['auto_fight_count']}，"
                f"请求 {int(expected_challenge_count)}"
            )
        currency_delta = quick_auto["exchange_currency_delta"]
        current_currency = int(previous_exchange_currency) + currency_delta
        wallet_data = {
            "exchange_currency": current_currency,
            "currency_amount": current_currency,
            "currency_borrow": 0,
            "cumulative_currency": int(
                previous_cumulative_currency
                if previous_cumulative_currency is not None
                else previous_exchange_currency
            )
            + currency_delta,
        }
        currency_derivation = "previous_measurement_plus_quick_auto_reward"
        quick_auto["quick_auto_run_key"] = (
            f"{memory.process_start_ticks}:"
            f"{quick_auto.get('quick_auto_result_address')}:"
            f"{quick_auto.get('quick_auto_reward_list_address')}"
        )
    rank_root, rank_cache_hit = resolve_activity_rank_root(
        memory,
        allow_discovery=False,
    )
    rank_data = _rank_data(
        reader,
        rank_root,
        int(rank_activity_id),
        reward_tiers=reward_tiers,
    )
    captured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "source": "runtime_memory",
        "protocol": {
            "score": (
                "ActivityrankMgr.Model.ActivityrankData."
                f"V_RankDataDic[{int(rank_activity_id)}].selfRankVO"
            ),
            "exchange_currency": (
                (
                    "WalletMgr.Model.WalletData."
                    f"_WalletInfo[{int(currency_type)}].amount-borrow"
                )
                if currency_derivation == "wallet_amount_minus_borrow"
                else (
                    "previous_measurement + YunmengpkData."
                    f"_QuickAutoInfo.rewardList[code={int(currency_type)},type=1].amount"
                )
            ),
        },
        "rank_activity_id": int(rank_activity_id),
        "currency_type": int(currency_type),
        "reward_tiers": reward_tiers,
        "captured_at": captured_at,
        **rank_data,
        **wallet_data,
        **quick_auto,
        "currency_derivation": currency_derivation,
        "elapsed_seconds": time.perf_counter() - started_at,
        "evidence": {
            "pid": memory.pid,
            "process_start_ticks": memory.process_start_ticks,
            "rank_root_address": f"0x{rank_root:x}",
            "wallet_root_address": f"0x{wallet_root:x}" if wallet_root else None,
            "yunmeng_root_address": f"0x{yunmeng_root:x}" if yunmeng_root else None,
            "rank_root_cache_hit": rank_cache_hit,
            "wallet_root_cache_hit": wallet_cache_hit,
            "yunmeng_root_cache_hit": yunmeng_cache_hit,
        },
    }
