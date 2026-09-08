import pytest
from backend.core.fanxiu.catalog.spirit_artifact_wash_rules import spirit_artifact_strategy_codes
from backend.core.fanxiu.catalog.inventory_models import classify_spirit_artifact_stage


def test_sword_completes_four_a_with_attack_and_mp():
    policy = spirit_artifact_strategy_codes(['CRI_VALUE', 'CRI_DAMAGE_FIX'])
    assert set(policy['a_codes']) == {'CRI_VALUE','CRI_DAMAGE_FIX','ATTACK','MAXMP'}
    assert policy['b_codes'] == ['MAXHP','DEFENSE']
    effects = [dict(code=c, affix='满' if c!='MAXMP' else '') for c in policy['a_codes']]
    effects += [dict(code=c, affix='') for c in policy['b_codes']]
    assert classify_spirit_artifact_stage(rank=10, base_id=14000406, is_break=True,
        effects=effects, a_codes=policy['a_codes']) == '错升'
    for e in effects:
        if e['code']=='MAXMP': e['affix']='满'
    assert classify_spirit_artifact_stage(rank=10, base_id=14000406, is_break=True,
        effects=effects, a_codes=policy['a_codes']) == '突破'


def test_three_core_and_deduplication_follow_base_priority():
    assert spirit_artifact_strategy_codes(['CORE1','CORE2','CORE3'])['b_codes'] == ['MAXMP','MAXHP','DEFENSE']
    assert set(spirit_artifact_strategy_codes(['CORE1','ATTACK'])['a_codes']) == {'CORE1','ATTACK','MAXMP','MAXHP'}


@pytest.mark.parametrize('core', [[], ['C1','C2','C3','C4','C5'], ['', 'CORE']])
def test_incomplete_or_oversized_core_is_not_silently_truncated(core):
    with pytest.raises(ValueError): spirit_artifact_strategy_codes(core)
