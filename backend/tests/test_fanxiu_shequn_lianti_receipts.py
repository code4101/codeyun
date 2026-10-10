"""Real locked file transactions and exact effect arithmetic; no fake game."""
import pytest
from backend.core.fanxiu.activity.shequn_lianti_receipts import (
    begin_shequn_lianti_training_action, reconcile_shequn_lianti_training_action,
    read_shequn_lianti_training_receipts,
    read_shequn_lianti_training_score_rule,
    record_shequn_lianti_goal_result, read_shequn_lianti_goal_result,
    review_shequn_lianti_legacy_inventory_baseline,
)


KEY = '8043401|1791579605000|1791727200000|8'


def test_goal_result_preserves_partial_and_requires_verified_actions(tmp_path):
    goal, dialog = facts()
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    after = {**goal, 'available': True, 'goal_completed': False, 'activity_progress': 9274}
    result = dict(result='partial', goal_completed=False, occurrence_key=KEY,
                  target_score=600000, goal_evidence=after, action_ids=[action])
    with pytest.raises(ValueError, match='action_id'):
        record_shequn_lianti_goal_result(result, receipt_dir=tmp_path)
    assert read_shequn_lianti_goal_result(KEY, receipt_dir=tmp_path) is None
    reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=action, book=dict(base_id=910515, stage=55, current_node=2),
        inventory_count=214372, goal=after, receipt_dir=tmp_path)
    stored = record_shequn_lianti_goal_result(result, receipt_dir=tmp_path)
    assert read_shequn_lianti_goal_result(KEY, receipt_dir=tmp_path) == stored
    assert stored['goal_completed'] is False
    assert stored['target_score'] == 600000


def test_goal_completion_requires_all_fourteen_claimed_identities(tmp_path):
    goal, _ = facts()
    after = {**goal, 'available': True, 'goal_completed': True,
             'task_ids': list(range(14)), 'claimed_task_ids': list(range(13)),
             'pending_task_ids': [], 'claimable_task_ids': []}
    result = dict(result='success', goal_completed=True, occurrence_key=KEY,
                  target_score=600000, goal_evidence=after, action_ids=[])
    with pytest.raises(ValueError, match='14'):
        record_shequn_lianti_goal_result(result, receipt_dir=tmp_path)
    after['claimed_task_ids'] = list(range(14))
    stored = record_shequn_lianti_goal_result(result, receipt_dir=tmp_path)
    assert read_shequn_lianti_goal_result(KEY, receipt_dir=tmp_path) == stored
    with pytest.raises(ValueError, match='事实'):
        record_shequn_lianti_goal_result({**result, 'target_score': 5000}, receipt_dir=tmp_path)


def facts():
    goal = dict(complete=True, progress_is_exact=True, training_binding_verified=True,
                training_activity_id=43403, activity_progress=4637,
                target_score=600000, occurrence_key=KEY)
    dialog = dict(complete=True, selection_cost_verified=True, activity_id=43403,
                  auto_buy_enabled=False, multiplicity_enabled=False,
                  breakthrough_material_cost=0, base_id=910515, current_node=1,
                  stage=55, selected_nodes=1, normal_item_id=5030001,
                  normal_material_cost=8, available_normal_material=214380)
    return goal, dialog


@pytest.mark.parametrize('active,inventory,expected', [
    (True, 214204, 'pending'), (False, 214203, 'pending'),
    (False, 214204, 'verified'),
])
def test_attempts_and_node_gain_are_independent_effect_units(tmp_path, active, inventory, expected):
    goal, dialog = facts()
    dialog.update(current_node=20, selected_nodes=10, normal_material_cost=80,
                  available_normal_material=214284)
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    row = reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=action,
        book=dict(base_id=910515, stage=55, current_node=37), inventory_count=inventory,
        goal=goal, execution=dict(complete=True, active=active), receipt_dir=tmp_path)
    assert row['status'] == expected
    if expected == 'verified':
        assert row['effect'] == dict(material_spent=80, selected_attempts=10,
                                     actual_node_gain=17, successful_attempts=None)


def test_pending_survives_unchanged_state_and_blocks_new_action(tmp_path):
    goal, dialog = facts()
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    row = reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=action, book=dict(base_id=910515, stage=55,current_node=1),
        inventory_count=214380, goal=goal, receipt_dir=tmp_path)
    assert row['status'] == 'pending'
    with pytest.raises(RuntimeError, match='原action_id'):
        begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    assert read_shequn_lianti_training_receipts(KEY, receipt_dir=tmp_path)[0]['action_id'] == action


