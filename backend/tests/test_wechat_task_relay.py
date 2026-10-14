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
    assert classify_message(message, [], [], HOOK, chat=lambda **kw: {'content': '{"code":"I"}'})['action'] == 'clarify'
    with pytest.raises(ValueError):
        classify_message(message, [], [], HOOK, chat=lambda **kw: {'content': '{"code":"invented"}'})


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
