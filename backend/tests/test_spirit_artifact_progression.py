import pytest
from copy import deepcopy
from backend.core.fanxiu.catalog.spirit_artifact_progression import scan_spirit_artifact_upgrades


def test_full_scan_finds_daily_loot_without_any_purchase_list():
    current = dict(item_id='equipped', base_id=100, ware_id=3, part=4,
                   quality=6, grade=10, realm=1, quantity=1, is_break=True)
    raw = dict(current, item_id='daily-drop', grade=1, realm=0, is_break=False)
    owned = {'inventory': {'pid': 1, 'process_start_ticks': 2, 'complete': True,
                            'items': [current, raw]},
             'equipped': {'pid': 1, 'process_start_ticks': 2, 'complete': True,
                          'items': [{'item_id': 'equipped', 'ware_id': 3, 'part': 4}]}}
    rules = {'grade': {(100, 11): (100, 1)}, 'realm': {(100, 2): (200, 1)},
             'items': {100: {'canUltra': 1}}}
    candidates = scan_spirit_artifact_upgrades(owned, {200: 3}, rules=rules)
    assert {c.dimension for c in candidates} == {'grade', 'realm'}
    assert candidates[0].material_item_ids == ('daily-drop',)
    assert candidates[1].available == 3
    owned['inventory']['items'] = [current]
    assert [c.dimension for c in scan_spirit_artifact_upgrades(owned, {200: 3}, rules=rules)] == ['realm']
    owned['inventory']['complete'] = False
    with pytest.raises(ValueError):
        scan_spirit_artifact_upgrades(owned, {200: 3}, rules=rules)


def test_upgrade_delta_rejects_wrong_material_and_other_attribute_changes():
    from backend.core.fanxiu.catalog.spirit_artifact_progression import SpiritArtifactUpgradeCandidate
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_upgrade_all import verify_spirit_artifact_upgrade_delta
    item = dict(item_id='target', base_id=100, ware_id=1, part=2, grade=12, realm=0, is_break=True)
    raw = dict(item, item_id='raw', grade=1, is_break=False)
    before = dict(inventory=dict(pid=1, process_start_ticks=2, complete=True, items=[item, raw]),
                  equipped=dict(pid=1, process_start_ticks=2, items=[item]))
    after = deepcopy(before)
    after['inventory']['items'] = [dict(item, grade=13)]
    after['equipped']['items'] = [dict(item, grade=13)]
    candidate = SpiritArtifactUpgradeCandidate('target', 1, 2, 100, 'grade', 12, 13, 100, 1, 1, ('raw',))
    assert verify_spirit_artifact_upgrade_delta(before, after, candidate=candidate,
                                               counts_before={}, counts_after={}) == ('raw',)
    after['inventory']['items'][0]['is_break'] = False
    with pytest.raises(ValueError):
        verify_spirit_artifact_upgrade_delta(before, after, candidate=candidate, counts_before={}, counts_after={})
