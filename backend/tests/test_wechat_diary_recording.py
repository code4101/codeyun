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
    def post(self,url,files,timeout):
        filename,stream,mime=files['file']
        body=stream.read()
        self.uploaded=getattr(self,'uploaded',[])+[(filename,body,mime)]
        return Response({'errno':0,'data':{'url':'/static/attachments/'+filename}})


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


def test_explained_image_is_uploaded_after_text_context_and_saved_with_its_source(tmp_path,monkeypatch):
    import sys,types
    image_path=tmp_path/'dinner.jpg';image_path.write_bytes(b'jpeg bytes')
    export={'readable':True,'kind':'image','variant':'thumbnail','width':210,'height':118,
            'stored_path':str(image_path),'file_name':'dinner.jpg'}
    storage=types.SimpleNamespace(message_resources=lambda chat,lid:{'items':[{'export':export}]})
    module=types.ModuleType('pyxllib.autogui.wechat_accounts')
    module.get_account_storage=lambda account:storage
    monkeypatch.setitem(sys.modules,'pyxllib.autogui.wechat_accounts',module)
    photo={'seq':1301,'message_id':'47794939644@chatroom:83','sender_id':'wxid_m1cd4f5aahut22',
           'message_type':3,'timestamp':1791280722,'local_id':83,'mentions':[]}
    dinner={'seq':1302,'message_id':'47794939644@chatroom:84','sender_id':'wxid_m1cd4f5aahut22',
            'message_type':1,'timestamp':1791280750,'text':'晚饭吉野家，好贵，50 元。不过还行，好久没有这胃口加了米饭吃的饱饱的。','mentions':[]}
    assert plain_diary_batch(HOOK,{'new_messages':[photo,dinner]})
    client=Client()
    result=record_plain_diary(HOOK,{'new_messages':[photo,dinner]},client=client,base_url='http://test')
    assert result['action']=='ignore' and len(client.uploaded)==1
    assert client.uploaded[0][1]==b'jpeg bytes'
    assert '47794939644@chatroom:83' in client.note['content']
    assert '47794939644@chatroom:84' in client.note['content']
    assert '/static/attachments/dinner.jpg' in client.note['content']


@pytest.fixture
def dash_lane(tmp_path, monkeypatch):
    from backend.core.messaging import wechat_agent
    monkeypatch.setattr(wechat_agent, 'agent_root', lambda:tmp_path)
    return {**HOOK, 'diary_dash_document':True}


class DashClient(Client):
    def __init__(self):
        super().__init__()
        self.notes = [self.note]
        self.creates = []
        self.fail_after_commit = False

    def request(self, method, url, **kwargs):
        if url.endswith('/query'):
            rules = kwargs['json']['rules']
            notes = [n for n in self.notes if all(
                (r['value'] in n['content']) == (r['op']=='contains') for r in rules)]
            return Response({'nodes':notes, 'total_nodes':len(notes)})
        if method == 'POST':
            body = kwargs['json']
            note = dict(id=20+len(self.creates), user_id=2, version=1, **body)
            self.creates.append(deepcopy(body))
            self.notes.append(note)
            if self.fail_after_commit:
                self.fail_after_commit = False
                raise TimeoutError('response lost after commit')
            return Response(note)
        if method == 'GET':
            note = next(n for n in self.notes if str(n['id']) == url.rsplit('/',1)[-1])
            result = deepcopy(note)
            if self.bad_readback:
                result['private_level'] = 0
            return Response(result)
        return super().request(method,url,**kwargs)


