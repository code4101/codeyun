from copy import deepcopy
import pytest

from backend.core.messaging.wechat_diary_recording import record_plain_diary, plain_diary_batch

HOOK = {'engineering_diary':True, 'diary_silent':True, 'domain':'general',
        'description':'所有者生活日记', 'routine_model':'gpt-6-luna',
        'chat_id':'47794939644@chatroom', 'account_id':'wxid_gxgjjgft1oj722'}


def payload(text='到了国家图书馆', **overrides):
    event = dict(seq=973, message_id='47794939644@chatroom:45', message_type=1,
                 timestamp=1791258464, sender_id='wxid_m1cd4f5aahut22', text=text)
    event.update(overrides)
    return {'new_messages':[event]}


class Response:
    def __init__(self, data): self.data = deepcopy(data)
    def raise_for_status(self): pass
    def json(self): return self.data


class Client:
    def __init__(self):
        self.note = dict(id=10, user_id=2, version=3, format_type='html', custom_fields=[],
                         content='<p>Earlier event</p><!-- 47794939644@chatroom:450 -->',
                         start_at=1791216000)
        self.writes = []
        self.bad_readback = False
    def request(self, method, url, **kwargs):
        if url.endswith('/query'):
            return Response({'nodes':[self.note], 'total_nodes':1})
        if method == 'PUT':
            body = kwargs['json']
            assert body['base_version'] == self.note['version']
            assert body['expected_fields']['content'] == self.note['content']
            self.writes.append(body)
            self.note['content'] = body['content']
            self.note['version'] += 1
        result = deepcopy(self.note)
        if self.bad_readback and self.writes and method == 'GET': result['content']='lost write'
        return Response(result)


def test_verified_literal_html_write_is_silent_and_exact_source_dedupes():
    client = Client()
    result = record_plain_diary(HOOK, payload(), client=client, base_url='http://test')
    assert result['action']=='ignore' and result['text']=='' and result['question_seqs']==[973]
    assert len(client.writes)==1
    assert '<strong>11:47</strong> 到了国家图书馆' in client.note['content']
    assert record_plain_diary(HOOK, payload(), client=client, base_url='http://test')['action']=='ignore'
    assert len(client.writes)==1


@pytest.mark.parametrize('text', ['昨晚吃必胜客', '记录：4号中午探索海淀图书馆',
    '这是什么植物', '日记不要回复', '照片识别错了', '午餐吃昨天的披萨', '早上到了图书馆'])
def test_historical_dates_and_instructions_require_ai(text):
    assert not plain_diary_batch(HOOK, payload(text))


def test_images_pending_work_and_other_people_cannot_enter_program_lane():
    assert not plain_diary_batch(HOOK, payload(message_type=3))
    assert not plain_diary_batch(HOOK, payload(sender_id='friend'))
    assert not plain_diary_batch(HOOK, {**payload(), 'previous_reply_unsent':True})
    assert not plain_diary_batch({**HOOK, 'engineering_diary':False}, payload())


def test_missing_target_and_failed_verification_do_not_claim_success():
    client=Client();client.note['start_at']=1791043200
    assert record_plain_diary(HOOK,payload(),client=client,base_url='http://test') is None
    client=Client();client.bad_readback=True
    with pytest.raises(RuntimeError, match='readback'):
        record_plain_diary(HOOK,payload(),client=client,base_url='http://test')
