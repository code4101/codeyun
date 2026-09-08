import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_reset_action import classify_owned_raw_reset
from backend.core.fanxiu.instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget


def test_reset_gate_classifies_inventory_and_equipped_facts_without_realm_constraint():
    target = SpiritArtifactWashTarget('new', 3, 3, (11, 22), 140003)
    shared = dict(base_id=140003, ware_id=3, part=3, quantity=1, quality=6)
    old = dict(shared, item_id='old', grade=5, is_break=True, realm=1)
    new = dict(shared, item_id='new', grade=1, is_break=False, realm=0)
    kwargs = dict(target=target, previous_item_id='old', expected_previous_grade=5)
    assert classify_owned_raw_reset([old, new], equipped_item_id='old', **kwargs) == 'replace'
    assert classify_owned_raw_reset([old, new], equipped_item_id='new', **kwargs) == 'upgrade'
    assert classify_owned_raw_reset([dict(new, grade=6, realm=9)],
                                   equipped_item_id='new', **kwargs) == 'finish'
    for rows, equipped in [([new], 'new'), ([old, new], 'other'),
                           ([old, new, dict(new, item_id='third')], 'old'),
                           ([dict(new, grade=6, is_break=True)], 'new'),
                           ([dict(new, grade=6)], 'old'),
                           ([old, dict(new, part=2)], 'old')]:
        with pytest.raises(ValueError):
            classify_owned_raw_reset(rows, equipped_item_id=equipped, **kwargs)