@pytest.mark.parametrize('break_after,expected', [(77, 'verified'), (76, 'pending')])
def test_fresh_backpack_baseline_is_separate_from_dialog_budget(tmp_path, break_after, expected):
    import time
    goal, dialog = facts()
    identity = dict(pid=123, process_start_ticks=456)
    dialog.update(breakthrough_item_id=5030002, evidence=identity)
    evidence = dict(**identity, source='BackpackMgr.Model.BackpackData.ItemVoDic',
                    force_refresh=True, read_only=True, completed_at=time.time())
    baseline = dict(counts={5030001: 214390, 5030002: 77}, evidence=evidence)
    action = begin_shequn_lianti_training_action(
        goal=goal, dialog=dialog, inventory_before=baseline, receipt_dir=tmp_path)
    row = reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=action, book=dict(base_id=910515, stage=55, current_node=2),
        inventory_count=214382, breakthrough_inventory_count=break_after,
        inventory_evidence=evidence, goal=goal, execution=dict(complete=True, active=False),
        receipt_dir=tmp_path)
    assert row['status'] == expected
    if expected == 'verified':
        assert row['effect']['material_spent'] == 8
        assert row['effect']['inventory_baseline_verified'] is True


def test_partial_net_effect_requires_explicit_recovery_and_never_infers_count(tmp_path):
    goal, dialog = facts()
    dialog.update(current_node=37, selected_nodes=10, normal_material_cost=80,
                  available_normal_material=214204)
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    args = dict(occurrence_key=KEY, action_id=action,
                book=dict(base_id=910515, stage=55, current_node=54), inventory_count=214134,
                goal=goal, execution=dict(complete=True, active=False), receipt_dir=tmp_path)
    assert reconcile_shequn_lianti_training_action(**args)['status'] == 'pending'
    row = reconcile_shequn_lianti_training_action(**args, allow_partial_material_effect=True)
    assert row['status'] == 'verified'
    assert row['effect']['material_spent'] == 70
    assert row['effect']['configured_material_budget'] == 80
    assert row['effect']['kind'] == 'partial_net_material_effect'
    assert row['effect']['gross_server_cost_verified'] is False
    assert row['effect']['successful_attempts'] is None
    assert read_shequn_lianti_training_score_rule(
        occurrence_key=KEY, item_id=5030001, native_rate=100,
        receipt_dir=tmp_path)['reason'] == 'latest_material_effect_partial'


@pytest.mark.parametrize('fresh_original_baseline', [False, True])
def test_legacy_review_keeps_history_unproven_and_cannot_replay(tmp_path, fresh_original_baseline):
    import time
    goal, dialog = facts()
    identity = dict(pid=123, process_start_ticks=456)
    dialog.update(breakthrough_item_id=5030002, evidence=identity)
    evidence = dict(**identity, source='BackpackMgr.Model.BackpackData.ItemVoDic',
                    force_refresh=True, read_only=True, completed_at=time.time())
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog,
        inventory_before=(dict(counts={5030001:214380,5030002:77}, evidence=evidence)
                          if fresh_original_baseline else None), receipt_dir=tmp_path)
    book = dict(base_id=910515, stage=55, current_node=2)
    execution = dict(complete=True, active=False, evidence=identity)
    old = reconcile_shequn_lianti_training_action(occurrence_key=KEY, action_id=action,
        book=book, inventory_count=214373, breakthrough_inventory_count=77,
        inventory_evidence=evidence, execution=execution, goal=goal,
        allow_partial_material_effect=True, receipt_dir=tmp_path)
    args = dict(occurrence_key=KEY, action_id=action, book=book,
        inventory={5030001:214373,5030002:77}, inventory_evidence=evidence,
        execution=execution, goal=goal, receipt_dir=tmp_path,
        cost_range=dict(complete=True, base_id=910515, evidence=identity,
                        cost_nodes=[dict(node_id=2,item_id=5030001,quantity=8,required_stage=0)]))
    if fresh_original_baseline:
        with pytest.raises(ValueError, match='历史动作'):
            review_shequn_lianti_legacy_inventory_baseline(**args)
    else:
        review = review_shequn_lianti_legacy_inventory_baseline(**args)
        assert review['original_consumption_proven'] is False
        assert review['replay_allowed'] is False
        row = read_shequn_lianti_training_receipts(KEY, receipt_dir=tmp_path)[0]
        assert row['effect'] == old['effect']
        assert row['inventory_before'] is None
        with pytest.raises(RuntimeError, match='旧配置'):
            begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)


def test_exact_effect_verified_once_and_stale_node_never_replayed(tmp_path):
    goal, dialog = facts()
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    row = reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=action, book=dict(base_id=910515, stage=55,current_node=2),
        inventory_count=214372, goal={**goal, 'activity_progress':5437}, receipt_dir=tmp_path)
    assert row['status'] == 'verified'
    assert reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=action, book={},inventory_count=None,goal={},
        receipt_dir=tmp_path) == row
    with pytest.raises(RuntimeError, match='旧配置'):
        begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)


