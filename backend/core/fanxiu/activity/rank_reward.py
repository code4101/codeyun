from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


_ACTIVITY_DATE_CONDITION = re.compile(
    r"^ActivityIdOpen(?P<side>Before|After)\|(?P<activity_id>\d+)_(?P<day>\d{8})$"
)


class ActivityRankRewardConfigError(RuntimeError):
    """The static activity-rank reward configuration is incomplete or ambiguous."""


@lru_cache(maxsize=16)
def _cached_config_rows(
    path: Path,
    modified_ns: int,
    size: int,
) -> tuple[dict[str, Any], ...]:
    del modified_ns, size
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ActivityRankRewardConfigError(f"无法读取凡修配置表 {path}") from exc
    rows = payload if isinstance(payload, list) else payload.get("rows", payload)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ActivityRankRewardConfigError(f"凡修配置表 {path} 不是有效行列表")
    return tuple(row for row in rows if isinstance(row, dict))


def _config_rows(root: Path, name: str) -> tuple[dict[str, Any], ...]:
    path = root / "parsed_configs" / name / "rows.json"
    try:
        stat = path.stat()
    except OSError as exc:
        raise ActivityRankRewardConfigError(f"无法读取凡修配置表 {path}") from exc
    return _cached_config_rows(path, stat.st_mtime_ns, stat.st_size)


def _condition_matches(
    raw_condition: Any,
    *,
    rank_activity_id: int,
    event_date: date,
) -> bool:
    condition = str(raw_condition or "").strip()
    if not condition:
        return True
    matches = []
    for clause in re.split(r"[;,]", condition):
        match = _ACTIVITY_DATE_CONDITION.fullmatch(clause.strip())
        if match is not None and int(match.group("activity_id")) == int(rank_activity_id):
            matches.append(match)
    # Shared reward groups can mention sibling variants in the same condition.
    # Only the predicate for the activity being rendered is authoritative.
    for match in matches:
        threshold = date.fromisoformat(
            f"{match.group('day')[:4]}-{match.group('day')[4:6]}-{match.group('day')[6:]}"
        )
        if match.group("side") == "After" and event_date < threshold:
            return False
        if match.group("side") == "Before" and event_date >= threshold:
            return False
    return True


def load_activity_rank_reward_tiers(
    *,
    rank_activity_id: int,
    event_date: str,
    export_root: str | Path | None = None,
    server_day: int | None = None,
    world_level: int | None = None,
) -> list[dict[str, Any]]:
    """Load the effective rank intervals used by an activity's reward view."""

    root = resolve_fanxiu_export_root(export_root)
    activity_rows = _config_rows(root, "Activity")
    activity = next(
        (row for row in activity_rows if int(row.get("id") or 0) == int(rank_activity_id)),
        None,
    )
    if activity is None:
        raise ActivityRankRewardConfigError(
            f"Activity 配置不存在：{int(rank_activity_id)}"
        )
    reward_group = int(activity.get("rewardGroup") or 0)
    if reward_group <= 0:
        raise ActivityRankRewardConfigError(
            f"Activity {int(rank_activity_id)} 缺少 rewardGroup"
        )
    target_date = date.fromisoformat(str(event_date))
    tiers: list[dict[str, Any]] = []
    for row in _config_rows(root, "ActivityListReward"):
        if int(row.get("group") or 0) != reward_group:
            continue
        if not _condition_matches(
            row.get("condition"),
            rank_activity_id=int(rank_activity_id),
            event_date=target_date,
        ):
            continue
        server_range = row.get("serverDay") or []
        if server_day is not None and len(server_range) >= 2:
            if not int(server_range[0]) <= int(server_day) <= int(server_range[1]):
                continue
        world_range = row.get("worldLevel") or []
        if world_level is not None and len(world_range) >= 2:
            if not int(world_range[0]) <= int(world_level) <= int(world_range[1]):
                continue
        ranking_range = row.get("rankingRange") or []
        if len(ranking_range) < 2:
            continue
        start, end = int(ranking_range[0]), int(ranking_range[1])
        if start <= 0 or end < start:
            continue
        tiers.append(
            {
                "config_id": int(row.get("id") or 0),
                "reward_group": reward_group,
                "rank_start": start,
                "rank_end": end,
                "rewards": list(row.get("reward") or []),
            }
        )
    tiers.sort(key=lambda row: (row["rank_start"], row["rank_end"], row["config_id"]))
    if not tiers:
        raise ActivityRankRewardConfigError(
            f"ActivityListReward 未找到活动 {int(rank_activity_id)} 的生效档位"
        )
    starts = [row["rank_start"] for row in tiers]
    if len(starts) != len(set(starts)):
        raise ActivityRankRewardConfigError(
            f"活动 {int(rank_activity_id)} 的排名奖励档位存在冲突"
        )
    return tiers


__all__ = [
    "ActivityRankRewardConfigError",
    "load_activity_rank_reward_tiers",
]
