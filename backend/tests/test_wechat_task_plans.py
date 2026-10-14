import json
import time
import pytest
from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging.wechat_task_store import WechatTaskStore
from backend.core.messaging.wechat_task_relay import process_task_relay
from backend.core.messaging.wechat_task_routing import classify_task_plan
from backend.tests.test_wechat_task_relay import ParallelRemote, HOOK, new_task
from backend.tests.test_wechat_device_agent import setup
from backend.tests.test_wechat_message_routing import ingest


def two_tasks(*args):
    return [{'action':'new_task','task_id':None,'text':'解释并发'},
            {'action':'new_task','task_id':None,'text':'解释幂等'}]


def test_shared_source_replies_are_independent_and_cursor_waits_for_all(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store,1,'解释并发；解释幂等')
    process_task_relay(service,HOOK,remote,classifier=two_tasks)
    assert remote.posts == 2
    assert [t['context']['messages'][0]['text'] for t in remote.turns.values()] == ['解释并发','解释幂等']
    assert all(t['context']['messages'][0]['original_text']=='解释并发；解释幂等' for t in remote.turns.values())
    remote.complete('t1')
    process_task_relay(service,HOOK,remote,classifier=two_tasks)
    assert len(sent)==1 and service.store.hook('group')['consumed_seq']==0
    assert len(service.store.pending_messages('group',all_lanes=True))==1
    # Restart the store after one lane delivered; no old reply may be replayed.
    service.store=AgentStore(service.store.path)
    remote.complete('t2')
    process_task_relay(service,HOOK,remote,classifier=two_tasks)
    process_task_relay(service,HOOK,remote,classifier=two_tasks)
    assert len(sent)==2 and remote.posts==2
    assert service.store.hook('group')['consumed_seq']==1
    assert len(service.store.deliveries('group'))==2


def test_ignore_one_lane_cannot_consume_sibling_work(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store,1,'解释并发；解释幂等')
    process_task_relay(service,HOOK,remote,classifier=two_tasks)
    remote.complete('t1',action='ignore')
    process_task_relay(service,HOOK,remote,classifier=two_tasks)
    assert not sent and service.store.hook('group')['consumed_seq']==0
    remote.complete('t2')
    process_task_relay(service,HOOK,remote,classifier=two_tasks)
    assert len(sent)==1 and service.store.hook('group')['consumed_seq']==1


def test_bad_batch_rolls_back_every_assignment(tmp_path):
    service, _, _ = setup(tmp_path)
    ingest(service.store,1,'解释并发；停止未知任务')
    message=service.store.unassigned_task_inputs('group')[0]
    with pytest.raises(ValueError,match='Unknown task'):
        service.store.assign_task_plan('group',message,[two_tasks()[0],
             {'action':'cancel','task_id':'invented','text':'停止未知任务'}])
    assert not service.store.conversation_tasks('group')
    assert service.store.unassigned_task_inputs('group')[0]['seq']==1


def test_scoped_receipt_reconciliation_preserves_unfinished_sibling(tmp_path):
    service, _, _ = setup(tmp_path)
    store=service.store
    ingest(store,1,'解释并发；解释幂等')
    message=store.unassigned_task_inputs('group')[0]
    first,second=store.assign_task_plan('group',message,two_tasks())
    reply=store.reserve_task_reply('group',first,[1],'并发解释')
    store.set_meta(f'reply_groups:{reply}',[{'text':'并发解释','question_seqs':[1]}])
    store.finish_reply(reply,'uncertain',{'error':'lost receipt'})
    ingest(store,2,'新的消息')
    store.retire_uncertain_reply(reply,confirmed_groups=[0],evidence='archive confirmed first task text')
    assert not store.task_inputs('group',first)
    assert store.task_inputs('group',second)
    assert store.hook('group')['consumed_seq']==0