@pytest.mark.parametrize('book,count', [
    (dict(base_id=910515,stage=55,current_node=2),214371),
    (dict(base_id=910515,stage=55,current_node=3),214372),
    (dict(base_id=910516,stage=55,current_node=2),214372),
])
def test_partial_or_wrong_identity_never_clears_pending(tmp_path,book,count):
    goal, dialog = facts()
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog,receipt_dir=tmp_path)
    row = reconcile_shequn_lianti_training_action(
        occurrence_key=KEY,action_id=action,book=book,inventory_count=count,goal=goal,
        receipt_dir=tmp_path)
    assert row['status'] == 'pending'


def test_target_reached_and_purchase_gate_reject_before_writing(tmp_path):
    goal, dialog = facts()
    for g,d in (({**goal,'activity_progress':600000},dialog),
                (goal,{**dialog,'auto_buy_enabled':True})):
        with pytest.raises(ValueError):
            begin_shequn_lianti_training_action(goal=g,dialog=d,receipt_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_concurrent_submit_reserves_only_one_action(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    goal, dialog = facts()
    def reserve():
        try:
            return begin_shequn_lianti_training_action(goal=goal,dialog=dialog,receipt_dir=tmp_path)
        except RuntimeError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(value is not None for value in ids) == 1
    assert len(read_shequn_lianti_training_receipts(KEY,receipt_dir=tmp_path)) == 1


def test_changed_occurrence_does_not_verify_an_existing_effect(tmp_path):
    goal, dialog = facts()
    action = begin_shequn_lianti_training_action(goal=goal,dialog=dialog,receipt_dir=tmp_path)
    row = reconcile_shequn_lianti_training_action(
        occurrence_key=KEY,action_id=action,book=dict(base_id=910515,stage=55,current_node=2),
        inventory_count=214372,goal={**goal,'occurrence_key':'8043401|200|300|8'},receipt_dir=tmp_path)
    assert row['status'] == 'pending'


@pytest.mark.parametrize('delta,verified', [(800,True),(4637,False)])
def test_only_native_rate_matching_real_receipt_can_size_batches(tmp_path,delta,verified):
    goal, dialog = facts()
    action = begin_shequn_lianti_training_action(goal=goal,dialog=dialog,receipt_dir=tmp_path)
    reconcile_shequn_lianti_training_action(
        occurrence_key=KEY,action_id=action,book=dict(base_id=910515,stage=55,current_node=2),
        inventory_count=214372,goal={**goal,'activity_progress':4637+delta},receipt_dir=tmp_path)
    rule = read_shequn_lianti_training_score_rule(
        occurrence_key=KEY,item_id=5030001,native_rate=100,receipt_dir=tmp_path)
    assert rule['verified'] == verified


@pytest.mark.parametrize('deltas', [(800, 4637), (4637, 800)])
def test_latest_score_receipt_overrides_old_rate_evidence(tmp_path, deltas):
    goal, dialog = facts()
    for delta in deltas:
        action = begin_shequn_lianti_training_action(
            goal=goal, dialog=dialog, receipt_dir=tmp_path)
        next_goal = {**goal, 'activity_progress': goal['activity_progress'] + delta}
        reconcile_shequn_lianti_training_action(
            occurrence_key=KEY, action_id=action,
            book=dict(base_id=910515, stage=55, current_node=dialog['current_node'] + 1),
            inventory_count=dialog['available_normal_material'] - 8,
            goal=next_goal, receipt_dir=tmp_path)
        goal = next_goal
        dialog = {**dialog, 'current_node': dialog['current_node'] + 1,
                  'available_normal_material': dialog['available_normal_material'] - 8}
    rule = read_shequn_lianti_training_score_rule(
        occurrence_key=KEY, item_id=5030001, native_rate=100, receipt_dir=tmp_path)
    assert rule['action_id'] == action
    assert rule['verified'] == (deltas[-1] == 800)
    assert rule['task_score_delta'] == deltas[-1]
    assert rule['native_expected_delta'] == 800


def test_pending_latest_receipt_cannot_reuse_old_score_rule(tmp_path):
    goal, dialog = facts()
    first = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    after = {**goal, 'activity_progress': goal['activity_progress'] + 800}
    reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=first, book=dict(base_id=910515, stage=55, current_node=2),
        inventory_count=214372, goal=after, receipt_dir=tmp_path)
    pending = begin_shequn_lianti_training_action(
        goal=after, dialog={**dialog, 'current_node': 2, 'available_normal_material': 214372},
        receipt_dir=tmp_path)
    rule = read_shequn_lianti_training_score_rule(
        occurrence_key=KEY, item_id=5030001, native_rate=100, receipt_dir=tmp_path)
    assert rule == dict(verified=False, reason='latest_score_sample_pending',
                        action_id=pending, occurrence_key=KEY)
