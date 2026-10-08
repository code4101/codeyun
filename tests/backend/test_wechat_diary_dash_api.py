"""The model-free marker lane must work against the real notes HTTP contract."""
from urllib.parse import urlsplit

from backend.app import app
from backend.core.access.auth import get_current_user_from_token, get_optional_current_user_from_token
from backend.core.messaging.wechat_diary_recording import record_plain_diary
from backend.models import User


def test_dash_real_create_query_readback_and_owner_access(client, session, tmp_path, monkeypatch):
    from backend.core.messaging import wechat_agent
    monkeypatch.setattr(wechat_agent, 'agent_root', lambda:tmp_path)
    owner = User(id=2, username='code4101', hashed_password='test', is_active=True)
    stranger = User(id=3, username='other', hashed_password='test', is_active=True)
    session.add(owner); session.add(stranger); session.commit()
    app.dependency_overrides[get_current_user_from_token] = lambda:owner
    app.dependency_overrides[get_optional_current_user_from_token] = lambda:owner

    class NotesClient:
        def request(self, method, url, **kwargs):
            return client.request(method, urlsplit(url).path, json=kwargs.get('json'))

    hook = dict(engineering_diary=True, diary_dash_document=True, domain='general',
                description='星图日记', routine_model='gpt-6-luna', chat_id='47794939644@chatroom',
                account_id='wxid_gxgjjgft1oj722')
    event = dict(seq=1, message_id=hook['chat_id']+':100', message_type=1,
                 sender_id='wxid_m1cd4f5aahut22', timestamp=1791416790, text='-')
    adapter = NotesClient()
    for _ in range(2):
        result = record_plain_diary(hook,{'new_messages':[event]},client=adapter,base_url='http://test')
        assert result['action']=='ignore'
    response = client.post('/api/notes/query',json={
        'rules':[{'field':'content','op':'contains','value':'<!-- diary-dash:'}],
        'include_edges':False})
    assert response.status_code==200
    data = response.json()
    assert data['total_nodes']==1
    note_id = data['nodes'][0]['id']
    note = client.get('/api/notes/'+str(note_id)).json()
    assert '<!-- diary-dash:' in note['content']
    assert note['private_level']==1 and note['note_form']=='document'
    assert note['title']=='-' and note['start_at']==event['timestamp']
    normal = client.post('/api/notes/',json={
        'title':'经历', 'content':'<p>Earlier</p><!-- '+hook['chat_id']+':99 -->',
        'format_type':'html', 'note_form':'document', 'start_at':event['timestamp'],
        'private_level':1}).json()
    next_event = {**event, 'seq':2, 'message_id':hook['chat_id']+':101', 'text':'到了图书馆'}
    assert record_plain_diary(hook,{'new_messages':[next_event]},client=adapter,
                              base_url='http://test')['action']=='ignore'
    updated = client.get('/api/notes/'+str(normal['id'])).json()
    assert '到了图书馆' in updated['content']
    assert client.get('/api/notes/'+str(note_id)).json()['content']==note['content']
    app.dependency_overrides[get_current_user_from_token] = lambda:stranger
    app.dependency_overrides[get_optional_current_user_from_token] = lambda:stranger
    assert client.get('/api/notes/'+str(note_id)).status_code==404
