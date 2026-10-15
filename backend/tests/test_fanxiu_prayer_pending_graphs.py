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


def test_measured_fingerprint_survives_index_drift_only_with_full_reward_contract():
    from backend.core.fanxiu.prayer_research_planning import plan_unknown_prayer_boxes
    from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
    cards = {'7': {'id': 7, 'type': 2, 'name': '淬体箱', 'optional_gift_rewards': [{'id': 8, 'count': 2}]},
             '8': {'id': 8, 'type': 1, 'name': '淬体精魄'}}
    saved = classify_prayer_box(cards['7'], cards)
    cards['7'] = {**cards['7'], 'small_icon_reuse_count': 73}
    current = refresh_prayer_research_rules({7}, cards, [saved])[0]
    assert current['catalog_fingerprint'] == saved['catalog_fingerprint'] != current['card_fingerprint']
    summary = {'base_id': 7, 'prayer_category': '淬体', 'catalog_fingerprint': saved['catalog_fingerprint'],
               'mean_score_per_box': 33.0, 'eligibility': 'verified_positive'}
    plan = plan_unknown_prayer_boxes(prayer_category='淬体', research={'rules': [current], 'summaries': [summary]},
                                     inventory=[{'base_id': 7, 'quantity': 5, 'catalog_fingerprint': current['catalog_fingerprint']}])
    assert plan['known'][0]['expected_score_per_box'] == 33.0 and not plan['deferred']
    # Old schema without attribution leaves, or a changed reward, still waits for reconciliation.
    legacy = {k: v for k, v in saved.items() if k != 'reward_attribution_leaves'}
    assert 'card_fingerprint' not in refresh_prayer_research_rules({7}, cards, [legacy])[0]
    cards['7']['optional_gift_rewards'] = [{'id': 8, 'count': 3}]
    assert refresh_prayer_research_rules({7}, cards, [saved])[0]['catalog_fingerprint'] != saved['catalog_fingerprint']
