"""实验身份、目录版本和幂等持久化契约；使用真实SQLModel内存数据库。"""
from copy import deepcopy

import pytest
from sqlmodel import Session,create_engine

from backend.models import FanxiuPacketBusinessRecord
from backend.core.fanxiu.storage_bag_prayer_research import (
    BOX_EXPERIMENT_DOMAIN, persist_prayer_box_rule, record_prayer_box_experiment,
    record_fixed_box_non_prayer_conclusion,validate_prayer_box_experiment,
)


def experiment():
    before = dict(complete=True,activity_key="flower-week-A",current_round=1,
                  total_rounds=5,current_score=10,round_target_score=100)
    return dict(action_key="open1",base_id=1,opened_count=2,catalog_fingerprint="v1",
                isolated=True,activity_key="flower-week-A",prayer_category="仙花",
                prayer_before=before,prayer_after={**before,"current_score":30})


def test_complete_atomic_observation_computes_only_this_action():
    e=experiment()
    out=validate_prayer_box_experiment(e)
    assert out["score_delta"] == 20
    assert out["score_per_box"] == 10
    assert "score_delta" not in e


def test_caller_cannot_promote_a_sample_by_supplying_a_condition_fingerprint():
    with Session(database()) as session:
        with pytest.raises(ValueError,match='完整技能证据'):
            record_prayer_box_experiment(session,
                {**experiment(),'reward_modifier_fingerprint':'unverified-condition'})


@pytest.mark.parametrize("change",[
    {"activity_key":"flower-week-B"}, {"complete":False}, {"complete":1},
    {"current_round":2}, {"total_rounds":6}, {"round_target_score":200},
    {"current_score":9}, {"current_score":True},
])
def test_incomplete_cross_activity_round_or_rules_are_rejected(change):
    e=experiment();e["prayer_after"].update(change)
    with pytest.raises(ValueError):validate_prayer_box_experiment(e)


def test_missing_activity_on_observation_is_not_inferred_from_outer_label():
    e=experiment();del e["prayer_after"]["activity_key"]
    with pytest.raises(ValueError,match="同一活动"):validate_prayer_box_experiment(e)


def test_new_experiment_never_infers_catalog_fingerprint_at_recording_time():
    e=experiment();del e["catalog_fingerprint"]
    with pytest.raises(ValueError,match="目录版本"):validate_prayer_box_experiment(e)


def database():
    engine=create_engine("sqlite://")
    FanxiuPacketBusinessRecord.__table__.create(engine)
    return engine


def material_experiment():
    e = experiment()
    e.pop('opened_count', None)
    e.update(base_id=2006001, prayer_category='炼丹', consumed_count=10,
             action_result={'spent': 10, 'breakthroughs': 1})
    for side, quantity, timestamp in [('before', 20, 1.0), ('after', 10, 2.0)]:
        e['inventory_' + side] = dict(complete=True,
            source='active_backpack_panel_item_info_list', base_id=2006001,
            quantity=quantity, observed_at=timestamp, fingerprint=side,
            evidence={'pid': 2, 'process_start_ticks': 1})
    return e


def test_material_experiment_is_idempotent_and_separate_from_box_statistics():
    from backend.core.fanxiu.storage_bag_prayer_research import (
        record_prayer_material_experiment, load_prayer_box_research,
    )
    e = material_experiment()
    with Session(database()) as session:
        saved = record_prayer_material_experiment(session, e)
        assert record_prayer_material_experiment(session, e) == saved
        assert 'opened_count' not in saved and 'score_per_box' not in saved
        result = load_prayer_box_research(session)
        assert result['material_experiments'] == [saved]
        assert result['experiments'] == [] and result['summaries'] == []


def test_capped_material_observation_is_preserved_without_expected_score():
    from backend.core.fanxiu.storage_bag_prayer_research import record_prayer_material_experiment
    e = material_experiment()
    e['prayer_after']['current_score'] = e['prayer_after']['round_target_score']
    with Session(database()) as session:
        saved = record_prayer_material_experiment(session, e)
        assert saved['score_capped'] is True and saved['score_per_material'] is None


