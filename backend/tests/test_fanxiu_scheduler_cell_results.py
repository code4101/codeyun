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


@pytest.mark.parametrize('lose_cas,missing_payload,fault', [(False,False,'none'),
    (True,False,'none'),(False,True,'none'),(False,False,'journal'),(False,False,'world_fact')])
@pytest.mark.parametrize('task_id,outcome', [('ranking-lifecycle','partial'),
    ('storage-bag-operation','partial'),('storage-bag-operation','complete')])
def test_detached_terminal_recovery_requires_real_cas_before_native_receipt(tmp_path, monkeypatch, lose_cas, missing_payload, fault, task_id, outcome):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.data_annotation.state import read_data_annotation_json
    scheduler, settings, world = (tmp_path / name for name in ('scheduler.json','settings.json','world.json'))
    attempt = 'f' * 32
    business = {'ok':True,'outcome':outcome,'remaining_current_items':[42] if outcome=='partial' else []}
    from datetime import datetime
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_operation import storage_bag_operation_follow_up
    business_next = (storage_bag_operation_follow_up(business,now=datetime(2026,10,11,1,0))['next_time']
                     if task_id=='storage-bag-operation' else '2099-01-01 00:00:00')
    task = {'id':task_id,'task_type':task_id.replace('-','_'),'last_result':'running',
            'attempt_id':attempt,'attempt_kernel_generation':1,'started_at':'2026-10-11 01:00:00',
            'last_run_at':'2026-10-11 01:00:00','next_time':business_next}
    control.write_scheduler_tasks([task],scheduler_state_path=scheduler)
    control.write_scheduler_settings({'job_group_enabled':True},scheduler_settings_path=settings)
    payload = {'result':'success','business_result':business}
    context = {'scheduler_task_id':task['id'],'scheduler_attempt_id':attempt,
               'scheduler_terminal_result':'success','scheduler_terminal_at':1791655841.,
               'scheduler_terminal_payload':None if missing_payload else payload}
    monkeypatch.setattr('backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status',
                        lambda: {'alive':True,'execution_state':'idle','generation':1})
    monkeypatch.setattr(control,'behavior_tree_executor_status',lambda: context)
    monkeypatch.setattr(control,'read_kernel_scheduler_status',lambda: {})
    if fault == 'journal':
        (tmp_path / 'terminal-cell-results').write_text('preserved existing file',encoding='utf-8')
    if fault == 'world_fact':
        def unavailable_fact(*args,**kwargs):
            raise OSError('isolated secondary projection failure')
        monkeypatch.setattr(control,'record_scheduler_task_fact',unavailable_fact)
    real_write = control.write_scheduler_tasks
    def write(rows, **kwargs):
        if lose_cas:
            other = {**task,'attempt_id':'a'*32}
            real_write([other],scheduler_state_path=scheduler,execution_update_ids={task['id']})
        return real_write(rows,**kwargs)
    monkeypatch.setattr(control,'write_scheduler_tasks',write)
    reports = []
    def report(task_id, attempt_id, **kwargs):
        receipt = read_scheduler_cell_result(task_id,attempt_id,directory=kwargs['directory'])
        assert receipt['found']
        reports.append(receipt)
        return {'status':'disabled'}
    monkeypatch.setattr('backend.core.fanxiu.activity.ranking_completion_reporting.publish_committed_ranking_attempt',report)
    control.reconcile_stale_scheduler_attempts([dict(task)],scheduler_state_path=scheduler,
        scheduler_settings_path=settings,world_facts_path=world)
    receipt = ({'found':False} if fault=='journal' else
               read_scheduler_cell_result(task['id'],attempt,directory=tmp_path/'terminal-cell-results'))
    assert receipt['found'] is (not lose_cas and not missing_payload and fault!='journal')
    assert len(reports) == int(not lose_cas and not missing_payload and fault!='journal' and task_id=='ranking-lifecycle')
    if receipt['found']:
        assert receipt['business_result'] == payload['business_result']
        assert receipt['cell_result'] == payload
    persisted = next(row for row in control.read_scheduler_tasks(scheduler_state_path=scheduler)
                     if row['id'] == task['id'])
    if lose_cas:
        assert persisted['attempt_id'] == 'a'*32
    else:
        assert persisted['last_terminal_attempt']['attempt_id'] == attempt
        assert persisted['next_time'] == task['next_time']
        if task_id=='storage-bag-operation':
            assert persisted['next_time'] == ('2026-10-11 01:01:00' if outcome=='partial'
                                              else '2026-10-12 00:00:00')
    if missing_payload:
        facts=read_data_annotation_json(world,{})
        assert 'native_terminal_payload_missing' in str(facts)
    if fault in {'journal','world_fact'}:
        facts=read_data_annotation_json(world,{})
        diagnostic=facts['discoveries']['scheduler-terminal-recovery:'+attempt]
        assert diagnostic['terminal_receipt_error' if fault=='journal' else 'terminal_world_fact_error']
        assert persisted['last_result']=='success' and persisted['attempt_id'] is None


@pytest.mark.parametrize('lose_terminal_ownership', [False, True])
@pytest.mark.parametrize('task_id', ['legacy-daily-dongtian-clear', 'ranking-lifecycle'])
def test_terminal_cas_receipt_is_independent_of_real_world_fact_failure(tmp_path, monkeypatch, lose_terminal_ownership, task_id):
    """Use real JSON/CAS/receipt writers; only the Cell transport is replaced."""
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    from backend.core.fanxiu.data_annotation.state import read_data_annotation_json, write_data_annotation_json

    scheduler = tmp_path / 'scheduler.json'
    settings = tmp_path / 'settings.json'
    world = tmp_path / 'world.json'
    execution = tmp_path / 'execution.json'
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
    report_calls = []
    def report(*args, **kwargs):
        # Real journal must exist before reporting; a reporting fault is separate
        # from both the committed game terminal and the secondary world failure.
        report_calls.append(read_scheduler_cell_result(*args, directory=kwargs['directory']))
        raise OSError('isolated reporting projection failure')
    monkeypatch.setattr('backend.core.fanxiu.activity.ranking_completion_reporting.publish_committed_ranking_attempt', report)
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
        assert report_calls == []
        assert returned['phase'] == 'scheduler_attempt_ownership_lost'
        assert receipt['found'] is False
        assert terminal_facts == []
        assert durable['attempt_id'] == 'f'*32
        assert durable.get('last_terminal_attempt') is None
    else:
        if task_id == 'ranking-lifecycle':
            assert len(report_calls) == 1 and report_calls[0]['found']
            assert returned['terminal_reporting_error']['error_type'] == 'OSError'
            from backend.core.fanxiu.activity.ranking_completion_reporting import read_ranking_reporting_obligation
            obligation = read_ranking_reporting_obligation(task_id, attempt,
                configuration_path=tmp_path / 'ranking-completion-reporting.json')
            assert obligation['status'] == 'error' and obligation['attempt_id'] == attempt
        else:
            assert report_calls == []
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
