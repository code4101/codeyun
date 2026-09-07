from dataclasses import replace

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_breakthrough import plan_breakthrough_preparation
from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_yinxian import YinxianAttribute


def test_three_full_a_require_red_b_but_not_full_b():
    codes = ('ATTACK', 'CRI_VALUE', 'CRI_DAMAGE_FIX', 'MAXMP', 'MAXHP', 'DEFENSE')
    rows = tuple(YinxianAttribute(i + 1, code, 100 if i < 3 else 40,
                 6 if i < 3 else 4, True, 100) for i, code in enumerate(codes))
    policy = dict(a_codes=set(codes[:3]), is_break=False, required_red_count=4)
    plan = plan_breakthrough_preparation(rows, client_can_breakthrough=False, **policy)
    assert (plan.action, plan.target_code) == ('supplement_b', 'MAXMP')
    assert plan_breakthrough_preparation(rows, client_can_breakthrough=True, **policy).action == 'blocked'
    rows = tuple(replace(e, quality=6, value=81) if e.code == 'MAXMP' else e for e in rows)
    assert plan_breakthrough_preparation(rows, client_can_breakthrough=True, **policy).action == 'ready'
    assert plan_breakthrough_preparation(rows, client_can_breakthrough=False, **policy).action == 'blocked'


def test_four_a_cannot_omit_one_core_even_if_client_allows_breakthrough():
    codes = ('ATTACK', 'CORE1', 'CORE2', 'CORE3', 'MAXMP', 'MAXHP')
    rows = tuple(YinxianAttribute(i + 1, code, 99 if i == 3 else 100, 6, True, 100)
                 for i, code in enumerate(codes))
    plan = plan_breakthrough_preparation(rows, a_codes=set(codes[:4]),
        client_can_breakthrough=True, is_break=False, required_red_count=4)
    assert plan.action == 'collect_a'
