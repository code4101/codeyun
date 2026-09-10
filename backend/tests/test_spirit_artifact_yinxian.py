from decimal import Decimal

import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import meets_yinxian_target
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import (
    YinxianAttribute, analyze_yinxian_sample, refine_other_lock_ids,
)


@pytest.mark.parametrize(('score', 'full', 'ratio', 'accepted'), [
    (90, 100, '.90', True), ('89.999', 100, '.90', False),
    (135, 150, '.90', True), (90, 150, '.90', False),
    ('142.5', 150, '.95', True), ('142.499', 150, '.95', False),
    ('80.8', 100, '.90', False),
])
def test_threshold_uses_part_full_score(score, full, ratio, accepted):
    assert meets_yinxian_target(is_needed_a=True, basic_score=Decimal(str(score)),
                                basic_full_score=full, target_ratio=Decimal(ratio)) is accepted


def test_unneeded_attribute_does_not_hit_even_when_full():
    assert not meets_yinxian_target(is_needed_a=False,
                                    basic_score=150, basic_full_score=150)


@pytest.mark.parametrize(('score', 'full', 'accepted'), [
    ('91.999', 100, False), (92, 100, True),
    ('137.999', 150, False), (138, 150, True),
])
def test_default_target_is_92_percent(score, full, accepted):
    assert meets_yinxian_target(is_needed_a=True, basic_score=Decimal(str(score)),
                                basic_full_score=full) is accepted


def test_invalid_ratio_does_not_silently_accept():
    with pytest.raises(ValueError):
        meets_yinxian_target(is_needed_a=True, basic_score=100,
                             basic_full_score=100, target_ratio=float('nan'))


def candidates():
    return (
        YinxianAttribute(1, 'ATTACK', 92, 6, False, 100),
        YinxianAttribute(2, 'CRI_VALUE', 100, 6, False, 100),
        YinxianAttribute(3, 'C', 110, 6, False, 100),
        YinxianAttribute(4, 'MAXMP', 10, 3, True, 100),
        YinxianAttribute(5, 'MAXHP', 100, 6, True, 100),
        YinxianAttribute(6, 'DEFENSE', 100, 6, True, 100),
    )


def test_all_hits_reported_but_highest_red_ratio_includes_non_a():
    sample = analyze_yinxian_sample(candidates(), roll_index=1, needed_a_codes={'ATTACK', 'CRI_VALUE'})
    assert [e.cleanse_id for e in sample.hits] == [1, 2]
    assert sample.stop_yinxian
    assert sample.hits[1].is_full
    assert sample.highest_red_ratio == Decimal('1.1')
    assert [e.cleanse_id for e in sample.unlocked_candidates] == [1, 2, 3]


def test_identical_results_remain_independent_roll_samples():
    first = analyze_yinxian_sample(candidates(), roll_index=1, needed_a_codes=set())
    second = analyze_yinxian_sample(candidates(), roll_index=2, needed_a_codes=set())
    assert first.unlocked_candidates == second.unlocked_candidates
    assert first.roll_index != second.roll_index
    assert not first.hits
    assert len(first.unlocked_candidates) == 3


def test_full_unlocked_b_is_probability_sample_not_missing_a_hit_or_refinement():
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    rows = (
        YinxianAttribute(1, 'ATTACK', 100, 6, True, 100),
        YinxianAttribute(2, 'A2', 100, 6, True, 100),
        YinxianAttribute(3, 'A3', 100, 6, True, 100),
        YinxianAttribute(4, 'MAXMP', 70, 5, True, 100),
        YinxianAttribute(5, 'MAXHP', 60, 4, True, 100),
        YinxianAttribute(6, 'DEFENSE', 100, 6, False, 100),
    )
    sample = analyze_yinxian_sample(rows, roll_index=8, needed_a_codes={'A4'})
    assert sample.highest_red_ratio == Decimal('1')
    assert not sample.stop_yinxian and not sample.hits
    plan = plan_a_collection(rows, a_codes={'ATTACK', 'A2', 'A3', 'A4'},
                             b_codes={'MAXMP', 'MAXHP', 'DEFENSE'}, c_codes=set())
    assert plan.action == 'yinxian' and plan.target_cleanse_id is None
    assert plan.desired_lock_ids == (1, 2, 3, 4, 5)


def test_hit_uses_ratio_while_red_statistics_keep_original_quality():
    rows = tuple(YinxianAttribute(e.cleanse_id, e.code, e.value, 3, e.locked, e.normal_max)
                 for e in candidates())
    sample = analyze_yinxian_sample(rows, roll_index=3, needed_a_codes={'ATTACK'})
    assert len(sample.unlocked_candidates) == 3
    assert sample.highest_red_ratio is None
    assert sample.stop_yinxian
    assert [e.cleanse_id for e in sample.hits] == [1]
    assert sample.hits[0].quality == 3


