from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from backend.core.fanxiu.activity.bothdraw_lottery_points import (
    BothdrawLotteryPointSpec,
    record_bothdraw_lottery_point,
)
from backend.core.fanxiu.activity.lottery_observation_ledger import (
    LotteryObservationConflict,
    build_paired_draw_observations,
    validate_lottery_observation_append,
)


def _point(x: int, y: int, *, available: int, progress: int) -> dict:
    return {
        "complete": True,
        "activity_id": 103,
        "x": x,
        "y": y,
        "available_draws": available,
        "progress": progress,
        "selected_big_reward": {"library_id": 700106},
    }


def test_build_pair_materializes_monotonic_before_after_scatter():
    before, after = build_paired_draw_observations(
        _point(10, 0, available=13, progress=10),
        _point(20, 1, available=3, progress=20),
        action_id="draw-2",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )

    assert (before["action_phase"], before["dx"], before["dy"]) == (
        "before_draw",
        0,
        0,
    )
    assert (after["action_phase"], after["dx"], after["dy"]) == (
        "after_draw",
        10,
        1,
    )
    assert before["action_id"] == after["action_id"] == "draw-2"


def test_append_requires_before_before_after_and_is_idempotent():
    before, after = build_paired_draw_observations(
        _point(0, 0, available=10, progress=0),
        _point(10, 1, available=0, progress=10),
        action_id="draw-1",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )

    before_result = validate_lottery_observation_append([], before)
    after_result = validate_lottery_observation_append([before], after)
    retry_result = validate_lottery_observation_append([before, after], after)

    assert before_result.idempotent is False
    assert (after_result.dx, after_result.dy) == (10, 1)
    assert retry_result.idempotent is True


def test_after_without_matching_before_fails_closed():
    _before, after = build_paired_draw_observations(
        _point(0, 0, available=10, progress=0),
        _point(10, 0, available=0, progress=10),
        action_id="draw-orphan",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )

    with pytest.raises(LotteryObservationConflict, match="缺少匹配"):
        validate_lottery_observation_append([], after)


def test_new_batch_must_not_skip_an_unclosed_before_point():
    first_before, _first_after = build_paired_draw_observations(
        _point(0, 0, available=20, progress=0),
        _point(10, 0, available=10, progress=10),
        action_id="draw-1",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )
    second_before, _second_after = build_paired_draw_observations(
        _point(0, 0, available=20, progress=0),
        _point(10, 0, available=10, progress=10),
        action_id="draw-2",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )

    with pytest.raises(LotteryObservationConflict, match="只有 before"):
        validate_lottery_observation_append([first_before], second_before)


@pytest.mark.parametrize(
    ("after", "message"),
    [
        (_point(9, 0, available=1, progress=8), "累抽进度差"),
        (_point(10, 0, available=1, progress=10), "可用次数差"),
        (_point(10, 11, available=0, progress=10), "大奖累计"),
    ],
)
def test_pair_conflicts_fail_closed(after, message):
    with pytest.raises(LotteryObservationConflict, match=message):
        build_paired_draw_observations(
            _point(0, 0, available=10, progress=0),
            after,
            action_id="draw-conflict",
            draw_mode="ten_draw",
            requested_batch_size=10,
        )


def test_same_action_phase_with_changed_cumulative_value_is_a_conflict():
    before, after = build_paired_draw_observations(
        _point(0, 0, available=10, progress=0),
        _point(10, 0, available=0, progress=10),
        action_id="draw-1",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )
    changed = {**after, "y": 1, "dy": 1}

    with pytest.raises(LotteryObservationConflict, match="同一动作阶段发生冲突"):
        validate_lottery_observation_append([before, after], changed)


def test_next_before_must_continue_the_last_after_point():
    before, after = build_paired_draw_observations(
        _point(0, 0, available=20, progress=0),
        _point(10, 1, available=10, progress=10),
        action_id="draw-1",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )
    skipped_before, _ = build_paired_draw_observations(
        _point(11, 1, available=9, progress=11),
        _point(12, 1, available=8, progress=12),
        action_id="draw-2",
        draw_mode="single_draw",
        requested_batch_size=1,
    )

    with pytest.raises(LotteryObservationConflict, match="未承接上一累计点"):
        validate_lottery_observation_append([before, after], skipped_before)


def test_shared_sql_store_enforces_the_paired_protocol():
    before, after = build_paired_draw_observations(
        _point(0, 0, available=10, progress=0),
        _point(10, 1, available=0, progress=10),
        action_id="persisted-draw",
        draw_mode="ten_draw",
        requested_batch_size=10,
    )
    for point in (before, after):
        point["captured_at"] = "2026-08-19T12:00:00+08:00"
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    spec = BothdrawLotteryPointSpec(
        namespace="paired-test",
        activity_label="严格账本测试",
        entity_name="严格散点",
    )
    with Session(engine) as session:
        with pytest.raises(LotteryObservationConflict, match="缺少匹配"):
            record_bothdraw_lottery_point(
                session,
                spec=spec,
                snapshot=after,
                instance_id="strict-instance",
            )
        record_bothdraw_lottery_point(
            session,
            spec=spec,
            snapshot=before,
            instance_id="strict-instance",
        )
        dataset = record_bothdraw_lottery_point(
            session,
            spec=spec,
            snapshot=after,
            instance_id="strict-instance",
        )

    assert [point.action_phase for point in dataset.samples] == [
        "before_draw",
        "after_draw",
    ]
    assert all(point.ledger_protocol == "paired_draw_v1" for point in dataset.samples)
