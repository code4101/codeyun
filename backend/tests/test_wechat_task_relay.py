import json
import pytest
from backend.tests.test_wechat_device_agent import setup, REMOTE_HOOK
from backend.tests.test_wechat_message_routing import ingest
from backend.core.messaging.wechat_task_relay import process_task_relay
from backend.core.messaging.wechat_task_routing import classify_message

HOOK = {**REMOTE_HOOK, 'task_routing': True, 'wake_mode': 'all_members'}


class ParallelRemote:
    def __init__(self):
        self.turns = {}
        self.sessions = {}
        self.posts = 0
        self.lose_response = False

    def validate_target(self):
        return {'enabled': True}

    def request(self, method, path, **kwargs):
        if method == 'POST':
            self.posts += 1
            body = kwargs['json']
            sid = f's{self.posts}' if path == '/sessions' else path.split('/')[2]
            tid = f't{self.posts}'
            self.sessions.setdefault(sid, {'id': sid, 'title': body.get('title'), 'turns': []})
            turn = {'id': tid, 'context': body['context'], 'status': 'running'}
            self.sessions[sid]['turns'].append(turn)
            self.turns[tid] = turn
            if self.lose_response:
                raise TimeoutError('lost response after acceptance')
            return {'id': sid if path == '/sessions' else tid, 'last_turn_id': tid}
        if path == '/sessions':
            return {'items': list(self.sessions.values())}
        if path.startswith('/sessions/'):
            return self.sessions[path.split('/')[2]]
        return self.turns[path.split('/')[2]]

    def complete(self, tid, action='reply'):
        turn = self.turns[tid]
        turn.update(status='completed', result_report={'final_message': json.dumps({
            'action': action, 'text': '完成 ' + tid, 'question_seqs': [m['seq'] for m in turn['context']['messages']]})})


def new_task(*args):
    return {'action': 'new_task', 'task_id': None}


def test_parallel_tasks_do_not_share_session_or_suppress_unrelated_reply(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store, 1, '修复登录')
    ingest(service.store, 2, '整理考勤')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    tasks = service.store.conversation_tasks('group')
    assert len(tasks) == 2
    assert tasks[0]['session_id'] != tasks[1]['session_id']
    ingest(service.store, 3, '登录改成验证码')
    def followup(*args):
        return {'action': 'continue', 'task_id': tasks[0]['id']}
    remote.complete('t1')
    remote.complete('t2')
    process_task_relay(service, HOOK, remote, classifier=followup)
    assert remote.posts == 3
    assert len(sent) == 1 and 't2' in sent[0][1]
    assert remote.turns['t3']['context']['task_id'] == tasks[0]['id']
    assert [m['seq'] for m in remote.turns['t3']['context']['messages']] == [1, 3]
    remote.complete('t3')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    assert len(sent) == 2
    assert not service.store.pending_messages('group', all_lanes=True)


