"""同条件估计复用与新周研究范围的确定性契约；不模拟游戏。"""
from backend.core.fanxiu.prayer_research_planning import plan_unknown_prayer_boxes


def plan(category='炼丹', *, categories=None, summaries=(), kind='random', choices=(), modifier=None):
    rule = dict(base_id=1, catalog_fingerprint='v2', resource_categories=categories or [category],
                classification_complete=True, operation_kind=kind)
    return plan_unknown_prayer_boxes(prayer_category=category,
        research=dict(rules=[rule], summaries=list(summaries), choice_plans=list(choices)),
        inventory=[dict(base_id=1, quantity=3, catalog_fingerprint='v2')],
        reward_modifier_fingerprints=modifier)


def sample(**overrides):
    return dict(dict(base_id=1, catalog_fingerprint='v2', prayer_category='炼丹',
        choice_key='', mean_score_per_box=116, eligibility='verified_positive'), **overrides)


def test_current_estimate_is_reused_without_research():
    result = plan(summaries=[sample()])
    assert not result['unknown'] and result['known'][0]['expected_score_per_box'] == 116


def test_isolated_measurements_require_review_instead_of_consuming_again():
    row = sample()
    for domain in ('excluded_experiments', 'round_capped_experiments', 'quarantined_experiments'):
        research = dict(rules=[dict(base_id=1, catalog_fingerprint='v2', resource_categories=['炼丹'],
            classification_complete=True, operation_kind='fixed')], summaries=[],
            **{domain: [{'experiment': row}] if domain == 'quarantined_experiments' else [row]})
        result = plan_unknown_prayer_boxes(prayer_category='炼丹', research=research,
            inventory=[dict(base_id=1, quantity=3, catalog_fingerprint='v2')])
        assert not result['unknown']
        assert result['deferred'][0]['reason'] == 'waiting_isolated_experiment_review'


def test_actual_physical_prayer_entry_maps_to_cycle_category():
    from backend.core.fanxiu.prayer_cycle import prayer_entry_category
    assert prayer_entry_category('锻体祈愿') == '淬体'


def test_new_category_or_reward_condition_requires_new_estimate():
    for s in (sample(prayer_category='淬体'),
              sample(reward_modifier_fingerprint='old-condition')):
        assert len(plan(summaries=[s])['unknown']) == 1


def test_old_version_estimate_requires_catalog_reconciliation_before_research():
    result = plan(summaries=[sample(catalog_fingerprint='v1')])
    assert not result['unknown']
    assert result['deferred'][0]['reason'] == 'waiting_catalog_reconciliation'


def test_alchemy_excludes_mixed_while_other_weeks_preserve_them():
    assert not plan(categories=['仙花', '炼丹'])['unknown']
    assert len(plan('仙花', categories=['仙花', '炼丹'])['unknown']) == 1


def test_only_unmeasured_current_week_choice_is_planned():
    choices = [dict(base_id=1, catalog_fingerprint='v2', options=[
        dict(choice_key='a', prayer_category='淬体'), dict(choice_key='b', prayer_category='淬体'),
        dict(choice_key='c', prayer_category='洗灵')])]
    result = plan('淬体', kind='choice', choices=choices,
                  summaries=[sample(prayer_category='淬体', choice_key='a')])
    assert [r['choice_key'] for r in result['unknown']] == ['b']
    assert [r['choice_key'] for r in result['known']] == ['a']


def test_zero_observed_estimate_is_reused_without_changing_pool_conclusion():
    result = plan(summaries=[sample(mean_score_per_box=0, eligibility='zero_observed_unproven_pool')])
    assert not result['unknown']
    assert result['known'][0]['eligibility'] == 'zero_observed_unproven_pool'


def test_soil_box_requires_current_modifier_and_matches_exact_condition():
    research = dict(rules=[dict(base_id=39032101, catalog_fingerprint='v',
        resource_categories=['炼丹'], classification_complete=True, operation_kind='random')],
        summaries=[dict(base_id=39032101, catalog_fingerprint='v', prayer_category='炼丹',
                        reward_modifier_fingerprint='skill', mean_score_per_box=64.2)])
    stock = [dict(base_id=39032101, quantity=10, catalog_fingerprint='v')]
    result = plan_unknown_prayer_boxes(prayer_category='炼丹', research=research, inventory=stock)
    assert result['deferred'][0]['reason'] == 'waiting_current_reward_modifier'
    result = plan_unknown_prayer_boxes(prayer_category='炼丹', research=research, inventory=stock,
                                      reward_modifier_fingerprints={39032101: 'skill'})
    assert len(result['known']) == 1 and not result['unknown']
