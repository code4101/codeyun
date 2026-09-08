import pytest
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