def test_lost_acceptance_is_reconciled_never_reposted(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    remote.lose_response = True
    ingest(service.store, 1, '执行任务')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    task = service.store.conversation_tasks('group')[0]
    assert task['request'] and 'turn_id' not in task['request']
    task['retry_at'] = 0
    service.store.update_conversation_task('group', task)
    remote.complete('t1')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    assert remote.posts == 1 and len(sent) == 1


def test_cancel_is_queued_on_its_task_and_does_not_claim_interrupt(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store, 1, '操作设备')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    task = service.store.conversation_tasks('group')[0]
    ingest(service.store, 2, '停止操作')
    cancel = lambda *args: {'action': 'cancel', 'task_id': task['id']}
    process_task_relay(service, HOOK, remote, classifier=cancel)
    assert remote.posts == 1 and not sent
    remote.complete('t1')
    process_task_relay(service, HOOK, remote, classifier=cancel)
    assert remote.posts == 2 and not sent
    assert remote.turns['t2']['context']['messages'][-1]['intake']['action'] == 'cancel'


def test_chatter_is_silent_and_ambiguous_reference_gets_local_clarification(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store, 1, '你今晚几点吃饭')
    ingest(service.store, 2, '那个先别做了')
    classify = lambda m, *a: {'action': 'ignore' if m['seq'] == 1 else 'clarify', 'task_id': None}
    process_task_relay(service, HOOK, remote, classifier=classify)
    assert remote.posts == 0 and len(sent) == 1
    assert not service.store.pending_messages('group', all_lanes=True)


def test_reply_waits_for_unclassified_input_and_is_never_resent(tmp_path):
    service, _, sent = setup(tmp_path)
    ingest(service.store, 1, '任务')
    tid = service.store.assign_task_input('group', service.store.pending_messages('group', all_lanes=True)[0], new_task())
    ingest(service.store, 2, '更改任务')
    assert service.store.reserve_task_reply('group', tid, [1], '旧答复') is None
    with pytest.raises(ValueError, match='Unknown task'):
        service.store.assign_task_input('group', service.store.pending_messages('group', all_lanes=True)[-1],
                                        {'action': 'continue', 'task_id': 'invented'})
    remote = ParallelRemote()
    process_task_relay(service, HOOK, remote, classifier=lambda *a: {'action': 'continue', 'task_id': tid})
    remote.complete('t1')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    process_task_relay(service, HOOK, remote, classifier=new_task)
    assert len(sent) == 1 and remote.posts == 1


def test_classifier_validates_choice_and_mention_cannot_be_ignored():
    message = {'seq': 1, 'sender_id': 'player', 'text': '@bot 什么', 'mentions': ['bot']}
    tasks=[{'id':'A','title':'已有任务','owner_id':'player','status':'running'}]
    assert classify_message(message, tasks, [], HOOK, chat=lambda **kw: {'content': '{"code":"A"}'})['action'] == 'clarify'
    with pytest.raises(ValueError):
        classify_message(message, tasks, [], HOOK, chat=lambda **kw: {'content': '{"code":"invented"}'})


@pytest.mark.parametrize('text',['先别做了','Code4102，继续刚才那个','@Code4102 先别继续了'])
def test_bare_control_phrase_cannot_guess_between_parallel_tasks(text):
    tasks = [{'id': t, 'title': t, 'status':'running', 'owner_id':'player'} for t in ['A','B']]
    hook={**HOOK,'mention_aliases':['Code4102']}
    result = classify_message({'seq':1,'sender_id':'player','text':text}, tasks, [], hook,
                              chat=lambda **kw: pytest.fail('ambiguous control must not be guessed'))
    assert result['action'] == 'clarify'


def test_native_quote_survives_intake_projection_and_does_not_trigger_bare_phrase_guard():
    tasks = [{'id': t, 'title': t, 'status':'running', 'owner_id':'player'} for t in ['A','B']]
    message = {'seq':1,'sender_id':'player','text':'player:\n<msg><appmsg><title>继续</title><type>57</type>'
               '<refermsg><displayname>Code4102</displayname><content>任务B：考勤</content><svrid>123</svrid></refermsg>'
               '</appmsg></msg>'}
    def chat(**kwargs):
        content = json.loads(kwargs['messages'][0]['content'])
        assert content['current']['text'] == '继续'
        assert content['current']['quote']['content'] == '任务B：考勤'
        return {'content':'{"code":"D"}'}
    assert classify_message(message, tasks, [], HOOK, chat=chat)['task_id'] == 'B'


def test_history_bound_keeps_older_unfinished_lane(tmp_path):
    service, _, _ = setup(tmp_path)
    first = None
    for i in range(1, 6):
        ingest(service.store, i, f'任务{i}')
        message = service.store.unassigned_task_inputs('group')[0]
        tid = service.store.assign_task_input('group', message, new_task())
        if i == 1:
            first = tid
        else:
            service.store.resolve_routed_messages('group', [message['seq']])
    tasks = service.store.conversation_tasks('group', recent_limit=2)
    assert len(tasks) == 3
    assert tasks[0]['id'] == first
    assert len(service.store.conversation_tasks('group', recent_limit=10)) == 5
    assert service.store.conversation_task_reference('group', first[:8])['id'] == first
    assert service.store.conversation_task_reference('group', '0'*8) is None


def test_remote_task_failure_is_visible_when_hook_worker_is_idle(tmp_path):
    from backend.core.messaging.wechat_agent import describe_health
    service, _, _ = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store, 1, '任务')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    remote.turns['t1']['status'] = 'failed'
    process_task_relay(service, HOOK, remote, classifier=new_task)
    state = {'running':True, **service.store.status()}
    health = describe_health(state, {'hooks':[HOOK]})
    assert health['health'] == 'degraded'
    assert any('task' in e for e in health['errors'])


