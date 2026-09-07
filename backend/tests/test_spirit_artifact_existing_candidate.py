import pytest
from dataclasses import replace
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_a_collection import (
    evaluate_existing_yinxian_candidate, evaluate_existing_refinement_candidate,
)
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import YinxianAttribute


def rows():
    return tuple(YinxianAttribute(i+1, "CODE"+str(i), 100 if i<4 else 10, 6, i<4, 100) for i in range(6))


def evaluate(current, pending, **kwargs):
    return evaluate_existing_yinxian_candidate(current, pending, plan_action='yinxian',
        a_codes={"ATTACK", "CRI"}, target_ratio=.9, **kwargs)


def test_existing_candidate_multiple_hits_and_locked_values_do_not_trigger():
    current = rows()
    assert evaluate(current, current) is False
    pending = (*current[:4], replace(current[4], code="ATTACK", value=95), replace(current[5], code="CRI", value=100))
    assert evaluate(current, pending) is True
    assert evaluate(current, (*pending[:4], replace(pending[4], value=89), replace(pending[5], value=89))) is False


def test_unknown_refinement_origin_or_lock_changes_block():
    current = rows()
    with pytest.raises(ValueError):
        evaluate_existing_yinxian_candidate(current, current, plan_action='refine', a_codes={"ATTACK"}, target_ratio=.9)
    with pytest.raises(ValueError):
        evaluate(current, (replace(current[0], value=99), *current[1:]))
    with pytest.raises(ValueError):
        evaluate(current, (*current[:5], replace(current[5], locked=True)))


def test_b_recovery_needs_red_but_not_ninety_percent():
    current = rows()
    pending = (*current[:5], replace(current[5], code="MAXMP", value=30))
    assert evaluate(current, pending, supplement_b_code="MAXMP") is True


def refinement_rows():
    locked = tuple(YinxianAttribute(i, 'LOCKED'+str(i), 100, 6, True, 100) for i in range(1, 6))
    old = YinxianAttribute(6, 'ATTACK', 95, 6, False, 100)
    return (*locked, old), (*locked, replace(old, value=100))


def evaluate_refinement(current, pending, **kwargs):
    return evaluate_existing_refinement_candidate(current, pending,
        **{'plan_action': 'refine', 'target_cleanse_id': 6, 'a_codes': {'ATTACK'}, **kwargs})


def test_existing_refinement_adopts_partial_or_full_improvement_without_source_claim():
    current, pending = refinement_rows()
    assert evaluate_refinement(current, pending)
    assert evaluate_refinement(current, (*pending[:5], replace(pending[5], value=98)))
    assert evaluate_refinement(current, (*pending[:5], replace(pending[5], cleanse_id=7, quality=7)))


@pytest.mark.parametrize('changes', [dict(value=95), dict(quality=5), dict(code='OTHER'),
    dict(normal_max=150), dict(basic_full_score=150), dict(locked=True)])
def test_existing_refinement_rejects_non_improvement_or_changed_identity(changes):
    current, pending = refinement_rows()
    with pytest.raises(ValueError):
        evaluate_refinement(current, (*pending[:5], replace(pending[5], **changes)))


def test_existing_refinement_requires_exact_five_locks_and_needed_unfinished_target():
    current, pending = refinement_rows()
    variants = [
        (current, (replace(pending[0], value=99), *pending[1:]), {}),
        ((replace(current[0], locked=False), *current[1:]),
         (replace(pending[0], locked=False), *pending[1:]), {}),
        ((*current[:5], replace(current[5], value=100)), pending, {}),
        (current, pending, {'target_cleanse_id': 5}),
        (current, pending, {'a_codes': {'OTHER'}}),
        (current, pending, {'plan_action': 'locks'}),
    ]
    for old, new, changed in variants:
        with pytest.raises(ValueError):
            evaluate_existing_refinement_candidate(old, new, **{
                'plan_action': 'refine', 'target_cleanse_id': 6, 'a_codes': {'ATTACK'}, **changed})
