import pytest

from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import merge_xianyuan_job_scope


def previous():
    return {'remaining_items': [{'base_id': 1, 'quantity': 437},
                                {'base_id': 2, 'quantity': 60}], 'action_ids': ['original']}


def test_due_yield_preserves_full_stock_and_original_effects():
    value = merge_xianyuan_job_scope(previous(), {'outcome': 'partial', 'action_result': {'actions': []}})
    assert value['remaining_items'] == previous()['remaining_items']
    assert value['action_ids'] == ['original']
    assert value['outcome'] == 'partial'


def test_one_npc_progress_does_not_drop_unvisited_npc():
    value = merge_xianyuan_job_scope(previous(), {'outcome': 'partial', 'action_result': {
        'remaining_items': [{'base_id': 1, 'quantity': 317}],
        'actions': [{'action_id': 'new', 'status': 'verified'}]}})
    assert value['remaining_items'] == [{'base_id': 1, 'quantity': 317}, {'base_id': 2, 'quantity': 60}]
    assert value['action_ids'] == ['original', 'new']


@pytest.mark.parametrize('rows', [None, [{'base_id': 1, 'quantity': 0}]])
def test_completion_requires_whole_scope_zero(rows):
    with pytest.raises(ValueError, match='全范围'):
        merge_xianyuan_job_scope(previous(), {'outcome': 'complete',
            'action_result': {'remaining_items': rows}})


def test_fresh_empty_selection_can_close_scope():
    value = merge_xianyuan_job_scope(previous(), {'outcome': 'complete', 'plan': {'item_count': 0}})
    assert value['remaining_items'] == []
    assert value['scope_known'] is True


def test_first_yield_keeps_scope_explicitly_unknown():
    value = merge_xianyuan_job_scope({}, {'outcome': 'partial'})
    assert value['remaining_items'] is None
    assert value['scope_known'] is False
    with pytest.raises(ValueError, match='全范围'):
        merge_xianyuan_job_scope({}, {'outcome': 'complete'})


def test_repeated_action_and_noop_do_not_erase_current_scope():
    current = {'remaining_items': [{'base_id': 1, 'quantity': 317},
                                  {'base_id': 2, 'quantity': 60}], 'action_ids': ['original']}
    value = merge_xianyuan_job_scope(current, {'outcome': 'partial', 'action_result': {
        'actions': [{'action_id': 'original', 'status': 'verified'}]}})
    assert value['action_ids'] == ['original']
    assert value['remaining_items'] == current['remaining_items']


def test_complete_new_whole_scope_zero_is_accepted():
    value = merge_xianyuan_job_scope(previous(), {'outcome': 'complete',
        'plan': {'item_count': 2, 'items': [{'base_id': 1}, {'base_id': 2}]}, 'action_result': {
        'remaining_items': [{'base_id': 1, 'quantity': 0}, {'base_id': 2, 'quantity': 0}]}})
    assert value['outcome'] == 'complete'
    assert all(row['quantity'] == 0 for row in value['remaining_items'])


def test_persisted_json_string_ids_merge_with_integer_ids():
    value = merge_xianyuan_job_scope(previous(), {'outcome': 'partial', 'action_result': {
        'remaining_items': [{'base_id': '1', 'quantity': 317}]}})
    assert value['remaining_items'] == [{'base_id': 1, 'quantity': 317}, {'base_id': 2, 'quantity': 60}]


@pytest.mark.parametrize('base_id', [True, 0, -1, 'invalid', '1.5'])
def test_invalid_inventory_ids_are_rejected(base_id):
    with pytest.raises(ValueError):
        merge_xianyuan_job_scope({}, {'outcome': 'partial', 'action_result': {
            'remaining_items': [{'base_id': base_id, 'quantity': 1}]}})


def test_historical_duplicate_aliases_merge_only_when_quantities_agree():
    old = {'remaining_items': [{'base_id': 1, 'quantity': 317}, {'base_id': '1', 'quantity': 317}]}
    assert len(merge_xianyuan_job_scope(old, {'outcome': 'partial'})['remaining_items']) == 1
    old['remaining_items'][1]['quantity'] = 999
    with pytest.raises(ValueError, match='冲突'):
        merge_xianyuan_job_scope(old, {'outcome': 'partial'})


@pytest.mark.parametrize('rows', [[], [{'base_id': 1, 'quantity': 0}]])
def test_complete_must_freshly_observe_each_current_plan_item(rows):
    old = {'remaining_items': [{'base_id': 1, 'quantity': 0}, {'base_id': 2, 'quantity': 0}]}
    with pytest.raises(ValueError, match='全范围'):
        merge_xianyuan_job_scope(old, {'outcome': 'complete',
            'plan': {'item_count': 2, 'items': [{'base_id': 1}, {'base_id': 2}]},
            'action_result': {'remaining_items': rows}})


def test_verified_effect_is_durable_before_later_failure(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.data_annotation.tasks.xianyuan_gift_gui import record_verified_npc_gift_progress
    read, record = control.read_world_facts, control.record_world_discovery
    path = tmp_path / 'facts.json'
    record('xianyuan-auto-gift:scope', previous(), path)
    monkeypatch.setattr(control, 'read_world_facts', lambda: read(path))
    monkeypatch.setattr(control, 'record_world_discovery', lambda key, value: record(key, value, path))
    progress = {'actions': [{'action_id': 'new', 'status': 'verified'}], 'remaining_items': {1: 317}}
    with pytest.raises(RuntimeError, match='later navigation'):
        record_verified_npc_gift_progress(progress)
        raise RuntimeError('later navigation')
    record_verified_npc_gift_progress(progress)
    stored = read(path)['discoveries']['xianyuan-auto-gift:scope']
    assert stored['outcome'] == 'partial'
    assert stored['remaining_items'] == [{'base_id': 1, 'quantity': 317}, {'base_id': 2, 'quantity': 60}]
    assert stored['action_ids'] == ['original', 'new']


def test_latest_sealed_reward_does_not_reopen_older_receipts():
    from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import unclosed_xianyuan_reward_actions
    actions = [{'action_id': 'old', 'status': 'verified', 'batch': {'npc_id': 1}},
               {'action_id': 'new', 'status': 'verified', 'batch': {'npc_id': 1}},
               {'action_id': 'pending', 'status': 'pending', 'batch': {'npc_id': 2}}]
    result = unclosed_xianyuan_reward_actions(actions, {'xianyuan-rewards:new': {}})
    assert [row['action_id'] for row in result] == ['pending']
