from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    claim_beast_abyss_task_rewards,
    discover_beast_abyss_reward_tabs,
)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


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


class RewardContext:
    def __init__(self) -> None:
        self.scene = 657
        self.active_tab = ""
        self.actions: list[tuple[object, ...]] = []
        self.titles = {
            "榜单": iter(("排行任务三", "排行任务四", "排行任务四", "排行任务四", "排行任务四")),
            "积分": iter(("积分任务三", "积分任务三", "积分任务三", "积分任务三")),
        }

    def current_scene(self, _expected, update=False):
        return self.scene, 100.0, "frame"

    def wait_click_then_scene(self, scene_id, title, targets, **_options):
        self.actions.append(("transition", scene_id, title))
        self.scene = int(tuple(targets)[0])
        if False:
            yield None
        return self.scene

    def go_scene(self, scene_id):
        self.actions.append(("goto", scene_id))
        self.scene = int(scene_id)
        if False:
            yield None

    def wait_action_settle(self, seconds):
        self.actions.append(("wait", seconds))
        if False:
            yield None

    def ocr_lines_in_shapes(self, *_args, **_kwargs):
        return [
            {"text": "积分", "score": 0.99, "x": 210, "y": 28, "w": 89, "h": 45},
            {"text": "领", "score": 1.0, "x": 117, "y": 2, "w": 41, "h": 33},
            {"text": "榜单", "score": 0.99, "x": 55, "y": 27, "w": 85, "h": 43},
        ]

    def click_frame_point(self, _scene_id, x, _y):
        self.active_tab = "榜单" if x < 200 else "积分"
        self.actions.append(("tab", self.active_tab))

    def cur_frame(self, update=False):
        return "frame"

    def ocr_text_in_shapes(self, *_args, **_kwargs):
        return next(self.titles[self.active_tab])

    def click_shape_center(self, scene_id, title):
        self.actions.append(("click", scene_id, title, self.active_tab))


def test_claims_each_tab_until_three_consecutive_no_change_cycles() -> None:
    context = RewardContext()

    result = _drain(claim_beast_abyss_task_rewards(context))

    assert result["tabs"] == ["榜单", "积分"]
    assert result["detected_advances"] == 1
    assert [item["clicks"] for item in result["tab_results"]] == [4, 3]
    assert all(item["unchanged_confirmations"] == 3 for item in result["tab_results"])
    assert context.scene == 535
    assert ("transition", 657, "返回") in context.actions
    assert ("transition", 535, "任务") in context.actions
    assert ("transition", 664, "兽渊探秘页签") in context.actions
    assert context.actions.count(("wait", 3.0)) == 7


def test_missing_tabs_fails_closed_without_clicking_first_row() -> None:
    context = RewardContext()
    context.scene = 664
    context.ocr_lines_in_shapes = lambda *_args, **_kwargs: []

    with pytest.raises(RuntimeError, match="没有识别到任何 Tab"):
        _drain(claim_beast_abyss_task_rewards(context))

    assert not any(action[0] == "click" for action in context.actions)
