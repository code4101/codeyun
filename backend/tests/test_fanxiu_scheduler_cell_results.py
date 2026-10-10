"""Real isolated persistence and exact-attempt identity; no simulated game."""
import pytest
from backend.core.fanxiu.data_annotation.kernel_scheduler_results import (
    record_scheduler_cell_result, read_scheduler_cell_result,
)


def test_exact_attempt_retains_native_cell_and_distinct_business_result(tmp_path):
    for attempt, remaining in (("a" * 32, 2), ("b" * 32, 0)):
        record_scheduler_cell_result(
            "bag", attempt, cell_result={"msg_id": attempt + "_0", "status": "success"},
            business_result={"outcome": "partial" if remaining else "complete",
                             "remaining_current_items": remaining},
            started_at="start", finished_at="finish", scheduler_result="success",
            directory=tmp_path)
    first = read_scheduler_cell_result("bag", "a" * 32, directory=tmp_path)
    assert first['business_result']['remaining_current_items'] == 2
    assert first['cell_result']['msg_id'] == "a" * 32 + "_0"
    assert read_scheduler_cell_result("bag", "b" * 32, directory=tmp_path)['business_result']['outcome'] == 'complete'
    with pytest.raises(ValueError, match="身份"):
        read_scheduler_cell_result("other", "a" * 32, directory=tmp_path)


def test_missing_receipt_does_not_create_directory_or_use_newer_attempt(tmp_path):
    absent = tmp_path / "absent"
    value = read_scheduler_cell_result("bag", "c" * 32, directory=absent)
    assert value['found'] is False
    assert not absent.exists()
    with pytest.raises(ValueError):
        read_scheduler_cell_result("bag", "../escape", directory=absent)


@pytest.mark.parametrize('field,value', [('scheduler_result','error'), ('started_at','other'), ('finished_at','later')])
def test_terminal_status_and_time_conflicts_do_not_replace_receipt(tmp_path, field, value):
    arguments = dict(cell_result={'status':'success','msg_id':'native-cell'},
                     business_result={'outcome':'partial'}, started_at='start',
                     finished_at='finish', scheduler_result='success', directory=tmp_path)
    record_scheduler_cell_result('bag', 'd'*32, **arguments)
    original = (tmp_path / ('d'*32 + '.json')).read_bytes()
    assert record_scheduler_cell_result('bag', 'd'*32, **arguments)['scheduler_result'] == 'success'
    with pytest.raises(ValueError, match='冲突'):
        record_scheduler_cell_result('bag', 'd'*32, **{**arguments, field:value})
    assert (tmp_path / ('d'*32 + '.json')).read_bytes() == original


def test_real_storage_failure_preserves_committed_native_outcome(tmp_path):
    from backend.core.fanxiu.data_annotation.kernel_scheduler_results import preserve_scheduler_cell_result
    invalid_directory = tmp_path / 'file'
    invalid_directory.write_text('existing file', encoding='utf-8')
    native = {'status':'success','msg_id':'original-cell','result_text':"{'outcome':'partial'}"}
    returned = preserve_scheduler_cell_result(
        'bag', 'e'*32, cell_result=native, business_result={'outcome':'partial'},
        started_at='start', finished_at='finish', scheduler_result='success',
        directory=invalid_directory)
    assert returned['status'] == native['status'] == 'success'
    assert returned['msg_id'] == 'original-cell'
    assert returned['result_text'] == native['result_text']
    assert returned['terminal_receipt_error']
    assert 'terminal_receipt_error' not in native
    assert invalid_directory.read_text(encoding='utf-8') == 'existing file'