def test_unknown_task_reference_cannot_be_reassigned_to_another_lane(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store, 1, '任务 deadbeef 继续')
    process_task_relay(service, HOOK, remote,
                       classifier=lambda *a: pytest.fail('unknown task ID must be clarified'))
    assert len(sent) == 1 and remote.posts == 0


def test_unassigned_filter_precedes_pagination_and_does_not_read_entire_history(tmp_path, monkeypatch):
    service, _, _ = setup(tmp_path)
    store = service.store
    ingest(store, 1, '任务一')
    first = store.unassigned_task_inputs('group')[0]
    tid = store.assign_task_input('group', first, new_task())
    ingest(store, 2, '任务一补充')
    second = store.unassigned_task_inputs('group')[0]
    store.assign_task_input('group', second, {'action':'continue','task_id':tid})
    ingest(store, 3, '新的任务')
    monkeypatch.setattr(store, 'pending_messages', lambda *a,**kw: pytest.fail('must paginate only unassigned inputs'))
    assert [m['seq'] for m in store.unassigned_task_inputs('group', limit=1)] == [3]


def test_concurrency_limit_starts_waiting_lane_after_completion(tmp_path):
    service, _, _ = setup(tmp_path)
    remote = ParallelRemote()
    for i in range(1, 4):
        ingest(service.store, i, f'任务{i}')
    hook = {**HOOK, 'task_concurrency': 2}
    process_task_relay(service, hook, remote, classifier=new_task)
    assert remote.posts == 2
    remote.complete('t1')
    process_task_relay(service, hook, remote, classifier=new_task)
    assert remote.posts == 3


def test_waiting_for_user_does_not_hold_concurrency_slot(tmp_path):
    service, _, _ = setup(tmp_path)
    remote = ParallelRemote()
    ingest(service.store, 1, '先处理任务一')
    hook = {**HOOK, 'task_concurrency': 1}
    process_task_relay(service, hook, remote, classifier=new_task)
    remote.complete('t1', action='defer')
    ingest(service.store, 2, '独立任务二')
    process_task_relay(service, hook, remote, classifier=new_task)
    assert remote.posts == 2
    first = service.store.conversation_tasks('group')[0]
    assert first['status'] == 'waiting'
    ingest(service.store, 3, '补充任务一')
    process_task_relay(service, hook, remote, classifier=lambda *a: {'action':'continue','task_id':first['id']})
    assert remote.posts == 2
    remote.complete('t2')
    process_task_relay(service, hook, remote, classifier=new_task)
    process_task_relay(service, hook, remote, classifier=new_task)
    assert remote.posts == 3


def test_uncertain_delivery_never_replays_after_restart(tmp_path):
    service, _, _ = setup(tmp_path)
    remote = ParallelRemote()
    def uncertain(*args, **kwargs):
        raise TimeoutError('send may have succeeded')
    service.sender = uncertain
    service.recover_delivery_for_new_questions = lambda h: False
    ingest(service.store, 1, '处理任务')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    remote.complete('t1')
    process_task_relay(service, HOOK, remote, classifier=new_task)
    assert len(service.store.uncertain_replies('group')) == 1
    for _ in range(2):
        assert not process_task_relay(service, HOOK, remote, classifier=new_task)
    assert remote.posts == 1


def test_terminal_failed_task_does_not_block_new_lane(tmp_path):
    service, _, sent = setup(tmp_path)
    remote = ParallelRemote()
    hook = {**HOOK, 'task_concurrency':1}
    ingest(service.store, 1, '任务一')
    process_task_relay(service, hook, remote, classifier=new_task)
    remote.turns['t1']['status'] = 'failed'
    ingest(service.store, 2, '任务二')
    process_task_relay(service, hook, remote, classifier=new_task)
    assert remote.posts == 2
    remote.complete('t2')
    process_task_relay(service, hook, remote, classifier=new_task)
    assert len(sent) == 1
