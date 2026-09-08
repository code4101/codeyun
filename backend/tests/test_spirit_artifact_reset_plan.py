from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_reset_plan import (
    plan_spirit_artifact_stable_resets, SpiritArtifactResetSupply,
)
import pytest


def facts():
    codes = ['ATTACK','CRI_VALUE','CRI_DAMAGE_FIX','MAXMP','MAXHP','DEFENSE']
    rows, stock = [], []
    for part in range(1,7):
        base = 14000000 + part*100 + 6
        rows.append(dict(order=part, part_name=str(part), rank=7, runtime_ware_id=1,
            runtime_part=part, runtime_base_id=base, runtime_item_id=str(part), runtime_is_break=True,
            runtime_effects=[dict(code=c, affix='' if part in (1,5) else '满') for c in codes]))
        stock.append(dict(item_id=str(part), base_id=base, ware_id=1, part=part,
                          grade=7, quality=6, is_break=True, quantity=1))
    return dict(runtime_complete=True,runtime_debug=dict(pid=1,process_start_ticks=2),
                artifacts=[dict(order=1,name='血晶摩诃剑',rows=rows)]),dict(complete=True,pid=1,process_start_ticks=2,items=stock)


def test_sources_prioritize_five_six_and_skip_manually_repaired():
    hall, inventory = facts()
    source = SpiritArtifactResetSupply('market',14000506,123,1,15100001,80)
    plan = plan_spirit_artifact_stable_resets(hall,inventory,supplies=[source],balances={15100001:80})
    assert plan.next_entry.assessment.part == 5
    assert plan.next_entry.action == 'obtain_raw'
    assert (1,4,'突破') in plan.ignored
    assert len(plan.entries)==2


def test_owned_raw_prevents_purchase_and_no_source_is_explicitly_blocked():
    hall, inventory = facts()
    inventory['items'].append(dict(item_id='raw',base_id=14000106,ware_id=1,part=1,
                                   grade=1,quality=6,is_break=False,quantity=1))
    plan = plan_spirit_artifact_stable_resets(hall,inventory,supplies=[],balances={})
    assert plan.next_entry.action=='reset_owned' and plan.next_entry.raw_item_ids==('raw',)
    assert plan.entries[0].assessment.readiness=='blocked' and plan.entries[0].assessment.reason


def test_multiple_external_sources_require_explicit_preference():
    hall, inventory = facts()
    sources=[SpiritArtifactResetSupply('market',14000506,123,1,15100001,80),
             SpiritArtifactResetSupply('storage_bag',14000506,15100002,2)]
    plan=plan_spirit_artifact_stable_resets(hall,inventory,supplies=sources,balances={15100001:80})
    assert plan.status=='analyze' and plan.next_entry.action=='choose_source'
    plan=plan_spirit_artifact_stable_resets(hall,inventory,supplies=sources,balances={15100001:80},
                                         source_order=('storage_bag','market'))
    assert plan.next_entry.supplies[0].kind=='storage_bag'


def test_random_catalog_reward_cannot_be_smuggled_into_stable_plan():
    hall, inventory = facts()
    source = SpiritArtifactResetSupply('storage_bag', 14001906, 19010180, 1)
    with pytest.raises(ValueError, match='明确自选箱'):
        plan_spirit_artifact_stable_resets(hall, inventory, supplies=[source], balances={})
