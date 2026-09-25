"""ActiveTasks.ActiveProgress 的周常奖励静态契约。

Runtime 事实校验与界面档位识别共享此声明；导入不读取游戏或数据库。
"""
WEEKLY_ACTIVITY_TIERS = (
    {"config_id": 13, "threshold": 400, "reward": ("Item|1010_50",)},
    {"config_id": 14, "threshold": 600, "reward": ("Item|1010_50",)},
    {"config_id": 15, "threshold": 800, "reward": ("Item|1010_50",)},
    {"config_id": 16, "threshold": 1200, "reward": ("Item|1010_100",)},
    {"config_id": 17, "threshold": 1600, "reward": ("Item|1010_100",)},
    {"config_id": 18, "threshold": 2000, "reward": ("Item|1010_150",)},
    {"config_id": 19, "threshold": 2400, "reward": ("Item|1010_200",)},
)

WEEKLY_ACTIVITY_REWARD_MILESTONES = tuple(tier["threshold"] for tier in WEEKLY_ACTIVITY_TIERS)
