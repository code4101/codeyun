"""Pure loading-text classification; real login is accepted in the live game."""
import pytest
from backend.core.fanxiu.data_annotation.tasks.login_game import LoginGameTaskMixin

@pytest.mark.parametrize('text,expected', [
    ('AppVer:2.46 正在初始化资源 83%', True),
    ('AppVer 正 在 初 始 化 化 资 源 69%', True),
    ('AppVer 正在加载资源', True),
    ('AppVer 凡人修仙传', False),
    ('正在初始化资源', False),
    ('AppVer 网络错误', False),
    ('AppVer 初始化错误资源', False),
])
def test_loading_text_needs_version_and_resource_loading_evidence(text, expected):
    assert LoginGameTaskMixin._is_resource_loading_frame(text) is expected
