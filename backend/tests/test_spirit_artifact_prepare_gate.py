import pytest

from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_prepare_action import (
    authorized_raw_upgrade_candidates, verify_raw_upgrade_delta,
)
from backend.core.fanxiu.instrumentation.spirit_artifact_wash_observation import SpiritArtifactWashTarget


def facts():
    shared = dict(base_id=100, ware_id=1, part=2, quality=6, quantity=1, is_break=False, realm=0)
    return [dict(shared, item_id='target', grade=4), dict(shared, item_id='a', grade=1),
            dict(shared, item_id='b', grade=1), dict(shared, item_id='backup', grade=5)]


def test_only_authorized_lowest_raw_pool_can_be_consumed():
    target = SpiritArtifactWashTarget('target', 1, 2, (1, 2), 100)
    rows = facts()
    assert authorized_raw_upgrade_candidates(rows, target=target, authorized_raw_ids={'a', 'b'}) == ('a', 'b')
    with pytest.raises(ValueError):
        authorized_raw_upgrade_candidates(rows, target=target, authorized_raw_ids={'a'})
    with pytest.raises(ValueError):
        authorized_raw_upgrade_candidates([rows[0], rows[3]], target=target, authorized_raw_ids={'backup'})
    assert authorized_raw_upgrade_candidates([dict(rows[0], grade=6), rows[3]],
        target=target, authorized_raw_ids=set()) == ()


def test_each_step_proves_one_raw_and_protects_other_instances():
    target = SpiritArtifactWashTarget('target', 1, 2, (1, 2), 100)
    before = facts()
    after = [dict(before[0], grade=5), before[2], before[3]]
    assert verify_raw_upgrade_delta(before, after, target=target, candidates=('a', 'b')) == 'a'
    for bad in ([dict(before[0], grade=6), before[2], before[3]],
                [dict(before[0], grade=5), before[2], dict(before[3], grade=4)],
                [dict(before[0], grade=5), before[3]]):
        with pytest.raises(ValueError):
            verify_raw_upgrade_delta(before, bad, target=target, candidates=('a', 'b'))
