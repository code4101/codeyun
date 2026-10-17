"""Exact record/entry identity separates same-sized forwarded attachments."""
from io import BytesIO
import hashlib
from pathlib import Path
import sqlite3

from PIL import Image
import pytest

from pyxllib.autogui.wechat_db import WeChatDbStorage, message_table_name


@pytest.fixture
def forwarded_storage(tmp_path, monkeypatch):
    chat, record = '123@chatroom', '1234567890abcdef'
    storage = WeChatDbStorage(tmp_path / 'archive' / 'db_storage')
    storage.paths.message.parent.mkdir(parents=True)
    png = BytesIO()
    Image.new('RGB', (48, 32), '#2156ab').save(png, format='PNG')
    image = bytes(value ^ 156 for value in png.getvalue())
    video = b'\x00\x00\x00\x18ftypisom' + b'fixture-video' * 32
    record_root = tmp_path / 'account' / 'msg' / 'attach' / hashlib.md5(chat.encode()).hexdigest() / '2026-10' / 'Rec'
    for record_id in (record, 'fedcba0987654321'):
        (record_root / record_id / 'Img').mkdir(parents=True)
        (record_root / record_id / 'V').mkdir()
        (record_root / record_id / 'Img' / '2').write_bytes(image)
        (record_root / record_id / 'Img' / '2_t').write_bytes(image)
        # A competing record deliberately has equal byte length.
        content = video if record_id == record else video[:-1] + b'X'
        (record_root / record_id / 'V' / '4.mp4').write_bytes(content)
    xml = ('<msg><appmsg><type>19</type><recorditem><![CDATA[<recordinfo><datalist>'
           '<dataitem datatype="2"><datadesc>[图片]</datadesc></dataitem>'
           '<dataitem datatype="4"><datadesc>[视频]</datadesc><datasize>' + str(len(video)) + '</datasize>'
           '<fullmd5>' + hashlib.md5(video).hexdigest() + '</fullmd5></dataitem>'
           '</datalist></recordinfo>]]></recorditem></appmsg></msg>')
    # Entries retain their real array indexes: use two text entries before 2,
    # and a text entry before video index 4.
    xml = xml.replace('<datalist>', '<datalist><dataitem datatype="1"/><dataitem datatype="1"/>')
    xml = xml.replace('<dataitem datatype="4">', '<dataitem datatype="1"/><dataitem datatype="4">')
    with sqlite3.connect(storage.paths.message) as conn:
        table = message_table_name(chat)
        conn.execute(f'CREATE TABLE "{table}" (local_id INTEGER, message_content TEXT, packed_info_data BLOB)')
        conn.execute(f'INSERT INTO "{table}" VALUES (35, ?, ?)', (xml, b'\xffJ\x12\x0a\x10' + record.encode()))
    with sqlite3.connect(storage.paths.resource) as conn:
        conn.executescript('CREATE TABLE ChatName2Id (user_name TEXT);'
                           'CREATE TABLE MessageResourceInfo (message_local_id INTEGER, message_id INTEGER, chat_id INTEGER);'
                           'CREATE TABLE MessageResourceDetail (message_id INTEGER, resource_id INTEGER, type INTEGER, size INTEGER, data_index INTEGER, packed_info BLOB);')
        conn.execute('INSERT INTO ChatName2Id VALUES (?)', (chat,))
        conn.execute('INSERT INTO MessageResourceInfo VALUES (35, 1, 1)')
        for rid, kind, size, index in ((1, 65537, len(image), 2), (2, 262145, len(image), 2), (3, 131074, len(video), 4)):
            conn.execute('INSERT INTO MessageResourceDetail VALUES (1, ?, ?, ?, ?, ?)', (rid, kind, size, index, b''))
    monkeypatch.setattr(storage, '_resolve_chat_username', lambda value: value)
    monkeypatch.setattr(storage, '_wechat_account_root', lambda: tmp_path / 'account')
    monkeypatch.setattr(storage, '_wechat_v4_image_xor_key', lambda _: 156)
    return storage, chat, record_root / record / 'V' / '4.mp4', video


