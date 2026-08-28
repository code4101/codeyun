from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from backend.core.fanxiu.activity.rank_reward import (
    load_activity_rank_reward_tiers,
)
from backend.core.fanxiu.instrumentation.activity_rank_runtime import (
    read_activity_rank_snapshot,
    resolve_activity_rank_root,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
    LuaJitReader,
    MumuProcessMemory,
)
from backend.core.fanxiu.instrumentation.activity_rank_projection import (
    reward_guard_tiers,
)


LINGZHUANG_HUADAO_RANK_ACTIVITY_ID = 44307
LINGZHUANG_HUADAO_PLANE_RANK_ACTIVITY_ID = 44308
TALENT_PILL_ITEM_ID = 9070095

def _reward_item_count(tier: dict[str, Any], *, item_id: int) -> int:
    total = 0
    for raw_reward in tier.get("rewards") or []:
        reward_type, separator, payload = str(raw_reward).partition("|")
        if separator != "|" or reward_type != "Item":
            continue
        parts = payload.split("_")
        if len(parts) < 2:
            continue
        try:
            reward_item_id = int(parts[0])
            reward_count = int(parts[1])
        except (TypeError, ValueError):
            continue
        if reward_item_id == int(item_id):
            total += reward_count
    return total


def _rank_data(
    reader: LuaJitReader,
    root_address: int,
    rank_activity_id: int,
    *,
    reward_tiers: list[dict[str, Any]] | None = None,
    key_points_only: bool = True,
) -> dict[str, Any]:
    snapshot = read_activity_rank_snapshot(reader, root_address, rank_activity_id)
    self_row = dict(snapshot.self_ranking)
    loaded_rankings = []
    for source_row in snapshot.rankings:
        row = dict(source_row)
        row.update(
            is_self=False,
            is_reward_guard=False,
            reward_rank_start=None,
            reward_rank_end=None,
            talent_pill_count=None,
            score_per_talent_pill=None,
            has_player=True,
            is_last_player=False,
        )
        loaded_rankings.append(row)
    self_row.update(
        is_self=False,
        is_reward_guard=False,
        reward_rank_start=None,
        reward_rank_end=None,
        talent_pill_count=None,
        score_per_talent_pill=None,
        has_player=True,
        is_last_player=False,
    )
    rank_list_size = snapshot.rank_list_size
    declared_count = snapshot.declared_rank_count

    if not key_points_only:
        matching_self = None
        for row in loaded_rankings:
            if (
                int(self_row["rank"]) > 0
                and row["rank"] == self_row["rank"]
                and row["role_key"] == self_row["role_key"]
            ):
                row["is_self"] = True
                matching_self = row
        if matching_self is None:
            own_row = dict(self_row)
            own_row["is_self"] = True
            loaded_rankings.append(own_row)
            loaded_rankings.sort(key=lambda row: int(row["rank"]))
        return {
            "rank_list_size": rank_list_size,
            "loaded_rank_count": len(loaded_rankings),
            "declared_rank_count": declared_count,
            "self_ranking": self_row,
            "rankings": loaded_rankings,
        }

    if not reward_tiers:
        raise FanxiuRuntimeMemoryError("灵装化道排名奖励档位配置为空")
    rows_by_rank = {int(row["rank"]): row for row in loaded_rankings}
    active_tiers = reward_guard_tiers(reward_tiers, rank_list_size)
    rankings: list[dict[str, Any]] = []
    for tier in active_tiers:
        guard_rank = int(tier["rank_end"])
        talent_pill_count = _reward_item_count(
            tier,
            item_id=TALENT_PILL_ITEM_ID,
        )
        row = dict(rows_by_rank.get(guard_rank) or {
            "rank": guard_rank,
            "score": 0,
            "role_key": f"reward-tier:{int(tier['rank_start'])}-{guard_rank}",
            "name": "",
            "server_id": None,
            "server_name": "",
            "club_name": "",
            "has_player": False,
            "is_self": False,
            "is_last_player": False,
        })
        row.update(
            is_reward_guard=True,
            reward_rank_start=int(tier["rank_start"]),
            reward_rank_end=guard_rank,
            talent_pill_count=talent_pill_count,
            score_per_talent_pill=(
                row["score"] / talent_pill_count
                if row["has_player"] and talent_pill_count > 0
                else None
            ),
        )
        rankings.append(row)

    matching_self = next(
        (
            row
            for row in rankings
            if row["rank"] == self_row["rank"]
            and row["role_key"] == self_row["role_key"]
        ),
        None,
    )
    if matching_self is not None:
        matching_self["is_self"] = True
    else:
        own_row = dict(self_row)
        own_row["is_self"] = True
        if int(own_row["rank"]) > 0:
            own_tier = next(
                (
                    tier
                    for tier in reward_tiers
                    if int(tier["rank_start"])
                    <= int(own_row["rank"])
                    <= int(tier["rank_end"])
                ),
                None,
            )
            if own_tier is not None:
                own_row["reward_rank_start"] = int(own_tier["rank_start"])
                own_row["reward_rank_end"] = int(own_tier["rank_end"])
                own_row["talent_pill_count"] = _reward_item_count(
                    own_tier,
                    item_id=TALENT_PILL_ITEM_ID,
                )
                own_row["score_per_talent_pill"] = (
                    own_row["score"] / own_row["talent_pill_count"]
                    if own_row["talent_pill_count"] > 0
                    else None
                )
        rankings.append(own_row)

    if rank_list_size > 0:
        # rankListSize is a scope/cache hint, not proof that a player row exists
        # at that rank. Only label a last player when that exact runtime row was
        # actually loaded; never synthesize an empty participant from the hint.
        loaded_last_row = rows_by_rank.get(rank_list_size)
        if loaded_last_row is not None:
            last_row = next(
                (row for row in rankings if int(row["rank"]) == rank_list_size),
                None,
            )
            if last_row is None:
                last_row = dict(loaded_last_row)
                rankings.append(last_row)
            last_row["is_last_player"] = True

    rankings.sort(
        key=lambda row: (
            int(row["rank"]) <= 0,
            int(row["rank"]) if int(row["rank"]) > 0 else 0,
            not bool(row.get("is_reward_guard")),
            str(row.get("name") or ""),
        )
    )
    return {
        "rank_list_size": rank_list_size,
        "loaded_rank_count": len(loaded_rankings),
        "declared_rank_count": declared_count,
        "self_ranking": self_row,
        "reward_guard_ranks": [int(row["rank_end"]) for row in active_tiers],
        "rankings": rankings,
    }