def test_dash_creates_independent_private_document_and_retries_dedupe(dash_lane):
    client = DashClient()
    event = payload('-')
    result = record_plain_diary(dash_lane,event,client=client,base_url='http://test')
    assert result['action'] == 'ignore' and result['text'] == ''
    assert len(client.creates) == 1 and not client.writes
    body = client.creates[0]
    assert body['title'] == '-' and body['private_level'] == 1
    assert body['note_form'] == 'document' and body['format_type'] == 'html'
    assert body['start_at'] == event['new_messages'][0]['timestamp']
    assert body['custom_fields'] == [] and body['content'].startswith('<!-- diary-dash:')
    record_plain_diary(dash_lane,event,client=client,base_url='http://test')
    assert len(client.creates) == 1
    # Placeholders must not make the ordinary day's aggregation ambiguous.
    assert record_plain_diary(dash_lane,payload(),client=client,base_url='http://test')['action']=='ignore'
    assert len(client.writes) == 1 and client.notes[1]['content'] == body['content']


def test_dash_commit_timeout_and_failed_privacy_readback_are_reconciled(dash_lane):
    client = DashClient(); client.fail_after_commit = True
    with pytest.raises(TimeoutError):
        record_plain_diary(dash_lane,payload('-'),client=client,base_url='http://test')
    assert len(client.creates) == 1
    client.bad_readback = True
    with pytest.raises(RuntimeError,match='readback'):
        record_plain_diary(dash_lane,payload('-'),client=client,base_url='http://test')
    client.bad_readback = False
    assert record_plain_diary(dash_lane,payload('-'),client=client,base_url='http://test')['action']=='ignore'
    assert len(client.creates) == 1


@pytest.mark.parametrize('changes', [
    {'text':'--'}, {'text':'－'}, {'text':'说到-这个标记'}, {'sender_id':'friend'},
    {'message_type':3}, {'quote':{'text':'-'}}, {'mentions':['bot']},
    {'chat_id':'other@chatroom'}, {'timestamp':0}])
def test_dash_requires_exact_owner_command(dash_lane,changes):
    from backend.core.messaging.wechat_diary_recording import record_diary_dash
    client = DashClient()
    event = payload('-')
    event['new_messages'][0].update(changes)
    assert record_diary_dash(dash_lane,event,client=client,base_url='http://test') is None
    assert not client.creates


def test_dash_opt_in_pending_work_and_concurrent_retries(dash_lane):
    from concurrent.futures import ThreadPoolExecutor
    from backend.core.messaging.wechat_diary_recording import record_diary_dash
    client = DashClient()
    for change in ({'previous_reply_unsent':True},{'pending_question_seqs':[1]},{'hard_event_seqs':[1]}):
        assert record_diary_dash(dash_lane,{**payload('-'),**change},client=client,base_url='http://test') is None
    assert record_diary_dash(HOOK,payload('-'),client=client,base_url='http://test') is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _:record_plain_diary(dash_lane,payload('-'),client=client,
                                                           base_url='http://test'),range(2)))
    assert all(r['action']=='ignore' for r in results) and len(client.creates)==1
    # A second physical '-' message creates another document, even at the same second.
    record_plain_diary(dash_lane,payload('-',message_id=HOOK['chat_id']+':46',seq=974),
                       client=client,base_url='http://test')
    assert len(client.creates)==2


def test_dash_mixed_batch_preserves_effects_and_photo_waits_for_explanation(dash_lane):
    client = DashClient()
    dash = payload('-',message_id=HOOK['chat_id']+':46')['new_messages'][0]
    normal = payload()['new_messages'][0]
    batch = {'new_messages':[dash,normal]}
    result = record_plain_diary(dash_lane,batch,client=client,base_url='http://test')
    assert result['action']=='ignore' and len(client.creates)==1 and len(client.writes)==1
    assert 'engineering_diary_dash_attempt' in batch
    # The command is not an explanation for a preceding photo.
    photo = payload(message_type=3)['new_messages'][0]
    batch = {'new_messages':[photo,dash]}
    assert record_plain_diary(dash_lane,batch,client=client,base_url='http://test') is None
    assert batch['engineering_diary_dash_attempt']['action']=='ignore'
    assert len(client.creates)==1
