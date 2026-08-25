from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.vip_progress import (
    VipProgressError,
    compose_vip_progress_snapshot,
    normalize_vip_progress,
    vip_progress_delta,
)


VIP_EXP = {1: 100, 2: 1_000, 3: 2_000}


def test_normalize_vip_zero_uses_current_remainder_directly() -> None:
    result = normalize_vip_progress(
        vip_level=0,
        current_vip_exp=40,
        vip_exp_by_level=VIP_EXP,
    )

    assert result == {
        "vip_level": 0,
        "current_vip_exp": 40,
        "completed_level_exp": 0,
        "cumulative_vip_exp": 40,
        "next_vip_level": 1,
        "next_level_required_exp": 100,
        "remaining_to_next_level": 60,
        "at_max_known_level": False,
    }


def test_normalize_vip_progress_sums_each_completed_level_threshold() -> None:
    result = normalize_vip_progress(
        vip_level=2,
        current_vip_exp=350,
        vip_exp_by_level=VIP_EXP,
    )

    assert result["completed_level_exp"] == 1_100
    assert result["cumulative_vip_exp"] == 1_450
    assert result["next_vip_level"] == 3
    assert result["remaining_to_next_level"] == 1_650


def test_vip_progress_delta_survives_one_level_transition() -> None:
    before = normalize_vip_progress(
        vip_level=1,
        current_vip_exp=900,
        vip_exp_by_level=VIP_EXP,
    )
    after = normalize_vip_progress(
        vip_level=2,
        current_vip_exp=100,
        vip_exp_by_level=VIP_EXP,
    )

    assert vip_progress_delta(before, after) == 200


def test_vip_progress_delta_survives_multiple_level_transitions() -> None:
    before = normalize_vip_progress(
        vip_level=0,
        current_vip_exp=90,
        vip_exp_by_level=VIP_EXP,
    )
    after = normalize_vip_progress(
        vip_level=2,
        current_vip_exp=90,
        vip_exp_by_level=VIP_EXP,
    )

    assert vip_progress_delta(before, after) == 1_100


def test_max_known_level_does_not_invent_a_next_threshold() -> None:
    result = normalize_vip_progress(
        vip_level=3,
        current_vip_exp=0,
        vip_exp_by_level=VIP_EXP,
    )

    assert result["cumulative_vip_exp"] == 3_100
    assert result["next_vip_level"] is None
    assert result["remaining_to_next_level"] is None
    assert result["at_max_known_level"] is True


@pytest.mark.parametrize(
    ("level", "current", "table"),
    [
        (-1, 0, VIP_EXP),
        (True, 0, VIP_EXP),
        (0, -1, VIP_EXP),
        (0, True, VIP_EXP),
        (0, 0, {}),
        (0, 0, {0: 100}),
        (0, 0, {1: 0}),
        (0, 0, {1: 1.5}),
    ],
)
def test_normalize_vip_progress_rejects_invalid_inputs(level, current, table) -> None:
    with pytest.raises(VipProgressError):
        normalize_vip_progress(
            vip_level=level,
            current_vip_exp=current,
            vip_exp_by_level=table,
        )


def test_normalize_vip_progress_requires_every_completed_level() -> None:
    with pytest.raises(VipProgressError, match="缺少已完成等级配置：2"):
        normalize_vip_progress(
            vip_level=2,
            current_vip_exp=10,
            vip_exp_by_level={1: 100, 3: 2_000},
        )


def test_normalize_vip_progress_rejects_remainder_above_next_threshold() -> None:
    with pytest.raises(VipProgressError, match="超过下一等级阈值"):
        normalize_vip_progress(
            vip_level=1,
            current_vip_exp=1_001,
            vip_exp_by_level=VIP_EXP,
        )


def test_vip_progress_delta_rejects_regression() -> None:
    with pytest.raises(VipProgressError, match="累计经验倒退"):
        vip_progress_delta(
            {"cumulative_vip_exp": 200},
            {"cumulative_vip_exp": 199},
        )


def _runtime_snapshot(*, source: str, pid: int = 10, **values):
    return {
        "complete": True,
        "source": source,
        **values,
        "evidence": {"pid": pid, "process_start_ticks": 20},
    }


def test_compose_vip_progress_requires_same_process_runtime_facts() -> None:
    result = compose_vip_progress_snapshot(
        role_snapshot=_runtime_snapshot(
            source="runtime_memory.role.vip_level",
            vip_level=1,
        ),
        charge_snapshot=_runtime_snapshot(
            source="runtime_memory.charge.vip_exp",
            vip_exp=500,
            captured_at="2026-08-20T01:00:00+08:00",
        ),
        vip_exp_by_level=VIP_EXP,
    )

    assert result["complete"] is True
    assert result["cumulative_vip_exp"] == 600
    assert result["remaining_to_next_level"] == 500
    assert result["evidence"] == {
        "pid": 10,
        "process_start_ticks": 20,
        "role_source": "runtime_memory.role.vip_level",
        "charge_source": "runtime_memory.charge.vip_exp",
        "read_only": True,
    }


def test_compose_vip_progress_rejects_cross_process_or_incomplete_input() -> None:
    role = _runtime_snapshot(
        source="runtime_memory.role.vip_level",
        vip_level=1,
    )
    charge = _runtime_snapshot(
        source="runtime_memory.charge.vip_exp",
        pid=11,
        vip_exp=500,
    )
    with pytest.raises(VipProgressError, match="不是同一游戏进程"):
        compose_vip_progress_snapshot(
            role_snapshot=role,
            charge_snapshot=charge,
            vip_exp_by_level=VIP_EXP,
        )

    incomplete = dict(charge)
    incomplete["complete"] = False
    with pytest.raises(VipProgressError, match="Charge VIP 经验快照不完整"):
        compose_vip_progress_snapshot(
            role_snapshot=role,
            charge_snapshot=incomplete,
            vip_exp_by_level=VIP_EXP,
        )

    wrong_source = dict(role)
    wrong_source["source"] = "ocr"
    with pytest.raises(VipProgressError, match="Role VIP 等级快照来源无效"):
        compose_vip_progress_snapshot(
            role_snapshot=wrong_source,
            charge_snapshot=_runtime_snapshot(
                source="runtime_memory.charge.vip_exp",
                vip_exp=500,
            ),
            vip_exp_by_level=VIP_EXP,
        )
