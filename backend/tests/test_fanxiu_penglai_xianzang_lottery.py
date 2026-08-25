from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.penglai_xianzang_lottery import (
    list_xianzang_lottery_points,
    record_xianzang_lottery_point,
    xianzang_week_instance_id,
)
from backend.core.fanxiu.activity.lottery_observation_ledger import (
    build_draw_before_observation,
)
from backend.core.fanxiu.data_annotation.tasks import penglai_xianzang_lottery as task
from backend.core.fanxiu.data_annotation.tasks.bothdraw_lottery import (
    close_bothdraw_result,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    build_bothdraw_cumulative_rewards,
    build_bothdraw_task_snapshot,
)


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _snapshot(x: int, y: int) -> dict:
    return {
        "complete": True,
        "captured_at": "2026-08-06T21:10:00+08:00",
        "activity_id": 102,
        "x": x,
        "y": y,
        "hit_big": y,
        "hit_big_total": y,
        "selected_big_count": y,
        "selected_big_reward": {
            "big_id": 102,
            "library_id": 800101,
            "item_id": 20,
            "name": "太玄护符",
        },
        "evidence": {"pid": 1},
    }


def test_week_instance_is_scoped_to_thursday():
    assert xianzang_week_instance_id("2026-08-06T21:10:00+08:00") == "penglai-xianzang-2026-08-06"
    assert xianzang_week_instance_id("2026-08-12T21:10:00+08:00") == "penglai-xianzang-2026-08-06"


def test_points_preserve_actual_dx_and_grand_prize_dy():
    engine = _engine()
    instance_id = "penglai-xianzang-2026-08-06"
    with Session(engine) as session:
        record_xianzang_lottery_point(session, snapshot=_snapshot(0, 0), instance_id=instance_id)
        record_xianzang_lottery_point(session, snapshot=_snapshot(10, 2), instance_id=instance_id)
        dataset = record_xianzang_lottery_point(
            session,
            snapshot=_snapshot(16, 3),
            instance_id=instance_id,
        )

    assert [(point.x, point.y, point.dx, point.dy) for point in dataset.samples] == [
        (0, 0, 0, 0),
        (10, 2, 10, 2),
        (16, 3, 6, 1),
    ]


def test_same_point_is_idempotent():
    engine = _engine()
    instance_id = "penglai-xianzang-2026-08-06"
    with Session(engine) as session:
        record_xianzang_lottery_point(session, snapshot=_snapshot(0, 0), instance_id=instance_id)
        record_xianzang_lottery_point(session, snapshot=_snapshot(0, 0), instance_id=instance_id)
        dataset = list_xianzang_lottery_points(session, instance_id=instance_id)

    assert len(dataset.samples) == 1


def test_result_close_retries_only_on_fresh_reliable_result_frames():
    class Runtime:
        def __init__(self):
            self.clicks = []
            self.frames = 0

        def current_scene(self, scene_ids, *, update):
            assert scene_ids == [451]
            assert update is True
            self.frames += 1
            if len(self.clicks) < 2:
                return 451, 100.0, f"frame-{self.frames}"
            return 447, 100.0, "main"

        def click_shape(self, scene_id, title, *, frame_data_url):
            self.clicks.append((scene_id, title, frame_data_url))

    runtime = Runtime()
    spec = SimpleNamespace(
        require_executable_assets=lambda: None,
        draw_result_scene_id=451,
        draw_result_close_shape="继续",
        draw_shape="鉴宝",
        activity_label="蓬莱仙藏",
        main_page_name="蓬莱仙藏",
        read_page=lambda _runtime: (
            SimpleNamespace(page="蓬莱仙藏", scene_id=447, score=100.0)
            if len(runtime.clicks) >= 2
            else None
        ),
    )

    result = close_bothdraw_result(
        runtime,
        spec,
        timeout_seconds=1.0,
        poll_seconds=0.01,
        retry_click_seconds=0.01,
    )

    assert result["clicked_count"] == 2
    assert runtime.clicks[0][:2] == (451, "继续")
    assert runtime.clicks[1][:2] == (451, "继续")
    assert runtime.clicks[0][2] != runtime.clicks[1][2]


class _Runtime:
    def __init__(self):
        self.clicks = []

    def cur_frame(self, *, update: bool):
        assert update is True
        return "frame"

    def click_shape(self, scene_id, title, **kwargs):
        self.clicks.append((scene_id, title, kwargs))


