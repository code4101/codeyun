from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.kunlun_secret_lottery import (
    KUNLUN_LOTTERY_NAMESPACE,
    list_kunlun_lottery_points,
    kunlun_week_instance_id,
    record_kunlun_lottery_point,
)
from backend.core.fanxiu.data_annotation.tasks import kunlun_secret_lottery as task


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _snapshot(x: int, y: int) -> dict:
    return {
        "complete": True,
        "captured_at": "2026-08-13T21:10:00+08:00",
        "activity_id": 103,
        "x": x,
        "y": y,
        "hit_big": y,
        "hit_big_total": y,
        "selected_big_count": y,
        "selected_big_reward": {
            "big_id": 103,
            "library_id": 900201,
            "item_id": 2016,
            "name": "古·山河无疆屏",
        },
        "evidence": {"pid": 1},
    }


def test_kunlun_points_have_independent_namespace_and_instance() -> None:
    assert kunlun_week_instance_id("2026-08-13T21:10:00+08:00") == (
        "kunlun-secret-2026-08-13"
    )
    assert KUNLUN_LOTTERY_NAMESPACE == "kunlun-secret-draw-grand-prize"

    engine = _engine()
    instance_id = "kunlun-secret-2026-08-13"
    with Session(engine) as session:
        record_kunlun_lottery_point(
            session, snapshot=_snapshot(0, 0), instance_id=instance_id
        )
        dataset = record_kunlun_lottery_point(
            session, snapshot=_snapshot(10, 1), instance_id=instance_id
        )
        stored = list_kunlun_lottery_points(session, instance_id=instance_id)

    assert dataset.namespace == KUNLUN_LOTTERY_NAMESPACE
    assert [(point.x, point.y, point.dx, point.dy) for point in stored.samples] == [
        (0, 0, 0, 0),
        (10, 1, 10, 1),
    ]


def test_kunlun_point_preserves_strategy_observation_fields() -> None:
    engine = _engine()
    snapshot = {
        **_snapshot(10, 1),
        "observation_kind": "after_draw",
        "action_id": "action-1",
        "draw_mode": "ten_draw",
        "batch_size": 10,
        "available_currency": 13,
        "available_draws": 13,
        "cost_type": 4001,
        "cost_per_draw": 1,
        "progress": 10,
        "claimed_count": 0,
    }
    with Session(engine) as session:
        record_kunlun_lottery_point(session, snapshot=snapshot)
        point = list_kunlun_lottery_points(
            session, instance_id="kunlun-secret-2026-08-13"
        ).samples[0]

    assert point.observation_kind == "after_draw"
    assert point.action_id == "action-1"
    assert point.draw_mode == "ten_draw"
    assert point.batch_size == 10
    assert point.available_currency == 13
    assert point.progress == 10


class _NoActionRuntime:
    def __getattr__(self, name):
        raise AssertionError(f"safe failure must happen before runtime action: {name}")


def test_kunlun_lottery_uses_independently_verified_result_asset() -> None:
    assert task.KUNLUN_DRAW_RESULT_SCENE_ID == 544


def test_kunlun_lottery_fails_before_any_action_without_result_asset(
    monkeypatch,
) -> None:
    monkeypatch.setattr(task, "KUNLUN_DRAW_RESULT_SCENE_ID", None)
    with pytest.raises(RuntimeError, match="独立且已验收的场景资产"):
        task.complete_kunlun_lottery(_NoActionRuntime())


@pytest.mark.parametrize(
    ("available", "remaining", "progress", "expected_action", "batch"),
    [
        (23, 20, 0, "ten_draw", 10),
        (10, 20, 0, "ten_draw", 10),
        (9, 20, 0, "single_draw", 1),
        (0, 20, 0, "stop_exhausted", 0),
        (12, 19, 10, "stop_first_grand_prize", 0),
    ],
)
def test_kunlun_strategy_targets_only_first_grand_prize(
    available, remaining, progress, expected_action, batch
) -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": available,
            "selected_big_capacity": 20,
            "selected_big_remaining": remaining,
            "progress": progress,
            "cost_type": 9001,
            "rewards": [],
            "claimable": [],
        }
    )
    assert decision.action == expected_action
    assert decision.expected_batch_size == batch


