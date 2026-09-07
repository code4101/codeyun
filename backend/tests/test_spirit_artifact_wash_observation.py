"""洗炼观察的纯数据身份契约；不模拟分层 Runtime 或游戏流程。"""

import pytest

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.spirit_artifact_wash_observation import (
    SpiritArtifactWashTarget, SpiritArtifactWashTargetChanged,
    validate_spirit_artifact_wash_snapshot,
)


TARGET = SpiritArtifactWashTarget('123', 1, 4, (100, 200), 301)


def snapshot():
    return {'item_id': '123', 'ware_id': 1, 'part': 4, 'base_id': 301,
            'pid': 100, 'process_start_ticks': 200, 'refine_num': 4,
            'effects': [{'cleanse_id': 1, 'value': 100, 'quality': 3, 'locked': True}],
            'pending_effects': []}


def test_item_observation_does_not_claim_ui_identity():
    data = snapshot()
    validate_spirit_artifact_wash_snapshot(data, TARGET, verify_ui=False)
    with pytest.raises(SpiritArtifactWashTargetChanged):
        validate_spirit_artifact_wash_snapshot(data, TARGET, verify_ui=True)
    data['is_wash'] = True
    validate_spirit_artifact_wash_snapshot(data, TARGET, verify_ui=True)


@pytest.mark.parametrize(('field', 'value'), [
    ('item_id', '456'), ('pid', 101), ('process_start_ticks', 201),
    ('ware_id', 2), ('part', 3), ('base_id', 302),
])
def test_same_position_or_new_process_cannot_replace_requested_instance(field, value):
    data = snapshot()
    data[field] = value
    with pytest.raises(SpiritArtifactWashTargetChanged):
        validate_spirit_artifact_wash_snapshot(data, TARGET, verify_ui=False)


@pytest.mark.parametrize('locked', [None, 1, 'true'])
def test_ambiguous_lock_state_is_not_coerced(locked):
    data = snapshot()
    data['effects'][0]['locked'] = locked
    with pytest.raises(FanxiuRuntimeMemoryError, match='结构无效'):
        validate_spirit_artifact_wash_snapshot(data, TARGET, verify_ui=False)


def test_duplicate_attributes_are_incomplete_evidence():
    data = snapshot()
    data['effects'] *= 2
    with pytest.raises(FanxiuRuntimeMemoryError, match='身份重复'):
        validate_spirit_artifact_wash_snapshot(data, TARGET, verify_ui=False)