def test_draw_mode_reopens_main_once_when_active_panel_is_not_loaded(monkeypatch):
    runtime = _Runtime()
    snapshots = iter(
        (
            {
                "complete": False,
                "reason": "NotLoaded: active Bothdraw panel 未加载",
            },
            {"complete": True, "ten_draw_enabled": False},
        )
    )
    lifecycle = []
    monkeypatch.setattr(
        task,
        "read_bothdraw_lottery_runtime",
        lambda: {"complete": True, "activity_id": 102},
    )
    monkeypatch.setattr(
        task,
        "read_bothdraw_ten_draw_runtime",
        lambda **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        task, "leave_xianzang", lambda _runtime: lifecycle.append("leave")
    )
    monkeypatch.setattr(
        task, "enter_xianzang", lambda _runtime: lifecycle.append("enter")
    )

    result = task.ensure_xianzang_draw_mode(runtime, ten_draw=False)

    assert result == {
        "result": "already_set",
        "ten_draw_enabled": False,
        "panel_reloaded": True,
    }
    assert lifecycle == ["leave", "enter"]
    assert runtime.clicks == []


def test_draw_mode_stops_if_reopened_panel_is_still_not_loaded(monkeypatch):
    runtime = _Runtime()
    lifecycle = []
    monkeypatch.setattr(
        task,
        "read_bothdraw_lottery_runtime",
        lambda: {"complete": True, "activity_id": 102},
    )
    monkeypatch.setattr(
        task,
        "read_bothdraw_ten_draw_runtime",
        lambda **_kwargs: {
            "complete": False,
            "reason": "NotLoaded: active Bothdraw panel 未加载",
        },
    )
    monkeypatch.setattr(
        task, "leave_xianzang", lambda _runtime: lifecycle.append("leave")
    )
    monkeypatch.setattr(
        task, "enter_xianzang", lambda _runtime: lifecycle.append("enter")
    )

    try:
        task.ensure_xianzang_draw_mode(runtime, ten_draw=False)
    except RuntimeError as exc:
        assert "NotLoaded" in str(exc)
    else:
        raise AssertionError("二次 NotLoaded 必须失败关闭")

    assert lifecycle == ["leave", "enter"]
    assert runtime.clicks == []


def test_draw_once_records_only_observed_delta(monkeypatch):
    runtime = _Runtime()
    snapshots = iter((_snapshot(0, 0), _snapshot(10, 1)))
    resources = iter(
        (
            {
                "complete": True,
                "activity_id": 102,
                "x": 0,
                "available_currency": 23,
                "available_draws": 23,
                "cost_type": 1,
                "cost_per_draw": 1,
                "progress": 0,
                "claimed_count": 0,
            },
            {
                "complete": True,
                "activity_id": 102,
                "x": 10,
                "available_currency": 13,
                "available_draws": 13,
                "cost_type": 1,
                "cost_per_draw": 1,
                "progress": 10,
                "claimed_count": 0,
            },
        )
    )
    recorded = []
    monkeypatch.setattr(task, "open_xianzang_tab", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task, "_reconcile_pending_xianzang_draw", lambda: None)
    monkeypatch.setattr(task, "read_bothdraw_lottery_runtime", lambda: next(snapshots))
    monkeypatch.setattr(
        task,
        "read_bothdraw_cumulative_rewards_runtime",
        lambda: next(resources),
    )
    monkeypatch.setattr(task, "_record_snapshot", lambda value, **_kwargs: recorded.append(value))

    result = task.draw_xianzang_once(runtime)

    assert result["dx"] == 10
    assert result["dy"] == 1
    assert result["draw_mode"] == "ten_draw"
    assert result["available_currency_before"] == 23
    assert result["available_currency_after"] == 13
    assert recorded[0]["observation_kind"] == "before_draw"
    assert recorded[1]["observation_kind"] == "after_draw"
    assert recorded[1]["batch_size"] == 10
    assert [item["x"] for item in recorded] == [0, 10]
    assert runtime.clicks[0][:2] == (447, "鉴宝")