@pytest.mark.parametrize("progress", [17, 18, 19, 37, 38, 39])
def test_kunlun_strategy_tops_up_near_four_draw_refund(progress: int) -> None:
    threshold = 20 if progress < 20 else 40
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 10,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": progress,
            "cost_type": 9001,
            "rewards": [
                {
                    "threshold": threshold,
                    "reward": "Item|9001_4",
                    "state": "locked",
                }
            ],
            "claimable": [],
        }
    )
    assert decision.action == "single_draw"
    assert decision.expected_batch_size == 1
    assert decision.target_threshold == threshold


@pytest.mark.parametrize("progress", [16, 36])
def test_kunlun_strategy_does_not_continue_for_break_even_refund(progress: int) -> None:
    threshold = 20 if progress < 20 else 40
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 10,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": progress,
            "cost_type": 9001,
            "rewards": [
                {
                    "threshold": threshold,
                    "reward": "Item|9001_4",
                    "state": "locked",
                }
            ],
            "claimable": [],
        }
    )

    assert decision.action == "stop_first_grand_prize"


def test_kunlun_strategy_does_not_top_up_when_wallet_cannot_reach_refund() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 2,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": 16,
            "cost_type": 9001,
            "rewards": [
                {"threshold": 20, "reward": "Item|9001_4", "state": "locked"}
            ],
            "claimable": [],
        }
    )
    assert decision.action == "stop_first_grand_prize"


def test_kunlun_strategy_claims_reached_milestone_before_more_draws() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 3,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": 20,
            "claimable": [{"threshold": 20}],
        }
    )
    assert decision.action == "claim_rewards"


def test_config_phase_defers_sub_ten_inventory_without_first_prize() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 3,
            "selected_big_capacity": 20,
            "selected_big_remaining": 20,
            "progress": 20,
            "claimable": [],
        },
        allow_single_draws=False,
    )

    assert decision.action == "stop_single_draws_deferred"
    assert decision.expected_batch_size == 0


def test_config_phase_still_allows_near_milestone_top_up_after_hit() -> None:
    decision = task.decide_kunlun_next_draw(
        {
            "complete": True,
            "available_draws": 3,
            "selected_big_capacity": 20,
            "selected_big_remaining": 19,
            "progress": 17,
            "cost_type": 9001,
            "rewards": [
                {"threshold": 20, "reward": "Item|9001_4", "state": "locked"}
            ],
            "claimable": [],
        },
        allow_single_draws=False,
    )

    assert decision.action == "single_draw"
    assert decision.target_threshold == 20


@pytest.mark.parametrize("remaining", [None, 0, 18, 21, "20"])
def test_kunlun_strategy_fails_closed_without_exact_pool_remaining(remaining) -> None:
    with pytest.raises(RuntimeError, match="大奖剩余数量"):
        task.decide_kunlun_next_draw(
            {
                "complete": True,
                "available_draws": 23,
                "selected_big_capacity": 20,
                "selected_big_remaining": remaining,
                "progress": 0,
                "claimable": [],
            }
        )


def test_draw_mode_switch_reads_clicks_and_rereads_authoritative_boolean(monkeypatch) -> None:
    calls: list[str] = []
    states = iter(
        [
            {"complete": True, "ten_draw_enabled": False},
            {"complete": True, "ten_draw_enabled": True},
        ]
    )

    class Runtime:
        def cur_frame(self, *, update):
            assert update is True
            return "frame"

        def click_shape(self, scene, shape, *, frame_data_url):
            calls.append(f"{scene}:{shape}:{frame_data_url}")

    monkeypatch.setattr(
        task,
        "read_bothdraw_lottery_runtime",
        lambda: {"complete": True, "activity_id": 101},
    )
    monkeypatch.setattr(
        task,
        "read_bothdraw_ten_draw_runtime",
        lambda **_kwargs: next(states),
    )

    result = task.ensure_kunlun_draw_mode(Runtime(), ten_draw=True, poll_seconds=0.01)

    assert result == {"result": "changed", "ten_draw_enabled": True}
    assert calls == [f"{task.KUNLUN_MAIN_SCENE_ID}:鉴宝十次:frame"]


