from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks.gameplay_rank_task_rewards import (
    GameplayRankTaskAssets,
    GameplayRankTaskTab,
    claim_gameplay_rank_task_tabs,
)


def _run(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


class _Runtime:
    def __init__(self, entry_scene: int):
        self.entry_scene = entry_scene
        self.clicks = []

    def wait_click_then_view(self, source, shape, target, **_options):
        self.clicks.append((source, shape, target))
        if False:
            yield None
        if source == 10:
            return self.entry_scene
        return target[0]

    def click_shape_center(self, source, shape):
        self.clicks.append((source, shape, None))

    def wait_action_settle(self, _seconds):
        if False:
            yield None


ASSETS = GameplayRankTaskAssets(
    activity_label="测试玩法榜",
    home_scene_id=10,
    tabs=(
        GameplayRankTaskTab("修炼", 6, 12, "修炼页签"),
        GameplayRankTaskTab("夺分", 7, 11, "夺分页签"),
    ),
    home_shape="玩法主页",
)


def _snapshot(authorized, claimed=()):
    return {
        "ok": True,
        "available": True,
        "complete": True,
        "authorized_claim_task_ids": list(authorized),
        "claimed_task_ids": list(claimed),
        "task_subtypes": {"101": 6, "102": 6, "201": 7},
    }


def test_visits_every_tab_and_verifies_each_exact_task_migration() -> None:
    runtime = _Runtime(entry_scene=11)
    states = iter([
        _snapshot([102, 201], [101]),
        _snapshot([201], [101, 102]),
        _snapshot([], [101, 102, 201]),
    ])

    def reader(*, expected_claimed_task_id):
        state = next(states)
        state["expected_task_claimed"] = expected_claimed_task_id in state["claimed_task_ids"]
        return state

    result = _run(
        claim_gameplay_rank_task_tabs(
            runtime,
            _snapshot([101, 102, 201]),
            assets=ASSETS,
            reader=reader,
        )
    )

    assert result["visited_tabs"] == ["修炼", "夺分"]
    assert result["claimed_task_ids"] == [101, 102, 201]
    assert runtime.clicks == [
        (10, "任务", [12, 11]),
        (11, "修炼页签", [12]),
        (12, "首条任务领取区", None),
        (12, "首条任务领取区", None),
        (12, "夺分页签", [11]),
        (11, "首条任务领取区", None),
        (11, "玩法主页", [10]),
    ]


def test_no_claimable_rewards_still_inspects_every_configured_tab() -> None:
    runtime = _Runtime(entry_scene=12)
    result = _run(
        claim_gameplay_rank_task_tabs(
            runtime,
            _snapshot([], [101, 102, 201]),
            assets=ASSETS,
            reader=lambda **_options: {},
        )
    )

    assert result["visited_tabs"] == ["修炼", "夺分"]
    assert result["claimed_task_ids"] == []
    assert runtime.clicks == [
        (10, "任务", [12, 11]),
        (12, "夺分页签", [11]),
        (11, "玩法主页", [10]),
    ]


def test_incomplete_runtime_facts_fail_before_any_gui_action() -> None:
    runtime = _Runtime(entry_scene=12)
    snapshot = _snapshot([101])
    snapshot["complete"] = False

    with pytest.raises(RuntimeError, match="Runtime 事实不完整"):
        _run(
            claim_gameplay_rank_task_tabs(
                runtime,
                snapshot,
                assets=ASSETS,
                reader=lambda **_options: {},
            )
        )

    assert runtime.clicks == []


def test_unknown_task_subtype_fails_before_any_gui_action() -> None:
    runtime = _Runtime(entry_scene=12)
    snapshot = _snapshot([301])
    snapshot["task_subtypes"]["301"] = 8

    with pytest.raises(RuntimeError, match="未映射到已验证奖励页签"):
        _run(
            claim_gameplay_rank_task_tabs(
                runtime,
                snapshot,
                assets=ASSETS,
                reader=lambda **_options: {},
            )
        )

    assert runtime.clicks == []


def test_failed_exact_migration_stops_before_next_row_or_tab() -> None:
    runtime = _Runtime(entry_scene=12)
    unchanged = _snapshot([101, 102, 201])
    unchanged["expected_task_claimed"] = False

    with pytest.raises(RuntimeError, match="未形成精确单步状态迁移"):
        _run(
            claim_gameplay_rank_task_tabs(
                runtime,
                _snapshot([101, 102, 201]),
                assets=ASSETS,
                reader=lambda **_options: unchanged,
            )
        )

    assert runtime.clicks == [
        (10, "任务", [12, 11]),
        (12, "首条任务领取区", None),
    ]
