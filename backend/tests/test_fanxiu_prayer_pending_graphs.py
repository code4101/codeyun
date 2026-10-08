"""Reward-graph completion depends on evidence, independently of stock."""
from backend.core.fanxiu.storage_bag_prayer_research import refresh_prayer_research_rules


def test_zero_stock_retains_unresolved_graph_and_missing_catalog_evidence():
    pending = {'base_id': 30070004, 'classification_complete': False,
               'unresolved_item_ids': [30070004]}
    assert refresh_prayer_research_rules(set(), {}, [pending]) == [pending]
    cards = {'30070004': {'id': 30070004, 'type': 2, 'name': '军功·神焰宝箱'}}
    result = refresh_prayer_research_rules(set(), cards, [pending])
    assert result[0]['classification_complete'] is False
    assert result[0]['unresolved_item_ids'] == [30070004]


def test_complete_graph_resolves_pending_without_inventory():
    pending = {'base_id': 7, 'classification_complete': False, 'unresolved_item_ids': [7]}
    cards = {'7': {'id': 7, 'type': 2, 'optional_gift_rewards': [{'id': 8, 'count': 2}]},
             '8': {'id': 8, 'type': 1, 'name': '饲灵丸'}}
    result = refresh_prayer_research_rules(set(), cards, [pending])
    assert result[0]['classification_complete'] is True
    assert result[0]['resource_categories'] == ['灵兽']


def test_finished_absent_boxes_do_not_expand_current_discovery():
    assert refresh_prayer_research_rules(set(), {}, [
        {'base_id': 7, 'classification_complete': True}]) == []
