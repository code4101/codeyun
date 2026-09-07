import pytest

from backend.core.fanxiu.instrumentation.spirit_artifact_equipped import project_spirit_artifact_equipped
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


def test_actual_reference_wins_over_higher_grade_spare():
    inventory = {'complete': True, 'items': [
        {'item_id': 'low', 'ware_id': 1, 'part': 4, 'grade': 1},
        {'item_id': 'high', 'ware_id': 1, 'part': 4, 'grade': 10},
    ]}
    result = project_spirit_artifact_equipped({1: ['low'], 2: []}, inventory)
    assert [row['item_id'] for row in result['items']] == ['low']
    assert len(result['slots']) == 12
    assert result['slots'][3]['item_id'] == 'low'
    assert sum(row['item_id'] is None for row in result['slots']) == 11


@pytest.mark.parametrize('references', [{1: ['missing']}, {2: ['x']}, {1: ['x', 'x']}, {1: ['x', 'y']}])
def test_reject_invalid_equipment_references(references):
    inventory = {'complete': True, 'items': [
        {'item_id': 'x', 'ware_id': 1, 'part': 1},
        {'item_id': 'y', 'ware_id': 1, 'part': 1},
    ]}
    with pytest.raises(FanxiuRuntimeMemoryError):
        project_spirit_artifact_equipped(references, inventory)
