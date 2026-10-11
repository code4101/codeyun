"""Pure pre-click authorization policy; no scene/click simulation."""
import pytest

from backend.core.fanxiu.data_annotation.tasks.daily_task_reward_navigation import daily_task_reward_cover_rebind


@pytest.mark.parametrize('source,actual', [(296,549),(549,296)])
def test_only_declared_lundao_pair_can_rebind_before_input(source, actual):
    assert daily_task_reward_cover_rebind('lundao', source_scene_id=source,
        expected_scene_id=source, actual_scene_id=actual, rebind_count=0) == actual


@pytest.mark.parametrize('domain,source,expected,actual,count', [
    ('lundao',296,296,549,1), ('lundao',549,549,296,1),
    ('lundao',296,296,304,0), ('lundao',296,296,391,0),
    ('lundao',296,296,550,0), ('lundao',296,296,296,0),
    ('lundao',296,549,296,0), ('qixi_mojie',296,296,549,0),
    ('lingmai',285,285,312,0),
])
def test_second_flip_or_undeclared_source_cannot_authorize_rebinding(domain,source,expected,actual,count):
    assert daily_task_reward_cover_rebind(domain, source_scene_id=source,
        expected_scene_id=expected, actual_scene_id=actual, rebind_count=count) is None
