from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    discover_beast_abyss_reward_tabs,
)


def test_discovers_tabs_left_to_right_and_ignores_badges_and_noise() -> None:
    tabs = discover_beast_abyss_reward_tabs(
        [
            {"text": "领", "score": 0.999, "x": 117, "y": 2, "w": 41, "h": 33},
            {"text": "修炼", "score": 0.998, "x": 367, "y": 28, "w": 85, "h": 42},
            {"text": "X", "score": 0.57, "x": 333, "y": 40, "w": 15, "h": 15},
            {"text": "榜单", "score": 0.999, "x": 55, "y": 27, "w": 85, "h": 43},
            {"text": "积分", "score": 0.999, "x": 210, "y": 28, "w": 89, "h": 45},
        ]
    )

    assert [item.title for item in tabs] == ["榜单", "积分", "修炼"]
    assert tabs[0].center_x == pytest.approx(97.5)
