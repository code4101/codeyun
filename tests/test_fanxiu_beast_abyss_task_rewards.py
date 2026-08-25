from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.core.fanxiu.data_annotation.tasks.beast_abyss_native_auto import (
    BeastAbyssNativeAutoAssets,
    BeastAbyssNativeAutoRequest,
    prepare_beast_abyss_native_auto,
)
from backend.core.fanxiu.data_annotation.tasks.beast_abyss_task_rewards import (
    claim_beast_abyss_cultivation_rewards,
)
from backend.core.fanxiu.instrumentation.beast_abyss_task_rewards import (
    build_beast_abyss_cultivation_task_snapshot,
)


def finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def snapshot(*, claimable=(), claimed=()):
    return {
        "ok": True,
        "complete": True,
        "authorized_claim_task_ids": list(claimable),
        "claimed_task_ids": list(claimed),
    }


@dataclass
class View:
    id: int


class Runtime:
    def __init__(self, *, start_scene=657):
        self.scene = start_scene
        self.actions = []

    def current_scene(self, _candidates, *, update=True):
        return self.scene, 100.0, "frame"

    def wait_click_then_view(self, scene, shape, targets, **_kwargs):
        self.actions.append(("wait_click_then_view", scene, shape, tuple(targets)))
        self.scene = int(targets[0])
        if False:
            yield None
        return View(self.scene)

    def click_shape_center(self, scene, shape):
        self.actions.append(("click", scene, shape))

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        yield None

    def goto_view(self, scene):
        self.actions.append(("goto_view", scene))
        self.scene = int(scene)
        if False:
            yield None
        return View(self.scene)


def test_build_snapshot_authorizes_current_cultivation_rung():
    result = build_beast_abyss_cultivation_task_snapshot(
        task_entries=[
            {
                "taskId": 440222,
                "status": 4,
                "turn": 1,
                "rewardTime": 0,
                "progressList": [{"finish": True, "progress": 3936000, "target": 3936000}],
            }
        ],
        finished_task_ids=[440239],
    )

    assert result["ok"] is True
    assert result["authorized_claim_task_ids"] == [440222]
    assert result["claimed_task_ids"] == []


def test_no_reward_retry_checks_without_opening_gui():
    runtime = Runtime()
    result = finish(
        claim_beast_abyss_cultivation_rewards(
            runtime,
            reader=lambda: snapshot(),
        )
    )

    assert result == {
        "checked": True,
        "claimed_task_ids": [],
        "remaining_claimable": [],
        "gui_opened": False,
    }
    assert runtime.actions == []


def test_claimable_rung_is_verified_then_returns_to_explore():
    runtime = Runtime()
    reads = iter(
        [
            snapshot(claimable=[440222]),
            snapshot(claimed=[440222]),
        ]
    )
    result = finish(
        claim_beast_abyss_cultivation_rewards(
            runtime,
            reader=lambda: next(reads),
        )
    )

    assert result["claimed_task_ids"] == [440222]
    assert runtime.actions == [
        ("wait_click_then_view", 657, "任务", (664,)),
        ("click", 664, "首条任务进度区"),
        ("settle", 1.2),
        ("wait_click_then_view", 664, "兽渊探秘页签", (535,)),
        ("goto_view", 657),
    ]


def test_click_must_move_exact_task_into_claimed_ledger():
    runtime = Runtime(start_scene=664)
    reads = iter(
        [
            snapshot(claimable=[440222]),
            snapshot(claimable=[440222]),
        ]
    )
    with pytest.raises(RuntimeError, match="440222.*精确已领取迁移"):
        finish(
            claim_beast_abyss_cultivation_rewards(
                runtime,
                reader=lambda: next(reads),
            )
        )


def test_native_prepare_runs_reward_check_before_auto_settings(monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import beast_abyss_native_auto as module

    events = []

    def fake_claim(_runtime):
        events.append("reward_check")
        if False:
            yield None
        return {"checked": True}

    def stop_after_reward(*_args, **_kwargs):
        events.append("observe")
        raise RuntimeError("stop-after-reward")

    runtime = Runtime()
    monkeypatch.setattr(module, "claim_beast_abyss_cultivation_rewards", fake_claim)
    monkeypatch.setattr(module, "_observe", stop_after_reward)
    assets = BeastAbyssNativeAutoAssets(
        explore_scene_id=657,
        help_view_scene_id=658,
        terminal_scene_ids=(662,),
    )
    request = BeastAbyssNativeAutoRequest(
        auto_use_explore_items=True,
        measurement=False,
        requested_explores=1,
    )

    with pytest.raises(RuntimeError, match="stop-after-reward"):
        finish(prepare_beast_abyss_native_auto(runtime, assets, request))
    assert events == ["reward_check", "observe"]
