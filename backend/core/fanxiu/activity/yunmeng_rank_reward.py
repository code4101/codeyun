from __future__ import annotations

"""Compatibility surface for Yunmeng callers of generic rank rewards."""

from typing import Any

from backend.core.fanxiu.activity.rank_reward import (
    ActivityRankRewardConfigError as YunmengRankRewardConfigError,
    load_activity_rank_reward_tiers,
)


def load_yunmeng_rank_reward_tiers(**kwargs: Any) -> list[dict[str, Any]]:
    return load_activity_rank_reward_tiers(**kwargs)


__all__ = [
    "YunmengRankRewardConfigError",
    "load_activity_rank_reward_tiers",
    "load_yunmeng_rank_reward_tiers",
]