@pytest.mark.parametrize('lose_terminal_ownership', [False, True])
def test_terminal_cas_receipt_is_independent_of_real_world_fact_failure(tmp_path, monkeypatch, lose_terminal_ownership):
    """Use real JSON/CAS/receipt writers; only the Cell transport is replaced."""
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.data_annotation.state import read_data_annotation_json, write_data_annotation_json

    scheduler = tmp_path / 'scheduler.json'
    settings = tmp_path / 'settings.json'
    world = tmp_path / 'world.json'
    execution = tmp_path / 'execution.json'
    task_id = 'legacy-daily-dongtian-clear'
    task = {'id':task_id, 'task_type':'daily_dongtian_clear', 'next_time':'2020-01-01 21:00:00'}
    control.write_scheduler_tasks([task], scheduler_state_path=scheduler)
    control.write_scheduler_settings({'job_group_enabled':True}, scheduler_settings_path=settings)
    monkeypatch.setattr('backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status', lambda: {'generation':1})
    monkeypatch.setattr(control, '_higher_level_due_task_for_attempt', lambda *a, **kw: None)
    submitted = []
    native = {'status':'success', 'msg_id':'native-test-transport', 'result_text':"{'outcome': 'complete'}"}
    def submit(**kwargs):
        submitted.append(kwargs)
        # Model only the Job's durable trigger write at the transport boundary;
        # no Runtime, scenes or game effects are simulated.
        rows = read_data_annotation_json(scheduler, [])
        next(r for r in rows if r['id'] == task_id)['next_time'] = '2099-01-01 21:00:00'
        write_data_annotation_json(scheduler, rows)
        return dict(native)
    monkeypatch.setattr(control, 'submit_task_cell', submit)
    real_write = control.write_scheduler_tasks
    writes = []
    def write(tasks, **kwargs):
        writes.append(kwargs)
        if lose_terminal_ownership and len(writes) == 2:
            rows = read_data_annotation_json(scheduler, [])
            row = next(r for r in rows if r['id'] == task_id)
            row['attempt_id'] = 'f'*32
            write_data_annotation_json(scheduler, rows)
        return real_write(tasks, **kwargs)
    monkeypatch.setattr(control, 'write_scheduler_tasks', write)
    real_fact = control.record_scheduler_task_fact
    terminal_facts = []
    def fact(row, result, **kwargs):
        if result != 'running':
            terminal_facts.append(result)
            # A directory at the destination is a real atomic replace failure,
            # while the initial claim's world projection was stored normally.
            world.unlink()
            world.mkdir()
        return real_fact(row, result, **kwargs)
    monkeypatch.setattr(control, 'record_scheduler_task_fact', fact)
    returned = control._run_scheduler_task_cell_and_record_terminal_owned(
        entry=object(), entry_id='isolated-persistence-test', task=task,
        scheduler_state_path=scheduler, scheduler_settings_path=settings,
        execution_state_path=execution, world_facts_path=world,
    )
    assert len(submitted) == 1
    attempt = writes[-1]['expected_execution_attempt_ids'][task_id]
    receipt = read_scheduler_cell_result(task_id, attempt, directory=tmp_path / 'terminal-cell-results')
    durable = next(r for r in read_data_annotation_json(scheduler, []) if r['id'] == task_id)
    if lose_terminal_ownership:
        assert returned['phase'] == 'scheduler_attempt_ownership_lost'
        assert receipt['found'] is False
        assert terminal_facts == []
        assert durable['attempt_id'] == 'f'*32
        assert durable.get('last_terminal_attempt') is None
    else:
        assert returned['status'] == durable['last_result'] == 'success'
        assert durable['next_time'] == '2099-01-01 21:00:00'
        assert durable['attempt_id'] is None
        assert durable['last_terminal_attempt']['attempt_id'] == attempt
        assert returned['terminal_world_fact_error']['error_type']
        assert returned['terminal_projection_error']['error_type']
        assert receipt['found'] is True
        assert receipt['cell_result'] == native
        assert terminal_facts == ['success']
        assert read_data_annotation_json(execution, {})['status'] == 'success'
        again = control._run_scheduler_task_cell_and_record_terminal_owned(
            entry=object(), entry_id='isolated-persistence-test', task=task,
            scheduler_state_path=scheduler, scheduler_settings_path=settings,
            execution_state_path=execution, world_facts_path=world,
            scheduled_attempt=True,
        )
        assert again['phase'] == 'scheduler_task_no_longer_due'
        assert len(submitted) == 1


def test_task_terminal_reference_survives_maintenance_and_distinguishes_live_attempt(tmp_path):
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import write_scheduler_tasks
    from backend.core.fanxiu.data_annotation.kernel_scheduler_results import read_scheduler_task_terminal_result
    scheduler = tmp_path / 'scheduler.json'
    task_id = 'storage-bag-operation'
    attempt = 'a' * 32
    reference = dict(attempt_id=attempt, started_at='2026-10-11 00:15:50',
                     finished_at='2026-10-11 00:15:53', scheduler_result='success')
    task = dict(id=task_id, task_type='storage_bag_operation', last_result='success',
                last_run_at=reference['started_at'], finished_at=reference['finished_at'],
                last_terminal_attempt=reference)
    write_scheduler_tasks([task], scheduler_state_path=scheduler)
    missing = read_scheduler_task_terminal_result(task_id, scheduler_state_path=scheduler)
    assert missing['reason'] == 'terminal_receipt_missing'
    assert not (tmp_path / 'terminal-cell-results').exists()
    record_scheduler_cell_result(task_id, attempt, cell_result={'status':'success'},
        business_result={'outcome':'partial'}, started_at=reference['started_at'],
        finished_at=reference['finished_at'], scheduler_result='success',
        directory=tmp_path / 'terminal-cell-results')
    # A configuration writer with stale data cannot erase the committed reference.
    write_scheduler_tasks([dict(id=task_id, task_type='storage_bag_operation')],
                         scheduler_state_path=scheduler, execution_update_ids=set())
    task.update(attempt_id='b'*32, last_result='running')
    write_scheduler_tasks([task], scheduler_state_path=scheduler, execution_update_ids={task_id})
    result = read_scheduler_task_terminal_result(task_id, scheduler_state_path=scheduler)
    assert result['found'] is True and result['business_result']['outcome'] == 'partial'
    assert result['attempt_id'] == attempt
    assert result['current_attempt_id'] == 'b'*32
    task.update(attempt_id=None, last_result='error', finished_at='2026-10-11 00:20:00')
    write_scheduler_tasks([task], scheduler_state_path=scheduler, execution_update_ids={task_id})
    stale = read_scheduler_task_terminal_result(task_id, scheduler_state_path=scheduler)
    assert stale['found'] is False and stale['reason'] == 'terminal_reference_stale'


def test_old_task_without_terminal_reference_never_guesses_receipt(tmp_path):
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import write_scheduler_tasks
    from backend.core.fanxiu.data_annotation.kernel_scheduler_results import read_scheduler_task_terminal_result
    scheduler = tmp_path / 'scheduler.json'
    write_scheduler_tasks([dict(id='storage-bag-operation', task_type='storage_bag_operation',
                               last_result='success')], scheduler_state_path=scheduler)
    result = read_scheduler_task_terminal_result('storage-bag-operation', scheduler_state_path=scheduler)
    assert result['found'] is False and result['reason'] == 'terminal_reference_missing'
