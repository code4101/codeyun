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
