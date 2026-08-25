from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeVar


RankingItem = TypeVar("RankingItem")


def _get(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def _set(item: Any, field: str, value: Any) -> None:
    if isinstance(item, dict):
        item[field] = value
    else:
        setattr(item, field, value)


def is_ranking_key_point(item: Any) -> bool:
    """Return whether a ranking row belongs in the shared activity view."""

    return bool(
        _get(item, "is_reward_guard")
        or _get(item, "is_self")
        or _get(item, "is_last_player")
    )


def rank_reward_item_count(tier: dict[str, Any], *, item_id: int) -> int:
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


def project_ranking_key_points(
    items: Iterable[RankingItem],
    *,
    reward_tiers: Iterable[dict[str, Any]],
    reward_count: Callable[[dict[str, Any]], int],
    placeholder_factory: Callable[[int, int, int], RankingItem] | None = None,
    rank_list_size: int | None = None,
    retain_non_key_rows: bool = False,
    include_placeholders: bool = True,
) -> list[RankingItem]:
    """Decorate and retain the shared guard/self/last ranking contract.

    The input may contain a full runtime cache. Exact reward-boundary players are
    promoted to guards; missing boundaries become placeholders. Non-key players
    are never allowed to leak into an activity page.
    """

    projected = list(items)
    rows_by_rank: dict[int, RankingItem] = {}
    for item in projected:
        rank = int(_get(item, "rank") or 0)
        if rank > 0 and (rank not in rows_by_rank or bool(_get(item, "has_player"))):
            rows_by_rank[rank] = item

    if rank_list_size and int(rank_list_size) > 0:
        loaded_last = rows_by_rank.get(int(rank_list_size))
        if loaded_last is not None:
            _set(loaded_last, "is_last_player", True)

    for tier in reward_tiers:
        rank_start = int(tier["rank_start"])
        rank_end = int(tier["rank_end"])
        talent_pill_count = int(reward_count(tier))
        self_item = next(
            (
                item
                for item in projected
                if bool(_get(item, "is_self"))
                and rank_start <= int(_get(item, "rank") or 0) <= rank_end
            ),
            None,
        )
        if self_item is not None:
            _apply_reward_tier(
                self_item,
                rank_start=rank_start,
                rank_end=rank_end,
                talent_pill_count=talent_pill_count,
            )

        guard = rows_by_rank.get(rank_end)
        if guard is None and include_placeholders:
            if placeholder_factory is None:
                raise ValueError("排名关键点投影缺少占位行工厂")
            guard = placeholder_factory(rank_start, rank_end, talent_pill_count)
            projected.append(guard)
            rows_by_rank[rank_end] = guard
        if guard is None:
            continue
        _set(guard, "is_reward_guard", True)
        _apply_reward_tier(
            guard,
            rank_start=rank_start,
            rank_end=rank_end,
            talent_pill_count=talent_pill_count,
        )

    result = (
        projected
        if retain_non_key_rows
        else [item for item in projected if is_ranking_key_point(item)]
    )
    result.sort(
        key=lambda item: (
            int(_get(item, "rank") or 0) <= 0,
            int(_get(item, "rank") or 0),
            not bool(_get(item, "is_reward_guard")),
            str(_get(item, "name") or ""),
        )
    )
    return result


def _apply_reward_tier(
    item: Any,
    *,
    rank_start: int,
    rank_end: int,
    talent_pill_count: int,
) -> None:
    _set(item, "reward_rank_start", rank_start)
    _set(item, "reward_rank_end", rank_end)
    _set(item, "talent_pill_count", talent_pill_count)
    score = int(_get(item, "score") or 0)
    has_player = bool(_get(item, "has_player", True))
    _set(
        item,
        "score_per_talent_pill",
        score / talent_pill_count
        if has_player and talent_pill_count > 0
        else None,
    )


__all__ = [
    "is_ranking_key_point",
    "project_ranking_key_points",
    "rank_reward_item_count",
]
