"""日常挑战的兼容组合入口；各玩法实现由独立业务模块持有。"""
from __future__ import annotations

from .daily_assistant import DailyAssistantTaskMixin
from .daily_dungeon import DailyDungeonTaskMixin
from .daily_free_challenge import DailyFreeChallengeTaskMixin
from .shuangxiu import DailyShuangxiuTaskMixin


class DailyChallengeTaskMixin(
    DailyAssistantTaskMixin,
    DailyShuangxiuTaskMixin,
    DailyDungeonTaskMixin,
    DailyFreeChallengeTaskMixin,
):
    """组合日常助手、副本、双修和免费剿灭的原有任务入口。"""
