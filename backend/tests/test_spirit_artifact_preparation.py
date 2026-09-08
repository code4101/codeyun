import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_preparation import (
    calculate_preparation_gap,
    spirit_artifact_priority,
)


def test_stage_then_all_high_parts_then_all_low_parts():
    entries = [(stage, ware, part) for stage in ("初始", "错升")
               for ware in (2, 1) for part in range(1, 7)]
    ordered = sorted(entries, key=lambda row: spirit_artifact_priority(*row))
    expected = [(ware, part) for parts in ((5, 6), (1, 2, 3, 4))
                for ware in (1, 2) for part in parts]
    assert ordered == [(stage, ware, part) for stage in ("错升", "初始")
                       for ware, part in expected]


@pytest.mark.parametrize("grade,materials,raw,reset,missing_raw,missing", [
    (0, 0, 0, False, 1, 6),  # 无红色，共六件包含装备
    (1, 0, 0, False, 0, 5),
    (4, 1, 1, False, 0, 1),
    (6, 0, 0, False, 0, 0),
    (6, 0, 0, True, 1, 1),  # 阶数足够仍缺重置原始本体
    (4, 0, 0, True, 1, 2),  # 原始本体不能与阶数缺口重复累加
    (4, 1, 1, True, 0, 1),
    (6, 3, 3, True, 0, 0),
])
def test_red_body_gap(grade, materials, raw, reset, missing_raw, missing):
    result = calculate_preparation_gap(equipped_red_grade=grade,
        verified_material_grade_units=materials, available_raw_bodies=raw,
        reset_required=reset)
    assert (result.raw_bodies_missing, result.red_grade_units_missing) == (missing_raw, missing)


def test_reject_raw_material_double_accounting():
    with pytest.raises(ValueError):
        calculate_preparation_gap(equipped_red_grade=0,
            verified_material_grade_units=0, available_raw_bodies=1, reset_required=False)



def test_global_plan_returns_to_earlier_stage_after_one_part_advances():
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_preparation import (
        SpiritArtifactStageCandidate as C, plan_spirit_artifact_stage_round as plan,
    )
    rows = [C("预备", 1, 4, "ready"), C("错升", 4, 2, "ready"), C("初始", 1, 3, "ready")]
    assert plan(rows).candidate == rows[1]
    # 4-2修复完成后回全局：初始1-3先于预备1-4，不能接着洗4-2。
    rows[1] = C("预备", 4, 2, "ready")
    assert plan(rows).candidate == rows[2]


def test_blocked_parts_skip_with_reason_without_calling_round_complete():
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_preparation import (
        SpiritArtifactStageCandidate as C, plan_spirit_artifact_stage_round as plan,
    )
    blocked = C("错升", 1, 1, "blocked", "无红色本体或可兑换来源")
    later = C("初始", 1, 3, "ready")
    assert plan([blocked, later]).candidate == later
    exhausted = plan([blocked])
    assert exhausted.action == "round_exhausted" and exhausted.skipped == (blocked,)
    assert plan([blocked, C("错升", 2, 4)]).action == "analyze"
    with pytest.raises(ValueError): C("错升", 1, 1, "blocked")


def test_global_plan_all_five_six_before_any_one_four_in_same_stage():
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_preparation import (
        SpiritArtifactStageCandidate as C, plan_spirit_artifact_stage_round as plan,
    )
    rows = [C("初始", 1, 1, "ready"), C("初始", 9, 6, "ready"), C("预备", 1, 5, "ready")]
    assert plan(rows).candidate == rows[1]
    assert plan([C("待识别", 9, 1), *rows]).action == "analyze"
