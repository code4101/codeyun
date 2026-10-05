"""灵脉目标身份互证；只校验事实，不模拟导航或点击。"""
from copy import deepcopy

import pytest

from backend.core.fanxiu.data_annotation.tasks.lingmai import validate_lingmai_interaction_target


def facts():
    selected=dict(seat_id=17,role_id=99,team_uid=22)
    observed=dict(source='active_npc_dialogue_scene_seat_projection',is_ally=False,
        seat=dict(seat_id=17,owner=dict(role_id=99,team_uid=22)))
    return selected,observed


def test_same_selected_seat_and_player_are_accepted():
    selected,observed=facts()
    validate_lingmai_interaction_target(selected,observed)


@pytest.mark.parametrize('change',['seat','player','team','ally','unknown_relation','source'])
def test_changed_or_friendly_npc_target_is_rejected(change):
    selected,observed=facts();observed=deepcopy(observed)
    if change=='seat':observed['seat']['seat_id']=18
    elif change=='player':observed['seat']['owner']['role_id']=100
    elif change=='team':observed['seat']['owner']['team_uid']=23
    elif change=='ally':observed['is_ally']=True
    elif change=='unknown_relation':observed.pop('is_ally')
    else:observed['source']='list_click_intent'
    with pytest.raises(ValueError):validate_lingmai_interaction_target(selected,observed)