def test_pending_draw_is_reconciled_from_authoritative_counter_without_click(monkeypatch):
    engine = _engine()
    monkeypatch.setattr("backend.db.engine", engine)
    instance_id = "penglai-xianzang-2026-08-06"
    monkeypatch.setattr(task, "xianzang_week_instance_id", lambda *_args: instance_id)
    before = build_draw_before_observation(
        {
            **_snapshot(10, 1),
            "available_draws": 7,
            "progress": 10,
            "claimed_count": 1,
            "claimed_ids": [10201],
            "cost_type": 40020,
            "cost_per_draw": 1,
        },
        action_id="pending-single",
        draw_mode="single_draw",
        requested_batch_size=1,
    )
    with Session(engine) as session:
        record_xianzang_lottery_point(session, snapshot=before, instance_id=instance_id)
    monkeypatch.setattr(task, "read_bothdraw_lottery_runtime", lambda: _snapshot(11, 1))
    monkeypatch.setattr(
        task,
        "read_bothdraw_cumulative_rewards_runtime",
        lambda: {
            "complete": True,
            "activity_id": 102,
            "x": 11,
            "available_currency": 6,
            "available_draws": 6,
            "cost_type": 40020,
            "cost_per_draw": 1,
            "progress": 11,
            "claimed_count": 1,
            "claimed_ids": [10201],
        },
    )

    result = task._reconcile_pending_xianzang_draw()

    assert result["recovered_pending_action"] is True
    assert result["dx"] == 1
    with Session(engine) as session:
        points = list_xianzang_lottery_points(session, instance_id=instance_id).samples
    assert [(p.action_phase, p.x) for p in points] == [
        ("before_draw", 10),
        ("after_draw", 11),
    ]


def test_pending_draw_with_unchanged_counter_fails_closed(monkeypatch):
    engine = _engine()
    monkeypatch.setattr("backend.db.engine", engine)
    instance_id = "penglai-xianzang-2026-08-06"
    monkeypatch.setattr(task, "xianzang_week_instance_id", lambda *_args: instance_id)
    before = build_draw_before_observation(
        {**_snapshot(10, 1), "available_draws": 7, "progress": 10},
        action_id="pending-single",
        draw_mode="single_draw",
        requested_batch_size=1,
    )
    with Session(engine) as session:
        record_xianzang_lottery_point(session, snapshot=before, instance_id=instance_id)
    monkeypatch.setattr(task, "read_bothdraw_lottery_runtime", lambda: _snapshot(10, 1))
    monkeypatch.setattr(
        task,
        "read_bothdraw_cumulative_rewards_runtime",
        lambda: {"complete": True, "activity_id": 102, "x": 10},
    )

    import pytest

    with pytest.raises(RuntimeError, match="拒绝自动重放不可逆动作"):
        task._reconcile_pending_xianzang_draw()


def test_task_snapshot_uses_finished_ids_as_authoritative_claim_state():
    result = build_bothdraw_task_snapshot(
        activity_id=102,
        task_configs=[
            {"id": 10208, "name": "炼宝试炼八", "sort": 8},
            {"id": 10209, "name": "炼宝试炼九", "sort": 9},
        ],
        task_entries=[
            {"taskId": 10208, "status": 4, "turn": 1, "targetTurn": 1},
            {"taskId": 10209, "status": 4, "turn": 1, "targetTurn": 1},
        ],
        finished_task_ids=[10208],
    )

    assert result["claimed_count"] == 1
    assert [item["task_id"] for item in result["claimable"]] == [10209]


def test_cumulative_rewards_follow_game_progress_and_claimed_order():
    result = build_bothdraw_cumulative_rewards(
        progress=30,
        milestones=[
            {"id": 101, "progress": 10, "reward": "Item|1_1"},
            {"id": 102, "progress": 20, "reward": "Item|2_1"},
            {"id": 103, "progress": 30, "reward": "Item|3_1"},
            {"id": 104, "progress": 40, "reward": "Item|4_1"},
        ],
        claimed_ids=[101],
    )

    assert result["claimed_count"] == 1
    assert [row["state"] for row in result["rewards"]] == [
        "claimed",
        "claimable",
        "claimable",
        "locked",
    ]
    assert [row["visible_slot"] for row in result["visible_claimable"]] == [2, 3]


def test_cumulative_rewards_support_a_verified_two_row_grid_without_changing_default():
    result = build_bothdraw_cumulative_rewards(
        progress=80,
        milestones=[
            {"id": 100 + index, "progress": index * 10, "reward": f"Item|{index}_1"}
            for index in range(1, 13)
        ],
        claimed_ids=[101, 102],
        visible_slot_count=8,
    )

    assert [row["visible_slot"] for row in result["rewards"][:8]] == list(range(1, 9))
    assert [row["visible_slot"] for row in result["rewards"][8:]] == [None] * 4
    assert [row["visible_slot"] for row in result["visible_claimable"]] == list(range(3, 9))


def test_cumulative_reward_slot_centers_allow_a_verified_two_row_grid():
    legacy = task._spec()
    assert legacy.cumulative_reward_slot_center(2) == (0.375, 0.30)

    grid = replace(
        legacy,
        activity_label="两行测试活动",
        cumulative_reward_slots=8,
        cumulative_reward_slot_centers=(
            (0.125, 0.25), (0.375, 0.25), (0.625, 0.25), (0.875, 0.25),
            (0.125, 0.75), (0.375, 0.75), (0.625, 0.75), (0.875, 0.75),
        ),
    )

    assert grid.cumulative_reward_slot_center(5) == (0.125, 0.75)