def test_refine_plan_locks_all_five_other_rows_even_c():
    assert refine_other_lock_ids(candidates(), 1) == (2, 3, 4, 5, 6)


@pytest.mark.parametrize('lock_mask', range(64))
@pytest.mark.parametrize('target_value,expected_locks,target_id', [
    (7709, (1, 2, 3, 4, 6), 5),  # 达标 A 精炼时临时保护 C。
    (7000, (1, 2), None),         # 未达标则释放全部 C 和低 A 的遗留锁。
    (8000, (1, 2, 5), None),     # 精炼完成锁住满 A，再释放 C。
])
def test_lock_plan_reconciles_any_inherited_state(lock_mask, target_value, expected_locks, target_id):
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    codes = ['MAXHP', 'DEFENSE', 'MAXHP_RECOVER_FIX', 'MP_RECOVER_FIX',
             'CRI_DAMAGE_FIX', 'SPECIAL_DAMAGE_REDUCE']
    rows = tuple(YinxianAttribute(i + 1, code, target_value if i == 4 else 40,
                 6 if i == 4 else 3, bool(lock_mask & (1 << i)), 8000)
                 for i, code in enumerate(codes))
    plan = plan_a_collection(rows,
        a_codes={'ATTACK', 'MAXMP', 'CRI_VALUE', 'CRI_DAMAGE_FIX'},
        b_codes={'MAXHP', 'DEFENSE'},
        c_codes={'MAXHP_RECOVER_FIX', 'MP_RECOVER_FIX', 'SPECIAL_DAMAGE_REDUCE'})
    assert plan.desired_lock_ids == expected_locks
    assert plan.target_cleanse_id == target_id
    actual = {e.cleanse_id for e in rows if e.locked}
    assert plan.action == ('locks' if actual != set(expected_locks)
                           else 'refine' if target_id else 'yinxian')


def test_collection_preserves_other_hit_then_releases_c_after_refining():
    from dataclasses import replace
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    policy = dict(a_codes={'ATTACK', 'CRI_VALUE', 'CRI_DAMAGE_FIX'}, c_codes={'C'},
                  b_codes={'MAXMP', 'MAXHP', 'DEFENSE'})
    rows = candidates()
    plan = plan_a_collection(rows, **policy)
    assert plan.action == 'locks' and plan.target_cleanse_id == 1
    assert plan.desired_lock_ids == (2, 3, 4, 5, 6)
    rows = tuple(replace(e, locked=e.cleanse_id in plan.desired_lock_ids) for e in rows)
    assert plan_a_collection(rows, **policy).action == 'refine'
    rows = tuple(replace(e, value=100) if e.cleanse_id == 1 else e for e in rows)
    plan = plan_a_collection(rows, **policy)
    assert plan.action == 'locks' and plan.desired_lock_ids == (1, 2, 4, 5, 6)
    rows = tuple(replace(e, locked=e.cleanse_id in plan.desired_lock_ids) for e in rows)
    assert plan_a_collection(rows, **policy).action == 'yinxian'
    rows = tuple(replace(e, code='CRI_DAMAGE_FIX', value=100, locked=True)
                 if e.cleanse_id == 3 else e for e in rows)
    assert plan_a_collection(rows, **policy).action == 'complete'
    # 最后一条精炼完仍未锁时也已完成；客户端禁止第六把锁。
    rows = tuple(replace(e, locked=False) if e.cleanse_id == 3 else e for e in rows)
    assert plan_a_collection(rows, **policy).action == 'complete'


def test_b_supplement_requires_full_a_but_only_red_b():
    from dataclasses import replace
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_b_supplement
    rows = tuple(replace(e, value=100) if e.code == 'ATTACK' else e for e in candidates())
    policy = dict(a_codes={'ATTACK', 'CRI_VALUE'}, target_code='MAXMP')
    plan = plan_b_supplement(rows, **policy)
    assert plan.action == 'locks' and plan.desired_lock_ids == (1, 2, 3, 5, 6)
    rows = tuple(replace(e, locked=e.cleanse_id in plan.desired_lock_ids) for e in rows)
    assert plan_b_supplement(rows, **policy).action == 'yinxian'
    rows = tuple(replace(e, quality=6) if e.code == 'MAXMP' else e for e in rows)
    assert plan_b_supplement(rows, **policy).action == 'complete'


def test_four_a_missing_one_releases_only_lowest_priority_b_and_keeps_full_a():
    from dataclasses import replace
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    rows = tuple(YinxianAttribute(i, code, 100, 6, True, 100) for i, code in enumerate(
        ['ATTACK', 'A2', 'A3', 'MAXMP', 'MAXHP', 'DEFENSE'], 1))
    policy = dict(a_codes={'ATTACK', 'A2', 'A3', 'A4'},
                  b_codes={'MAXMP', 'MAXHP', 'DEFENSE'}, c_codes=set())
    plan = plan_a_collection(rows, **policy)
    assert plan.action == 'locks' and plan.desired_lock_ids == (1, 2, 3, 4, 5)
    rows = tuple(replace(e, locked=e.cleanse_id != 6) for e in rows)
    assert plan_a_collection(rows, **policy).action == 'yinxian'
    rows = tuple(replace(e, code='A4') if e.cleanse_id == 6 else e for e in rows)
    plan = plan_a_collection(rows, **policy)
    assert plan.action == 'complete' and plan.desired_lock_ids == (1, 2, 3, 4, 5)


