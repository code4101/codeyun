from copy import deepcopy

import pytest

from backend.core.fanxiu.instrumentation.spirit_artifact_grade import (
    project_spirit_artifact_grade_observation,
)
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


def facts():
    ui = dict(pid=1, process_start_ticks=2, item_id='3', ware_id=2, part=3,
              grade=1, panel_address='0x10')
    item = dict(item_id='3', ware_id=2, part=3, grade=1, base_id=100)
    inventory = dict(complete=True, pid=1, process_start_ticks=2, items=[item])
    equipped = dict(pid=1, process_start_ticks=2, items=[dict(item)])
    return ui, deepcopy(ui), inventory, equipped


def test_joint_projection_accepts_exact_facts():
    before, after, inventory, equipped = facts()
    result = project_spirit_artifact_grade_observation(before, after, inventory, equipped=equipped)
    assert result['base_id'] == 100
    assert 'panel_address' not in result


@pytest.mark.parametrize('fault', ['ui', 'incomplete', 'process', 'duplicate', 'grade', 'equip'])
def test_joint_projection_rejects_inconsistent_observation(fault):
    before, after, inventory, equipped = facts()
    if fault == 'ui':
        after['panel_address'] = '0x20'
    elif fault == 'incomplete':
        inventory['complete'] = False
    elif fault == 'process':
        inventory['process_start_ticks'] = 3
    elif fault == 'duplicate':
        inventory['items'] *= 2
    elif fault == 'grade':
        inventory['items'][0]['grade'] = 2
    elif fault == 'equip':
        equipped['items'][0]['item_id'] = '4'
    with pytest.raises(FanxiuRuntimeMemoryError):
        project_spirit_artifact_grade_observation(before, after, inventory, equipped=equipped)
