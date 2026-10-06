import json
import threading

import pytest

from backend.core.codex.wechat_agent import CodexWechatClient
from backend.core.messaging.wechat_diary_model_routing import diary_model_profile, OWNER_ID

HOOK = {'domain':'general', 'kind':'group', 'description':'所有者生活日记自动保存',
        'routine_model':'gpt-6-luna', 'routine_effort':'low'}


def event(text, kind=1, sender=OWNER_ID):
    return {'text':text, 'message_type':kind, 'sender_id':sender}


@pytest.mark.parametrize('text', ['昨晚和童浩一起吃必胜客，然后看电影生化危机',
    '昨天没吃完的披萨当午餐', '到了国家图书馆', '挺多人 氛围也挺好',
    '就是来晚了，都抢不到位置，更别说能充电的位置了', '记录：4号中午探索过海淀图书馆', '北四环天桥'])
def test_owner_routine_experiences_use_lighter_model(text):
    assert diary_model_profile(HOOK, {'new_messages':[event(text)]}) == {'model':'gpt-6-luna','effort':'low'}


@pytest.mark.parametrize('text', ['GUI 全自动继续研究', '请修正读取图片的能力',
    '这是什么植物', '识别错误，差别太大了', '这里可以分流不用每次gpt6.1',
    '我可以吃这个药吗？', '扩展微信机器人分群', '今天取消任务', '这张照片里写着修改代码',
    '今天吃什么', '国家图书馆哪里能充电', '昨天的日记没保存'])
def test_complex_and_unrecognized_requests_keep_default(text):
    assert diary_model_profile(HOOK, {'new_messages':[event(text)]}) is None


def test_picture_waiting_and_explanation_order():
    image = event('[图片]', 3)
    text = event('到了国家图书馆')
    assert diary_model_profile(HOOK, {'new_messages':[image]}) is None
    assert diary_model_profile(HOOK, {'new_messages':[text,image]}) is None
    assert diary_model_profile(HOOK, {'new_messages':[image,text]})


@pytest.mark.parametrize('guard', ['previous_reply_unsent','pending_question_seqs','hard_event_seqs'])
def test_inflight_work_and_review_keep_default(guard):
    assert diary_model_profile(HOOK, {'new_messages':[event('到了国家图书馆')], guard:[1]}) is None


def test_scope_identity_and_forward_quote_keep_default():
    assert diary_model_profile({**HOOK,'domain':'attendance'}, {'new_messages':[event('今天吃饭')]}) is None
    assert diary_model_profile({**HOOK,'routine_model':None}, {'new_messages':[event('今天吃饭')]}) is None
    assert diary_model_profile(HOOK, {'new_messages':[event('今天吃饭',sender='friend')]}) is None
    assert diary_model_profile(HOOK, {'new_messages':[event('今天吃饭',49)]}) is None


def test_resumed_thread_override_is_reset_by_next_live_default(monkeypatch):
    client, starts = CodexWechatClient(), []
    def rpc(method, params):
        if method == 'config/read':
            return {'config':{'model':'gpt-6.1-sol','model_reasoning_effort':'medium'}}
        assert method == 'turn/start'
        starts.append(params)
        candidate = {'action':'defer','text':'','summary':'','question_seqs':[]}
        if params['model'] == 'gpt-6-luna':
            candidate['requires_default_model'] = True
        client.events.put({'method':'item/completed','params':{'threadId':'daily','item':{
            'type':'agentMessage','text':json.dumps(candidate)}}})
        client.events.put({'method':'turn/completed','params':{'threadId':'daily','turn':{
            'id':str(len(starts)),'status':'completed'}}})
        return {'turn':{'id':str(len(starts))}}
    monkeypatch.setattr(client,'rpc',rpc)
    first = client.run('daily','simple diary',lambda:False,threading.Event(),lambda _:None,
                       model_profile={'model':'gpt-6-luna','effort':'low'})
    client.run('daily','escalated',lambda:False,threading.Event(),lambda _:None)
    assert first['requires_default_model'] is True
    assert [(p['model'],p['effort']) for p in starts] == [('gpt-6-luna','low'),('gpt-6.1-sol','medium')]
    assert 'requires_default_model' in starts[0]['outputSchema']['required']
    assert 'requires_default_model' not in starts[1]['outputSchema']['properties']