def read_lingzhuang_huadao_snapshot() -> dict[str, Any]:
    """Read the loaded Lingzhuang Huadao leaderboard without game-side actions."""

    started_at = time.perf_counter()
    memory: MumuProcessMemory | None = None
    try:
        reward_tiers = load_activity_rank_reward_tiers(
            rank_activity_id=LINGZHUANG_HUADAO_RANK_ACTIVITY_ID,
            event_date=datetime.now().astimezone().date().isoformat(),
        )
        memory = MumuProcessMemory.discover_cached(fallback_to_discovery=False)
        root, root_cache_hit = resolve_activity_rank_root(
            memory,
            allow_discovery=False,
        )
        reader = LuaJitReader(memory)
        rank_data = _rank_data(
            reader,
            root,
            LINGZHUANG_HUADAO_RANK_ACTIVITY_ID,
            reward_tiers=reward_tiers,
        )
        plane_rank_data = _rank_data(
            reader,
            root,
            LINGZHUANG_HUADAO_PLANE_RANK_ACTIVITY_ID,
            key_points_only=False,
        )
        incomplete_scopes: list[str] = []
        if (
            int(rank_data["rank_list_size"]) > 0
            and int(rank_data["loaded_rank_count"]) <= 0
        ):
            incomplete_scopes.append("个人榜")
        if (
            int(plane_rank_data["rank_list_size"]) > 0
            and int(plane_rank_data["loaded_rank_count"]) <= 0
        ):
            incomplete_scopes.append("位面榜")
        if incomplete_scopes:
            raise FanxiuRuntimeMemoryError(
                f"灵装化道{'、'.join(incomplete_scopes)}明细尚未加载完整"
            )
        return {
            "ok": True,
            "available": True,
            "complete": True,
            "source": "runtime_memory",
            "activity": {
                "key": "lingzhuang-huadao",
                "name": "灵装化道",
                "resource_name": "玄铁",
                "rank_activity_id": LINGZHUANG_HUADAO_RANK_ACTIVITY_ID,
            },
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **rank_data,
            "plane_rank_activity_id": LINGZHUANG_HUADAO_PLANE_RANK_ACTIVITY_ID,
            "plane_rank_list_size": plane_rank_data["rank_list_size"],
            "plane_loaded_rank_count": plane_rank_data["loaded_rank_count"],
            "plane_declared_rank_count": plane_rank_data["declared_rank_count"],
            "plane_self_ranking": plane_rank_data["self_ranking"],
            "plane_rankings": plane_rank_data["rankings"],
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid,
                "process_start_ticks": memory.process_start_ticks,
                "root_cache_hit": root_cache_hit,
                "resolution_path": ["process_cache", "root_cache", "snapshot"],
            },
        }
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else f"{type(exc).__name__}: {exc}"
        )
        error_code = (
            exc.code
            if isinstance(exc, FanxiuRuntimeMemoryError)
            else "unexpected_error"
        )
        return {
            "ok": False,
            "available": False,
            "complete": False,
            "source": "runtime_memory",
            "activity": {
                "key": "lingzhuang-huadao",
                "name": "灵装化道",
                "resource_name": "玄铁",
                "rank_activity_id": LINGZHUANG_HUADAO_RANK_ACTIVITY_ID,
            },
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": reason,
            "error_code": error_code,
            "recovery_required": error_code in {
                "process_cache_miss",
                "root_cache_miss",
                "data_not_loaded",
            },
            "rank_list_size": 0,
            "loaded_rank_count": 0,
            "declared_rank_count": 0,
            "self_ranking": None,
            "rankings": [],
            "plane_rank_activity_id": LINGZHUANG_HUADAO_PLANE_RANK_ACTIVITY_ID,
            "plane_rank_list_size": 0,
            "plane_loaded_rank_count": 0,
            "plane_declared_rank_count": 0,
            "plane_self_ranking": None,
            "plane_rankings": [],
            "elapsed_seconds": time.perf_counter() - started_at,
            "evidence": {
                "pid": memory.pid if memory is not None else None,
                "process_start_ticks": (
                    memory.process_start_ticks if memory is not None else None
                ),
                "root_cache_hit": False,
                "resolution_path": [
                    "process_cache"
                    if error_code == "process_cache_miss"
                    else "root_cache"
                    if error_code == "root_cache_miss"
                    else "snapshot"
                ],
            },
        }
