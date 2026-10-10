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


def test_checkpoint_distinguishes_material_boundary_from_minute_continuation():
    from datetime import datetime, timedelta, timezone
    from backend.core.fanxiu.data_annotation.tasks.shequn_lianti_checkpoint import project_shequn_lianti_checkpoint_result
    now = datetime(2026, 10, 10, 4, tzinfo=timezone.utc)
    recorded = {'result': 'partial', 'goal_completed': False, 'occurrence_key': KEY,
                'activity_progress': 389518, 'target_score': 600000}
    blocked = project_shequn_lianti_checkpoint_result(
        {**recorded, 'reason': 'breakthrough_material_boundary'}, now=now)
    assert blocked['status'] == 'blocked'
    assert 'retry_at' not in blocked  # Parent owns ordinary blocked timing.
    assert blocked['goal_completed'] is False
    due = project_shequn_lianti_checkpoint_result(
        {**recorded, 'reason': 'due_jobs_take_priority'}, now=now)
    assert due['status'] == 'pending'
    assert due['yield_reason'] == 'higher_priority_trigger'
    assert datetime.fromisoformat(due['retry_at']) == now + timedelta(minutes=1)
    for reason in ('investigate_original_material_cost_variance', 'unknown_reason'):
        with pytest.raises(RuntimeError):
            project_shequn_lianti_checkpoint_result({**recorded, 'reason': reason}, now=now)


def test_real_node_200_boundary_cannot_be_avoided_by_shrinking_attempts():
    from backend.core.fanxiu.activity.shequn_lianti import normal_only_shequn_lianti_quantum
    # The 11:22 first-fault observation: node 190, nine normal entries,
    # followed by node 200 requiring two breakthrough materials.
    dialog = dict(cost_path_complete=True, activity_id=43403,
                  auto_buy_enabled=False, multiplicity_enabled=False,
                  current_node=190, maximum_nodes=7810, stage=55,
                  normal_item_id=5030001, breakthrough_item_id=5030002,
                  cost_nodes=[dict(node_id=n, item_id=5030001, quantity=8, required_stage=0)
                              for n in range(191, 200)] +
                             [dict(node_id=200, item_id=5030002, quantity=2, required_stage=0)])
    assert normal_only_shequn_lianti_quantum(dialog, maximum_attempts=10) == 0
    assert normal_only_shequn_lianti_quantum(dialog, maximum_attempts=5) == 0
    boundary = {**dialog, 'current_node': 199, 'cost_nodes': dialog['cost_nodes'][-1:],
                'maximum_nodes': 1}
    assert normal_only_shequn_lianti_quantum(boundary, maximum_attempts=10) == 0
    with pytest.raises(ValueError, match='覆盖'):
        normal_only_shequn_lianti_quantum({**dialog, 'cost_nodes': []}, maximum_attempts=10)
    with pytest.raises(ValueError, match='身份'):
        normal_only_shequn_lianti_quantum({**dialog, 'cost_nodes': list(reversed(dialog['cost_nodes']))}, maximum_attempts=10)


def test_checkpoint_binding_preserves_real_period_and_rejects_other_ladders():
    from dataclasses import replace
    from datetime import datetime, timezone
    from backend.core.fanxiu.activity.ranking_lifecycle import RankingOccurrence
    from backend.core.fanxiu.activity.shequn_lianti import (
        shequn_lianti_goal_key_for_occurrence, plan_shequn_lianti_goal_checkpoint)
    start = datetime.fromtimestamp(1791579605, timezone.utc)
    end = datetime.fromtimestamp(1791727200, timezone.utc)
    occurrence = RankingOccurrence(
        activity_type='shequn-lianti', family='resource_rank', runtime_id='8043401400004',
        activity_id=8043401, start_at=start, end_at=end, prepare_at=start,
        close_at=end, cross_count=8, base_id=43400)
    assert shequn_lianti_goal_key_for_occurrence(occurrence) == KEY
    checkpoint = plan_shequn_lianti_goal_checkpoint(occurrence)
    assert checkpoint.due_at.timestamp() == 1791579605
    assert checkpoint.business_date == start.astimezone().date().isoformat()
    assert checkpoint.checkpoint_kind == 'shequn_lianti_goal_0500'
    refreshed = replace(occurrence, runtime_id='8043401300004')
    assert plan_shequn_lianti_goal_checkpoint(refreshed).key == checkpoint.key
    # A refreshed Runtime row retains the period; a different ladder never
    # inherits this account's taught material/goal policy through its name.
    assert shequn_lianti_goal_key_for_occurrence(replace(occurrence, runtime_id='8043401300004')) == KEY
    for other in (replace(occurrence, cross_count=16),
                  replace(occurrence, activity_id=16043401),
                  replace(occurrence, start_at=start.replace(tzinfo=None))):
        with pytest.raises(ValueError, match='8跨'):
            shequn_lianti_goal_key_for_occurrence(other)


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
             'activity_progress': 600000,
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


def test_claimed_ladder_cannot_hide_low_progress_or_an_unreferenced_pending_action(tmp_path):
    goal, dialog = facts()
    after = {**goal, 'available': True, 'goal_completed': True,
             'task_ids': list(range(14)), 'claimed_task_ids': list(range(14)),
             'pending_task_ids': [], 'claimable_task_ids': []}
    result = dict(result='success', goal_completed=True, occurrence_key=KEY,
                  target_score=600000, goal_evidence=after, action_ids=[])
    with pytest.raises(ValueError, match='600000'):
        record_shequn_lianti_goal_result(result, receipt_dir=tmp_path)
    after['activity_progress'] = 600000
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    with pytest.raises(ValueError, match='待核验原action_id'):
        record_shequn_lianti_goal_result(result, receipt_dir=tmp_path)
    assert read_shequn_lianti_goal_result(KEY, receipt_dir=tmp_path) is None
    assert read_shequn_lianti_training_receipts(KEY, receipt_dir=tmp_path)[0]['action_id'] == action


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


@pytest.mark.parametrize('gain,expected', [(8, 'verified'), (9, 'pending')])
def test_declared_node_envelope_is_reconciled_without_replaying(tmp_path, gain, expected):
    goal, dialog = facts()
    dialog['node_gain_envelope'] = {'max_node_gain': 8}
    action = begin_shequn_lianti_training_action(goal=goal, dialog=dialog, receipt_dir=tmp_path)
    row = reconcile_shequn_lianti_training_action(
        occurrence_key=KEY, action_id=action,
        book=dict(base_id=910515, stage=55, current_node=dialog['current_node'] + gain),
        inventory_count=dialog['available_normal_material'] - dialog['normal_material_cost'],
        goal=goal, execution=dict(complete=True, active=False), receipt_dir=tmp_path)
    assert row['status'] == expected
    assert read_shequn_lianti_training_receipts(KEY, receipt_dir=tmp_path)[0]['action_id'] == action


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
