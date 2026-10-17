"""Emoji exports must use exact original bytes rather than an encrypted payload."""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
import hashlib

from PIL import Image
import pytest

from pyxllib.autogui.wechat_emoji import read_emoji_message


@pytest.mark.parametrize('damage', [None, 'size', 'digest'])
def test_original_emoji_download_and_cache(tmp_path, monkeypatch, damage):
    stream = BytesIO()
    Image.new('RGB', (8, 8), 'white').save(stream, format='GIF')
    data = stream.getvalue()
    digest = hashlib.md5(data).hexdigest()
    row = {'local_id': 549, 'local_type_normalized': 47,
           'message_content': f'<msg><emoji md5="{digest}" len="{len(data)}" '
                              'cdnurl="https://wxapp.tc.qq.com/test"/></msg>'}
    storage = SimpleNamespace(root=tmp_path / 'archive', list_messages=Mock(return_value={'items': [row]}))
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [data[:-1] if damage == 'size' else data[:-1] + b'X' if damage else data]
    download = Mock(return_value=response)
    monkeypatch.setattr('requests.get', download)
    if damage:
        with pytest.raises(ValueError, match='校验'):
            read_emoji_message(storage, 'chat', 549)
    else:
        asset = read_emoji_message(storage, 'chat', 549)['items'][0]['export']
        assert asset['readable'] and asset['frames'] == 1 and asset['md5'] == digest
        read_emoji_message(storage, 'chat', 549)
        assert download.call_count == 1


def test_other_message_is_not_downloaded(tmp_path, monkeypatch):
    storage = SimpleNamespace(root=tmp_path, list_messages=lambda *a, **k: {'items': [{'local_id': 549, 'local_type_normalized': 1}]})
    download = Mock(side_effect=AssertionError('text must not download'))
    monkeypatch.setattr('requests.get', download)
    assert read_emoji_message(storage, 'chat', 549) == {}