def test_full_slots_do_not_guess_unknown_b_priority():
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    rows = tuple(YinxianAttribute(i, code, 100, 6, True, 100) for i, code in enumerate(
        ['ATTACK', 'A2', 'A3', 'MAXMP', 'MAXHP', 'UNKNOWN_B'], 1))
    plan = plan_a_collection(rows, a_codes={'ATTACK', 'A2', 'A3', 'A4'},
                            b_codes={'MAXMP', 'MAXHP', 'UNKNOWN_B'}, c_codes=set())
    assert plan.action == 'blocked'


def test_multiple_eligible_a_refine_one_by_one_before_releasing_b():
    from dataclasses import replace
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    rows = tuple(YinxianAttribute(i, code, 95 if i < 3 else 100, 6, True, 100)
        for i, code in enumerate(['ATTACK', 'A2', 'A3', 'MAXMP', 'MAXHP', 'DEFENSE'], 1))
    policy = dict(a_codes={'ATTACK', 'A2', 'A3', 'A4'},
                  b_codes={'MAXMP', 'MAXHP', 'DEFENSE'}, c_codes=set())
    first = plan_a_collection(rows, **policy)
    assert first.target_cleanse_id == 1 and first.desired_lock_ids == (2, 3, 4, 5, 6)
    rows = tuple(replace(e, value=100) if e.cleanse_id == 1 else e for e in rows)
    second = plan_a_collection(rows, **policy)
    assert second.target_cleanse_id == 2 and second.desired_lock_ids == (1, 3, 4, 5, 6)


@pytest.mark.parametrize("full_a_count,expected_unlocked", [(1, 2), (2, 1), (3, 1)])
def test_four_a_collection_keeps_capacity_without_early_b_release(full_a_count, expected_unlocked):
    from dataclasses import replace
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    a_codes = {"ATTACK", "A2", "A3", "A4"}
    codes = ["ATTACK", "A2", "A3"][:full_a_count] + ["MAXHP", "DEFENSE", "MAXMP"]
    codes += ["C" + str(i) for i in range(6-len(codes))]
    rows = tuple(YinxianAttribute(i+1, code, 100 if code in a_codes else 40,
                 6, False, 100) for i, code in enumerate(codes))
    policy = dict(a_codes=a_codes, b_codes={"MAXMP", "MAXHP", "DEFENSE"},
                  c_codes={code for code in codes if code.startswith("C")})
    plan = plan_a_collection(rows, **policy)
    locked_codes = {e.code for e in rows if e.cleanse_id in plan.desired_lock_ids}
    assert set(codes) & a_codes <= locked_codes
    assert {"MAXMP", "MAXHP"} <= locked_codes
    assert ("DEFENSE" in locked_codes) is (full_a_count < 3)
    assert 6-len(plan.desired_lock_ids) == expected_unlocked
    settled = tuple(replace(e, locked=e.cleanse_id in plan.desired_lock_ids) for e in rows)
    assert plan_a_collection(settled, **policy).action == "yinxian"


def test_five_slots_refine_and_release_b_then_finish_four_a():
    from dataclasses import replace
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import plan_a_collection
    policy = dict(a_codes={'ATTACK', 'MAXMP', 'CRI_VALUE', 'CRI_DAMAGE_FIX'},
                  b_codes={'MAXHP', 'DEFENSE'}, c_codes=set())
    rows = tuple(YinxianAttribute(i, code, 95 if i == 3 else 100, 6, True, 100)
        for i, code in enumerate(['ATTACK', 'MAXMP', 'CRI_VALUE', 'MAXHP', 'DEFENSE'], 1))
    plan = plan_a_collection(rows, **policy)
    assert plan.target_cleanse_id == 3 and plan.desired_lock_ids == (1, 2, 4, 5)
    rows = tuple(replace(e, value=100) if e.cleanse_id == 3 else e for e in rows)
    plan = plan_a_collection(rows, **policy)
    assert plan.desired_lock_ids == (1, 2, 3, 4)  # 释放最低优先级守御，不覆盖满 A。
    rows = tuple(replace(e, code='CRI_DAMAGE_FIX', locked=False) if e.cleanse_id == 5 else e for e in rows)
    assert plan_a_collection(rows, **policy).action == 'complete'
    assert analyze_yinxian_sample(rows, roll_index=1, needed_a_codes={'CRI_DAMAGE_FIX'}).stop_yinxian