def test_draw_mode_incomplete_identity_causes_zero_clicks(monkeypatch) -> None:
    class Runtime:
        def __getattr__(self, name):
            raise AssertionError(f"zero action expected: {name}")

    monkeypatch.setattr(
        task,
        "read_bothdraw_lottery_runtime",
        lambda: {"complete": False, "reason": "identity missing"},
    )
    with pytest.raises(RuntimeError, match="identity missing"):
        task.ensure_kunlun_draw_mode(Runtime(), ten_draw=True)


def test_complete_kunlun_lottery_stops_after_first_prize(monkeypatch) -> None:
    class Spec:
        def require_executable_assets(self):
            return None

        def open_main_page(self, runtime):
            assert runtime == "runtime"

    states = iter(
        [
            {
                "complete": True,
                "activity_id": 101,
                "available_draws": 10,
                "selected_big_capacity": 20,
                "selected_big_remaining": 20,
                "progress": 0,
                "claimable": [],
            },
            {
                "complete": True,
                "activity_id": 101,
                "available_draws": 0,
                "selected_big_capacity": 20,
                "selected_big_remaining": 19,
                "progress": 10,
                "claimable": [],
            },
        ]
    )
    calls: list[str] = []
    monkeypatch.setattr(task, "_spec", lambda: Spec())
    monkeypatch.setattr(task, "_read_coherent_state", lambda: next(states))
    monkeypatch.setattr(
        task,
        "ensure_kunlun_draw_mode",
        lambda *_args, **kwargs: calls.append(f"mode:{kwargs['ten_draw']}") or {},
    )
    monkeypatch.setattr(
        task,
        "draw_kunlun_once",
        lambda _runtime, **_kwargs: calls.append("draw") or {"dx": 10},
    )
    monkeypatch.setattr(
        task,
        "close_kunlun_draw_result",
        lambda _runtime: calls.append("close") or {},
    )
    monkeypatch.setattr(
        task,
        "claim_kunlun_cumulative_rewards",
        lambda _runtime: calls.append("claim") or {},
    )

    result = task.complete_kunlun_lottery("runtime")

    assert result["stop_reason"] == "stop_first_grand_prize"
    assert calls == ["mode:True", "draw", "close", "claim"]


def test_config_phase_with_23_draws_uses_only_two_ten_draws(monkeypatch) -> None:
    class Spec:
        def require_executable_assets(self):
            return None

        def open_main_page(self, runtime):
            assert runtime == "runtime"

    states = iter(
        [
            {"complete": True, "activity_id": 101, "available_draws": 23,
             "selected_big_capacity": 20, "selected_big_remaining": 20,
             "progress": 0, "claimable": []},
            {"complete": True, "activity_id": 101, "available_draws": 13,
             "selected_big_capacity": 20, "selected_big_remaining": 20,
             "progress": 10, "claimable": []},
            {"complete": True, "activity_id": 101, "available_draws": 3,
             "selected_big_capacity": 20, "selected_big_remaining": 20,
             "progress": 20, "claimable": []},
        ]
    )
    calls: list[str] = []
    monkeypatch.setattr(task, "_spec", lambda: Spec())
    monkeypatch.setattr(task, "_read_coherent_state", lambda: next(states))
    monkeypatch.setattr(
        task,
        "ensure_kunlun_draw_mode",
        lambda *_args, **kwargs: calls.append(f"mode:{kwargs['ten_draw']}") or {},
    )
    monkeypatch.setattr(
        task,
        "draw_kunlun_once",
        lambda _runtime, **_kwargs: calls.append("draw") or {"dx": 10},
    )
    monkeypatch.setattr(
        task,
        "close_kunlun_draw_result",
        lambda _runtime: calls.append("close") or {},
    )
    monkeypatch.setattr(
        task,
        "claim_kunlun_cumulative_rewards",
        lambda _runtime: calls.append("claim") or {},
    )

    result = task.complete_kunlun_lottery(
        "runtime",
        allow_single_draws=False,
    )

    assert result["stop_reason"] == "stop_single_draws_deferred"
    assert result["final_state"]["available_draws"] == 3
    assert calls == [
        "mode:True", "draw", "close", "claim",
        "mode:True", "draw", "close", "claim",
    ]
