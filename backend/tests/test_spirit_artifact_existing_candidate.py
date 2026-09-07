import pytest
from dataclasses import replace
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_a_collection import evaluate_existing_yinxian_candidate
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
