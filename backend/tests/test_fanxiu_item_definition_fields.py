"""Stable sparse configuration parsing; no Fake Runtime or reader."""
from backend.core.fanxiu.instrumentation.item_config import decode_item_definition_fields
from backend.core.fanxiu.storage_bag_catalog_purpose import classify_storage_bag_catalog_purpose


def test_observed_name_only_row_uses_declared_defaults():
    # Actual 400014002 row and Item column/default map, PID2769/start5126.
    result = decode_item_definition_fields(direct={}, array=[None, 400014002, 218880],
        indexes={'id': 1, 'name': 2, 'type': 15, 'subType': 16, 'canUse': 22},
        defaults={'id': 0, 'name': '', 'type': 0, 'subType': 0, 'canUse': 0},
        fields=('id', 'name', 'type', 'subType', 'canUse'))
    assert result == {'id': 400014002, 'name': 218880, 'type': 0, 'subType': 0, 'canUse': 0}


def test_numeric_hash_and_explicit_zero_override_defaults():
    result = decode_item_definition_fields(direct={15.0: 8, 16.0: 6, 'canUse': 0}, array=[],
        indexes={'type': 15, 'subType': 16, 'canUse': 22, 'quality': 19},
        defaults={'type': 0, 'subType': 0, 'canUse': 1}, fields=('type', 'subType', 'canUse', 'quality'))
    assert result == {'type': 8, 'subType': 6, 'canUse': 0, 'quality': None}


def test_default_zero_definition_does_not_prove_a_nonusable_business_entity():
    card = {'id': 400014002, 'name': '【中秋】', 'type': 0, 'sub_type': 0,
            'can_use': 0, 'effect_value': '', 'definition_defaults_validated': True}
    assert classify_storage_bag_catalog_purpose(card)['route'] == 'unresolved_metadata'
    card['definition_defaults_validated'] = False
    assert classify_storage_bag_catalog_purpose(card)['route'] == 'unresolved_metadata'
