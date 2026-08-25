from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.bothdraw_lottery_points import (
    BothdrawLotteryPointSpec,
    list_bothdraw_lottery_points,
    record_bothdraw_lottery_point,
)
from backend.core.fanxiu.data_annotation.tasks.bothdraw_lottery import (
    BothdrawLotterySpec,
    _merge_draw_observation,
)
from backend.core.fanxiu.instrumentation import bothdraw
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


SPEC = BothdrawLotteryPointSpec(
    namespace="test-bothdraw",
    activity_label="测试秘藏",
    entity_name="测试散点",
)


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _snapshot(
    x: int,
    y: int,
    *,
    action_id: str | None = None,
    phase: str | None = None,
    claimed_ids: list[int] | None = None,
) -> dict:
    return {
        "complete": True,
        "captured_at": "2026-08-13T14:10:00+08:00",
        "activity_id": 103,
        "x": x,
        "y": y,
        "action_id": action_id,
        "observation_kind": phase,
        "action_phase": phase,
        "selected_big_reward": {
            "library_id": 700106,
            "item_id": 4130017,
            "name": "古·山河无疆屏",
        },
        "selected_big_count": y,
        "selected_big_capacity": 20,
        "selected_big_remaining": 20 - y,
        "progress": x,
        "claimed_count": len(claimed_ids or []),
        "claimed_ids": claimed_ids or [],
    }


def test_same_x_preserves_distinct_action_phases_without_inventing_delta():
    engine = _engine()
    instance_id = "kunlun-secret-2026-08-13"
    with Session(engine) as session:
        record_bothdraw_lottery_point(
            session,
            spec=SPEC,
            snapshot=_snapshot(10, 0, action_id="draw-1", phase="after_draw"),
            instance_id=instance_id,
        )
        record_bothdraw_lottery_point(
            session,
            spec=SPEC,
            snapshot=_snapshot(
                10,
                0,
                action_id="claim-1",
                phase="after_claim",
                claimed_ids=[501],
            ),
            instance_id=instance_id,
        )
        # Retrying the exact observation remains idempotent.
        record_bothdraw_lottery_point(
            session,
            spec=SPEC,
            snapshot=_snapshot(
                10,
                0,
                action_id="claim-1",
                phase="after_claim",
                claimed_ids=[501],
            ),
            instance_id=instance_id,
        )
        samples = list_bothdraw_lottery_points(
            session, spec=SPEC, instance_id=instance_id
        ).samples

    assert len(samples) == 2
    assert [(row.action_phase, row.dx, row.dy) for row in samples] == [
        ("after_draw", 0, 0),
        ("after_claim", 0, 0),
    ]
    assert samples[1].claimed_ids == [501]


def test_two_phases_share_instance_and_next_draw_uses_previous_x():
    engine = _engine()
    instance_id = "kunlun-secret-2026-08-13"
    with Session(engine) as session:
        for value in (
            _snapshot(0, 0, action_id="stage-1-draw", phase="before_draw"),
            _snapshot(10, 0, action_id="stage-1-draw", phase="after_draw"),
            _snapshot(10, 0, action_id="stage-2-draw", phase="before_draw"),
            _snapshot(20, 1, action_id="stage-2-draw", phase="after_draw"),
        ):
            record_bothdraw_lottery_point(
                session, spec=SPEC, snapshot=value, instance_id=instance_id
            )
        samples = list_bothdraw_lottery_points(
            session, spec=SPEC, instance_id=instance_id
        ).samples

    assert [(row.x, row.dx, row.dy) for row in samples] == [
        (0, 0, 0),
        (10, 10, 0),
        (10, 0, 0),
        (20, 10, 1),
    ]


def test_merge_requires_probability_and_resource_same_activity_and_x():
    lottery = _snapshot(10, 0)
    resources = {
        "complete": True,
        "activity_id": 103,
        "x": 11,
        "available_currency": 13,
        "available_draws": 13,
        "cost_type": 9001,
        "cost_per_draw": 1,
        "progress": 10,
        "claimed_count": 0,
        "claimed_ids": [],
    }
    with pytest.raises(RuntimeError, match="不属于同一状态"):
        _merge_draw_observation(
            lottery,
            resources,
            observation_kind="after_draw",
            action_id="draw-1",
        )


def test_cumulative_claim_assets_do_not_require_a_draw_result_page():
    spec = BothdrawLotterySpec(
        activity_label="测试秘藏",
        main_scene_id=1,
        draw_shape="寻宝",
        cumulative_reward_shape="累计奖励容器",
        cumulative_reward_slots=2,
        draw_result_scene_id=None,
        draw_result_close_shape="继续",
        main_page_name="测试主页",
        open_main_page=lambda _runtime: None,
        read_page=lambda _runtime: None,
        read_lottery=lambda: {},
        read_cumulative_rewards=lambda: {},
        resolve_instance_id=lambda _captured_at: "test",
        record_snapshot=lambda _snapshot, _instance_id: None,
        cumulative_reward_slot_centers=((0.25, 0.5), (0.75, 0.5)),
    )

    spec.require_cumulative_claim_assets()
    with pytest.raises(RuntimeError, match="结果页"):
        spec.require_executable_assets()


def test_lottery_snapshot_exposes_authoritative_capacity_and_remaining(monkeypatch):
    info = object()
    big = object()
    monkeypatch.setattr(
        bothdraw,
        "_dictionary_items",
        lambda _reader, value: {
            "info-map": {103: info},
            "optional-map": {101: 700106},
            "big-count": {101: 1},
        }.get(value, {}),
    )
    monkeypatch.setattr(
        bothdraw,
        "_fields",
        lambda _reader, value: {
            info: {
                "rewardOptionalMap": "optional-map",
                "bigCount": "big-count",
                "bigItems": "big-items",
                "times": 10,
                "hitBig": 1,
                "hitBigTotal": 1,
            },
            big: {"id": 101, "times": 20},
        }.get(value, {}),
    )
    monkeypatch.setattr(
        bothdraw,
        "_list_values",
        lambda _reader, value: [big] if value == "big-items" else [],
    )

    snapshot = bothdraw._lottery_snapshot(
        object(),
        {"_BothInfoMap": "info-map"},
        reward_items=[
            {"library_id": 700106, "item_id": 4130017, "name": "古·山河无疆屏"}
        ],
    )

    assert snapshot["selected_big_count"] == 1
    assert snapshot["selected_big_capacity"] == 20
    assert snapshot["selected_big_remaining"] == 19
    assert snapshot["selected_big_reward"]["remaining"] == 19


def test_lottery_snapshot_rejects_count_above_capacity(monkeypatch):
    info = object()
    big = object()
    monkeypatch.setattr(
        bothdraw,
        "_dictionary_items",
        lambda _reader, value: {
            "info-map": {103: info},
            "optional-map": {101: 700106},
            "big-count": {101: 21},
        }.get(value, {}),
    )
    monkeypatch.setattr(
        bothdraw,
        "_fields",
        lambda _reader, value: {
            info: {
                "rewardOptionalMap": "optional-map",
                "bigCount": "big-count",
                "bigItems": "big-items",
            },
            big: {"id": 101, "times": 20},
        }.get(value, {}),
    )
    monkeypatch.setattr(
        bothdraw,
        "_list_values",
        lambda _reader, value: [big] if value == "big-items" else [],
    )

    with pytest.raises(FanxiuRuntimeMemoryError, match="库存异常"):
        bothdraw._lottery_snapshot(
            object(),
            {"_BothInfoMap": "info-map"},
            reward_items=[{"library_id": 700106}],
        )
