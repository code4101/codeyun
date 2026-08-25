from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.lingxiao_xianhui_lottery import (
    LINGXIAO_LOTTERY_NAMESPACE,
    lingxiao_instance_id,
    list_lingxiao_lottery_points,
    record_lingxiao_lottery_point,
)
from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
    build_lingxiao_ten_draw_observations,
)


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def _snapshot(x: int, y: int) -> dict:
    return {
        "complete": True,
        "captured_at": "2026-08-15T23:15:00+08:00",
        "activity_id": 3001003,
        "x": x,
        "y": y,
        "hit_big_total": y,
        "evidence": {"pid": 1},
    }


def test_lingxiao_pool_points_use_activity_end_instance_and_are_monotonic() -> None:
    instance_id = lingxiao_instance_id("2026-08-16T23:59:45+08:00")
    assert instance_id == "lingxiao-xianhui-2026-08-16"
    engine = _engine()
    with Session(engine) as session:
        record_lingxiao_lottery_point(
            session, snapshot=_snapshot(0, 0), instance_id=instance_id
        )
        record_lingxiao_lottery_point(
            session, snapshot=_snapshot(10, 1), instance_id=instance_id
        )
        stored = list_lingxiao_lottery_points(session, instance_id=instance_id)

    assert stored.namespace == LINGXIAO_LOTTERY_NAMESPACE
    assert [(point.x, point.y, point.dx, point.dy) for point in stored.samples] == [
        (0, 0, 0, 0),
        (10, 1, 10, 1),
    ]
    assert {point.selected_library_id for point in stored.samples} == {3001003}


def test_lingxiao_draw_point_preserves_runtime_multi_big_prize_breakdown() -> None:
    instance_id = lingxiao_instance_id("2026-08-16T23:59:45+08:00")
    before = {
        **_snapshot(20, 1),
        "big_prize_items": [
            {"id": 300100301, "reward": "A", "hit_count": 1},
            {"id": 300100302, "reward": "B", "hit_count": 0},
        ],
    }
    after = {
        **_snapshot(23, 3),
        "big_prize_items": [
            {"id": 300100301, "reward": "A", "hit_count": 2},
            {"id": 300100302, "reward": "B", "hit_count": 1},
        ],
    }
    before_observation, after_observation = build_lingxiao_ten_draw_observations(
        before, after, action_id="runtime-two-big-prizes"
    )
    engine = _engine()
    with Session(engine) as session:
        record_lingxiao_lottery_point(
            session, snapshot=before_observation, instance_id=instance_id
        )
        record_lingxiao_lottery_point(
            session, snapshot=after_observation, instance_id=instance_id
        )
        stored = list_lingxiao_lottery_points(session, instance_id=instance_id)

    after_point = next(point for point in stored.samples if point.action_phase == "after_draw")
    assert after_point.draw_mode == "ten_draw"
    assert after_point.requested_batch_size == 10
    assert after_point.batch_size == 3
    assert {(row["id"], row["hit_increment"]) for row in after_point.big_prize_hits} == {
        (300100301, 1),
        (300100302, 1),
    }
