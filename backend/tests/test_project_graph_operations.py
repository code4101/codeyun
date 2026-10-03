from backend.tests.test_project_graph_files import library, prg
from backend.tests.test_project_graph_collaboration import api, prepare, headers, join, lock, receive, A


def create(client, title, kind='document', parent=0):
    response = client.post('/files', json={'title': title, 'kind': kind, 'parentId': parent, 'content': prg() if kind == 'document' else ''})
    assert response.status_code == 200, response.text
    return response.json()['id']


def test_move_folder_tree_nested_selection_and_collision_rollback(library):
    client, _, _ = library
    folder = create(client, 'parent', 'folder')
    child = create(client, 'child', 'folder', folder)
    file = create(client, 'file', parent=child)
    target = create(client, 'target', 'folder')
    response = client.post('/files/operations', json={'action': 'move', 'ids': [folder, child, file], 'parentId': target})
    assert response.status_code == 200, response.text
    assert client.get(f'/files/{folder}').json()['parentId'] == target
    assert client.get(f'/files/{child}').json()['parentId'] == folder
    assert client.get(f'/files/{file}').json()['parentId'] == child
    assert client.post('/files/operations', json={'action': 'move', 'ids': [folder], 'parentId': child}).status_code == 422
    first = create(client, 'first')
    conflict = create(client, 'conflict')
    create(client, 'conflict', parent=target)
    assert client.post('/files/operations', json={'action': 'move', 'ids': [first, conflict], 'parentId': target}).status_code == 409
    assert client.get(f'/files/{first}').json()['parentId'] == 0


def test_copy_subtree_names_and_recursive_delete(library):
    client, _, _ = library
    folder = create(client, 'folder', 'folder')
    file = create(client, 'file', parent=folder)
    response = client.post('/files/operations', json={'action': 'copy', 'ids': [folder, file]})
    assert response.status_code == 200, response.text
    copied = response.json()['entries']
    assert len(copied) == 1
    assert copied[0]['title'] == 'folder 副本'
    repeated = client.post('/files/operations', json={'action': 'copy', 'ids': [folder]})
    assert repeated.json()['entries'][0]['title'] == 'folder 副本 2'
    rows = client.get('/files').json()['entries']
    copied_file = next(row for row in rows if row['parentId'] == copied[0]['id'])
    assert client.get(f'/files/{copied_file["id"]}').json()['content'] == client.get(f'/files/{file}').json()['content']
    response = client.post('/files/operations', json={'action': 'delete', 'ids': [folder, file]})
    assert response.status_code == 200, response.text
    assert set(response.json()['ids']) == {folder, file}
    assert client.get(f'/files/{folder}').status_code == 404
    assert client.get(f'/files/{file}').status_code == 404
    assert client.get(f'/files/{copied_file["id"]}').status_code == 200


def test_batch_permission_failure_does_not_mutate_other_files(library):
    client, owner, _ = library
    own = create(client, 'own')
    owner['id'] = 2
    foreign = create(client, 'foreign')
    owner['id'] = 1
    assert client.post('/files/operations', json={'action': 'delete', 'ids': [own, foreign]}).status_code == 404
    assert client.get(f'/files/{own}').status_code == 200


def test_shared_collaborative_copy_captures_latest_content_without_grants(api):
    client, tokens = api
    rid, objects = prepare(client, tokens)
    with client.websocket_connect(f'/files/{rid}/collaboration/socket') as socket:
        join(socket, tokens[1])
        lease = lock(socket, A)
        socket.send_json({'type': 'commit', 'id': 'save', 'mutationId': 'copy-test', 'tokens': lease,
            'changes': [{'id': A, 'before': objects[A], 'after': {**objects[A], 'text': 'latest content'}}]})
        receive(socket, 'commit', 'save')
    copied = client.post('/files/operations', headers=headers(tokens, 3), json={'action': 'copy', 'ids': [rid]})
    assert copied.status_code == 200, copied.text
    copy_id = copied.json()['entries'][0]['id']
    source = client.get(f'/files/{rid}', headers=headers(tokens, 3)).json()
    copy = client.get(f'/files/{copy_id}', headers=headers(tokens, 3)).json()
    assert copy['content'] == source['content']
    assert copy['collaborative'] is False
    assert client.get(f'/files/{copy_id}', headers=headers(tokens, 2)).status_code == 404
