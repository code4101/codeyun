"""Pure settled-receipt accounting; actual gift/GUI flow is verified live."""
import pytest
from backend.core.fanxiu.data_annotation.tasks.xianyuan_gift_gui import remember_verified_npc_gift


def test_settled_effect_is_unique_and_preserves_unvisited_stock():
    progress = {'actions': [], 'remaining_items': {37092001: 2, 37130904: 3}}
    receipt = {'action_id': 'verified-consumption', 'status': 'verified'}
    for _ in range(2):
        remember_verified_npc_gift(progress, receipt, base_id=37092001, remaining_quantity=1)
    assert progress == {'actions': [receipt], 'remaining_items': {37092001: 1, 37130904: 3}}


@pytest.mark.parametrize('status,quantity', [('pending', 1), ('error', 1), ('verified', -1), ('verified', True)])
def test_unverified_effect_or_invalid_stock_is_rejected(status, quantity):
    progress = {'actions': [], 'remaining_items': {37092001: 2, 37130904: 3}}
    with pytest.raises(ValueError):
        remember_verified_npc_gift(progress, {'action_id': 'gift', 'status': status},
            base_id=37092001, remaining_quantity=quantity)
    assert progress == {'actions': [], 'remaining_items': {37092001: 2, 37130904: 3}}
