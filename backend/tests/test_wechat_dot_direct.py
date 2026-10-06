import json
from types import SimpleNamespace

import pytest

from backend.core.messaging.wechat_dot_direct import decode_dot_reply, process_direct_task
from backend.core.messaging.wechat_dots import DotsStore


HOOK = {'key':'future-community', 'chat_id':'44812733824@chatroom',
        'account_id':'wxid_gxgjjgft1oj722', 'processor':'dots'}


def test_binding_is_scoped_and_visible_in_public_status(tmp_path):
    store = DotsStore(tmp_path/'dots.sqlite')
    client = SimpleNamespace(current=lambda: {'paused':False,'room_id':'existing'})
    with pytest.raises(ValueError, match='limited'):
        store.bind_existing_dot({**HOOK,'chat_id':'another'}, client)
    store.bind_existing_dot(HOOK, client)
    assert store.direct_binding(HOOK['key'])['room_id'] == 'existing'
    assert store.status()['connection']['stage'] == 'direct_connected'


def test_partial_and_stale_cloud_results_are_not_queued():
    answer = {'request_id':'request','revision':2,'action':'reply','text':'done','question_seqs':[1]}
    assert decode_dot_reply(json.dumps(answer)[:-1], 'request', 2) is None
    assert decode_dot_reply(json.dumps(answer), 'request', 3) is None
    assert decode_dot_reply(json.dumps(answer), 'different', 2) is None
    assert decode_dot_reply('```json\n'+json.dumps(answer)+'\n```', 'request', 2) == answer


def test_direct_dispatch_survives_restart_and_never_sends_wechat(tmp_path, monkeypatch):
    store = DotsStore(tmp_path/'dots.sqlite')
    task_id = store.append(HOOK, [{'seq':1}], '2026-10-06')
    message = {'seq':1,'text':'明确问题','message_type':1}
    monkeypatch.setattr('backend.api.wechat_dots.call_tool', lambda *a: {'messages':[message]})
    sent = []
    class Client:
        def request_id(self, identity): return identity
        def send(self, *args, **kw):
            sent.append(kw['request_id'])
            return {'id':'cloud-message'}
        def reply(self, binding, request_id):
            return {'text':json.dumps({'request_id':request_id,'revision':1,'action':'reply',
                                      'text':'处理结果','question_seqs':[1]})}
    client = Client()
    service = SimpleNamespace()
    process_direct_task(service, HOOK, store, {}, direct_client=client)
    assert store.task(task_id)['status'] == 'pending'
    store = DotsStore(store.path)
    process_direct_task(service, HOOK, store, {}, direct_client=client)
    assert len(sent) == 1
    assert store.task(task_id)['status'] == 'queued'
    assert store.task(task_id)['answer'] == '处理结果'


def test_new_revision_does_not_accept_prior_cloud_answer(tmp_path, monkeypatch):
    store = DotsStore(tmp_path/'dots.sqlite')
    task_id = store.append(HOOK, [{'seq':1}], '2026-10-06')
    client = SimpleNamespace(request_id=lambda identity:identity)
    first = store.reserve_direct_request(task_id, 1, client)
    store.append(HOOK, [{'seq':2}], '2026-10-06')
    second = store.reserve_direct_request(task_id, 2, client)
    assert first['request_id'] != second['request_id']
    with pytest.raises(ValueError, match='Stale'):
        store.submit(task_id, 1, [1], '旧答案', available=[1,2])