@pytest.mark.parametrize('changed', ['consumption', 'process', 'time', 'complete'])
def test_material_experiment_requires_matching_current_inventory_evidence(changed):
    from backend.core.fanxiu.storage_bag_prayer_research import record_prayer_material_experiment
    e = material_experiment()
    inventory = e['inventory_after']
    if changed == 'consumption':
        inventory['quantity'] = 9
    elif changed == 'process':
        inventory['evidence']['process_start_ticks'] = 2
    elif changed == 'time':
        inventory['observed_at'] = 0.5
    else:
        inventory['complete'] = False
    with Session(database()) as session, pytest.raises(ValueError):
        record_prayer_material_experiment(session, e)


def test_unattributed_fixed_experiment_preserved_without_entering_statistics():
    from backend.core.fanxiu.storage_bag_prayer_research import (
        record_unattributed_fixed_box_experiment, load_prayer_box_research,
    )
    e = {**experiment(), 'operation_kind': 'fixed', 'rewards': [{'item_id': 3, 'quantity': 25}]}
    with Session(database()) as session:
        persist_prayer_box_rule(session, dict(base_id=1, catalog_fingerprint='v1',
            reward_leaves=[dict(item_id=3, count_path_product=12)]))
        fact = record_unattributed_fixed_box_experiment(session, e)
        assert record_unattributed_fixed_box_experiment(session, e) == fact
        result = load_prayer_box_research(session)
        assert result['experiments'] == [] and result['summaries'] == []
        assert result['excluded_experiments'] == [fact]
        with pytest.raises(ValueError, match='事实冲突'):
            record_unattributed_fixed_box_experiment(session, {**e, 'prayer_after': {**e['prayer_after'], 'current_score': 31}})
        with pytest.raises(ValueError, match='未归因审计事实'):
            record_prayer_box_experiment(session, e)


def test_fixed_audit_rejects_matching_rewards_and_incomplete_observations():
    from backend.core.fanxiu.storage_bag_prayer_research import record_unattributed_fixed_box_experiment
    e = {**experiment(), 'operation_kind': 'fixed', 'rewards': [{'item_id': 3, 'quantity': 24}]}
    with Session(database()) as session:
        persist_prayer_box_rule(session, dict(base_id=1, catalog_fingerprint='v1',
            reward_leaves=[dict(item_id=3, count_path_product=12)]))
        with pytest.raises(ValueError, match='不应作为未归因'):
            record_unattributed_fixed_box_experiment(session, e)
        with pytest.raises(ValueError):
            record_unattributed_fixed_box_experiment(session, {**e, 'prayer_after': {'complete': False}})


def test_full_round_fact_remains_durable_without_biasing_yield_mean():
    from backend.core.fanxiu.storage_bag_prayer_research import load_prayer_box_research, summarize_prayer_box_experiments
    e = experiment()
    e['prayer_after']['current_score'] = 100
    with Session(database()) as session:
        saved = record_prayer_box_experiment(session, e)
        assert record_prayer_box_experiment(session, e) == saved
        result = load_prayer_box_research(session)
        assert result['round_capped_experiments'] == [saved]
        assert result['experiments'] == [] and result['summaries'] == []
        assert summarize_prayer_box_experiments([saved]) == []


def test_alchemy_remains_a_research_candidate_without_choice_allocation():
    from backend.core.fanxiu.storage_bag_prayer_research import load_prayer_box_research
    with Session(database()) as session:
        persist_prayer_box_rule(session, dict(base_id=1, catalog_fingerprint="v1",
            resource_categories=["炼丹"], allocation_categories=[], operation_kind="random"))
        result = load_prayer_box_research(session)
        assert [r['base_id'] for r in result['prayer_candidates']] == [1]


