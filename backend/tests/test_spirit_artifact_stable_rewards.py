from backend.core.fanxiu.instrumentation.spirit_artifact_storage_bag import (
    resolve_stable_spirit_artifact_rewards, classify_spirit_artifact_bag_source,
    resolve_spirit_artifact_bag_source_coverage,
)


def test_only_explicit_selection_of_exact_red_body_is_stable():
    rules = {14001906: {'quality': 6}, 14001905: {'quality': 5}}
    rewards = [{'id': bid, 'count': 1} for bid in (14001906, 14001905, 14000031)]
    card = {'type': 21, 'sub_type': 21, 'optional_gift_rewards': rewards}
    assert resolve_stable_spirit_artifact_rewards(card, rules) == (14001906,)
    for changed in ({'type': 2, 'sub_type': 1}, {'type': None}, {'sub_type': None}):
        assert resolve_stable_spirit_artifact_rewards({**card, **changed}, rules) == ()
    rewards[0]['show_condition'] = 'CL|141'
    assert resolve_stable_spirit_artifact_rewards(card, rules) == ()


def test_old_claimed_stable_random_box_is_reclassified_without_hiding_display():
    item = {'title': 'random', 'stable_body_reward_ids': [14001906],
            'choices': [{'reward_id': 14001906, 'stable_body': True}]}
    card = {'type': 2, 'sub_type': 1,
            'optional_gift_rewards': [{'id': 14001906, 'count': 1}]}
    result = classify_spirit_artifact_bag_source(item, card, {14001906: {'quality': 6}})
    assert result['selection_kind'] == 'gift'
    assert result['stable_body_reward_ids'] == []
    assert result['choices'][0]['stable_body'] is False
    assert item['choices'][0]['stable_body'] is True


def test_non_choice_type_closes_choice_gap_without_claiming_fixed_gift_rewards():
    bag = {'evidence': {'pid': 1, 'process_start_ticks': 2}, 'unresolved_item_ids': [10, 20]}
    meta = {'pid': 1, 'process_start_ticks': 2, 'captured_at': 3,
            'items_by_id': {'10': {'item_resolved': True, 'item_type_id': 2},
                            '20': {'item_resolved': True, 'item_type_id': 5}}}
    result = resolve_spirit_artifact_bag_source_coverage(bag, meta)
    assert result['choice_sources_complete'] is True
    assert result['unresolved_gift_reward_ids'] == [10]
    assert result['unresolved_item_ids'] == [10, 20]  # 名称目录依旧未知
    meta['items_by_id']['10']['runtime_effect_description_id'] = 99
    result = resolve_spirit_artifact_bag_source_coverage(bag, meta,
        effect_text_snapshot={'pid': 1, 'process_start_ticks': 2,
                              'texts_by_id': {'99': '打开后会随机掉落以下道具'}})
    assert result['unresolved_gift_reward_ids'] == []
    assert result['verified_random_gift_ids'] == [10]
    assert result['stable_sources_complete'] is True
