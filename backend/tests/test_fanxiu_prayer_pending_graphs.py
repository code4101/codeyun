import pytest
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


def _body_cards():
    return {'7': {'id': 7, 'type': 2, 'name': '淬体箱', 'effect_value': 'pool_A',
                  'use_condition': 'ActivitybaseId|1_2', 'optional_gift_rewards': [{'id': 8, 'count': 2}]},
            '8': {'id': 8, 'type': 1, 'name': '淬体精魄', 'effect_value': 'native_A'}}


def _plan_after(change, *, saved=None):
    """Full public path: saved measurement -> refreshed current rule -> planner."""
    from backend.core.fanxiu.prayer_research_planning import plan_unknown_prayer_boxes
    from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
    cards = _body_cards()
    old = saved or classify_prayer_box(cards['7'], cards)
    change(cards)
    new = refresh_prayer_research_rules({7}, cards, [old])[0]
    summary = {'base_id': 7, 'prayer_category': '淬体', 'catalog_fingerprint': old['catalog_fingerprint'],
               'mean_score_per_box': 33., 'eligibility': 'verified_positive'}
    return plan_unknown_prayer_boxes(prayer_category='淬体', research={'rules': [new], 'summaries': [summary]},
                                     inventory=[{'base_id': 7, 'quantity': 5, 'catalog_fingerprint': new['catalog_fingerprint']}])


def test_display_only_drift_keeps_recursive_measurement_and_stays_known():
    plan = _plan_after(lambda c: c['7'].update(small_icon_reuse_count=73, source_path='rebuilt/rows.json'))
    assert plan['known'][0]['expected_score_per_box'] == 33.0 and not plan['deferred']


@pytest.mark.parametrize('change', [
    lambda c: c['8'].update(effect_value='native_B'),  # reviewer counterexample: child only, root raw hash unchanged
    lambda c: c['7'].update(effect_value='pool_B', use_condition='ActivitybaseId|2_2'),
    lambda c: c['7'].update(sub_type=2),
    lambda c: c['7']['optional_gift_rewards'][0].update(show_condition='lv>10'),
    lambda c: c['8'].update(use_condition='x'),
    lambda c: c['7'].update(unknown_new_field=1),  # unknown fields are semantic by default
])
def test_root_child_or_unknown_protocol_change_is_never_known(change):
    plan = _plan_after(change)
    assert not plan['known'] and not plan['unknown']
    assert plan['deferred'][0]['reason'] == 'waiting_catalog_reconciliation'


def test_legacy_root_hash_measurement_without_protocol_is_deferred_not_resampled():
    from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box, prayer_card_fingerprint
    cards = _body_cards()
    legacy = {**classify_prayer_box(cards['7'], cards), 'catalog_fingerprint': prayer_card_fingerprint(cards['7'])}
    plan = _plan_after(lambda c: None, saved=legacy)
    assert not plan['known'] and not plan['unknown']
    assert plan['deferred'][0]['reason'] == 'waiting_catalog_reconciliation'


def test_closeout_consumption_gate_rejects_child_protocol_change():
    from backend.core.fanxiu.data_annotation.tasks.prayer_box_closeout_execution import prayer_closeout_inventory
    from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import plan_prayer_closeout_boxes
    from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
    cards = _body_cards()
    saved = classify_prayer_box(cards['7'], cards)
    research = {'rules': [saved], 'summaries': [{'base_id': 7, 'prayer_category': '淬体', 'choice_key': '',
        'catalog_fingerprint': saved['catalog_fingerprint'], 'mean_score_per_box': 33., 'eligibility': 'verified_positive'}]}
    snapshot = {'complete': True, 'source': 'active_backpack_panel_item_info_list', 'fingerprint': 'bag',
                'evidence': {'pid': 1, 'process_start_ticks': 2}, 'items': [{'instance_id': 'a', 'base_id': 7, 'num': 5}]}
    progress = dict(complete=True, current_round=1, total_rounds=5, current_score=80,
                    round_target_score=100, round_score_gap=20)

    def next_open():
        return plan_prayer_closeout_boxes(progress, prayer_category='淬体', research=research,
            inventory=prayer_closeout_inventory(snapshot, cards, research), round_targets={'淬体': 100})['next_open']
    assert next_open()['base_id'] == 7
    cards['8'] = {**cards['8'], 'effect_value': 'native_B'}
    assert next_open() is None


def test_unscored_opening_under_old_fingerprint_stays_isolated():
    from backend.core.fanxiu.prayer_research_planning import plan_unknown_prayer_boxes
    from backend.core.fanxiu.storage_bag_prayer_research import classify_prayer_box
    cards = _body_cards()
    rule = classify_prayer_box(cards['7'], cards)
    opening = {'base_id': 7, 'catalog_fingerprint': 'old-version', 'prayer_category': '淬体', 'choice_key': None}
    plan = plan_unknown_prayer_boxes(prayer_category='淬体', research={'rules': [rule], 'verified_openings': [opening]},
                                     inventory=[{'base_id': 7, 'quantity': 149, 'catalog_fingerprint': rule['catalog_fingerprint']}])
    assert not plan['unknown'] and plan['deferred'][0]['reason'] == 'waiting_isolated_experiment_review'
