"""Gallery archive, API durability and collaboration-provider invariants."""
import base64
import copy
import io
import zipfile

import msgpack
import pytest
from fastapi import HTTPException

from backend.core.project_graph.codec import import_prg, export_prg, has_prg_content
from backend.core.project_graph.collaboration import validate_objects, provider
from backend.tests.test_project_graph_files import library  # isolated public API fixture


def document():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('stage.msgpack', msgpack.packb([{'_': 'TextNode', 'uuid': 'live', 'text': '画布'}]))
        archive.writestr('metadata.msgpack', msgpack.packb({'version': '2.7.0'}))
        archive.writestr('attachments/image.png', b'shared-image')
        archive.writestr('other-extension/data.bin', b'opaque')
        archive.writestr('gallery/index.msgpack', msgpack.packb({'version': 1, 'groups': [{'id': 'todo', 'title': '待办'}]}))
        archive.writestr('gallery/items/task.msgpack', msgpack.packb({
            'id': 'task', 'groupId': 'todo', 'title': '任务', 'createdAt': 10,
            'objects': {'stored': {'_': 'TextNode', 'uuid': 'stored', 'text': '任务', 'details': [{'text': '正文'}]},
                        '@order': {'value': ['stored']}}}))
    return output.getvalue()


def test_archive_roundtrip_assets_and_consumed_item_members():
    content = document()
    objects = import_prg(content)
    validate_objects(objects)
    exported = export_prg(content, objects)
    assert import_prg(exported) == objects
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        assert archive.read('attachments/image.png') == b'shared-image'
        assert archive.read('other-extension/data.bin') == b'opaque'
        assert msgpack.unpackb(archive.read('metadata.msgpack'), raw=False)['version'] == '2.7.0'
    item = objects.pop('@gallery:item:task')
    objects['stored'] = item['objects']['stored']
    objects['@order']['value'].append('stored')
    result = export_prg(exported, objects)
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert 'gallery/items/task.msgpack' not in archive.namelist()
    assert set(import_prg(result)['@order']['value']) == {'live', 'stored'}


@pytest.mark.parametrize('fault', ['duplicate', 'external-reference', 'group', 'version'])
def test_invalid_gallery_never_passes_provider_validation(fault):
    objects = import_prg(document())
    if fault == 'duplicate':
        objects['stored'] = copy.deepcopy(objects['@gallery:item:task']['objects']['stored'])
        objects['@order']['value'].append('stored')
    elif fault == 'external-reference':
        objects['@gallery:item:task']['objects']['stored']['target'] = {'$graphRef': 'live'}
    elif fault == 'group':
        objects['@gallery:item:task']['groupId'] = 'missing'
    else:
        objects['@gallery:index']['version'] = 2
    with pytest.raises(HTTPException):
        provider.validate(objects, [])


def test_gallery_only_daily_canvas_counts_as_authored_content():
    content = document()
    objects = import_prg(content)
    del objects['live']
    del objects['@attachment:image.png']
    objects['@order']['value'] = []
    assert has_prg_content(export_prg(content, objects))
    del objects['@gallery:item:task']
    assert has_prg_content(export_prg(content, objects)), 'explicit gallery group configuration must survive a daily canvas reopen'


def test_public_api_preserves_gallery_across_reopen_export_copy_and_revision_conflict(library):
    client, _, _ = library
    content = base64.b64encode(document()).decode()
    created = client.post('/files', json={'title': '图库项目', 'content': content}).json()
    rid = created['id']
    opened = client.get(f'/files/{rid}').json()
    assert import_prg(base64.b64decode(opened['content']))['@gallery:item:task']['title'] == '任务'
    objects = import_prg(base64.b64decode(opened['content']))
    objects['@gallery:item:task']['title'] = '已修改'
    updated = base64.b64encode(export_prg(document(), objects)).decode()
    assert client.put(f'/files/{rid}/content', json={'content': updated, 'expectedRevision': created['revision']}).status_code == 200
    assert client.put(f'/files/{rid}/content', json={'content': content, 'expectedRevision': created['revision']}).status_code == 409
    persisted = client.get(f'/files/{rid}').json()
    assert persisted['content'] == updated
    copied = client.post('/files', json={'title': '副本', 'content': persisted['content']}).json()
    assert copied['id'] != rid
    assert client.get(f'/files/{copied["id"]}').json()['content'] == updated
    enabled = client.post(f'/files/{rid}/collaboration', json={'expectedRevision': persisted['revision']})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()['objects']['@gallery:item:task']['title'] == '已修改'
    reread = client.get(f'/files/{rid}').json()
    assert import_prg(base64.b64decode(reread['content']))['@gallery:item:task']['title'] == '已修改'


def test_invalid_extension_write_keeps_previous_archive(library):
    client, _, _ = library
    good = document()
    created = client.post('/files', json={'title': '安全保存', 'content': base64.b64encode(good).decode()}).json()
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as source, zipfile.ZipFile(output, 'w') as archive:
        for name in source.namelist():
            archive.writestr(name, msgpack.packb({'version': 999, 'groups': []}) if name == 'gallery/index.msgpack' else source.read(name))
    response = client.put(f'/files/{created["id"]}/content', json={'content': base64.b64encode(output.getvalue()).decode(), 'expectedRevision': created['revision']})
    assert response.status_code == 422
    assert base64.b64decode(client.get(f'/files/{created["id"]}').json()['content']) == good