class _CumulativeRuntime:
    def __init__(self):
        self.clicks = []

    def click_shape_center(self, scene_id, title, **kwargs):
        self.clicks.append((scene_id, title, kwargs))


def _cumulative_snapshot(*, claimed_count: int, claimable: bool) -> dict:
    row = {
        "id": 501,
        "threshold": 10,
        "visible_slot": 1,
        "can_claim": claimable,
    }
    return {
        "complete": True,
        "activity_id": 102,
        "progress": 10,
        "claimed_count": claimed_count,
        "claimable": [row] if claimable else [],
        "visible_claimable": [row] if claimable else [],
    }


def test_claim_cumulative_reward_clicks_only_dynamically_claimable_slot(monkeypatch):
    runtime = _CumulativeRuntime()
    snapshots = iter(
        [
            _cumulative_snapshot(claimed_count=0, claimable=True),
            _cumulative_snapshot(claimed_count=1, claimable=False),
            _cumulative_snapshot(claimed_count=1, claimable=False),
        ]
    )
    monkeypatch.setattr(task, "open_xianzang_tab", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        task,
        "read_bothdraw_cumulative_rewards_runtime",
        lambda: next(snapshots),
    )

    result = task.claim_xianzang_cumulative_rewards(runtime, poll_seconds=0.01)

    assert result["clicked_count"] == 1
    assert result["claimed_count"] == 1
    assert runtime.clicks == [
        (
            447,
            "累抽奖励",
            {"x_ratio": 0.125, "y_ratio": 0.30},
        )
    ]


def _phase_state(available_draws: int) -> dict:
    return {
        "complete": True,
        "activity_id": 102,
        "x": 23 - available_draws,
        "available_draws": available_draws,
        "claimed_count": 0,
        "claimable": [],
    }


def _patch_phase_primitives(monkeypatch, states, draw_sizes):
    opened = []
    modes = []
    claims = []
    monkeypatch.setattr(
        task,
        "_spec",
        lambda: SimpleNamespace(
            require_executable_assets=lambda: None,
            open_main_page=lambda runtime: opened.append(runtime),
        ),
    )
    monkeypatch.setattr(
        task,
        "read_bothdraw_cumulative_rewards_runtime",
        lambda: next(states),
    )
    monkeypatch.setattr(
        task,
        "ensure_xianzang_draw_mode",
        lambda _runtime, *, ten_draw: modes.append(ten_draw)
        or {"result": "already_set", "ten_draw_enabled": ten_draw},
    )
    monkeypatch.setattr(
        task,
        "draw_xianzang_once",
        lambda _runtime, **_kwargs: {"dx": next(draw_sizes)},
    )
    monkeypatch.setattr(
        task,
        "close_xianzang_draw_result",
        lambda _runtime: {"result": "success"},
    )
    monkeypatch.setattr(
        task,
        "claim_xianzang_cumulative_rewards",
        lambda _runtime: claims.append(1) or {"result": "success"},
    )
    return opened, modes, claims


def test_config_phase_spends_only_complete_ten_draw_batches(monkeypatch):
    runtime = object()
    opened, modes, claims = _patch_phase_primitives(
        monkeypatch,
        iter((_phase_state(23), _phase_state(13), _phase_state(3))),
        iter((10, 10)),
    )

    result = task.complete_xianzang_config_ten_draws(runtime)

    assert opened == [runtime]
    assert modes == [True, True]
    assert len(claims) == 3
    assert result["round_count"] == 2
    assert result["final_state"]["available_draws"] == 3
    assert result["stop_reason"] == "fewer_than_ten_draws_preserved_for_late_phase"


def test_late_phase_switches_to_single_draws_and_exhausts_remainder(monkeypatch):
    runtime = object()
    opened, modes, _claims = _patch_phase_primitives(
        monkeypatch,
        iter(
            (
                _phase_state(13),
                _phase_state(3),
                _phase_state(2),
                _phase_state(1),
                _phase_state(0),
                _phase_state(0),
            )
        ),
        iter((10, 1, 1, 1)),
    )

    result = task.complete_xianzang_lottery(runtime)

    assert opened == [runtime]
    assert modes == [True, False, False, False]
    assert result["round_count"] == 4
    assert result["final_state"]["available_draws"] == 0
    assert result["stop_reason"] == "draws_exhausted_and_rewards_claimed"
