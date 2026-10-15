"""Non-operation state/ownership contracts and real TEMP durable publication."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from backend.core.codex import research_supervision as supervision


def test_native_error_projection_preserves_http_variant_and_first_details():
    error = {'message': 'remote compact stream disconnected',
             'codexErrorInfo': {'responseStreamDisconnected': {'httpStatusCode': 503}},
             'additionalDetails': 'response body decode failure', 'unrelated': 'discard'}
    assert supervision.project_codex_turn_error(error) == {
        key: error[key] for key in ('message', 'codexErrorInfo', 'additionalDetails')}
    assert len(supervision.project_codex_turn_error({'message': 'x' * 2000})['message']) == 1200


@pytest.mark.parametrize('enabled', [False, True])
def test_recovery_poll_requires_separate_explicit_policy(sources, delivery_store, tmp_path, monkeypatch, enabled):
    from backend.core.codex import research_recovery
    from backend.core.codex import research_recovery_worker
    calls = []
    supervision.register_codex_research('research', store=delivery_store, notify=False)
    research_recovery.configure_codex_research_recovery('research', store=delivery_store,
        owner_authorization='owner exact instruction', prompt='original scoped work',
        enabled=enabled, dry_run=True)
    def decide(thread_id, **kwargs):
        calls.append((thread_id, kwargs))
        return {'status': 'would_recover', 'recovery_performed': False}
    monkeypatch.setattr(research_recovery_worker, 'dispatch_codex_research_recovery', decide)
    health = supervision.inspect_registered_codex_research_once(
        store=delivery_store, state_dir=tmp_path / 'patrol')
    assert len(calls) == int(enabled)
    if enabled:
        assert calls[0][0] == 'research' and calls[0][1]['dry_run'] is True
        assert calls[0][1]['owner_authorization'] == 'owner exact instruction'
        assert health['results'][0]['recovery']['status'] == 'would_recover'
    else:
        assert health['results'][0]['recovery'] is None
    assert not health['recovery_performed']


def test_current_failure_requires_exact_live_native_head_and_inactive_writer(sources):
    data, _ = sources
    failure = data['history']['data'][0]
    data['desktop']['thread']['status']['type'] = 'idle'
    data['desktop']['turns'] = [{**failure, 'items': []}]
    result = supervision.read_codex_research_snapshot('research')
    assert result['failure_timeline']['state'] == 'current_failure'
    assert result['current_terminal_error']['id'] == 'failure'
    data['desktop']['thread']['status']['type'] = 'active'
    result = supervision.read_codex_research_snapshot('research')
    assert result['current_terminal_error'] is None
    assert result['failure_timeline']['state'] == 'active_or_unconfirmed'


def test_later_success_supersedes_error_without_goal_completion_or_lost_first_cause(sources):
    data, _ = sources
    success = {'id': 'new-success', 'status': 'completed', 'items': []}
    data['desktop']['thread']['status']['type'] = 'idle'
    data['desktop']['turns'] = [success]
    data['history']['data'].insert(0, success)
    result = supervision.read_codex_research_snapshot('research')
    assert result['current_terminal_error'] is None
    timeline = result['failure_timeline']
    assert timeline['state'] == 'latest_success_after_failure'
    assert timeline['historical_failures'][0]['relation'] == 'superseded_by_latest_success'
    assert timeline['historical_failures'][0]['failure']['error']['message'] == 'compact network failure'
    assert timeline['latest_success_after_failure']['id'] == 'new-success'
    assert result['goal']['value']['status'] == 'blocked'
    assert not timeline['business_goal_completed'] and not timeline['writer_gate_released']


@pytest.mark.parametrize('mismatch', ['head', 'unavailable'])
def test_unconfirmed_sources_never_fabricate_a_successful_recovery(sources, mismatch):
    data, _ = sources
    data['desktop']['thread']['status']['type'] = 'idle'
    data['desktop']['turns'] = [{'id': 'new-success', 'status': 'completed', 'items': []}]
    if mismatch == 'unavailable': data['history'] = ConnectionError('source unavailable')
    result = supervision.read_codex_research_snapshot('research')
    assert result['current_terminal_error'] is None
    assert result['failure_timeline']['latest_success_after_failure'] is None
    assert not result['recovery_performed'] and not result['writer_gate_released']


def _current_failure_sources(data):
    data['desktop']['thread']['status']['type'] = 'idle'
    data['desktop']['turns'] = [{**data['history']['data'][0], 'items': []}]


def _later_success_sources(data, *, retain_failure=True):
    success = {'id': 'later-success', 'status': 'completed', 'startedAt': 1010, 'completedAt': 1020, 'items': []}
    data['desktop']['thread']['status']['type'] = 'idle'
    data['desktop']['turns'] = [success]
    data['history']['data'] = [success, *data['history']['data']] if retain_failure else [success]
    data['goal']['goal'].update(status='active', updatedAt=11)


@pytest.mark.parametrize('retain_failure', [True, False])
def test_old_unsent_issue_does_not_handoff_after_later_success(sources, delivery_store, tmp_path, monkeypatch, retain_failure):
    clock = [1000.0]
    monkeypatch.setattr(supervision.time, 'time', lambda: clock[0])
    data, _ = sources
    _current_failure_sources(data)
    first = supervision.inspect_codex_research('research', state_dir=tmp_path)
    assert delivery_store.pending_codex_notifications() == []
    _later_success_sources(data, retain_failure=retain_failure)
    clock[0] = 1030
    result = supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    assert result['admitted'] == [] and result['pending'] == 0 and len(result['suppressed']) == 2
    assert delivery_store.pending_codex_notifications() == []
    ledger = json.loads(Path(first['evidence_path']).read_text())
    assert any(issue['kind'] == 'terminal_failure' for issue in ledger['seen_issues'].values())
    assert not result['notification_sent']


def test_old_pending_notice_is_cancelled_after_failure_exits_bounded_history(sources, delivery_store, tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(supervision.time, 'time', lambda: clock[0])
    data, _ = sources
    _current_failure_sources(data)
    supervision.inspect_codex_research('research', state_dir=tmp_path)
    supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    notices = delivery_store.pending_codex_notifications()
    assert len(notices) == 2
    _later_success_sources(data, retain_failure=False)
    clock[0] = 1030
    supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    for notice in notices:
        assert delivery_store.codex_notification('research', notice['turn_id'])['status'] == 'suppressed'
        assert delivery_store.reserve_codex_notification(notice['id']) is None


def test_unknown_holds_pending_without_claiming_covered_then_revalidates_exact_current(sources, delivery_store, tmp_path):
    data, _ = sources
    _current_failure_sources(data)
    first = supervision.inspect_codex_research('research', state_dir=tmp_path)
    supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    ledger = json.loads(Path(first['evidence_path']).read_text())
    failure_id = next(fp for fp, item in ledger['seen_issues'].items() if item['kind'] == 'terminal_failure')
    notice = delivery_store.codex_notification('research', 'stage:research-issue:' + failure_id)
    data['desktop']['thread']['status']['type'] = 'active'
    data['desktop']['turns'] = [{'id': 'live-new', 'status': 'inProgress', 'items': []}]
    result = supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    assert result['held_count'] == 1 and not result['suppressed']
    assert delivery_store.codex_notification('research', notice['turn_id'])['status'] == 'suppressed'
    assert delivery_store.reserve_codex_notification(notice['id']) is None
    _current_failure_sources(data)
    supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    restored = delivery_store.codex_notification('research', notice['turn_id'])
    assert restored['id'] == notice['id'] and restored['status'] == 'pending'


def test_sent_receipt_and_archive_are_immutable_when_error_becomes_historical(sources, delivery_store, tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(supervision.time, 'time', lambda: clock[0])
    data, _ = sources
    _current_failure_sources(data)
    supervision.inspect_codex_research('research', state_dir=tmp_path)
    supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    notice = delivery_store.pending_codex_notifications()[0]
    reply_id = delivery_store.reserve_codex_notification(notice['id'])
    delivery_store.finish_reply(reply_id, 'sent', {'result': 1})
    receipt = delivery_store.codex_notification('research', notice['turn_id'])
    marker = 'codex_result_verification:' + str(notice['id'])
    archive = {'status': 'verified', 'local_id': 1, 'server_id': 'test-formal-echo'}
    delivery_store.set_meta(marker, archive)
    _later_success_sources(data, retain_failure=False); clock[0] = 1030
    supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path)
    assert delivery_store.codex_notification('research', notice['turn_id']) == receipt
    assert delivery_store.get_meta(marker) == archive


def test_held_rows_do_not_starve_later_eligible_and_covered_rows(sources, delivery_store, tmp_path):
    from pathlib import Path
    data, _ = sources
    result = supervision.inspect_codex_research('research', state_dir=tmp_path)
    path = Path(result['evidence_path'])
    ledger = json.loads(path.read_text())
    old = next(i for i in ledger['seen_issues'].values() if i['kind'] == 'terminal_failure')
    holds = {}
    for index in range(25):
        item = {**old, 'fingerprint': 'held-' + str(index), 'evidence': {**old['evidence'], 'id': 'held-' + str(index)}}
        holds[item['fingerprint']] = item
    ledger['seen_issues'] = {**holds, **ledger['seen_issues']}
    path.write_text(json.dumps(ledger))
    handed = supervision.handoff_codex_research_issues('research', store=delivery_store, state_dir=tmp_path, limit=20)
    assert len(handed['admitted']) == 1  # Goal blocked remains eligible after 25 holds.
    assert handed['held_count'] == 26


def test_hold_restore_then_cover_cannot_restore_again(delivery_store):
    delivery_store.enqueue_codex_completion('research', 'cycle', 'old notice')
    row = delivery_store.codex_notification('research', 'cycle')
    first = delivery_store.suppress_pending_codex_notification('research', 'cycle',
        category='unconfirmed', reason='first unknown observation')
    assert first['suppressed']
    assert delivery_store.restore_held_codex_notification('research', 'cycle', text='fresh current notice')
    assert delivery_store.codex_notification('research', 'cycle')['status'] == 'pending'
    delivery_store.suppress_pending_codex_notification('research', 'cycle',
        category='covered', reason='later native success confirmed')
    assert not delivery_store.restore_held_codex_notification('research', 'cycle', text='must not reopen')
    audit = delivery_store.get_meta('codex_notification_suppression:' + str(row['id']))
    assert audit['category'] == 'covered' and audit['reason'] == 'first unknown observation'
    assert audit['last_reason'] == 'later native success confirmed' and audit.get('readmitted_at')
    assert delivery_store.codex_notification('research', 'cycle')['status'] == 'suppressed'


def test_read_snapshot_keeps_details_without_granting_recovery(sources):
    data, _ = sources
    data['history']['data'][0]['error'] = {
        'message': 'remote compact failed', 'codexErrorInfo': {'httpConnectionFailed': {'httpStatusCode': 502}},
        'additionalDetails': 'first native detail'}
    result = supervision.read_codex_research_snapshot('research')
    assert result['latest_terminal_error']['error']['additionalDetails'] == 'first native detail'
    assert result['latest_terminal_error']['error']['codexErrorInfo']['httpConnectionFailed']['httpStatusCode'] == 502
    assert not result['takeover_allowed'] and not result['recovery_performed']


@pytest.fixture
def sources(monkeypatch):
    data={'desktop':{'thread':{'id':'research','status':{'type':'active'}},
        'turns':[{'id':'live','status':'inProgress','items':[]}]},
        'history':{'data':[{'id':'failure','status':'failed','error':{'message':'compact network failure'}}]},
        'goal':{'goal':{'threadId':'research','objective':'original objectives','status':'blocked',
                       'updatedAt':10,'tokensUsed':20,'timeUsedSeconds':30}}}
    calls=[]
    def read(name):
        def provider(*args,**kwargs):
            calls.append((name,args,kwargs))
            value=data[name]
            if isinstance(value,Exception):raise value
            return value
        return provider
    monkeypatch.setattr(supervision,'call_desktop_tool',read('desktop'))
    monkeypatch.setattr(supervision,'read_codex_thread_turns',read('history'))
    monkeypatch.setattr(supervision,'read_codex_thread_goal',read('goal'))
    return data,calls


def test_read_contract_separates_live_writer_child_history_goal_and_errors(sources):
    data,calls=sources
    data['history']['data'].insert(0,{'id':'live','status':'interrupted','error':None})
    snapshot=supervision.read_codex_research_snapshot('research')
    assert snapshot['desktop']['writer']=='active'
    assert snapshot['turn_history']['turns'][0]['status']=='interrupted'
    assert snapshot['goal']['value']['status']=='blocked'
    assert snapshot['blocked_with_running_writer']
    assert not snapshot['takeover_allowed'] and not snapshot['writer_gate_released']
    assert snapshot['latest_terminal_error']['id']=='failure'
    assert snapshot['network_root_cause']=='unknown' and not snapshot['source_snapshot_atomic']
    assert calls==[
        ('desktop',('read_thread',{'threadId':'research','hostId':'local','turnLimit':1,
            'includeOutputs':False,'maxOutputCharsPerItem':100}),{'timeout':10}),
        ('history',('research',),{'limit':5,'timeout_seconds':10}),
        ('goal',('research',),{'timeout_seconds':10})]


@pytest.mark.parametrize('desktop_state,turn_state,writer',[
    ('idle','completed','inactive_observed'),('idle','inProgress','active'),
    ('notLoaded','interrupted','unknown'),('systemError','failed','unknown')])
def test_writer_state_matrix_never_grants_control(sources,desktop_state,turn_state,writer):
    data,_=sources;data['desktop']['thread']['status']['type']=desktop_state
    data['desktop']['turns'][0]['status']=turn_state
    result=supervision.read_codex_research_snapshot('research')
    assert result['desktop']['writer']==writer
    assert not result['takeover_allowed'] and not result['writer_gate_released']


@pytest.mark.parametrize('source',['desktop','history','goal'])
def test_unreadable_source_stays_unknown_and_never_releases_writer(sources,source):
    data,calls=sources;data[source]=OSError('transport not available')
    result=supervision.read_codex_research_snapshot('research')
    assert len(calls)==3 and len(result['source_errors'])==1
    assert any(issue['kind']=='observation_unavailable' for issue in result['issues'])
    assert not result['writer_gate_released'] and not result['recovery_performed']
    if source=='desktop':assert result['desktop']['writer']=='unknown'


@pytest.mark.parametrize('bad',[
    {'thread_id':''},{'thread_id':'research','turn_limit':11},
    {'thread_id':'research','timeout_seconds':float('nan')},
    {'thread_id':'research','timeout_seconds':True}])
def test_invalid_input_never_calls_sources(sources,bad):
    _,calls=sources
    with pytest.raises(ValueError):supervision.read_codex_research_snapshot(**bad)
    assert calls==[]


def test_mismatched_goal_and_desktop_do_not_cross_research_identity(sources):
    data,_=sources
    data['desktop']['thread']['id']='other';data['goal']['goal']['threadId']='other'
    result=supervision.read_codex_research_snapshot('research')
    assert not result['desktop']['available'] and not result['goal']['available']
    assert result['desktop']['writer']=='unknown'
    assert {e['source'] for e in result['source_errors']}=={'desktop','goal'}


def test_real_temp_persistence_deduplicates_failure_and_keeps_new_turn_independent(sources,tmp_path):
    data,_=sources
    first=supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert {i['kind'] for i in first['new_issues']}=={'terminal_failure','goal_blocked'}
    second=supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert second['new_issues']==[]
    # Same native failure identity with richer error details is still one issue.
    data['history']['data'][0]['error']['message']='richer network detail'
    assert supervision.inspect_codex_research('research',state_dir=tmp_path)['new_issues']==[]
    data['history']['data'][0]['id']='new-failure'
    third=supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert [i['kind'] for i in third['new_issues']]==['terminal_failure']
    ledger=json.loads(__import__('pathlib').Path(third['evidence_path']).read_text())
    assert len(ledger['seen_issues'])==3
    assert not third['notification_sent'] and not third['recovery_performed']


def test_corrupt_ledger_is_not_reset_and_no_duplicate_alert_claim_is_returned(sources,tmp_path):
    from pathlib import Path
    first=supervision.inspect_codex_research('research',state_dir=tmp_path)
    path=Path(first['evidence_path']);path.write_text('{broken')
    with pytest.raises(json.JSONDecodeError):supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert path.read_text()=='{broken'


def test_older_concurrent_snapshot_cannot_overwrite_newer_observation(sources,tmp_path,monkeypatch):
    from pathlib import Path
    snapshot=supervision.read_codex_research_snapshot('research')
    newer={**snapshot,'captured_at':20,'observed_at':21}
    older={**snapshot,'captured_at':10,'observed_at':30}
    monkeypatch.setattr(supervision,'read_codex_research_snapshot',lambda *a,**kw:newer)
    first=supervision.inspect_codex_research('research',state_dir=tmp_path)
    monkeypatch.setattr(supervision,'read_codex_research_snapshot',lambda *a,**kw:older)
    second=supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert second['superseded'] and not second['new_issues']
    assert json.loads(Path(first['evidence_path']).read_text())['snapshot']['captured_at']==20


def test_two_real_processes_publish_one_new_failure(tmp_path):
    script='''import json,sys
from pathlib import Path
from backend.core.codex import research_supervision as s
s.call_desktop_tool=lambda *a,**kw:{'thread':{'id':'research','status':{'type':'idle'}},'turns':[{'id':'done','status':'completed'}]}
s.read_codex_thread_turns=lambda *a,**kw:{'data':[{'id':'failure','status':'failed','error':{'message':'compact'}}]}
s.read_codex_thread_goal=lambda *a,**kw:{'goal':{'threadId':'research','objective':'same','status':'active'}}
r=s.inspect_codex_research('research',state_dir=Path(sys.argv[1]))
print(json.dumps({'new_count':len(r['new_issues'])}))
'''
    processes=[subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    counts=[]
    for process in processes:
        out,err=process.communicate(timeout=15)
        assert process.returncode==0,err.decode()
        counts.append(json.loads(out.decode().splitlines()[-1])['new_count'])
    assert sorted(counts)==[0,1]

def test_all_failed_turns_in_bounded_page_have_independent_fingerprints(sources,tmp_path):
    data,_=sources
    data['history']['data'].append({'id':'second-failure','status':'failed','error':{'message':'same compact cause'}})
    first=supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert {i['evidence']['id'] for i in first['new_issues'] if i['kind']=='terminal_failure'}=={'failure','second-failure'}
    assert first['snapshot']['latest_terminal_error']['id']=='failure'
    assert supervision.inspect_codex_research('research',state_dir=tmp_path)['new_issues']==[]


@pytest.mark.parametrize('source,bad',[
    ('desktop',{'thread':{'id':'research','status':{'type':'idle'}},'turns':[{'id':'','status':'completed'}]}),
    ('desktop',{'thread':{'id':'research','status':{'type':'idle'}},'turns':[{'id':'t','status':'completed','items':'bad'}]}),
    ('history',{'data':[{'id':'','status':'failed'}]}),
    ('goal',{'goal':{'threadId':'research','objective':{},'status':'active'}}),
])
def test_bad_source_schema_is_explicit_unknown(sources,source,bad):
    data,_=sources;data[source]=bad
    result=supervision.read_codex_research_snapshot('research')
    assert result['source_errors'] and not result['writer_gate_released']
    if source=='desktop':assert result['desktop']['writer']=='unknown' and not result['desktop']['available']


def test_real_threads_reverse_completion_after_recovery_defers_unseen_old_failure(sources,tmp_path,monkeypatch):
    import threading
    from pathlib import Path
    data,_=sources
    real_read=supervision.read_codex_research_snapshot
    staged=threading.Event();release=threading.Event();old_results=[];errors=[]
    def reader(*args,**kwargs):
        result=real_read(*args,**kwargs)
        if threading.current_thread().name=='late-old-observation':
            staged.set()
            assert release.wait(3)
        return result
    monkeypatch.setattr(supervision,'read_codex_research_snapshot',reader)
    def old():
        try:old_results.append(supervision.inspect_codex_research('research',state_dir=tmp_path))
        except Exception as exc:errors.append(exc)
    worker=threading.Thread(target=old,name='late-old-observation');worker.start()
    try:
        assert staged.wait(3)
        data['goal']['goal']['status']='active';data['goal']['goal']['updatedAt']=11
        data['history']['data']=[]
        recovered=supervision.inspect_codex_research('research',state_dir=tmp_path)
        assert not recovered['new_issues']
    finally:
        release.set();worker.join(timeout=3)
    assert not worker.is_alive() and not errors
    late=old_results[0]
    assert late['superseded'] and late['new_issues']==[]
    assert len(late['deferred_failures'])==1 and late['fresh_observation_required']
    ledger=json.loads(Path(late['evidence_path']).read_text())
    assert ledger['snapshot']['goal']['value']['status']=='active'
    assert ledger['seen_issues']=={}  # Late old failure was never consumed.
    assert len(ledger['deferred_failures'])==1
    # A normal fresh poll with no old failure keeps evidence unresolved, not lost.
    again=supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert again['new_issues']==[] and len(again['deferred_failures'])==1
    # A fresh bounded read corroborates the historical failure, admitting it once.
    data['history']['data']=[{'id':'failure','status':'failed','error':{'message':'compact network failure'}}]
    corroborated=supervision.inspect_codex_research('research',state_dir=tmp_path)
    assert [i['kind'] for i in corroborated['new_issues']]==['terminal_failure']
    assert corroborated['deferred_failures']==[]
    assert supervision.inspect_codex_research('research',state_dir=tmp_path)['new_issues']==[]

@pytest.fixture
def delivery_store(tmp_path):
    from backend.core.messaging.wechat_agent_store import AgentStore
    store=AgentStore(tmp_path/'owned-events.sqlite')
    store.ensure_hook('destination','robot','owned-test@chatroom')
    store.subscribe_codex_thread('research','destination',[])
    return store


def test_registered_periodic_sweep_recovers_diagnosis_before_enqueue_crash(sources,delivery_store,tmp_path,monkeypatch):
    clock=[10000.]
    monkeypatch.setattr(supervision.time,'time',lambda:clock[0])
    state=tmp_path/'issues'
    supervision.inspect_codex_research('research',state_dir=state)  # producer dies before handoff
    assert delivery_store.pending_codex_notifications()==[]
    entry=supervision.register_codex_research('research',store=delivery_store)
    assert entry['thread_id']=='research'
    first=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=state)
    assert first['error'] is None and len(delivery_store.pending_codex_notifications())==1
    assert first['results'][0]['handoff']['held_count'] == 1  # Active live work cannot admit old failure.
    assert all(row['status']=='pending' for row in delivery_store.codex_notifications_status()['notifications'])
    calls_before=len(sources[1])
    skipped=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=state)
    assert skipped['results']==[] and len(sources[1])==calls_before
    clock[0]+=61
    again=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=state)
    assert len(again['results'])==1 and len(delivery_store.pending_codex_notifications())==1
    assert again['results'][0]['handoff']['admitted']==[]
    assert not again['notification_sent'] and not again['recovery_performed']


def test_crash_after_enqueue_before_handoff_receipt_reconciles_exact_outbox_identity(sources,delivery_store,tmp_path,monkeypatch):
    data = sources[0]
    data['desktop']['thread']['status']['type'] = 'idle'
    data['desktop']['turns'] = [{**data['history']['data'][0], 'items': []}]
    state=tmp_path/'issues'
    supervision.inspect_codex_research('research',state_dir=state)
    write=supervision._write_document
    def fail(*args):raise OSError('crash after enqueue')
    monkeypatch.setattr(supervision,'_write_document',fail)
    with pytest.raises(OSError):supervision.handoff_codex_research_issues('research',store=delivery_store,state_dir=state)
    original=delivery_store.pending_codex_notifications()
    assert len(original)==2
    # The status page no longer contains the original IDs, but exact public
    # lookup still proves their admission. Neither is resent or re-enqueued.
    for index in range(30):delivery_store.enqueue_codex_completion('research','extra-'+str(index),'extra')
    monkeypatch.setattr(supervision,'_write_document',write)
    from backend.core.messaging.wechat_agent_store import AgentStore
    reopened=AgentStore(delivery_store.path)
    result=supervision.handoff_codex_research_issues('research',store=reopened,state_dir=state)
    assert len(result['admitted'])==2 and result['pending']==0
    assert {row['notification_id'] for row in result['admitted']}=={row['id'] for row in original}
    assert all(reopened.codex_notification('research','extra-'+str(index)) is not None for index in range(30))
    assert all(reopened.codex_notification('research',row['turn_id'])['id']==row['id'] for row in original)
    assert supervision.handoff_codex_research_issues('research',store=reopened,state_dir=state)['admitted']==[]
    supervision.inspect_codex_research('research',state_dir=state)
    assert supervision.handoff_codex_research_issues('research',store=reopened,state_dir=state)['admitted']==[]


def test_disabled_subscription_does_not_claim_outbox_admission(sources,delivery_store,tmp_path):
    data = sources[0]
    data['desktop']['thread']['status']['type'] = 'idle'
    data['desktop']['turns'] = [{**data['history']['data'][0], 'items': []}]
    state=tmp_path/'issues'
    supervision.inspect_codex_research('research',state_dir=state)
    delivery_store.subscribe_codex_thread('research','destination',[],enabled=False)
    with pytest.raises(ValueError,match='enabled'):
        supervision.register_codex_research('research',store=delivery_store)
    result=supervision.handoff_codex_research_issues('research',store=delivery_store,state_dir=state)
    assert result['admitted']==[] and result['pending']==2
    assert result['reason']=='outbox_not_admitted'
    delivery_store.subscribe_codex_thread('research','destination',[],enabled=True)
    assert len(supervision.handoff_codex_research_issues('research',store=delivery_store,state_dir=state)['admitted'])==2


def test_periodic_source_failure_backoff_and_health_survive_skipped_sweep(sources,delivery_store,tmp_path,monkeypatch):
    clock=[10000.];monkeypatch.setattr(supervision.time,'time',lambda:clock[0])
    supervision.register_codex_research('research',store=delivery_store,notify=False)
    sources[0]['desktop']=OSError('desktop disconnected')
    result=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=tmp_path/'issues')
    assert result['error'] and result['results'][0]['source_errors'][0]['source']=='desktop'
    assert result['registrations'][0]['next_check_at']==10120.
    skipped=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=tmp_path/'issues')
    assert skipped['results']==[] and skipped['error'] and skipped['failures']
    assert delivery_store.get_meta('codex_research_supervision_worker')['error']


def test_resident_research_loop_is_separate_from_runtime_and_ownership(monkeypatch):
    import threading
    from backend.core.fanxiu.data_annotation import inspection_service
    runtime=threading.Event();research=threading.Event();order=[]
    monkeypatch.setattr(inspection_service,'_inspect_runtime_loop',lambda stop:runtime.set())
    monkeypatch.setattr(inspection_service,'supervise_agent_research',lambda stop:research.set())
    def ownership(stop):
        assert runtime.wait(2) and research.wait(2)
        order.append('ownership')
    monkeypatch.setattr(inspection_service,'supervise_scheduler_ownership',ownership)
    inspection_service.run_inspection_service(threading.Event())
    assert order==['ownership']


def test_resident_research_loop_uses_stoppable_wait_and_exception_backoff(monkeypatch):
    from backend.core.fanxiu.data_annotation import inspection_service
    from backend.core.fanxiu.data_annotation import ai_assistance
    calls=[]
    monkeypatch.setattr(ai_assistance,'read_ai_assistance_status',lambda:{'dispatch':{'provider':'claude'}})
    def fail(**kwargs):
        calls.append('cc')
        raise OSError('public observer unavailable')
    monkeypatch.setattr(ai_assistance,'refresh_ai_assistance_status',fail)
    class Stop:
        def __init__(self):self.waits=[]
        def wait(self,seconds):
            self.waits.append(seconds)
            return len(self.waits)==3
    stop=Stop();inspection_service.supervise_agent_research(stop)
    assert stop.waits==[15,30,60]
    assert calls==['cc','cc']

def test_more_than_four_registrations_remain_fair_when_reads_exceed_interval(sources,delivery_store,tmp_path,monkeypatch):
    clock=[10000.];monkeypatch.setattr(supervision.time,'time',lambda:clock[0])
    data,_=sources;observed=[]
    real=supervision.read_codex_research_snapshot
    def slow(thread_id,**kwargs):
        data['desktop']['thread']['id']=thread_id;data['goal']['goal']['threadId']=thread_id
        result=real(thread_id,**kwargs)
        observed.append(thread_id);clock[0]+=90  # longer than the 60-second interval
        return result
    monkeypatch.setattr(supervision,'read_codex_research_snapshot',slow)
    ids=['original-'+str(index) for index in range(8)]
    for thread in ids:supervision.register_codex_research(thread,store=delivery_store,notify=False)
    for _ in range(2):
        result=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=tmp_path/'issues')
        assert len(result['results'])==4
    assert len(observed)==8 and set(observed)==set(ids)
    supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=tmp_path/'issues')
    assert observed[8:]==ids[:4]  # earliest next_check_at, not last inserted batch


def test_native_reads_hold_no_registry_lock_and_inflight_claim_prevents_duplicate_sweep(sources,delivery_store,tmp_path,monkeypatch):
    from contextlib import contextmanager
    depth=[0];real_lock=supervision._registry_lock;real_read=supervision.read_codex_research_snapshot
    @contextmanager
    def lock(store):
        with real_lock(store):
            depth[0]+=1
            try:yield
            finally:depth[0]-=1
    monkeypatch.setattr(supervision,'_registry_lock',lock)
    def read(*args,**kwargs):
        assert depth[0]==0
        another=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=tmp_path/'issues')
        assert another['results']==[]
        return real_read(*args,**kwargs)
    monkeypatch.setattr(supervision,'read_codex_research_snapshot',read)
    supervision.register_codex_research('research',store=delivery_store,notify=False)
    result=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=tmp_path/'issues')
    assert len(result['results'])==1


def test_registered_stage_outbox_uses_original_engine_delivery_and_archive_verification(sources,delivery_store,tmp_path,monkeypatch):
    import threading,time
    from types import SimpleNamespace
    from backend.core.messaging import codex_results
    supervision.register_codex_research('research',store=delivery_store)
    result=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=tmp_path/'issues')
    assert result['results'][0]['handoff']['pending']==0
    assert not result['notification_sent']
    codex_results.configure_codex_reporting('research',mode='stages_only',store=delivery_store)
    monkeypatch.setattr(codex_results,'read_codex_completed_turns',lambda *a:pytest.fail('stage must not discover native history'))
    echoes=[]
    def sender(chat,text,*,sender_account_id):
        echoes.append({'local_id':len(echoes)+1,'message_text':text,
            'sender_username':sender_account_id,'create_time':time.time()})
        return {'result':1}
    hook={'key':'destination','account_id':'robot','chat_id':'owned-test@chatroom'}
    service=SimpleNamespace(store=delivery_store,config={'hooks':[hook]},stop_event=threading.Event(),
        poll_once=lambda:None,sender=sender,image_sender=None,file_sender=None,
        source=lambda account:SimpleNamespace(list_messages=lambda *a,**kw:{'items':echoes}))
    codex_results.sync_codex_results(service);codex_results.sync_codex_results(service)
    assert len(echoes)==1
    for notification in delivery_store.codex_notifications_status()['notifications']:
        assert notification['status']=='sent'
        assert delivery_store.get_meta('codex_result_verification:'+str(notification['id']))['status']=='verified'
