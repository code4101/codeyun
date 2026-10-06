import json

import pytest

from backend.core.codex.dot import CurrentDotClient, DotConnectionError


def test_existing_request_is_reconciled_without_resending():
    client = CurrentDotClient(session=object())
    client.messages = lambda room: [{'id':'original', 'role':'user', 'account_user_id':'owner', 'request_id':'same'}]
    assert client.send({'room_id':'room','member_id':'dot'}, request_id='same', text='task')['id'] == 'original'


def test_response_requires_marker_from_assistant_not_user():
    client = CurrentDotClient(session=object())
    client.messages = lambda room: [
        {'id':'private', 'role':'user', 'account_user_id':'dot', 'content':{'text':'unrelated'}},
        {'id':'echo', 'role':'user', 'account_user_id':'owner', 'content':{'text':'marker'}},
        {'id':'answer', 'role':'user', 'account_user_id':'dot', 'content':{'text':'marker completed'}}]
    assert client.reply({'room_id':'room','member_id':'dot'}, 'marker') == {
        'message_id':'answer', 'text':'marker completed'}


def test_transport_error_never_exposes_credentials(tmp_path):
    auth = tmp_path/'auth.json'
    auth.write_text(json.dumps({'tokens':{'access_token':'private-secret', 'account_id':'owner'}}))
    class Session:
        def request(self, *args, **kwargs):
            raise RuntimeError(kwargs['headers']['Authorization'])
    client = CurrentDotClient(session=Session(), auth_path=auth)
    with pytest.raises(DotConnectionError, match='outcome is unknown') as error:
        client.current()
    assert 'private-secret' not in str(error.value)


def test_request_identity_is_stable_and_group_scoped():
    assert CurrentDotClient.request_id('task:1') == CurrentDotClient.request_id('task:1')
    assert CurrentDotClient.request_id('task:1') != CurrentDotClient.request_id('task:2')
