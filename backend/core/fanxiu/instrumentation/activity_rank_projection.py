from __future__ import annotations

"""Shared projection of activity-rank Runtime facts."""

from typing import Any

from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    read_activity_rank_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
)


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

def project_activity_rank_data(
    reader: LuaJitReader,
    root_address: int,
    rank_activity_id: int,
    *,
    include_rankings: bool = True,
    reward_tiers: list[dict[str, Any]] | None = None,
    label: str = "活动榜",
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
        raise FanxiuRuntimeMemoryError(f"{label}排名奖励档位配置为空")
    active_tiers = reward_guard_tiers(reward_tiers, rank_list_size)
    guard_ranks = [int(row["rank_end"]) for row in active_tiers]
    missing_guard_ranks = [value for value in guard_ranks if value not in rows_by_rank]
    if missing_guard_ranks:
        raise FanxiuRuntimeMemoryError(
            f"{label}奖励档位尚未完整同步："
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

__all__ = ["project_activity_rank_data", "reward_guard_tiers"]
