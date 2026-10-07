import io
import zipfile

import msgpack
import pytest
from fastapi import HTTPException

from backend.core.project_graph.codec import import_prg, export_prg
from backend.core.project_graph.collaboration import GraphProvider


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


def test_legacy_nodes_keep_unknown_dates_through_open_and_export():
    content = archive([{'_': 'TextNode', 'uuid': 'n', 'text': 'old'}])
    objects = import_prg(content)
    assert 'createdAt' not in objects['n'] and 'updatedAt' not in objects['n']
    assert import_prg(export_prg(content, objects)) == objects
    edited = {**objects, 'n': {**objects['n'], 'text': 'edited', 'createdAt': None, 'updatedAt': 1791000000100}}
    assert import_prg(export_prg(content, edited)) == edited


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
