import pytest

from backend.core.fanxiu.data_annotation.tasks.login_game import LoginGameTaskMixin


@pytest.mark.parametrize(('text', 'expected'), [
    ('AppVer:2 ResVer:x 正在初始 台化资源.69%(本次不会占用额外存储空间)', True),
    ('AppVer:2 正在初始化化资源 1%', True),
    ('AppVer:2 ResVer:x 69%', False),
    ('69%(本次不会占用额外存储空间)', False),
    ('AppVer:2 ResVer:x 本次不会占用额外存储空间', False),
])
def test_loading_text_requires_specific_evidence(text, expected):
    assert LoginGameTaskMixin._is_resource_loading_frame(text) is expected
