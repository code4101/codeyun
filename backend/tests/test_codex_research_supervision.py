"""Non-operation state/ownership contracts and real TEMP durable publication."""
import json
import subprocess
import sys

import pytest
from backend.core.codex import research_supervision as supervision


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
    assert first['error'] is None and len(delivery_store.pending_codex_notifications())==2
    assert all(row['status']=='pending' for row in delivery_store.codex_notifications_status()['notifications'])
    calls_before=len(sources[1])
    skipped=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=state)
    assert skipped['results']==[] and len(sources[1])==calls_before
    clock[0]+=61
    again=supervision.inspect_registered_codex_research_once(store=delivery_store,state_dir=state)
    assert len(again['results'])==1 and len(delivery_store.pending_codex_notifications())==2
    assert again['results'][0]['handoff']['admitted']==[]
    assert not again['notification_sent'] and not again['recovery_performed']


def test_crash_after_enqueue_before_handoff_receipt_reconciles_exact_outbox_identity(sources,delivery_store,tmp_path,monkeypatch):
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
    monkeypatch.setattr(inspection_service,'supervise_codex_research',lambda stop:research.set())
    def ownership(stop):
        assert runtime.wait(2) and research.wait(2)
        order.append('ownership')
    monkeypatch.setattr(inspection_service,'supervise_scheduler_ownership',ownership)
    inspection_service.run_inspection_service(threading.Event())
    assert order==['ownership']


def test_resident_research_loop_uses_stoppable_wait_and_persists_exception_backoff(delivery_store,monkeypatch):
    from backend.core.fanxiu.data_annotation import inspection_service
    from backend.core.messaging import codex_results
    monkeypatch.setattr(codex_results,'result_store',lambda:delivery_store)
    def fail(**kwargs):raise OSError('public observer unavailable')
    monkeypatch.setattr(supervision,'inspect_registered_codex_research_once',fail)
    class Stop:
        def __init__(self):self.waits=[]
        def wait(self,seconds):
            self.waits.append(seconds)
            return len(self.waits)==3
    stop=Stop();inspection_service.supervise_codex_research(stop)
    assert stop.waits==[15,30,60]
    assert 'public observer unavailable' in delivery_store.get_meta('codex_research_supervision_worker')['error']

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
    assert len(echoes)==2
    for notification in delivery_store.codex_notifications_status()['notifications']:
        assert notification['status']=='sent'
        assert delivery_store.get_meta('codex_result_verification:'+str(notification['id']))['status']=='verified'