def test_outbox_migration_preserves_legacy_receipt_and_uniqueness(tmp_path,monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(WechatTaskStore,'initialize_task_store',lambda self:None)
        legacy=AgentStore(tmp_path/'legacy.sqlite')
        legacy.ensure_hook('group','bot','1@chatroom')
        ingest(legacy,1,'旧消息')
        receipt=legacy.reserve_reply('group',1,'旧答复')
        legacy.set_meta(f'reply_groups:{receipt}',[{'text':'旧答复','question_seqs':[1]}])
    store=AgentStore(legacy.path)
    assert store.status()['outbox'][0]['id']==receipt
    assert store.reserve_reply('group',1,'不可重放') is None
    store.finish_reply(receipt,'sent',{})
    assert store.question_answered('group',1)


def test_compound_model_plan_maps_only_literal_scopes_and_known_targets():
    tasks=[{'id':'a'*32,'title':'登录','owner_id':'甲','status':'running'},
           {'id':'b'*32,'title':'考勤','owner_id':'乙','status':'running'}]
    message={'seq':1,'sender_id':'player','text':'登录暂停；考勤继续'}
    chat=lambda **kw:{'content':json.dumps({'directives':[
        {'action':'cancel','task':'T0','segment':0},
        {'action':'continue','task':'T1','segment':1}]})}
    result=classify_task_plan(message,tasks,[],HOOK,chat=chat)
    assert [(d['action'],d['task_id']) for d in result]==[('cancel','a'*32),('continue','b'*32)]
    invalid=lambda **kw:{'content':json.dumps({'directives':[
        {'action':'cancel','task':'T0','segment':0},
        {'action':'continue','task':'T9','segment':1}]})}
    assert classify_task_plan(message,tasks,[],HOOK,chat=invalid)[0]['action']=='clarify'


def test_native_robot_mention_can_use_a_group_remark_name():
    message={'seq':1,'sender_id':'player','mentions':['bot'], 'text':'@群内备注 解释并发；解释幂等'}
    chat=lambda **kw:{'content':json.dumps({'directives':[
        {'action':'new_task','task':None,'segment':0},
        {'action':'new_task','task':None,'segment':1}]})}
    assert len(classify_task_plan(message,[],[],HOOK,chat=chat))==2


def test_first_explicit_conversation_cannot_ask_for_a_nonexistent_task_id():
    message={'seq':1,'sender_id':'player','mentions':['bot'],'text':'解释一下并发是什么意思'}
    result=classify_task_plan(message,[],[],HOOK,chat=lambda **kw:pytest.fail('explicit first conversation needs no model gate'))
    assert result==[{'action':'new_task','task_id':None,'explicit':True}]


def test_forwarded_device_reference_and_authority_are_preserved(tmp_path):
    service,remote,_=setup(tmp_path)
    text='请查看 MF 本机上的会话状态'
    ingest(service.store,1,text)
    process_task_relay(service,HOOK,remote,classifier=lambda *a:new_task())
    payload=remote.calls[-1][2]['json']
    forwarded=payload['context']['messages'][0]
    assert forwarded['text']==forwarded['original_text']==text
    assert forwarded['verified_owner'] is False
    assert '均指当前执行请求的远端设备' not in payload['instruction']


def test_partial_answer_keeps_other_questions_in_same_lane(tmp_path):
    service, _, sent=setup(tmp_path)
    remote=ParallelRemote()
    ingest(service.store,1,'第一个问题')
    ingest(service.store,2,'第二个问题')
    def one_lane(message,tasks,*args):
        return new_task() if not tasks else {'action':'continue','task_id':tasks[0]['id']}
    process_task_relay(service,HOOK,remote,classifier=one_lane)
    remote.complete('t1')
    remote.turns['t1']['result_report']['final_message']=json.dumps({'action':'reply','text':'先回答第一题','question_seqs':[1]})
    process_task_relay(service,HOOK,remote,classifier=one_lane)
    assert len(sent)==1
    task=service.store.conversation_tasks('group')[0]
    assert [m['seq'] for m in service.store.task_inputs('group',task['id'])]==[2]
    process_task_relay(service,HOOK,remote,classifier=one_lane)
    assert remote.posts==2
    assert [m['seq'] for m in remote.turns['t2']['context']['messages']]==[2]
    remote.complete('t2')
    process_task_relay(service,HOOK,remote,classifier=one_lane)
    assert len(sent)==2 and service.store.hook('group')['consumed_seq']==2


def test_partial_send_crash_does_not_replay_committed_scope(tmp_path,monkeypatch):
    service, _, sent=setup(tmp_path)
    remote=ParallelRemote()
    ingest(service.store,1,'第一个问题')
    ingest(service.store,2,'第二个问题')
    def one_lane(message,tasks,*args):
        return new_task() if not tasks else {'action':'continue','task_id':tasks[0]['id']}
    process_task_relay(service,HOOK,remote,classifier=one_lane)
    remote.complete('t1')
    remote.turns['t1']['result_report']['final_message']=json.dumps({'action':'reply','text':'先回答第一题','question_seqs':[1]})
    original=service.store.update_conversation_task
    def crash_after_delivery(key,task):
        if task.get('request') is None:
            raise RuntimeError('crash before progress commit')
        return original(key,task)
    with monkeypatch.context() as patch:
        patch.setattr(service.store,'update_conversation_task',crash_after_delivery)
        with pytest.raises(RuntimeError,match='crash before progress'):
            process_task_relay(service,HOOK,remote,classifier=one_lane)
    service.store=AgentStore(service.store.path)
    task=service.store.conversation_tasks('group')[0]
    task['retry_at']=0;service.store.update_conversation_task('group',task)
    process_task_relay(service,HOOK,remote,classifier=one_lane)
    process_task_relay(service,HOOK,remote,classifier=one_lane)
    assert len(sent)==1 and remote.posts==2


def test_expired_unclaimed_input_cannot_block_new_reply_and_owned_input_does_not_expire(tmp_path):
    service, _, _=setup(tmp_path)
    store=service.store
    store.set_message_age_policy(max_age_seconds=60,reason='test queued age policy')
    store.ingest('bot',{'events':[{'message_id':'1','chat_id':'1@chatroom','sender_id':'player',
        'timestamp':time.time()-3600,'text':'过期消息','message_type':1}], 'cursor':{'n':1}})
    ingest(store,2,'当前新任务')
    message=store.unassigned_task_inputs('group')[0]
    assert message['seq']==2
    tid=store.assign_task_input('group',message,new_task())
    receipt=store.reserve_task_reply('group',tid,[2],'当前答复')
    assert receipt is not None
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr('backend.core.messaging.wechat_agent_store.time.time',lambda:message['timestamp']+7200)
        assert [m['seq'] for m in store.pending_messages('group',all_lanes=True)]==[2]
