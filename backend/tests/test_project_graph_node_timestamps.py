import base64
import io
import zipfile

import msgpack
import pytest
from fastapi import HTTPException

from backend.core.project_graph.codec import import_prg, export_prg
from backend.core.project_graph.collaboration import GraphProvider
from backend.core.project_graph.node_timestamps import complete_node_timestamps
from backend.tests.test_project_graph_files import library
from backend.core.collaboration.objects import ObjectValue
from sqlmodel import Session


def archive(stage):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('stage.msgpack', msgpack.packb(stage, use_bin_type=True))
    return out.getvalue()


def test_node_dates_survive_prg_roundtrip_references_and_collaboration():
    content = archive([{'_': 'LineEdge', 'uuid': 'e', 'nodes': [
        {'_': 'TextNode', 'uuid': 'n', 'text': 'idea', 'createdAt': 1791000000000, 'updatedAt': 1791000000100}
    ]}, {'$': '/0/nodes/0'}])
    objects = import_prg(content)
    node = objects['n']
    edited = {**objects, 'n': {**node, 'text': 'edited', 'updatedAt': 1791000000200}}
    changes = [{'id': 'n', 'before': node, 'after': edited['n']}]
    GraphProvider().validate(edited, changes)
    reopened = import_prg(export_prg(content, edited))
    assert reopened == edited
    assert reopened['n']['createdAt'] == node['createdAt']
    assert reopened['n']['updatedAt'] == 1791000000200
    assert reopened['e'] == objects['e']


def test_legacy_completion_is_stable_and_saved_in_archive():
    content = archive([{'_': 'TextNode', 'uuid': 'n', 'text': 'old'}])
    objects = import_prg(content)
    filled = complete_node_timestamps(objects, 1000, 3000)
    assert filled['n']['createdAt'] == filled['n']['updatedAt'] == 1000
    assert complete_node_timestamps(filled, 4000, 5000) == filled
    assert import_prg(export_prg(content, filled)) == filled


def test_completion_priority_and_gallery():
    objects = {'a': {'uuid': 'a', 'createdAt': 1000, 'updatedAt': 2000},
               'b': {'uuid': 'b', 'createdAt': None, 'updatedAt': 2000},
               '@gallery:item:test': {'objects': {'c': {'uuid': 'c', 'createdAt': None}}}}
    filled = complete_node_timestamps(objects, 1500, 3000)
    assert filled['a'] == objects['a']
    assert filled['b']['createdAt'] == 2000
    assert filled['@gallery:item:test']['objects']['c']['createdAt'] == 1500
    assert complete_node_timestamps({'n': {'uuid': 'n'}}, now=3000)['n']['createdAt'] == 3000


def test_library_open_completes_once_and_collaboration_repair_is_durable(library):
    client, _, engine = library
    content = archive([{'_': 'TextNode', 'uuid': 'n', 'text': 'old'}])
    created = client.post('/files', json={'title': 'legacy', 'content': base64.b64encode(content).decode()}).json()
    rid = created['id']
    opened = client.get(f'/files/{rid}').json()
    node = import_prg(base64.b64decode(opened['content']))['n']
    assert node['createdAt'] == node['updatedAt'] == opened['createdAt']
    assert opened['revision'] == created['revision'] + 1
    assert client.get(f'/files/{rid}').json() == opened
    enabled = client.post(f'/files/{rid}/collaboration', json={'expectedRevision': opened['revision']})
    assert enabled.status_code == 200
    # Simulate a collaboration baseline from before timestamps were supported.
    with Session(engine) as session:
        row = session.get(ObjectValue, (rid, 'n'))
        row.value = {**row.value, 'createdAt': None, 'updatedAt': node['updatedAt']}
        session.add(row); session.commit()
    repaired = client.get(f'/files/{rid}/collaboration').json()
    assert repaired['objects']['n'] == node
    assert repaired['revision'] == 1
    assert client.get(f'/files/{rid}/collaboration').json() == repaired
    log = client.get(f'/files/{rid}/collaboration?after=0').json()['operations']
    assert len(log) == 1 and log[0]['changes'][0]['after'] == node


@pytest.mark.parametrize('times', [
    {'createdAt': -1}, {'createdAt': True}, {'updatedAt': 'yesterday'}, {'updatedAt': 1.2},
    {'createdAt': 2**53}, {'createdAt': 2000, 'updatedAt': 1000},
])
def test_invalid_node_times_rejected_at_archive_and_collaboration_boundary(times):
    node = {'_': 'TextNode', 'uuid': 'n', 'text': 'idea', **times}
    with pytest.raises(HTTPException) as error:
        import_prg(archive([node]))
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        GraphProvider().validate({'n': node, '@order': {'value': ['n']}}, [{'id': 'n', 'before': None, 'after': node}])
    assert error.value.status_code == 422
