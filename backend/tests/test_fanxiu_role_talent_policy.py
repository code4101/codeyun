"""Pure policy only; GUI and Runtime readers are accepted in the real game."""
import pytest

from backend.core.fanxiu.data_annotation.tasks.role_talent_update import (
    ordered_talent_tabs, validate_talent_costs,
)
from backend.core.fanxiu.instrumentation.role_talent import talent_condition_met
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.runtime_gui.role_talent import role_talent_nodes


def test_priority_is_independent_of_menu_order_and_appends_new_trees():
    assert [t['type'] for t in ordered_talent_tabs([{'type':i} for i in (8,6,5,4,1,2,3,7)])] == [1,3,2,4,5,6,7,8]


def test_unknown_currency_is_not_implicitly_authorized_by_new_tree():
    validate_talent_costs([{'costs':{14:200000,41:1204}}])
    with pytest.raises(RuntimeError,match='未授权'):
        validate_talent_costs([{'costs':{1:1}}])


def test_native_conditions_preserve_or_and_precedence():
    rule='Talent|184_1,Talent|102_1;Talent|184_1,Talent|68_1'
    assert talent_condition_met(rule,{184:1,68:1})
    assert not talent_condition_met(rule,{68:1})
    with pytest.raises(FanxiuRuntimeMemoryError):
        talent_condition_met('Unknown|1_1',{})


def test_existing_branch_wins_over_left_default():
    def variant(ident,level):
        return dict(talent_id=ident,current_level=level,max_level=3,costs={14:1},unlocked=True,active=level>0)
    tab={'nodes':[dict(id=1,index=5,variants=[variant(10,0),variant(11,2)],requires_choice=False)]}
    node=role_talent_nodes(tab)[0]
    assert (node['talent_id'],node['x'],node['y'])==(11,1,1)
    tab['nodes'][0].update(variants=[variant(10,0),variant(11,0)],requires_choice=True)
    assert role_talent_nodes(tab)[0]['talent_id']==10