def test_public_forward_resources_keep_versions_and_exact_video_across_restart(forwarded_storage, monkeypatch):
    storage, chat, video_path, video = forwarded_storage
    result = storage.message_resources(chat, 35)
    assets = [item['export'] for item in result['items']]
    assert [(item['kind'], item['variant']) for item in assets] == [
        ('image', 'standard'), ('image', 'thumbnail'), ('video', 'standard')]
    assert all(item['readable'] for item in assets)
    assert Path(assets[-1]['stored_path']).read_bytes() == video
    assert assets[-1]['source_path'] == str(video_path)
    # The manifest must retain semantic keys for bulk/archive consumers too.
    reopened = WeChatDbStorage(storage.root)
    monkeypatch.setattr(reopened, '_resolve_chat_username', lambda value: value)
    monkeypatch.setattr(reopened, '_wechat_account_root', storage._wechat_account_root)
    monkeypatch.setattr(reopened, '_wechat_v4_image_xor_key', lambda _: 156)
    second = reopened.message_resources(chat, 35)
    assert second['items'][-1]['export']['md5'] == hashlib.md5(video).hexdigest()
    assert 'record:1234567890abcdef:4:video:standard' in storage._load_exported_resource_manifest()


@pytest.mark.parametrize('damage', ['truncate', 'equal_size_corrupt'])
def test_partial_or_wrong_video_never_becomes_readable_export(forwarded_storage, damage):
    storage, chat, video_path, video = forwarded_storage
    video_path.write_bytes(video[:-1] if damage == 'truncate' else video[:-1] + b'X')
    result = storage.message_resources(chat, 35)
    item = next(item for item in result['items'] if item['type'] == 131074)
    assert 'export' not in item
    assert item['export_status'] == 'unavailable'


def test_same_size_corrupt_export_recovers_from_valid_local_video(forwarded_storage):
    storage, chat, _, video = forwarded_storage
    first = storage.message_resources(chat, 35)
    asset = next(item['export'] for item in first['items'] if item['type'] == 131074)
    output = Path(asset['stored_path'])
    output.write_bytes(video[:-1] + b'X')
    second = storage.message_resources(chat, 35)
    assert next(item['export'] for item in second['items'] if item['type'] == 131074)['readable']
    assert output.read_bytes() == video


@pytest.mark.parametrize('damage', [None, 'truncate', 'digest', 'wrong_record', 'no_digest'])
@pytest.mark.parametrize('layout', ['File', 'F'])
def test_forwarded_json_file_is_bound_to_record_entry_and_integrity(forwarded_storage, damage, layout):
    storage, chat, video_path, _ = forwarded_storage
    content = b'{"env":{"HTTP_PROXY":"http://example.invalid"}}'
    record_root = video_path.parent.parent
    if damage == 'wrong_record':
        record_root = record_root.parent / 'fedcba0987654321'
    folder = record_root / layout
    if layout == 'F':
        folder = folder / '5'
    folder.mkdir(parents=True)
    (folder / ('settings_副本.json' if layout == 'F' else '5.json')).write_bytes(content[:-1] if damage == 'truncate' else content)
    digest = '0' * 32 if damage == 'digest' else hashlib.md5(content).hexdigest()
    if damage == 'no_digest':
        digest = ''
    with sqlite3.connect(storage.paths.message) as conn:
        table = message_table_name(chat)
        xml = conn.execute(f'SELECT message_content FROM "{table}"').fetchone()[0]
        xml = xml.replace('</datalist>', '<dataitem datatype="8"><datatitle>settings_副本.json</datatitle>'
                          f'<datasize>{len(content)}</datasize><fullmd5>{digest}</fullmd5>'
                          '</dataitem></datalist>')
        conn.execute(f'UPDATE "{table}" SET message_content=?', (xml,))
    with sqlite3.connect(storage.paths.resource) as conn:
        conn.execute('INSERT INTO MessageResourceDetail VALUES (1, 4, 3, 0, 5, ?)', (b'',))
    item = next(item for item in storage.message_resources(chat, 35)['items'] if str(item['data_index']) == '5')
    assert item['file_name'] == 'settings_副本.json' and item['datatype'] == 8
    if damage not in (None, 'no_digest'):
        assert 'export' not in item
    else:
        asset = item['export']
        assert asset['kind'] == 'file' and asset['readable']
        assert asset['attachment_name'] == 'settings_副本.json'
        assert Path(asset['stored_path']).read_bytes() == content