def test_outer_container_is_researchable_without_granting_top_up_permission():
    from backend.core.fanxiu.storage_bag_prayer_research import load_prayer_box_research
    with Session(database()) as session:
        persist_prayer_box_rule(session, dict(base_id=1, catalog_fingerprint="v1",
            resource_categories=["炼丹"], allocation_categories=[], operation_kind="random",
            is_outer_container=True, exclude_from_prayer_top_up=True))
        result = load_prayer_box_research(session)
        assert [r['base_id'] for r in result['research_candidates']] == [1]
        assert result['prayer_candidates'] == []
        assert result['research_candidates'][0]['exclude_from_prayer_top_up'] is True


def test_conditional_fixed_rewards_allow_active_subset_but_reject_extra_yield():
    from backend.core.fanxiu.storage_bag_prayer_research import validate_fixed_box_reward_attribution
    rule = dict(catalog_fingerprint="v1", reward_leaves=[
        dict(item_id=3, count_path_product=12), dict(item_id=4, count_path_product=12)])
    e = dict(catalog_fingerprint="v1", opened_count=10, rewards=[dict(item_id=3, quantity=120)])
    validate_fixed_box_reward_attribution(e, rule)
    with pytest.raises(ValueError, match="额外收益未归因"):
        validate_fixed_box_reward_attribution({**e, 'rewards': [dict(item_id=3, quantity=132)]}, rule)


def test_quarantine_preserves_fact_and_excludes_score_from_statistics():
    from backend.core.fanxiu.storage_bag_prayer_research import load_prayer_box_research, quarantine_prayer_box_experiment
    with Session(database()) as session:
        saved = record_prayer_box_experiment(session, experiment())
        evidence = dict(expected_quantity=120, actual_quantity=132)
        q = quarantine_prayer_box_experiment(session, action_key="open1", reason="额外到账待归因", evidence=evidence)
        assert quarantine_prayer_box_experiment(session, action_key="open1", reason="额外到账待归因", evidence=evidence) == q
        r = load_prayer_box_research(session)
        assert r['summaries'] == []
        assert r['experiments'] == []
        assert r['quarantined_experiments'][0]['experiment'] == saved


def test_new_fixed_sample_with_unattributed_yield_is_not_persisted():
    from backend.core.fanxiu.storage_bag_prayer_research import load_prayer_box_research
    with Session(database()) as session:
        persist_prayer_box_rule(session, dict(base_id=1, catalog_fingerprint="v1",
            operation_kind="fixed", reward_leaves=[dict(item_id=3, count_path_product=12)]))
        e = {**experiment(), 'operation_kind': 'fixed', 'rewards': [dict(item_id=3, quantity=26)]}
        with pytest.raises(ValueError, match="额外收益未归因"):
            record_prayer_box_experiment(session, e)
        assert load_prayer_box_research(session)['experiments'] == []


def test_same_receipt_can_replay_but_changed_fact_cannot_replace_it():
    with Session(database()) as session:
        e=experiment();saved=record_prayer_box_experiment(session,e)
        assert record_prayer_box_experiment(session,e)==saved
        conflict=deepcopy(e);conflict["prayer_after"]["current_score"]=31
        with pytest.raises(ValueError,match="事实冲突"):
            record_prayer_box_experiment(session,conflict)


def test_old_zero_score_cannot_be_applied_to_new_catalog():
    with Session(database()) as session:
        e=experiment();e["prayer_after"]["current_score"]=10
        record_prayer_box_experiment(session,e)
        persist_prayer_box_rule(session,dict(base_id=1,catalog_fingerprint="v2",
            operation_kind="fixed",classification_complete=True,resource_categories=["仙花"],
            reward_leaves=[dict(item_id=3,count_path_product=1)]))
        with pytest.raises(ValueError,match="相同目录版本"):
            record_fixed_box_non_prayer_conclusion(session,action_key="open1")


def test_exact_legacy_receipt_replay_preserves_existing_fact():
    e=experiment()
    for observation in (e["prayer_before"],e["prayer_after"]):del observation["activity_key"]
    saved={**e,"score_delta":20,"score_per_box":10}
    with Session(database()) as session:
        session.add(FanxiuPacketBusinessRecord(domain=BOX_EXPERIMENT_DOMAIN,record_key="open1",payload=saved))
        session.commit()
        assert record_prayer_box_experiment(session,e)==saved
