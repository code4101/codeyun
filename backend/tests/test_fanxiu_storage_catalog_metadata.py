"""Stable loaded-config parsing and real metadata persistence; no simulated game."""
import json
import pytest
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    build_storage_bag_runtime_metadata_cards, publish_storage_bag_catalog_metadata,
    load_storage_bag_atlas,
)


def evidence():
    # Fields observed in the public 2026-10-09 Runtime read, PID2769/start5126.
    return {'read_only': True, 'source': 'DBMgr.ConfigDic[Item.Item]', 'pid': 2769,
            'process_start_ticks': 5126, 'captured_at': 1791500400,
            'items_by_id': {400010009: {'item_resolved': True, 'item_name': '幽魂宝匣',
                'item_type_id': 2, 'item_type': '礼包宝匣', 'item_sub_type_id': 1,
                'effect_value': '98052065_29073', 'runtime_description_id': 10,
                'runtime_effect_description_id': 11}}}


def test_identity_projection_does_not_invent_use_or_reward_pool():
    cards = build_storage_bag_runtime_metadata_cards(evidence(), {'read_only': True,
        'pid': 2769, 'process_start_ticks': 5126, 'texts_by_id': {10: 'description', 11: 'random'}})
    card = cards['400010009']
    assert card['name'] == '幽魂宝匣' and card['type'] == 2
    assert card['description'] == 'description'
    assert card['metadata_only'] is True
    assert 'can_use' not in card and 'optional_gift_rewards' not in card
    with pytest.raises(ValueError):
        build_storage_bag_runtime_metadata_cards(evidence(), {'read_only': True, 'pid': 2769,
                                                             'process_start_ticks': 9999})


def test_metadata_publication_preserves_historical_inventory(tmp_path):
    path = tmp_path / 'atlas.json'
    row = {'base_id': 400010009, 'atlas_order': 1, 'num': 7, 'present': False,
           'quantity_observed_at': '2026-10-09T03:27:16+08:00', 'last_seen_at': 'historic'}
    path.write_text(json.dumps({'items': [row], 'updated_at': row['quantity_observed_at']}), encoding='utf-8')
    card = {'id': 400010009, 'name': '幽魂宝匣', 'metadata_only': True}
    publish_storage_bag_catalog_metadata({'400010009': card, '32010': {'id': 32010, 'name': '道则碎片·轮回域'}}, path=path)
    restarted = load_storage_bag_atlas(path=path)
    assert restarted['runtime_available'] is False
    assert restarted['captured_at'] == row['quantity_observed_at']
    assert len(restarted['items']) == 1
    actual = restarted['items'][0]
    assert {k: actual[k] for k in row} == row
    assert actual['item']['name'] == '幽魂宝匣'
    # Repeated/late incomplete metadata never erases a known card.
    publish_storage_bag_catalog_metadata({}, path=path)
    assert load_storage_bag_atlas(path=path)['items'][0]['item']['name'] == '幽魂宝匣'


def test_nameless_runtime_identity_remains_unresolved():
    metadata = evidence()
    metadata['items_by_id'][400010009]['item_name'] = ''
    assert build_storage_bag_runtime_metadata_cards(metadata, {'read_only': True,
        'pid': 2769, 'process_start_ticks': 5126, 'texts_by_id': {}}) == {}
